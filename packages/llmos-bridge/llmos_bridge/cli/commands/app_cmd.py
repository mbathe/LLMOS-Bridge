"""CLI commands for LLMOS App Language.

Usage:
    llmos-bridge app run <file.app.yaml> [--input "task"]
    llmos-bridge app validate <file.app.yaml>
    llmos-bridge app info <file.app.yaml>
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

app = typer.Typer(
    name="app",
    help="LLMOS App Language — run, validate, and inspect .app.yaml files.",
    no_args_is_help=True,
)

console = Console()


async def _init_memory_backends(runtime: Any, kv: Any) -> None:
    """Initialize memory module backends with the live KV store."""
    try:
        memory_module = getattr(runtime, "_memory_module", None)
        if memory_module is None:
            return
        # Inject the live KV store into the kv backend
        kv_backend = memory_module.get_backend("kv")
        if kv_backend is not None:
            kv_backend.set_store(kv)
        # Initialize all backends
        await memory_module.on_start()
    except Exception as e:
        console.print(f"[dim]Memory backend init: {e}[/dim]")


async def _init_kv_store():
    """Create and initialize a KV store for CLI mode (todo persistence, flow checkpoint, etc.)."""
    from llmos_bridge.memory.store import KeyValueStore

    kv_path = Path.home() / ".llmos" / "apps" / "cli_kv.db"
    kv_path.parent.mkdir(parents=True, exist_ok=True)
    kv = KeyValueStore(kv_path)
    await kv.init()
    return kv


def _create_standalone_runtime():
    """Fallback runtime with filesystem + os_exec + memory (no daemon)."""
    from llmos_bridge.apps.models import BrainConfig
    from llmos_bridge.apps.runtime import AppRuntime
    from llmos_bridge.apps.tool_executor import StandaloneToolExecutor

    executor = StandaloneToolExecutor(working_directory=os.getcwd())

    # Wire standalone memory module with all backends
    try:
        from llmos_bridge.modules.memory.module import MemoryModule
        from llmos_bridge.modules.memory.backends.kv_backend import KVMemoryBackend
        from llmos_bridge.modules.memory.backends.file_backend import FileMemoryBackend
        from llmos_bridge.modules.memory.backends.cognitive_backend import CognitiveMemoryBackend

        memory_module = MemoryModule()
        kv_backend = KVMemoryBackend(
            db_path=Path.home() / ".llmos" / "apps" / "memory_kv.db",
            namespace="memory",
        )
        memory_module.register_backend(kv_backend)

        file_backend = FileMemoryBackend(
            file_path=Path(os.getcwd()) / ".llmos" / "MEMORY.md",
        )
        memory_module.register_backend(file_backend)

        cognitive_backend = CognitiveMemoryBackend(
            persistence_path=Path.home() / ".llmos" / "apps" / "cognitive_state.json",
        )
        memory_module.register_backend(cognitive_backend)

        executor.set_memory_module(memory_module)
        _standalone_memory_module = memory_module
    except Exception as e:
        console.print(f"[yellow]Memory module init failed: {e}[/yellow]")
        _standalone_memory_module = None

    def llm_factory(brain: BrainConfig):
        if brain.provider == "anthropic":
            from llmos_bridge.apps.providers import AnthropicProvider
            return AnthropicProvider(model=brain.model)
        elif brain.provider == "openai":
            from llmos_bridge.apps.providers import OpenAIProvider
            return OpenAIProvider(model=brain.model)
        else:
            from llmos_bridge.apps.runtime import _StubLLMProvider
            console.print(f"[yellow]Warning: Unknown provider '{brain.provider}', using stub[/yellow]")
            return _StubLLMProvider()

    # Wire agent_spawn module for sub-agent support
    try:
        from llmos_bridge.modules.agent_spawn.module import AgentSpawnModule
        from llmos_bridge.modules.agent_spawn.factory import SpawnedAgentFactory

        agent_spawn = AgentSpawnModule()
        factory = SpawnedAgentFactory(
            llm_factory=llm_factory,
            execute_tool=executor.execute,
        )
        agent_spawn.set_agent_factory(factory.run_agent)
        executor.set_agent_spawn_module(agent_spawn)
    except Exception as e:
        console.print(f"[dim]Agent spawn init: {e}[/dim]")

    # Wire context_manager module for budget-aware context management
    _context_manager_module = None
    try:
        from llmos_bridge.modules.context_manager.module import ContextManagerModule

        ctx_mgr = ContextManagerModule()
        executor.set_context_manager_module(ctx_mgr)
        _context_manager_module = ctx_mgr
    except Exception as e:
        console.print(f"[dim]Context manager init: {e}[/dim]")

    rt = AppRuntime(
        module_info=executor.get_module_info(),
        llm_provider_factory=llm_factory,
        execute_tool=executor.execute,
        input_handler=_cli_input_handler,
        output_handler=_cli_output_handler,
    )
    if _standalone_memory_module is not None:
        rt.set_memory_module(_standalone_memory_module)
    if _context_manager_module is not None:
        rt.set_context_manager_module(_context_manager_module)
    return rt


# ─── Stream Event Renderer ────────────────────────────────────────────

class _TerminalRenderer:
    """Renders streaming agent events to the terminal in real-time.

    Produces a Claude Code-like experience:
    - Tool calls shown with module.action and params
    - Tool results shown collapsed (truncated)
    - Agent text output rendered as markdown
    - Spinner while waiting for LLM response
    """

    def __init__(self, console: Console):
        self._console = console
        self._turn_count = 0
        self._tool_count = 0
        self._start_time = time.monotonic()
        self._tool_call_start: float | None = None

    async def handle_event(self, event) -> None:
        """Handle a single stream event from the agent runtime."""
        etype = event.type
        data = event.data

        if etype == "thinking":
            # Agent returned text — could be intermediate thinking or final response
            self._turn_count += 1
            text = data.get("text", "")
            if text.strip():
                self._console.print()
                try:
                    self._console.print(Markdown(text))
                except Exception:
                    self._console.print(text)
                self._console.print()

        elif etype == "tool_call":
            self._tool_count += 1
            self._tool_call_start = time.monotonic()
            name = data.get("name", "?")
            args = data.get("arguments", {})
            # Format tool call like Claude Code
            display_name = name.replace("__", ".")
            args_str = self._format_args(args)
            self._console.print(
                f"  [dim]{display_name}[/dim]({args_str})",
            )

        elif etype == "tool_result":
            # Show per-tool timing
            elapsed_ms = ""
            if self._tool_call_start is not None:
                dt = (time.monotonic() - self._tool_call_start) * 1000
                if dt > 100:
                    elapsed_ms = f" [yellow]{dt:.0f}ms[/yellow]"
                else:
                    elapsed_ms = f" [dim]{dt:.0f}ms[/dim]"
                self._tool_call_start = None
            # Agent runtime emits "output" key, not "result"
            output = data.get("output", data.get("result", ""))
            is_error = data.get("is_error", False)
            if is_error:
                self._console.print(f"  [red]Error:[/red] {output}{elapsed_ms}")
            else:
                preview = self._result_preview(output)
                if preview:
                    self._console.print(f"  [dim]{preview}[/dim]{elapsed_ms}")
                elif elapsed_ms:
                    self._console.print(f"  [dim]OK[/dim]{elapsed_ms}")

        elif etype == "text":
            text = data.get("text", "")
            if text.strip():
                self._console.print()
                try:
                    self._console.print(Markdown(text))
                except Exception:
                    self._console.print(text)
                self._console.print()

        elif etype == "error":
            error = data.get("error", "Unknown error")
            self._console.print(f"\n[red]Error:[/red] {error}\n")

        elif etype == "done":
            elapsed = time.monotonic() - self._start_time
            stop = data.get("stop_reason", "done")
            self._console.print(
                f"[dim]({self._turn_count} turns, "
                f"{self._tool_count} tool calls, "
                f"{elapsed:.1f}s, {stop})[/dim]"
            )
            # Reset for next interaction
            self._turn_count = 0
            self._tool_count = 0
            self._start_time = time.monotonic()

    def _format_args(self, args: dict) -> str:
        """Format tool arguments for display, truncated."""
        if not args:
            return ""
        parts = []
        for k, v in args.items():
            val_str = str(v)
            if len(val_str) > 80:
                val_str = val_str[:77] + "..."
            parts.append(f"{k}={val_str}")
        result = ", ".join(parts)
        if len(result) > 200:
            result = result[:197] + "..."
        return result

    def _result_preview(self, result) -> str:
        """Generate a short preview of a tool result."""
        if isinstance(result, str):
            # Agent runtime sends output as string (JSON-serialized or raw)
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                if len(result) > 100:
                    return f"{len(result)} chars"
                return result[:100] if result.strip() else ""
        if isinstance(result, dict):
            # Common patterns
            if "content" in result:
                content = result["content"]
                lines = content.count("\n") + 1
                chars = len(content)
                return f"{chars} chars, {lines} lines"
            if "stdout" in result:
                stdout = result["stdout"]
                if stdout.strip():
                    lines = stdout.strip().split("\n")
                    if len(lines) <= 3:
                        return stdout.strip()
                    return f"{len(lines)} lines of output"
                return "OK (no output)"
            if "entries" in result:
                return f"{result.get('count', len(result['entries']))} entries"
            if "matches" in result:
                return f"{result.get('count', len(result['matches']))} matches"
            if "bytes_written" in result:
                return f"Wrote {result['bytes_written']} bytes"
            if "deleted" in result:
                return "Deleted"
            if "success" in result:
                return "OK" if result["success"] else "Failed"
        elif isinstance(result, str):
            if len(result) > 100:
                return f"{len(result)} chars"
            return result
        return ""


# ─── Daemon lifecycle helpers ──────────────────────────────────────────


def _daemon_url() -> str:
    return os.environ.get("LLMOS_DAEMON_URL", "http://localhost:8000")


def _check_daemon() -> bool:
    """Check if daemon is running and has App Language wired."""
    import httpx

    url = _daemon_url()
    try:
        resp = httpx.get(f"{url}/health", timeout=2.0)
        if resp.status_code != 200:
            return False
    except Exception:
        return False

    # Probe /apps/execute-tool to verify App Language is wired
    try:
        probe = httpx.post(
            f"{url}/apps/execute-tool",
            json={"module_id": "__probe__", "action": "__probe__", "params": {}},
            timeout=3.0,
        )
        if probe.status_code == 503:
            return False
    except Exception:
        pass
    return True


async def _daemon_register(yaml_path: Path, application_id: str | None = None) -> dict:
    """Register app via daemon API: compile + store + link to Application."""
    import httpx

    yaml_text = yaml_path.read_text()
    body: dict = {
        "yaml_text": yaml_text,
        "file_path": str(yaml_path.resolve()),
    }
    if application_id:
        body["application_id"] = application_id

    async with httpx.AsyncClient(base_url=_daemon_url(), timeout=30.0) as client:
        resp = await client.post("/apps/register", json=body)
        if resp.status_code == 201:
            return resp.json()
        elif resp.status_code == 403:
            raise typer.Exit(code=1)  # Error already shown by caller
        else:
            raise RuntimeError(f"Registration failed ({resp.status_code}): {resp.text}")


async def _daemon_prepare(app_id: str) -> dict:
    """Prepare app via daemon API: pre-load modules, warm LLM, etc."""
    import httpx

    async with httpx.AsyncClient(base_url=_daemon_url(), timeout=60.0) as client:
        resp = await client.post(f"/apps/{app_id}/prepare")
        if resp.status_code != 200:
            raise RuntimeError(f"Prepare failed ({resp.status_code}): {resp.text}")
        return resp.json()


async def _daemon_run_stream(app_id: str, input_text: str, renderer: '_TerminalRenderer'):
    """Run app via daemon SSE stream and render events to terminal."""
    import httpx

    body = {"input": input_text, "stream": True}
    async with httpx.AsyncClient(base_url=_daemon_url(), timeout=600.0) as client:
        async with client.stream(
            "POST", f"/apps/{app_id}/run", json=body,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                console.print(f"[red]Run failed ({resp.status_code}):[/red] {text.decode()}")
                return

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                try:
                    payload = json.loads(line[6:])
                except json.JSONDecodeError:
                    continue

                # Convert to StreamEvent-like object for the renderer
                class _Event:
                    def __init__(self, t, d):
                        self.type = t
                        self.data = d

                await renderer.handle_event(_Event(payload.get("type", ""), payload.get("data", {})))

                if payload.get("type") == "error":
                    break


async def _daemon_run_interactive(app_id: str, renderer: '_TerminalRenderer'):
    """Run app interactively: prompt user for input, run via daemon, repeat."""
    console.print("[dim]Type your message (Ctrl+C to exit)[/dim]\n")
    while True:
        try:
            user_input = input("> ")
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input.strip():
            continue

        await _daemon_run_stream(app_id, user_input, renderer)
        console.print()


# ─── Commands ──────────────────────────────────────────────────────────

@app.command("run")
def run_app(
    file: Path = typer.Argument(..., help="Path to .app.yaml file"),
    input_text: str = typer.Option("", "--input", "-i", help="Input text (skips interactive mode)"),
    standalone: bool = typer.Option(False, "--standalone", help="Force standalone mode (no daemon)"),
    application_id: str = typer.Option(None, "--app-id", help="Link to dashboard Application ID"),
) -> None:
    """Run an LLMOS application from a .app.yaml file.

    The app lifecycle is managed by the daemon:
    1. Register (compile + validate + link to dashboard Application)
    2. Prepare (pre-load modules, warm LLM, initialize memory)
    3. Run (execute with full security pipeline)

    Use --standalone to bypass the daemon (limited to filesystem + os_exec + memory).
    """
    # ── Daemon mode (default) ─────────────────────────────────────
    if not standalone and _check_daemon():
        console.print(f"[green]Connected to daemon at {_daemon_url()}[/green]")

        async def _daemon_lifecycle():
            # Step 1: Register (compile + store)
            t0 = time.monotonic()
            try:
                record = await _daemon_register(file, application_id=application_id)
            except RuntimeError as e:
                console.print(f"[red]Registration error:[/red] {e}")
                raise typer.Exit(1)

            app_id = record["id"]
            app_name = record.get("name", file.stem)
            app_version = record.get("version", "?")
            app_desc = record.get("description", "")

            console.print(f"[dim]Registered: {app_name} v{app_version} (id={app_id})[/dim]")

            # Step 2: Prepare (pre-load everything)
            try:
                prep = await _daemon_prepare(app_id)
            except RuntimeError as e:
                console.print(f"[yellow]Prepare warning:[/yellow] {e}")
                prep = {}

            dt = (time.monotonic() - t0) * 1000
            modules = prep.get("modules_checked", 0)
            tools = prep.get("tools_resolved", 0)
            missing = prep.get("modules_missing", [])
            if missing:
                console.print(f"[yellow]Missing modules: {', '.join(missing)}[/yellow]")

            console.print(Panel(
                f"[bold]{app_name}[/bold] v{app_version}\n"
                + (f"{app_desc}\n" if app_desc else "")
                + f"[dim]{modules} modules, {tools} tools | Ready in {dt:.0f}ms[/dim]",
                border_style="blue",
            ))

            renderer = _TerminalRenderer(console)

            # Step 3: Run
            if input_text:
                await _daemon_run_stream(app_id, input_text, renderer)
            else:
                await _daemon_run_interactive(app_id, renderer)

        try:
            asyncio.run(_daemon_lifecycle())
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted[/dim]")
        return

    # ── Standalone mode (fallback) ────────────────────────────────
    if not standalone:
        console.print("[yellow]Daemon not running — standalone mode (filesystem + os_exec + memory)[/yellow]")
    else:
        console.print("[yellow]Standalone mode (--standalone)[/yellow]")

    runtime = _create_standalone_runtime()

    try:
        app_def = runtime.load(file)
    except Exception as e:
        console.print(f"[red]Compilation error:[/red] {e}")
        raise typer.Exit(1)

    # Show app header
    agent_info = ""
    if app_def.agent:
        brain = app_def.agent.brain
        tool_count = len(app_def.get_all_tools() or app_def.agent.tools or [])
        agent_info = f"{brain.provider}/{brain.model} | {tool_count} tools"
    elif app_def.agents and app_def.agents.agents:
        agent_count = len(app_def.agents.agents)
        agent_info = f"{agent_count} agents"

    console.print(Panel(
        f"[bold]{app_def.app.name}[/bold] v{app_def.app.version}\n"
        + (f"{app_def.app.description}\n" if app_def.app.description else "")
        + (f"[dim]{agent_info}[/dim]" if agent_info else ""),
        border_style="blue",
    ))

    renderer = _TerminalRenderer(console)

    async def _run_standalone(interactive: bool, user_input: str = ""):
        t0 = time.monotonic()
        kv = await _init_kv_store()
        try:
            runtime._kv_store = kv
            await _init_memory_backends(runtime, kv)
            memory_module = getattr(runtime, "_memory_module", None)
            if memory_module is not None:
                try:
                    await memory_module.health_check()
                except Exception as e:
                    console.print(f"[yellow]Memory pre-warm warning: {e}[/yellow]")
            dt = (time.monotonic() - t0) * 1000
            console.print(f"[dim]Ready in {dt:.0f}ms[/dim]")
            if interactive:
                await runtime.run_interactive(app_def, on_event=renderer.handle_event)
            else:
                async for event in runtime.stream(app_def, user_input):
                    await renderer.handle_event(event)
        finally:
            await kv.close()

    try:
        if input_text:
            asyncio.run(_run_standalone(interactive=False, user_input=input_text))
        else:
            asyncio.run(_run_standalone(interactive=True))
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted[/dim]")


@app.command("validate")
def validate_app(
    file: Path = typer.Argument(..., help="Path to .app.yaml file"),
) -> None:
    """Validate a .app.yaml file without running it."""
    from llmos_bridge.apps.runtime import AppRuntime

    runtime = AppRuntime()
    errors = runtime.validate(file)

    if errors:
        console.print("[red]Validation failed:[/red]")
        for error in errors:
            console.print(f"  {error}")
        raise typer.Exit(1)
    else:
        console.print(f"[green]Valid:[/green] {file}")


@app.command("info")
def info_app(
    file: Path = typer.Argument(..., help="Path to .app.yaml file"),
) -> None:
    """Show information about a .app.yaml application."""
    from llmos_bridge.apps.runtime import AppRuntime

    runtime = AppRuntime()
    try:
        app_def = runtime.load(file)
    except Exception as e:
        console.print(f"[red]Error:[/red] {e}")
        raise typer.Exit(1)

    # App info panel
    info_lines = [
        f"[bold]{app_def.app.name}[/bold] v{app_def.app.version}",
        f"{app_def.app.description}" if app_def.app.description else "",
        f"Author: {app_def.app.author}" if app_def.app.author else "",
        f"Tags: {', '.join(app_def.app.tags)}" if app_def.app.tags else "",
    ]
    console.print(Panel("\n".join(line for line in info_lines if line), title="Application"))

    # Agent info
    if app_def.agent:
        brain = app_def.agent.brain
        console.print(f"\n[bold]Agent:[/bold] {brain.provider}/{brain.model}")
        console.print(f"  Loop: {app_def.agent.loop.type.value} (max {app_def.agent.loop.max_turns} turns)")
        if app_def.agent.system_prompt:
            prompt_preview = app_def.agent.system_prompt[:200]
            if len(app_def.agent.system_prompt) > 200:
                prompt_preview += "..."
            console.print(f"  Prompt: {prompt_preview}")
    elif app_def.agents:
        console.print(f"\n[bold]Multi-Agent:[/bold] {len(app_def.agents.agents)} agents")
        for a in app_def.agents.agents:
            console.print(f"  - {a.id} ({a.role.value}): {a.brain.provider}/{a.brain.model}")

    # Tools
    all_tools = app_def.get_all_tools()
    if all_tools:
        table = Table(title="Tools")
        table.add_column("Name")
        table.add_column("Type")
        for tool in all_tools:
            if tool.module:
                actions = tool.actions or ([tool.action] if tool.action else ["*"])
                table.add_row(f"{tool.module}.{','.join(actions)}", "module")
            elif tool.builtin:
                table.add_row(tool.builtin, "builtin")
        console.print(table)

    # Triggers
    if app_def.triggers:
        console.print(f"\n[bold]Triggers:[/bold] {len(app_def.triggers)}")
        for t in app_def.triggers:
            console.print(f"  - {t.id or t.type.value}: {t.type.value}")

    # Macros
    if app_def.macros:
        console.print(f"\n[bold]Macros:[/bold] {len(app_def.macros)}")
        for m in app_def.macros:
            params = ", ".join(f"{k}: {v.type}" for k, v in m.params.items())
            console.print(f"  - {m.name}({params})")

    # Flow
    if app_def.flow:
        console.print(f"\n[bold]Flow:[/bold] {len(app_def.flow)} steps")
        for step in app_def.flow:
            step_type = step.infer_type().value
            console.print(f"  - {step.id or '(anonymous)'}: {step_type}")

    # Capabilities
    if app_def.capabilities.grant:
        console.print(f"\n[bold]Capabilities:[/bold] {len(app_def.capabilities.grant)} grants, "
                      f"{len(app_def.capabilities.deny)} denials, "
                      f"{len(app_def.capabilities.approval_required)} approval rules")


async def _cli_input_handler(question: str) -> str:
    """Handle ask_user builtin in CLI mode."""
    console.print(f"\n[yellow]{question}[/yellow]")
    return input("> ")


async def _cli_output_handler(text: str) -> None:
    """Handle output in CLI mode."""
    console.print(text)
