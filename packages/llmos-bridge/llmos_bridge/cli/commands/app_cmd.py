"""CLI commands for LLMOS App Language.

Usage:
    llmos-bridge app run <file.app.yaml> [--input "task"]
    llmos-bridge app exec <app-id-or-name>  [--input "task"]
    llmos-bridge app validate <file.app.yaml>
    llmos-bridge app info <file.app.yaml>
    llmos-bridge app list
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import typer
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

secret_app = typer.Typer(
    name="secret",
    help="Manage secrets for a registered app (API keys, passwords, etc.).",
    no_args_is_help=True,
)

app = typer.Typer(
    name="app",
    help="LLMOS App Language — run, validate, and inspect .app.yaml files.",
    no_args_is_help=True,
)

app.add_typer(secret_app, name="secret")

console = Console()


def _resolve_greeting(greeting: str) -> str:
    """Resolve simple template expressions in CLI greeting text.

    Handles ``{{workspace}}``, ``{{env.VAR}}``, and other variables that
    are available at CLI display time.  Falls back to the raw text on error.
    """
    if not greeting or "{{" not in greeting:
        return greeting
    try:
        from llmos_bridge.apps.expression import ExpressionContext, ExpressionEngine
        engine = ExpressionEngine()
        ctx = ExpressionContext(
            variables={"workspace": os.getcwd()},
        )
        return str(engine.resolve(greeting, ctx))
    except Exception:
        return greeting


# ─── Standalone runtime helpers ────────────────────────────────────────

async def _init_memory_backends(runtime: Any, kv: Any) -> None:
    """Initialize memory module backends with the live KV store."""
    try:
        memory_module = getattr(runtime, "_memory_module", None)
        if memory_module is None:
            return
        kv_backend = memory_module.get_backend("kv")
        if kv_backend is not None:
            kv_backend.set_store(kv)
        await memory_module.on_start()
    except Exception as e:
        console.print(f"[dim]Memory backend init: {e}[/dim]")


async def _init_kv_store():
    """Create and initialize a KV store for CLI mode."""
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

    # Default base URLs for known OpenAI-compatible providers
    _PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
        "ollama": {"base_url": "http://localhost:11434/v1", "api_key": "ollama"},
        "google": {"base_url": "https://generativelanguage.googleapis.com/v1beta/openai/"},
    }

    def llm_factory(brain: BrainConfig):
        if brain.provider == "anthropic":
            from llmos_bridge.apps.providers import AnthropicProvider
            return AnthropicProvider(
                model=brain.model,
                api_key=brain.config.get("api_key", ""),
            )
        # All other providers use OpenAI-compatible API
        from llmos_bridge.apps.providers import OpenAIProvider
        defaults = _PROVIDER_DEFAULTS.get(brain.provider, {})
        return OpenAIProvider(
            model=brain.model,
            api_key=brain.config.get("api_key", defaults.get("api_key", "")),
            base_url=brain.config.get("base_url", defaults.get("base_url", "")),
        )

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


# ─── Terminal Renderer ────────────────────────────────────────────────


class _TerminalRenderer:
    """Renders streaming agent events to the terminal in real-time.

    Produces a Claude Code-like experience:
    - Tool calls shown with module.action and params
    - Tool results shown collapsed (truncated)
    - Agent text output rendered as markdown
    - Approval requests shown as interactive prompts
    """

    def __init__(self, console: Console):
        self._console = console
        self._turn_count = 0
        self._tool_count = 0
        self._start_time = time.monotonic()
        self._tool_call_start: float | None = None

    async def handle_event(self, event: Any) -> None:
        etype = event.type
        data = event.data

        if etype == "thinking":
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
            display_name = name.replace("__", ".")
            args_str = self._format_args(args)
            self._console.print(f"  [dim]{display_name}[/dim]({args_str})")

        elif etype == "tool_result":
            from_cache = data.get("from_cache", False)
            elapsed_ms = ""
            if from_cache:
                elapsed_ms = " [cyan](cached)[/cyan]"
                self._tool_call_start = None
            elif self._tool_call_start is not None:
                dt = (time.monotonic() - self._tool_call_start) * 1000
                elapsed_ms = (
                    f" [yellow]{dt:.0f}ms[/yellow]" if dt > 100 else f" [dim]{dt:.0f}ms[/dim]"
                )
                self._tool_call_start = None
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
            if stop == "error" and data.get("error"):
                self._console.print(f"\n[red]Error:[/red] {data['error']}\n")
            self._turn_count = 0
            self._tool_count = 0
            self._start_time = time.monotonic()

    def reset_timer(self) -> None:
        self._start_time = time.monotonic()
        self._turn_count = 0
        self._tool_count = 0

    def _format_args(self, args: dict) -> str:
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

    def _result_preview(self, result: Any) -> str:
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except (json.JSONDecodeError, TypeError):
                if len(result) > 100:
                    return f"{len(result)} chars"
                return result[:100] if result.strip() else ""
        if isinstance(result, dict):
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
        return ""


# ─── Daemon helpers ────────────────────────────────────────────────────


def _daemon_url() -> str:
    return os.environ.get("LLMOS_DAEMON_URL", "http://localhost:40000")


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


def _compute_app_id(yaml_path: Path) -> str | None:
    """Compute the daemon app_id from name:version in a YAML file (same logic as server)."""
    import hashlib
    import yaml as _yaml

    try:
        data = _yaml.safe_load(yaml_path.read_text()) or {}
        name = (data.get("app") or {}).get("name", "")
        version = str((data.get("app") or {}).get("version", "1.0"))
        if name:
            return hashlib.sha256(f"{name}:{version}".encode()).hexdigest()[:16]
    except Exception:
        pass
    return None


async def _daemon_get_app(app_id: str) -> dict | None:
    """Fetch an app record from the daemon, or None if not found."""
    import httpx

    try:
        async with httpx.AsyncClient(base_url=_daemon_url(), timeout=5.0) as client:
            resp = await client.get(f"/apps/{app_id}")
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        pass
    return None


async def _daemon_register(yaml_path: Path, application_id: str | None = None) -> dict:
    """Register app via daemon API: compile + store + link to Application."""
    import httpx

    body: dict = {
        "file_path": str(yaml_path.resolve()),
    }
    if application_id:
        body["application_id"] = application_id

    async with httpx.AsyncClient(base_url=_daemon_url(), timeout=30.0) as client:
        resp = await client.post("/apps/register", json=body)
        if resp.status_code == 201:
            return resp.json()
        elif resp.status_code == 403:
            raise typer.Exit(code=1)
        else:
            raise RuntimeError(f"Registration failed ({resp.status_code}): {resp.text}")


async def _daemon_prepare(app_id: str) -> dict:
    """Prepare app via daemon API."""
    import httpx

    async with httpx.AsyncClient(base_url=_daemon_url(), timeout=60.0) as client:
        resp = await client.post(f"/apps/{app_id}/prepare")
        if resp.status_code != 200:
            raise RuntimeError(f"Prepare failed ({resp.status_code}): {resp.text}")
        return resp.json()


async def _daemon_submit_decision(action_id: str, run_id: str, decision: str, reason: str = "") -> bool:
    """Submit an approval decision to the daemon."""
    import httpx

    try:
        async with httpx.AsyncClient(base_url=_daemon_url(), timeout=10.0) as client:
            resp = await client.post(
                f"/apps/approvals/{action_id}/decide",
                json={"decision": decision, "reason": reason},
                params={"run_id": run_id},
            )
            return resp.status_code == 200
    except Exception:
        return False


def _format_params_display(params: dict) -> str:
    """Format params dict for human-readable display."""
    if not params:
        return "[dim](no params)[/dim]"
    lines = []
    for k, v in list(params.items())[:8]:
        val = str(v)
        if len(val) > 120:
            val = val[:117] + "..."
        lines.append(f"  [dim]{k}:[/dim] {val}")
    if len(params) > 8:
        lines.append(f"  [dim]... +{len(params) - 8} more[/dim]")
    return "\n".join(lines)


async def _handle_approval_cli(req_data: dict, run_id: str) -> None:
    """Show an interactive approval prompt and submit the user's decision.

    Blocks until the user types a decision. Uses run_in_executor so the
    asyncio event loop is not blocked (other tasks can still run).
    """
    module = req_data.get("module", "?")
    action = req_data.get("action_name", "?")
    action_id = req_data.get("action_id", "")
    params = req_data.get("params", {})
    risk = req_data.get("risk_level", "")

    risk_color = {"low": "green", "medium": "yellow", "high": "red", "critical": "red bold"}.get(
        risk.lower(), "white"
    )

    params_text = _format_params_display(params)
    console.print()
    console.print(
        Panel(
            f"[bold]{module}[/bold].[cyan]{action}[/cyan]"
            + (f"  [{risk_color}]{risk.upper()}[/{risk_color}]" if risk else "")
            + f"\n\n{params_text}",
            title="[yellow bold]⚠  Approval Required[/yellow bold]",
            border_style="yellow",
        )
    )
    console.print(
        "  [dim](a)[/dim] Approve  "
        "[dim](r)[/dim] Reject  "
        "[dim](s)[/dim] Skip  "
        "[dim](aa)[/dim] Approve always  "
        "[dim](m)[/dim] Message"
    )

    loop = asyncio.get_event_loop()
    try:
        raw = await loop.run_in_executor(None, lambda: sys.stdin.readline().strip())
    except (EOFError, KeyboardInterrupt):
        raw = "r"

    raw_lower = raw.lower()
    decision_map = {
        "": "approve", "a": "approve", "approve": "approve",
        "r": "reject", "reject": "reject",
        "s": "skip", "skip": "skip",
        "aa": "approve_always", "approve_always": "approve_always",
        "m": "message", "message": "message",
    }
    decision = decision_map.get(raw_lower, "reject")
    reason = ""

    # For MESSAGE: prompt user for the feedback text to send to the agent
    if decision == "message":
        console.print("  [cyan]Type your message to the agent:[/cyan]")
        try:
            reason = await loop.run_in_executor(None, lambda: sys.stdin.readline().strip())
        except (EOFError, KeyboardInterrupt):
            reason = ""
        if not reason:
            reason = "The user wants you to do something different."

    ok = await _daemon_submit_decision(action_id, run_id, decision, reason=reason)
    if ok:
        icons = {
            "approve": "[green]✓ Approved[/green]",
            "reject": "[red]✗ Rejected[/red]",
            "skip": "[yellow]→ Skipped[/yellow]",
            "approve_always": "[green]✓ Always approved (this session)[/green]",
            "message": f"[cyan]✉ Message sent:[/cyan] {reason}",
        }
        console.print(icons.get(decision, f"[dim]{decision}[/dim]"))
    else:
        console.print("[red]Failed to submit decision (approval may have timed out)[/red]")
    console.print()


# ─── SSE stream runner ────────────────────────────────────────────────


async def _daemon_run_stream(
    app_id: str,
    input_text: str,
    renderer: "_TerminalRenderer",
    conversation_history: list | None = None,
) -> tuple[str, list]:
    """Run app via daemon SSE stream, render events, handle approvals.

    Returns (run_id, updated_conversation_history).
    """
    import httpx

    import os as _os
    run_id = ""
    new_history: list = list(conversation_history) if conversation_history else []
    body = {
        "input": input_text,
        "stream": True,
        "variables": {"workspace": _os.getcwd()},
        "conversation_history": new_history,
    }

    async with httpx.AsyncClient(base_url=_daemon_url(), timeout=600.0) as client:
        async with client.stream(
            "POST",
            f"/apps/{app_id}/run",
            json=body,
            headers={"Accept": "text/event-stream"},
        ) as resp:
            if resp.status_code != 200:
                text = await resp.aread()
                console.print(f"[red]Run failed ({resp.status_code}):[/red] {text.decode()}")
                return run_id

            try:
                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    try:
                        payload = json.loads(line[6:])
                    except json.JSONDecodeError:
                        continue

                    event_type = payload.get("type", "")
                    event_data = payload.get("data", {})

                    if event_type == "run_start":
                        run_id = event_data.get("run_id", "")
                        continue

                    if event_type == "approval_request":
                        # Blocking approval prompt — agent is paused waiting for this
                        await _handle_approval_cli(event_data, run_id)
                        continue

                    if event_type == "conversation_update":
                        # Capture updated history for multi-turn interactive mode
                        new_history = event_data.get("messages", new_history)
                        continue

                    class _Event:
                        def __init__(self, t: str, d: dict) -> None:
                            self.type = t
                            self.data = d

                    await renderer.handle_event(_Event(event_type, event_data))

                    if event_type == "error":
                        break
            except httpx.RemoteProtocolError:
                pass  # Server closed connection after stream ended — not an error

    return run_id, new_history


# ─── Interactive session ───────────────────────────────────────────────


async def _daemon_run_interactive(
    app_id: str,
    renderer: "_TerminalRenderer",
    prompt: str = "> ",
) -> None:
    """Run app interactively — conversational loop with approval handling."""
    console.print("[dim]Type your message. Ctrl+C or empty line to exit.[/dim]\n")

    loop = asyncio.get_event_loop()
    conversation_history: list = []

    while True:
        try:
            user_input = await loop.run_in_executor(None, lambda: input(prompt))
        except (EOFError, KeyboardInterrupt):
            break

        if not user_input.strip():
            break
        if user_input.strip().lower() in ("/clear", "/reset"):
            conversation_history = []
            console.print("[dim]Conversation cleared.[/dim]\n")
            continue

        renderer.reset_timer()
        _, conversation_history = await _daemon_run_stream(
            app_id, user_input, renderer, conversation_history=conversation_history
        )
        console.print()


# ─── Commands ──────────────────────────────────────────────────────────


@app.command("register")
def register_app(
    file: Path = typer.Argument(..., help="Path to .app.yaml file"),
    application_id: str = typer.Option(None, "--app-id", help="Link to existing dashboard Application ID"),
) -> None:
    """Register and prepare an app in the daemon (first-time setup).

    This is the setup step — run once before using 'app run' or 'app exec'.
    The daemon will:
      1. Compile and validate the YAML
      2. Create or update the Application identity (security, modules, permissions)
      3. Prepare: validate modules, warm LLM connections, initialize memory

    After this, run the app with:
      llmos-bridge app run <file>
      llmos-bridge app exec <name>
    """
    if not _check_daemon():
        console.print("[red]Daemon is not running. Start it with: llmos-bridge daemon start[/red]")
        raise typer.Exit(1)

    async def _do_register():
        t0 = time.monotonic()

        # Register
        try:
            record = await _daemon_register(file, application_id=application_id)
        except RuntimeError as e:
            console.print(f"[red]Registration error:[/red] {e}")
            raise typer.Exit(1)

        app_id = record["id"]
        app_name = record.get("name", file.stem)
        app_version = record.get("version", "?")
        console.print(f"[dim]Registered: {app_name} v{app_version} (id={app_id})[/dim]")

        # Prepare
        try:
            prep = await _daemon_prepare(app_id)
        except RuntimeError as e:
            console.print(f"[red]Prepare failed:[/red] {e}")
            raise typer.Exit(1)

        dt = (time.monotonic() - t0) * 1000
        missing = prep.get("modules_missing", [])
        if missing:
            console.print(f"[yellow]Missing modules: {', '.join(missing)}[/yellow]")
            console.print("[yellow]App registered but not fully prepared.[/yellow]")
        else:
            console.print(
                f"[green]✓[/green] {app_name} v{app_version} ready — "
                f"{prep.get('modules_checked', 0)} modules, "
                f"{prep.get('tools_resolved', 0)} tools | {dt:.0f}ms"
            )
            console.print(f"[dim]Run with: llmos-bridge app run {file}[/dim]")

    try:
        asyncio.run(_do_register())
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted[/dim]")


@app.command("run")
def run_app(
    file: Path = typer.Argument(..., help="Path to .app.yaml file"),
    input_text: str = typer.Option("", "--input", "-i", help="Input text (skips interactive mode)"),
    standalone: bool = typer.Option(False, "--standalone", help="Force standalone mode (no daemon)"),
    application_id: str = typer.Option(None, "--app-id", help="Application ID (used only if registering for first time)"),
) -> None:
    """Run a prepared app from a .app.yaml file via the daemon.

    The app must already be registered and prepared in the daemon.
    Run 'app register <file>' first if this is the first time.

    Approvals: when a tool requires user approval, the terminal shows a
    prompt. Type (a)pprove / (r)eject / (s)kip / (aa)approve-always.

    Use --standalone to bypass the daemon (no security pipeline, limited modules).
    """
    if not standalone and _check_daemon():
        async def _run():
            # Look up the app in the daemon by computing its ID from the YAML
            lookup_id = application_id or _compute_app_id(file)
            record: dict | None = None
            if lookup_id:
                record = await _daemon_get_app(lookup_id)

            if record is None:
                console.print(
                    f"[red]App not registered in daemon.[/red]\n"
                    f"[dim]Register it first: llmos-bridge app register {file}[/dim]"
                )
                raise typer.Exit(1)

            if not record.get("prepared"):
                console.print(
                    f"[red]App '{record.get('name')}' is not prepared.[/red]\n"
                    f"[dim]Prepare it first: llmos-bridge app register {file}[/dim]"
                )
                raise typer.Exit(1)

            # App is prepared — run directly
            app_id = record["id"]
            app_name = record.get("name", file.stem)
            app_version = record.get("version", "?")
            app_desc = record.get("description", "")
            cli_greeting = _resolve_greeting(record.get("cli_greeting", ""))
            cli_prompt = record.get("cli_prompt", "> ")
            cli_mode = record.get("cli_mode", "conversation")

            console.print(
                Panel(
                    f"[bold]{app_name}[/bold] v{app_version}\n"
                    + (f"{app_desc}\n" if app_desc else ""),
                    border_style="blue",
                )
            )
            if cli_greeting:
                console.print(cli_greeting)

            renderer = _TerminalRenderer(console)
            if input_text or cli_mode == "one_shot":
                await _daemon_run_stream(app_id, input_text, renderer)
            else:
                await _daemon_run_interactive(app_id, renderer, prompt=cli_prompt)


        try:
            asyncio.run(_run())
        except KeyboardInterrupt:
            console.print("\n[dim]Interrupted[/dim]")
        return

    # ── Standalone fallback ──────────────────────────────────────────
    if not standalone:
        console.print("[yellow]Daemon not running — standalone mode[/yellow]")
    else:
        console.print("[yellow]Standalone mode (--standalone)[/yellow]")

    runtime = _create_standalone_runtime()

    try:
        app_def = runtime.load(file)
    except Exception as e:
        console.print(f"[red]Compilation error:[/red] {e}")
        raise typer.Exit(1)

    agent_info = ""
    if app_def.agent:
        brain = app_def.agent.brain
        tool_count = len(app_def.get_all_tools() or app_def.agent.tools or [])
        agent_info = f"{brain.provider}/{brain.model} | {tool_count} tools"
    elif app_def.agents and app_def.agents.agents:
        agent_count = len(app_def.agents.agents)
        agent_info = f"{agent_count} agents"

    console.print(
        Panel(
            f"[bold]{app_def.app.name}[/bold] v{app_def.app.version}\n"
            + (f"{app_def.app.description}\n" if app_def.app.description else "")
            + (f"[dim]{agent_info}[/dim]" if agent_info else ""),
            border_style="blue",
        )
    )

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


@app.command("exec")
def exec_app(
    app_id_or_name: str = typer.Argument(..., help="App ID or name (from registered apps)"),
    input_text: str = typer.Option("", "--input", "-i", help="Input text (skips interactive mode)"),
) -> None:
    """Execute a registered app by ID or name (no file needed).

    Useful for apps already registered in the daemon (via dashboard or
    a previous ``app run``). Requires daemon to be running.
    """
    import httpx

    if not _check_daemon():
        console.print("[red]Daemon is not running. Start it with: llmos-bridge daemon start[/red]")
        raise typer.Exit(1)

    async def _find_and_run():
        async with httpx.AsyncClient(base_url=_daemon_url(), timeout=10.0) as client:
            resp = await client.get("/apps")
            if resp.status_code != 200:
                console.print(f"[red]Failed to list apps: {resp.text}[/red]")
                raise typer.Exit(1)
            apps = resp.json()

        # Find by exact ID, then by name (case-insensitive)
        target = None
        for a in apps:
            if a["id"] == app_id_or_name:
                target = a
                break
        if target is None:
            for a in apps:
                if a["name"].lower() == app_id_or_name.lower():
                    target = a
                    break

        if target is None:
            console.print(f"[red]App '{app_id_or_name}' not found.[/red]")
            console.print("[dim]Use 'llmos-bridge app list' to see registered apps.[/dim]")
            raise typer.Exit(1)

        if not target.get("prepared"):
            console.print(
                f"[red]App '{target['name']}' is not prepared.[/red]\n"
                f"[dim]Prepare it first: llmos-bridge app register <file.app.yaml>[/dim]"
            )
            raise typer.Exit(1)

        # Fetch full app details to get CLI trigger info (greeting, prompt, mode)
        async with httpx.AsyncClient(base_url=_daemon_url(), timeout=10.0) as client:
            detail_resp = await client.get(f"/apps/{target['id']}")
            if detail_resp.status_code == 200:
                target = detail_resp.json()

        cli_greeting = _resolve_greeting(target.get("cli_greeting", ""))
        cli_prompt = target.get("cli_prompt", "> ")
        cli_mode = target.get("cli_mode", "conversation")

        console.print(
            Panel(
                f"[bold]{target['name']}[/bold] v{target['version']}\n"
                + (f"{target.get('description', '')}\n" if target.get("description") else "")
                + f"[dim]id={target['id']}[/dim]",
                border_style="blue",
            )
        )

        if cli_greeting:
            console.print(cli_greeting)

        renderer = _TerminalRenderer(console)
        if input_text or cli_mode == "one_shot":
            await _daemon_run_stream(target["id"], input_text, renderer)
        else:
            await _daemon_run_interactive(target["id"], renderer, prompt=cli_prompt)


    try:
        asyncio.run(_find_and_run())
    except KeyboardInterrupt:
        console.print("\n[dim]Interrupted[/dim]")


@app.command("list")
def list_apps() -> None:
    """List all registered apps in the daemon."""
    import httpx

    if not _check_daemon():
        console.print("[red]Daemon is not running.[/red]")
        raise typer.Exit(1)

    try:
        resp = httpx.get(f"{_daemon_url()}/apps", timeout=5.0)
        resp.raise_for_status()
        apps = resp.json()
    except Exception as e:
        console.print(f"[red]Failed to list apps: {e}[/red]")
        raise typer.Exit(1)

    if not apps:
        console.print("[dim]No apps registered.[/dim]")
        return

    table = Table(title="Registered Apps")
    table.add_column("Name", style="bold")
    table.add_column("Version")
    table.add_column("Status")
    table.add_column("Prepared")
    table.add_column("Runs")
    table.add_column("ID", style="dim")

    status_colors = {
        "registered": "blue", "running": "green", "stopped": "dim",
        "error": "red", "idle": "white",
    }
    for a in apps:
        st = a.get("status", "")
        color = status_colors.get(st, "white")
        table.add_row(
            a.get("name", ""),
            a.get("version", ""),
            f"[{color}]{st}[/{color}]",
            "[green]✓[/green]" if a.get("prepared") else "[dim]no[/dim]",
            str(a.get("run_count", 0)),
            a.get("id", "")[:16],
        )

    console.print(table)


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

    info_lines = [
        f"[bold]{app_def.app.name}[/bold] v{app_def.app.version}",
        f"{app_def.app.description}" if app_def.app.description else "",
        f"Author: {app_def.app.author}" if app_def.app.author else "",
        f"Tags: {', '.join(app_def.app.tags)}" if app_def.app.tags else "",
    ]
    console.print(Panel("\n".join(line for line in info_lines if line), title="Application"))

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

    if app_def.triggers:
        console.print(f"\n[bold]Triggers:[/bold] {len(app_def.triggers)}")
        for t in app_def.triggers:
            console.print(f"  - {t.id or t.type.value}: {t.type.value}")

    if app_def.macros:
        console.print(f"\n[bold]Macros:[/bold] {len(app_def.macros)}")
        for m in app_def.macros:
            params = ", ".join(f"{k}: {v.type}" for k, v in m.params.items())
            console.print(f"  - {m.name}({params})")

    if app_def.flow:
        console.print(f"\n[bold]Flow:[/bold] {len(app_def.flow)} steps")
        for step in app_def.flow:
            step_type = step.infer_type().value
            console.print(f"  - {step.id or '(anonymous)'}: {step_type}")

    if app_def.capabilities.grant:
        console.print(
            f"\n[bold]Capabilities:[/bold] {len(app_def.capabilities.grant)} grants, "
            f"{len(app_def.capabilities.deny)} denials, "
            f"{len(app_def.capabilities.approval_required)} approval rules"
        )


async def _cli_input_handler(question: str) -> str:
    """Handle ask_user builtin in CLI mode."""
    console.print(f"\n[yellow]{question}[/yellow]")
    return input("> ")


# ─── Secret management commands ──────────────────────────────────────


def _resolve_app_id_for_secret(name_or_file: str) -> str:
    """Resolve app_id from a name or YAML file path."""
    import hashlib
    import yaml as _yaml
    p = Path(name_or_file)
    if p.exists() and p.suffix in (".yaml", ".yml"):
        try:
            data = _yaml.safe_load(p.read_text()) or {}
            name = (data.get("app") or {}).get("name", "")
            version = str((data.get("app") or {}).get("version", "1.0"))
            if name:
                return hashlib.sha256(f"{name}:{version}".encode()).hexdigest()[:16]
        except Exception:
            pass
    # Treat as name — look up from daemon
    return name_or_file


@secret_app.command("set")
def secret_set(
    app_name: str = typer.Argument(..., help="App name or path to .app.yaml"),
    key: str = typer.Argument(..., help="Secret key (e.g. ANTHROPIC_API_KEY)"),
    value: str = typer.Option(..., "--value", "-v", help="Secret value", prompt="Secret value", hide_input=True),
) -> None:
    """Store an encrypted secret for an app in the daemon."""
    import httpx

    app_id = _resolve_app_id_for_secret(app_name)

    # If name was given, try to find the real app_id from daemon
    if not app_id or len(app_id) != 16:
        try:
            r = httpx.get(f"{_daemon_url()}/apps", timeout=5.0)
            if r.status_code == 200:
                for a in r.json():
                    if a["name"] == app_name:
                        app_id = a["id"]
                        break
        except Exception:
            pass

    try:
        r = httpx.put(
            f"{_daemon_url()}/apps/{app_id}/secrets/{key}",
            json={"value": value},
            timeout=5.0,
        )
        if r.status_code in (200, 204):
            console.print(f"[green]✓[/green] Secret [bold]{key}[/bold] stored for app [bold]{app_name}[/bold]")
        else:
            console.print(f"[red]Failed ({r.status_code}):[/red] {r.text}")
            raise typer.Exit(1)
    except httpx.ConnectError:
        console.print("[red]Daemon not running.[/red] Start with: llmos-bridge daemon start")
        raise typer.Exit(1)


@secret_app.command("list")
def secret_list(
    app_name: str = typer.Argument(..., help="App name or path to .app.yaml"),
) -> None:
    """List secret keys stored for an app (values are never shown)."""
    import httpx

    app_id = _resolve_app_id_for_secret(app_name)

    try:
        r = httpx.get(f"{_daemon_url()}/apps/{app_id}/secrets", timeout=5.0)
        if r.status_code == 200:
            keys = r.json()
            if keys:
                for k in keys:
                    console.print(f"  {k}")
            else:
                console.print("[dim]No secrets stored.[/dim]")
        else:
            console.print(f"[red]Failed ({r.status_code}):[/red] {r.text}")
            raise typer.Exit(1)
    except httpx.ConnectError:
        console.print("[red]Daemon not running.[/red] Start with: llmos-bridge daemon start")
        raise typer.Exit(1)


@secret_app.command("delete")
def secret_delete(
    app_name: str = typer.Argument(..., help="App name or path to .app.yaml"),
    key: str = typer.Argument(..., help="Secret key to delete"),
) -> None:
    """Delete a secret for an app."""
    import httpx

    app_id = _resolve_app_id_for_secret(app_name)

    try:
        r = httpx.delete(f"{_daemon_url()}/apps/{app_id}/secrets/{key}", timeout=5.0)
        if r.status_code in (200, 204):
            console.print(f"[green]✓[/green] Secret [bold]{key}[/bold] deleted")
        else:
            console.print(f"[red]Failed ({r.status_code}):[/red] {r.text}")
            raise typer.Exit(1)
    except httpx.ConnectError:
        console.print("[red]Daemon not running.[/red] Start with: llmos-bridge daemon start")
        raise typer.Exit(1)


async def _cli_output_handler(text: str) -> None:
    """Handle output in CLI mode."""
    console.print(text)
