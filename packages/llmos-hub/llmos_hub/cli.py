"""CLI entry-point for the LLMOS Hub server."""

from __future__ import annotations

import asyncio
import secrets
import uuid

import typer
from rich.console import Console

app = typer.Typer(name="llmos-hub", help="LLMOS Module Hub Server")
console = Console()


@app.command()
def start(
    host: str = typer.Option("0.0.0.0", help="Bind address"),
    port: int = typer.Option(8080, help="Listen port"),
    data_dir: str = typer.Option("~/.llmos-hub", help="Data directory"),
    log_level: str = typer.Option("info", help="Log level"),
):
    """Start the LLMOS Hub server."""
    import uvicorn

    from llmos_hub.config import HubServerSettings
    from llmos_hub.api import create_hub_app

    settings = HubServerSettings(host=host, port=port, data_dir=data_dir, log_level=log_level)
    hub_app = create_hub_app(settings)

    console.print(f"[bold green]LLMOS Hub[/] starting on {host}:{port}")
    console.print(f"  Data dir: {settings.resolved_data_dir}")

    uvicorn.run(hub_app, host=host, port=port, log_level=log_level)


@app.command("create-publisher")
def create_publisher(
    name: str = typer.Option(..., help="Publisher display name"),
    data_dir: str = typer.Option("~/.llmos-hub", help="Data directory"),
):
    """Create a new publisher and print their API key (shown once)."""
    from llmos_hub.auth import generate_api_key, hash_api_key
    from llmos_hub.config import HubServerSettings
    from llmos_hub.store import HubStore

    settings = HubServerSettings(data_dir=data_dir)
    api_key = generate_api_key()
    key_hash = hash_api_key(api_key)
    publisher_id = str(uuid.uuid4())

    async def _create():
        settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
        store = HubStore(str(settings.resolved_db_path))
        await store.init()
        await store.create_publisher(publisher_id, name, key_hash)
        await store.close()

    asyncio.run(_create())

    console.print(f"[bold green]Publisher created:[/] {name}")
    console.print(f"  ID:      {publisher_id}")
    console.print(f"  API Key: [bold yellow]{api_key}[/]")
    console.print("[dim]Save this key — it cannot be retrieved later.[/dim]")


if __name__ == "__main__":
    app()
