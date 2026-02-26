"""CLI — Daemon management commands."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

app = typer.Typer(help="Start, stop, and inspect the LLMOS Bridge daemon.")
console = Console()


@app.command("start")
def start(
    host: str = typer.Option("127.0.0.1", help="Host to bind to."),
    port: int = typer.Option(40000, help="Port to listen on."),
    config: Annotated[
        Path | None, typer.Option("--config", "-c", help="Path to config.yaml.")
    ] = None,
    reload: bool = typer.Option(False, "--reload", help="Enable auto-reload (dev only)."),
    log_level: str = typer.Option("info", help="Log level."),
) -> None:
    """Start the LLMOS Bridge daemon."""
    from llmos_bridge.api.server import create_app
    from llmos_bridge.config import Settings

    settings = Settings.load(config_file=config)
    settings.server.host = host
    settings.server.port = port

    console.print(f"[bold green]Starting LLMOS Bridge on {host}:{port}[/bold green]")

    app_instance = create_app(settings=settings)

    uvicorn.run(
        app_instance,
        host=host,
        port=port,
        log_level=log_level,
        reload=reload,
    )


@app.command("status")
def status(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(40000),
) -> None:
    """Check daemon status."""
    import httpx

    try:
        resp = httpx.get(f"http://{host}:{port}/health", timeout=5.0)
        data = resp.json()
        table = Table(title="LLMOS Bridge Status")
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")
        for k, v in data.items():
            table.add_row(str(k), str(v))
        console.print(table)
    except Exception as exc:
        console.print(f"[red]Daemon unreachable: {exc}[/red]")
        raise typer.Exit(1)
