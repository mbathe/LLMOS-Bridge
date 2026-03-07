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


def _create_runtime():
    """Create an AppRuntime — connects to daemon if running, falls back to standalone."""
    runtime = _try_daemon_runtime()
    if runtime is not None:
        return runtime

    console.print("[yellow]Daemon not running — standalone mode (filesystem + os_exec + memory)[/yellow]")
    return _create_standalone_runtime()


def _try_daemon_runtime():
    """Try to create a runtime backed by the running daemon. Returns None if unavailable."""
    import httpx

    daemon_url = os.environ.get("LLMOS_DAEMON_URL", "http://localhost:8000")
    try:
        resp = httpx.get(f"{daemon_url}/health", timeout=2.0)
        if resp.status_code != 200:
            return None
    except (httpx.ConnectError, httpx.TimeoutException, Exception):
        return None

    console.print(f"[green]Connected to daemon at {daemon_url}[/green]")

    # Fetch module info from daemon
    try:
        resp = httpx.get(f"{daemon_url}/modules", timeout=5.0)
        raw_modules = resp.json()
    except Exception:
        raw_modules = []

    # Convert daemon /modules response to module_info format
    module_info: dict = {}
    for mod in raw_modules:
        mod_id = mod.get("module_id", mod.get("id", ""))
        actions_raw = mod.get("actions", [])
        actions = []
        for a in actions_raw:
            params = {}
            schema = a.get("params_schema", {})
            for pname, pdef in schema.get("properties", {}).items():
                params[pname] = {
                    "type": pdef.get("type", "string"),
                    "description": pdef.get("description", ""),
                    "required": pname in schema.get("required", []),
                }
                if "enum" in pdef:
                    params[pname]["enum"] = pdef["enum"]
            actions.append({
                "name": a.get("name", ""),
                "description": a.get("description", ""),
                "params": params,
            })
        if mod_id:
            module_info[mod_id] = {"actions": actions}

    # Create executor that dispatches through daemon's /apps/execute-tool endpoint.
    # This routes through the full DaemonToolExecutor pipeline (security, scanner,
    # sanitizer, capabilities, perception, audit) instead of bypassing it via POST /plans.
    _app_id_ref: dict[str, str] = {}  # Set later when app is loaded

    async def daemon_execute(module_id: str, action: str, params: dict):
        import httpx as _httpx
        async with _httpx.AsyncClient(base_url=daemon_url, timeout=120.0) as client:
            body = {
                "module_id": module_id,
                "action": action,
                "params": params,
            }
            if _app_id_ref.get("id"):
                body["app_id"] = _app_id_ref["id"]

            resp = await client.post("/apps/execute-tool", json=body)
            if resp.status_code != 200:
                return {"error": f"Daemon returned {resp.status_code}: {resp.text}"}
            data = resp.json()
            if data.get("success"):
                return data.get("result", data)
            return {"error": data.get("error", "Unknown error")}

    from llmos_bridge.apps.models import BrainConfig
    from llmos_bridge.apps.runtime import AppRuntime

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

    rt = AppRuntime(
        module_info=module_info,
        llm_provider_factory=llm_factory,
        execute_tool=daemon_execute,
        input_handler=_cli_input_handler,
        output_handler=_cli_output_handler,
    )
    # Attach the app_id reference so the run command can set it
    # after loading the YAML — this lets daemon_execute pass app_id
    # to /apps/execute-tool for per-app security enforcement.
    rt._daemon_app_id_ref = _app_id_ref  # type: ignore[attr-defined]
    return rt


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
            name = data.get("name", "?")
            args = data.get("arguments", {})
            # Format tool call like Claude Code
            display_name = name.replace("__", ".")
            args_str = self._format_args(args)
            self._console.print(
                f"  [dim]{display_name}[/dim]({args_str})",
            )

        elif etype == "tool_result":
            # Agent runtime emits "output" key, not "result"
            output = data.get("output", data.get("result", ""))
            is_error = data.get("is_error", False)
            if is_error:
                self._console.print(f"  [red]Error:[/red] {output}")
            else:
                preview = self._result_preview(output)
                if preview:
                    self._console.print(f"  [dim]{preview}[/dim]")

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


# ─── Commands ──────────────────────────────────────────────────────────

@app.command("run")
def run_app(
    file: Path = typer.Argument(..., help="Path to .app.yaml file"),
    input_text: str = typer.Option("", "--input", "-i", help="Input text (skips interactive mode)"),
) -> None:
    """Run an LLMOS application from a .app.yaml file."""
    runtime = _create_runtime()

    try:
        app_def = runtime.load(file)
    except Exception as e:
        console.print(f"[red]Compilation error:[/red] {e}")
        raise typer.Exit(1)

    # Set app_id for daemon mode so tool calls go through per-app security
    if hasattr(runtime, "_daemon_app_id_ref"):
        import hashlib as _hl
        _aid = _hl.sha256(f"{app_def.app.name}:{app_def.app.version}".encode()).hexdigest()[:16]
        runtime._daemon_app_id_ref["id"] = _aid

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

    async def _run_with_kv(interactive: bool, user_input: str = ""):
        """Run with KV store initialized for persistence (todo, checkpoint, memory, etc.)."""
        kv = await _init_kv_store()
        try:
            runtime._kv_store = kv
            # Initialize memory module backends now that KV store is ready
            await _init_memory_backends(runtime, kv)
            if interactive:
                await runtime.run_interactive(app_def, on_event=renderer.handle_event)
            else:
                async for event in runtime.stream(app_def, user_input):
                    await renderer.handle_event(event)
        finally:
            await kv.close()

    try:
        if input_text:
            asyncio.run(_run_with_kv(interactive=False, user_input=input_text))
        else:
            asyncio.run(_run_with_kv(interactive=True))
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
