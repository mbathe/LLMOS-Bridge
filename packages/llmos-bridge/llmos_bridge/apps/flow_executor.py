"""Flow executor — Executes the 18 flow constructs defined in .app.yaml.

Compiles FlowStep trees into executable operations using:
- Direct module execution (via execute_tool callback)
- Agent sub-runs (via agent_runner callback)
- Expression engine (for conditions, templates, dynamic dispatch)
- EventBus (for emit/wait constructs)

The FlowExecutor does NOT depend on the IML PlanExecutor — it's a
standalone interpreter for the app language's flow constructs. This keeps
the app language decoupled from the IML protocol layer.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable, Awaitable

from .expression import ExpressionContext, ExpressionEngine
from .models import (
    BranchConfig,
    CatchHandler,
    EndConfig,
    FlowStep,
    FlowStepType,
    LoopFlowConfig,
    MacroDefinition,
    MapConfig,
    ParallelConfig,
    RaceConfig,
    ReduceConfig,
    SpawnConfig,
)

logger = logging.getLogger(__name__)


# ─── Result types ─────────────────────────────────────────────────────


@dataclass
class StepResult:
    """Result of executing a single flow step."""
    step_id: str
    success: bool
    output: Any = None
    error: str | None = None
    duration_ms: float = 0.0
    children: list[StepResult] = field(default_factory=list)


@dataclass
class FlowResult:
    """Result of executing an entire flow."""
    success: bool
    results: dict[str, StepResult] = field(default_factory=dict)
    output: Any = None
    error: str | None = None
    status: str = "success"  # success | failure | cancelled
    duration_ms: float = 0.0


class FlowExecutionError(Exception):
    """Raised when flow execution fails unrecoverably."""
    pass


class FlowEndSignal(Exception):
    """Raised by 'end' step to terminate flow."""
    def __init__(self, status: str = "success", output: Any = None):
        self.status = status
        self.output = output


class FlowGotoSignal(Exception):
    """Raised by 'goto' step to jump to another step by ID."""
    def __init__(self, target_id: str):
        self.target_id = target_id


# ─── Flow Executor ────────────────────────────────────────────────────


class FlowCheckpoint:
    """Serializable checkpoint of flow execution state.

    Enables resuming a flow after interruption. Stored via KV store.
    """

    def __init__(
        self,
        flow_id: str,
        completed_steps: dict[str, Any] | None = None,
        current_step_index: int = 0,
    ):
        self.flow_id = flow_id
        self.completed_steps: dict[str, Any] = completed_steps or {}
        self.current_step_index = current_step_index

    def to_dict(self) -> dict[str, Any]:
        return {
            "flow_id": self.flow_id,
            "completed_steps": self.completed_steps,
            "current_step_index": self.current_step_index,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> FlowCheckpoint:
        return cls(
            flow_id=d["flow_id"],
            completed_steps=d.get("completed_steps", {}),
            current_step_index=d.get("current_step_index", 0),
        )


class FlowExecutor:
    """Executes FlowStep trees from .app.yaml flow definitions.

    Dependencies are injected via callbacks to keep this decoupled:
    - execute_action: (module_id, action_name, params) → result
    - run_agent: (agent_id, input_text) → result
    - emit_event: (topic, event_dict) → None
    - wait_event: (topic, filter_expr, timeout) → event_dict
    - approval_handler: (message, options, timeout) → user_choice

    Supports checkpoint/resume:
    - Pass kv_store + flow_id to enable checkpointing
    - After each step completes, state is saved
    - On resume, completed steps are skipped and their results restored
    """

    def __init__(
        self,
        *,
        expr_engine: ExpressionEngine | None = None,
        expr_context: ExpressionContext | None = None,
        execute_action: Callable[[str, str, dict[str, Any]], Awaitable[Any]] | None = None,
        run_agent: Callable[[str, str], Awaitable[Any]] | None = None,
        emit_event: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        wait_event: Callable[[str, str, float], Awaitable[dict[str, Any]]] | None = None,
        approval_handler: Callable[[str, list, float], Awaitable[str]] | None = None,
        spawn_app: Callable[[str, str, float], Awaitable[Any]] | None = None,
        macros: list[MacroDefinition] | None = None,
        kv_store: Any = None,
        flow_id: str | None = None,
    ):
        self._expr = expr_engine or ExpressionEngine()
        self._ctx = expr_context or ExpressionContext()
        self._execute_action = execute_action
        self._run_agent = run_agent
        self._emit_event = emit_event
        self._wait_event = wait_event
        self._approval_handler = approval_handler
        self._spawn_app = spawn_app
        self._macros: dict[str, MacroDefinition] = {m.name: m for m in (macros or [])}
        self._results: dict[str, StepResult] = {}
        self._stopped = False
        self._kv_store = kv_store
        self._flow_id = flow_id or str(uuid.uuid4())[:12]
        self._checkpoint: FlowCheckpoint | None = None

    @property
    def results(self) -> dict[str, StepResult]:
        return dict(self._results)

    def stop(self) -> None:
        self._stopped = True

    async def execute(
        self, steps: list[FlowStep], *, resume: bool = False
    ) -> FlowResult:
        """Execute a list of flow steps sequentially.

        Supports:
        - goto by building a step index and re-routing on FlowGotoSignal
        - checkpoint/resume: if kv_store is set, saves state after each step;
          if resume=True, loads checkpoint and skips already-completed steps.
        """
        start = time.monotonic()
        self._stopped = False
        self._results.clear()

        # Load checkpoint if resuming
        start_index = 0
        if resume:
            checkpoint = await self._load_checkpoint()
            if checkpoint:
                self._checkpoint = checkpoint
                start_index = checkpoint.current_step_index
                # Restore completed results into expression context
                for step_id, output in checkpoint.completed_steps.items():
                    self._results[step_id] = StepResult(
                        step_id=step_id, success=True, output=output,
                    )
                    self._ctx.results[step_id] = output
                logger.info(
                    "Resuming flow %s from step %d (%d steps already done)",
                    self._flow_id, start_index, len(checkpoint.completed_steps),
                )

        # Build step index for goto
        step_index: dict[str, int] = {}
        for idx, s in enumerate(steps):
            if s.id:
                step_index[s.id] = idx

        max_goto_jumps = 100

        try:
            i = start_index
            goto_jumps = 0
            while i < len(steps):
                if self._stopped:
                    break
                try:
                    await self._execute_step(steps[i])
                    i += 1
                    # Save checkpoint after each successful step
                    await self._save_checkpoint(i)
                except FlowGotoSignal as g:
                    goto_jumps += 1
                    if goto_jumps > max_goto_jumps:
                        raise FlowExecutionError(
                            f"Max goto jumps ({max_goto_jumps}) exceeded — possible infinite loop"
                        )
                    if g.target_id not in step_index:
                        raise FlowExecutionError(f"goto target '{g.target_id}' not found in flow")
                    i = step_index[g.target_id]

            # Flow completed — clear checkpoint
            await self._clear_checkpoint()

            return FlowResult(
                success=True,
                results=dict(self._results),
                output=self._get_last_output(),
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except FlowEndSignal as end:
            await self._clear_checkpoint()
            return FlowResult(
                success=end.status == "success",
                results=dict(self._results),
                output=end.output,
                status=end.status,
                duration_ms=(time.monotonic() - start) * 1000,
            )
        except FlowExecutionError as e:
            # Don't clear checkpoint on failure — allow retry
            return FlowResult(
                success=False,
                results=dict(self._results),
                error=str(e),
                status="failure",
                duration_ms=(time.monotonic() - start) * 1000,
            )

    # ─── Checkpoint persistence ──────────────────────────────────────────

    def _checkpoint_key(self) -> str:
        return f"llmos:flow:checkpoint:{self._flow_id}"

    async def _save_checkpoint(self, next_step_index: int) -> None:
        """Save flow state after each step for resume support."""
        if self._kv_store is None:
            return
        completed = {}
        for step_id, sr in self._results.items():
            if sr.success:
                try:
                    completed[step_id] = json.loads(json.dumps(sr.output, default=str))
                except (TypeError, ValueError):
                    completed[step_id] = str(sr.output)
        cp = FlowCheckpoint(
            flow_id=self._flow_id,
            completed_steps=completed,
            current_step_index=next_step_index,
        )
        try:
            await self._kv_store.set(
                self._checkpoint_key(),
                json.dumps(cp.to_dict()),
            )
        except Exception as e:
            logger.debug("Could not save flow checkpoint: %s", e)

    async def _load_checkpoint(self) -> FlowCheckpoint | None:
        """Load a saved checkpoint from KV store."""
        if self._kv_store is None:
            return None
        try:
            raw = await self._kv_store.get(self._checkpoint_key())
            if raw:
                data = json.loads(raw) if isinstance(raw, str) else raw
                return FlowCheckpoint.from_dict(data)
        except Exception as e:
            logger.debug("Could not load flow checkpoint: %s", e)
        return None

    async def _clear_checkpoint(self) -> None:
        """Remove checkpoint after successful flow completion."""
        if self._kv_store is None:
            return
        try:
            await self._kv_store.delete(self._checkpoint_key())
        except Exception:
            pass

    # ─── Step execution ──────────────────────────────────────────────────

    async def _execute_step(self, step: FlowStep) -> StepResult:
        """Execute a single flow step, dispatching by type."""
        step_type = step.infer_type()
        start = time.monotonic()

        try:
            result = await self._dispatch_step(step, step_type)
        except (FlowEndSignal, FlowGotoSignal):
            raise
        except Exception as e:
            result = StepResult(
                step_id=step.id or f"anon_{id(step)}",
                success=False,
                error=str(e),
            )
            if step.on_error == "fail":
                raise FlowExecutionError(f"Step '{step.id}' failed: {e}") from e
            elif step.on_error == "skip":
                pass  # continue to next step
            elif step.on_error not in ("continue", ""):
                raise FlowExecutionError(f"Step '{step.id}' failed: {e}") from e

        result.duration_ms = (time.monotonic() - start) * 1000

        if step.id:
            self._results[step.id] = result
            # Update expression context so later steps can access {{result.step_id}}
            self._ctx.results[step.id] = result.output if result.success else {"_error": result.error}

        return result

    async def _dispatch_step(self, step: FlowStep, step_type: FlowStepType) -> StepResult:
        """Dispatch to the appropriate handler based on step type."""
        step_id = step.id or f"anon_{id(step)}"

        handlers = {
            FlowStepType.action: self._exec_action,
            FlowStepType.agent: self._exec_agent,
            FlowStepType.sequence: self._exec_sequence,
            FlowStepType.parallel: self._exec_parallel,
            FlowStepType.branch: self._exec_branch,
            FlowStepType.loop: self._exec_loop,
            FlowStepType.map: self._exec_map,
            FlowStepType.reduce: self._exec_reduce,
            FlowStepType.race: self._exec_race,
            FlowStepType.pipe: self._exec_pipe,
            FlowStepType.spawn: self._exec_spawn,
            FlowStepType.approval: self._exec_approval,
            FlowStepType.try_catch: self._exec_try_catch,
            FlowStepType.dispatch: self._exec_dispatch,
            FlowStepType.emit: self._exec_emit,
            FlowStepType.wait: self._exec_wait,
            FlowStepType.end: self._exec_end,
            FlowStepType.use_macro: self._exec_use_macro,
            FlowStepType.goto: self._exec_goto,
        }

        handler = handlers.get(step_type)
        if handler is None:
            return StepResult(step_id=step_id, success=False, error=f"Unknown step type: {step_type}")

        return await handler(step)

    # ─── Step handlers ────────────────────────────────────────────────

    async def _exec_action(self, step: FlowStep) -> StepResult:
        """Execute a module.action step with timeout and retry enforcement."""
        step_id = step.id or f"action_{id(step)}"
        action_str = self._resolve(step.action)

        parts = str(action_str).split(".", 1)
        if len(parts) != 2:
            return StepResult(step_id=step_id, success=False, error=f"Invalid action: {action_str}")

        module_id, action_name = parts
        params = self._resolve(step.params)

        if not self._execute_action:
            return StepResult(step_id=step_id, success=False, error="No action executor configured")

        # Parse step-level timeout
        timeout = _parse_duration(step.timeout) if step.timeout else 0

        # Parse step-level retry config
        max_attempts = 1
        retry_backoff = "exponential"
        if step.retry:
            max_attempts = step.retry.max_attempts
            retry_backoff = step.retry.backoff

        last_error: str | None = None
        for attempt in range(max_attempts):
            if attempt > 0:
                if retry_backoff == "exponential":
                    delay = min(2 ** attempt, 30)
                elif retry_backoff == "linear":
                    delay = attempt * 2
                else:
                    delay = 2
                await asyncio.sleep(delay)

            try:
                coro = self._execute_action(module_id, action_name, params)
                if timeout > 0:
                    result = await asyncio.wait_for(coro, timeout=timeout)
                else:
                    result = await coro
                is_error = isinstance(result, dict) and result.get("error") is not None
                if is_error and attempt < max_attempts - 1:
                    last_error = str(result.get("error", ""))
                    continue
                return StepResult(step_id=step_id, success=not is_error, output=result)
            except asyncio.TimeoutError:
                last_error = f"Action timed out after {timeout}s"
                if attempt >= max_attempts - 1:
                    return StepResult(step_id=step_id, success=False, error=last_error)
            except Exception as e:
                last_error = str(e)
                if attempt >= max_attempts - 1:
                    raise

        return StepResult(step_id=step_id, success=False, error=last_error or "Unknown error")

    async def _exec_agent(self, step: FlowStep) -> StepResult:
        """Execute an agent step — run the agent with given input."""
        step_id = step.id or f"agent_{id(step)}"
        agent_id = self._resolve(step.agent) or "default"
        input_text = self._resolve(step.input) or ""

        if not self._run_agent:
            return StepResult(step_id=step_id, success=False, error="No agent runner configured")

        result = await self._run_agent(str(agent_id), str(input_text))
        return StepResult(step_id=step_id, success=True, output=result)

    async def _exec_sequence(self, step: FlowStep) -> StepResult:
        """Execute steps sequentially."""
        step_id = step.id or f"seq_{id(step)}"
        children: list[StepResult] = []

        for child in step.sequence or []:
            if self._stopped:
                break
            child_result = await self._execute_step(child)
            children.append(child_result)

        all_ok = all(c.success for c in children)
        last_output = children[-1].output if children else None
        return StepResult(step_id=step_id, success=all_ok, output=last_output, children=children)

    async def _exec_parallel(self, step: FlowStep) -> StepResult:
        """Execute steps in parallel with concurrency control."""
        step_id = step.id or f"par_{id(step)}"
        config = step.parallel
        if not config:
            return StepResult(step_id=step_id, success=False, error="No parallel config")

        semaphore = asyncio.Semaphore(config.max_concurrent)

        async def run_with_sem(child: FlowStep) -> StepResult:
            async with semaphore:
                return await self._execute_step(child)

        tasks = [asyncio.create_task(run_with_sem(child)) for child in config.steps]

        if config.fail_fast:
            children: list[StepResult] = []
            failed = False
            done, pending = set(), set(tasks)
            while pending and not failed:
                newly_done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED,
                )
                for t in newly_done:
                    done.add(t)
                    try:
                        result = t.result()
                    except Exception as exc:
                        child_id = f"par_child_{tasks.index(t)}"
                        result = StepResult(step_id=child_id, success=False, error=str(exc))
                    children.append(result)
                    if not result.success:
                        failed = True
                        for p in pending:
                            p.cancel()
                        if pending:
                            await asyncio.gather(*pending, return_exceptions=True)
                        # Collect cancelled tasks as skipped
                        for p in pending:
                            idx = tasks.index(p)
                            child_id = config.steps[idx].id or f"par_child_{idx}"
                            children.append(StepResult(
                                step_id=child_id, success=False, error="cancelled (fail_fast)",
                            ))
                        pending = set()
                        break
        else:
            raw_results = await asyncio.gather(*tasks, return_exceptions=True)
            children = []
            for i, r in enumerate(raw_results):
                if isinstance(r, BaseException):
                    child_id = config.steps[i].id or f"par_child_{i}"
                    children.append(StepResult(step_id=child_id, success=False, error=str(r)))
                else:
                    children.append(r)

        outputs = {c.step_id: c.output for c in children}
        all_ok = all(c.success for c in children)
        return StepResult(step_id=step_id, success=all_ok, output=outputs, children=list(children))

    async def _exec_branch(self, step: FlowStep) -> StepResult:
        """Execute a conditional branch."""
        step_id = step.id or f"branch_{id(step)}"
        config = step.branch
        if not config:
            return StepResult(step_id=step_id, success=False, error="No branch config")

        value = self._resolve(config.on)
        value_str = str(value).lower() if value is not None else ""

        # Try exact match on cases
        matched_steps = None
        for case_key, case_steps in config.cases.items():
            if case_key == value_str or case_key == str(value):
                matched_steps = case_steps
                break
            # Boolean match: "true"/"false"
            if isinstance(value, bool) and case_key.lower() == str(value).lower():
                matched_steps = case_steps
                break

        if matched_steps is None:
            matched_steps = config.default or []

        children: list[StepResult] = []
        for child in matched_steps:
            child_result = await self._execute_step(child)
            children.append(child_result)

        last_output = children[-1].output if children else None
        all_ok = all(c.success for c in children)
        return StepResult(step_id=step_id, success=all_ok, output=last_output, children=children)

    async def _exec_loop(self, step: FlowStep) -> StepResult:
        """Execute a loop with max iterations and until condition."""
        step_id = step.id or f"loop_{id(step)}"
        config = step.loop
        if not config:
            return StepResult(step_id=step_id, success=False, error="No loop config")

        max_iter = config.max_iterations
        if isinstance(max_iter, str):
            resolved = self._resolve(max_iter)
            max_iter = int(resolved) if resolved is not None else 10

        children: list[StepResult] = []
        iteration_outputs: list[Any] = []

        for i in range(max_iter):
            if self._stopped:
                break

            # Update loop context
            self._ctx.loop = {"iteration": i, "index": i}

            for child in config.body:
                child_result = await self._execute_step(child)
                children.append(child_result)

            if children:
                iteration_outputs.append(children[-1].output)

            # Check until condition
            if config.until:
                if self._expr.evaluate_condition(config.until, self._ctx):
                    break

        all_ok = all(c.success for c in children)
        return StepResult(step_id=step_id, success=all_ok, output=iteration_outputs, children=children)

    async def _exec_map(self, step: FlowStep) -> StepResult:
        """Execute steps for each item in a collection.

        When max_concurrent > 1, items execute concurrently.  Each concurrent
        task gets its own snapshot of ``variables`` and ``loop`` context to
        prevent shared-state race conditions.
        """
        step_id = step.id or f"map_{id(step)}"
        config = step.map
        if not config:
            return StepResult(step_id=step_id, success=False, error="No map config")

        collection = self._resolve(config.over)
        if not isinstance(collection, list):
            return StepResult(step_id=step_id, success=False, error=f"map.over did not resolve to a list: {type(collection)}")

        var_name = config.as_var
        max_concurrent = config.max_concurrent

        # Sequential fast-path — no concurrency issues
        if max_concurrent <= 1:
            results: list[StepResult] = []
            for idx, item in enumerate(collection):
                prev_var = self._ctx.variables.get(var_name)
                prev_loop = self._ctx.loop
                self._ctx.variables[var_name] = item
                self._ctx.loop = {"iteration": idx, "index": idx, "item": item}
                try:
                    children: list[StepResult] = []
                    for child in config.step:
                        child_result = await self._execute_step(child)
                        children.append(child_result)
                    last_output = children[-1].output if children else None
                    all_ok = all(c.success for c in children)
                    results.append(StepResult(
                        step_id=f"{step_id}[{idx}]", success=all_ok,
                        output=last_output, children=children,
                    ))
                finally:
                    if prev_var is not None:
                        self._ctx.variables[var_name] = prev_var
                    else:
                        self._ctx.variables.pop(var_name, None)
                    self._ctx.loop = prev_loop
            outputs = [r.output for r in results]
            all_ok = all(r.success for r in results)
            return StepResult(step_id=step_id, success=all_ok, output=outputs, children=list(results))

        # Concurrent path — each task works on an isolated context snapshot
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_item(idx: int, item: Any) -> StepResult:
            async with semaphore:
                # Create isolated context: snapshot current variables + set item
                saved_vars = dict(self._ctx.variables)
                saved_loop = self._ctx.loop
                self._ctx.variables[var_name] = item
                self._ctx.loop = {"iteration": idx, "index": idx, "item": item}

                try:
                    children: list[StepResult] = []
                    for child in config.step:
                        child_result = await self._execute_step(child)
                        children.append(child_result)

                    last_output = children[-1].output if children else None
                    all_ok = all(c.success for c in children)
                    return StepResult(
                        step_id=f"{step_id}[{idx}]",
                        success=all_ok,
                        output=last_output,
                        children=children,
                    )
                finally:
                    self._ctx.variables = saved_vars
                    self._ctx.loop = saved_loop

        tasks = [asyncio.create_task(process_item(i, item)) for i, item in enumerate(collection)]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        results = []
        for i, r in enumerate(raw_results):
            if isinstance(r, BaseException):
                results.append(StepResult(step_id=f"{step_id}[{i}]", success=False, error=str(r)))
            else:
                results.append(r)

        outputs = [r.output for r in results]
        all_ok = all(r.success for r in results)
        return StepResult(step_id=step_id, success=all_ok, output=outputs, children=list(results))

    async def _exec_reduce(self, step: FlowStep) -> StepResult:
        """Execute a reduce/fold over a collection."""
        step_id = step.id or f"reduce_{id(step)}"
        config = step.reduce
        if not config:
            return StepResult(step_id=step_id, success=False, error="No reduce config")

        collection = self._resolve(config.over)
        if not isinstance(collection, list):
            return StepResult(step_id=step_id, success=False, error="reduce.over not a list")

        accumulator = dict(config.initial) if config.initial else {}
        var_name = config.as_var

        for i, item in enumerate(collection):
            self._ctx.variables[var_name] = accumulator
            self._ctx.variables["reduce"] = {"item": item, "index": i}

            if config.step:
                child = config.step if isinstance(config.step, FlowStep) else FlowStep.model_validate(config.step)
                child_result = await self._execute_step(child)
                if child_result.output is not None:
                    if isinstance(child_result.output, dict):
                        accumulator.update(child_result.output)
                    else:
                        accumulator = child_result.output

        return StepResult(step_id=step_id, success=True, output=accumulator)

    async def _exec_race(self, step: FlowStep) -> StepResult:
        """Execute steps concurrently, first to finish wins."""
        step_id = step.id or f"race_{id(step)}"
        config = step.race
        if not config or not config.steps:
            return StepResult(step_id=step_id, success=False, error="No race config")

        tasks = [asyncio.create_task(self._execute_step(child)) for child in config.steps]

        done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)

        # Cancel remaining tasks
        for t in pending:
            t.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

        task = done.pop()
        try:
            winner = task.result()
        except Exception as e:
            return StepResult(step_id=step_id, success=False, error=str(e))
        return StepResult(step_id=step_id, success=winner.success, output=winner.output)

    async def _exec_pipe(self, step: FlowStep) -> StepResult:
        """Execute steps in a pipeline, each receiving the previous output."""
        step_id = step.id or f"pipe_{id(step)}"
        children: list[StepResult] = []
        pipe_input: Any = None

        for child in step.pipe or []:
            self._ctx.variables["pipe"] = {"input": pipe_input}
            child_result = await self._execute_step(child)
            children.append(child_result)
            pipe_input = child_result.output
            if not child_result.success:
                break

        all_ok = all(c.success for c in children)
        return StepResult(step_id=step_id, success=all_ok, output=pipe_input, children=children)

    async def _exec_spawn(self, step: FlowStep) -> StepResult:
        """Spawn a sub-application."""
        step_id = step.id or f"spawn_{id(step)}"
        config = step.spawn
        if not config:
            return StepResult(step_id=step_id, success=False, error="No spawn config")

        if not self._spawn_app:
            return StepResult(step_id=step_id, success=False, error="No spawn handler configured")

        app_path = self._resolve(config.app)
        input_text = self._resolve(config.input) or ""
        timeout = _parse_duration(config.timeout)

        try:
            coro = self._spawn_app(str(app_path), str(input_text), timeout)
            if timeout > 0:
                result = await asyncio.wait_for(coro, timeout=timeout)
            else:
                result = await coro
        except asyncio.TimeoutError:
            return StepResult(step_id=step_id, success=False, error=f"Spawn timed out after {timeout}s")
        return StepResult(step_id=step_id, success=True, output=result)

    async def _exec_use_macro(self, step: FlowStep) -> StepResult:
        """Execute a macro expansion — look up macro by name, inject params, run body."""
        step_id = step.id or f"macro_{id(step)}"
        macro_name = step.use
        if not macro_name:
            return StepResult(step_id=step_id, success=False, error="No macro name specified")

        macro = self._macros.get(macro_name)
        if macro is None:
            return StepResult(step_id=step_id, success=False, error=f"Macro '{macro_name}' not found")

        # Resolve with_params through expression engine
        resolved_params: dict[str, Any] = {}
        for key, val in step.with_params.items():
            resolved_params[key] = self._resolve(val)

        # Apply defaults from macro definition for missing params
        for param_name, param_def in macro.params.items():
            if param_name not in resolved_params:
                if hasattr(param_def, "default") and param_def.default is not None:
                    resolved_params[param_name] = param_def.default
                elif isinstance(param_def, dict) and param_def.get("default") is not None:
                    resolved_params[param_name] = param_def["default"]
                elif (hasattr(param_def, "required") and param_def.required) or (
                    isinstance(param_def, dict) and param_def.get("required", True)
                ):
                    return StepResult(
                        step_id=step_id,
                        success=False,
                        error=f"Macro '{macro_name}' missing required param: {param_name}",
                    )

        # Inject macro params into expression context under "macro" namespace
        prev_macro = self._ctx.variables.get("macro")
        self._ctx.variables["macro"] = resolved_params

        try:
            # Execute macro body steps sequentially
            children: list[StepResult] = []
            last_output: Any = None
            for body_step in macro.body:
                child_result = await self._execute_step(body_step)
                children.append(child_result)
                last_output = child_result.output
                if not child_result.success:
                    break

            all_ok = all(c.success for c in children)
            return StepResult(step_id=step_id, success=all_ok, output=last_output, children=children)
        finally:
            # Restore previous macro context
            if prev_macro is not None:
                self._ctx.variables["macro"] = prev_macro
            else:
                self._ctx.variables.pop("macro", None)

    async def _exec_approval(self, step: FlowStep) -> StepResult:
        """Execute an approval gate — wait for human decision."""
        step_id = step.id or f"approval_{id(step)}"
        config = step.approval
        if not config:
            return StepResult(step_id=step_id, success=False, error="No approval config")

        message = self._resolve(config.message) or ""
        timeout = _parse_duration(config.timeout)
        options = [{"label": o.label, "value": o.value} for o in config.options]

        if self._approval_handler:
            choice = await self._approval_handler(str(message), options, timeout)
            return StepResult(step_id=step_id, success=True, output={"choice": choice})

        # No handler — apply default on_timeout behavior
        if config.on_timeout == "approve":
            return StepResult(step_id=step_id, success=True, output={"choice": "approve"})
        return StepResult(step_id=step_id, success=False, output={"choice": "reject"}, error="No approval handler")

    async def _exec_try_catch(self, step: FlowStep) -> StepResult:
        """Execute try/catch/finally."""
        step_id = step.id or f"try_{id(step)}"
        try_results: list[StepResult] = []
        catch_result: StepResult | None = None
        finally_results: list[StepResult] = []

        try:
            for child in step.try_steps or []:
                child_result = await self._execute_step(child)
                try_results.append(child_result)
                if not child_result.success:
                    raise FlowExecutionError(child_result.error or "Step failed")

        except (FlowExecutionError, Exception) as exc:
            error_type = type(exc).__name__.lower()
            error_msg = str(exc)

            # Try to find matching catch handler
            matched = False
            if step.catch:
                for handler in step.catch:
                    if handler.error == "*" or handler.error in error_type:
                        matched = True
                        if handler.do:
                            catch_step = FlowStep.model_validate(handler.do)
                            catch_result = await self._execute_step(catch_step)
                        if handler.then == "continue":
                            break
                        elif handler.then == "fail":
                            raise
                        break

            if not matched:
                # No handler matched — record as failed step but don't crash the flow
                catch_result = StepResult(
                    step_id=f"{step_id}_unhandled",
                    success=False,
                    error=f"Unhandled error: {error_msg}",
                )
        finally:
            for child in step.finally_steps or []:
                child_result = await self._execute_step(child)
                finally_results.append(child_result)

        all_children = try_results + ([catch_result] if catch_result else []) + finally_results
        all_ok = all(c.success for c in all_children)
        last_output = all_children[-1].output if all_children else None
        return StepResult(step_id=step_id, success=all_ok, output=last_output, children=all_children)

    async def _exec_dispatch(self, step: FlowStep) -> StepResult:
        """Dynamic dispatch — resolve module/action at runtime."""
        step_id = step.id or f"dispatch_{id(step)}"
        config = step.dispatch
        if not config:
            return StepResult(step_id=step_id, success=False, error="No dispatch config")

        module = str(self._resolve(config.module) or "")
        action = str(self._resolve(config.action) or "")
        params_raw = config.params
        if isinstance(params_raw, str):
            params = self._resolve(params_raw)
            if isinstance(params, str):
                try:
                    params = json.loads(params)
                except (json.JSONDecodeError, TypeError):
                    params = {}
        else:
            params = self._resolve(params_raw)

        if not module or not action:
            return StepResult(step_id=step_id, success=False, error=f"dispatch: invalid module={module} action={action}")

        if not self._execute_action:
            return StepResult(step_id=step_id, success=False, error="No action executor")

        result = await self._execute_action(module, action, params or {})
        is_error = isinstance(result, dict) and result.get("error") is not None
        return StepResult(step_id=step_id, success=not is_error, output=result)

    async def _exec_emit(self, step: FlowStep) -> StepResult:
        """Emit an event to the event bus."""
        step_id = step.id or f"emit_{id(step)}"
        config = step.emit
        if not config:
            return StepResult(step_id=step_id, success=False, error="No emit config")

        topic = str(self._resolve(config.topic) or "")
        event = self._resolve(config.event) or {}

        if self._emit_event and topic:
            await self._emit_event(topic, event)
            return StepResult(step_id=step_id, success=True, output={"published": True, "topic": topic})

        return StepResult(step_id=step_id, success=True, output={"published": False})

    async def _exec_wait(self, step: FlowStep) -> StepResult:
        """Wait for an event on the event bus."""
        step_id = step.id or f"wait_{id(step)}"
        config = step.wait
        if not config:
            return StepResult(step_id=step_id, success=False, error="No wait config")

        topic = str(self._resolve(config.topic) or "")
        filter_expr = config.filter
        timeout = _parse_duration(config.timeout)

        if self._wait_event and topic:
            try:
                event = await self._wait_event(topic, filter_expr, timeout)
                return StepResult(step_id=step_id, success=True, output=event)
            except asyncio.TimeoutError:
                return StepResult(step_id=step_id, success=False, error="wait: timed out")

        return StepResult(step_id=step_id, success=False, error="No wait handler")

    async def _exec_end(self, step: FlowStep) -> StepResult:
        """End the flow with a status and optional output."""
        config = step.end or EndConfig()
        output = self._resolve(config.output) if config.output else None
        raise FlowEndSignal(status=config.status, output=output)

    async def _exec_goto(self, step: FlowStep) -> StepResult:
        """Jump to another step by ID."""
        target = self._resolve(step.goto)
        raise FlowGotoSignal(target_id=str(target))

    # ─── Helpers ──────────────────────────────────────────────────────

    def _resolve(self, value: Any) -> Any:
        """Resolve templates in a value."""
        return self._expr.resolve(value, self._ctx)

    def _get_last_output(self) -> Any:
        """Get the output of the last executed step."""
        if not self._results:
            return None
        last_key = list(self._results.keys())[-1]
        return self._results[last_key].output


# ─── Utilities ────────────────────────────────────────────────────────


def _parse_duration(s: str) -> float:
    """Parse a duration string like '30s', '5m', '1h' to seconds."""
    if not s:
        return 300.0
    s = s.strip().lower()
    try:
        if s.endswith("ms"):
            return float(s[:-2]) / 1000
        if s.endswith("s"):
            return float(s[:-1])
        if s.endswith("m"):
            return float(s[:-1]) * 60
        if s.endswith("h"):
            return float(s[:-1]) * 3600
        return float(s)
    except ValueError:
        return 300.0
