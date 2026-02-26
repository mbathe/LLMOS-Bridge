"""CLI — Module inspection commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

app = typer.Typer(help="Inspect loaded modules and their capabilities.")
console = Console()


def _client(host: str, port: int) -> "httpx.Client":
    import httpx

    return httpx.Client(base_url=f"http://{host}:{port}", timeout=10.0)


@app.command("list")
def list_modules(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(40000),
) -> None:
    """List all registered modules."""
    import httpx

    try:
        with _client(host, port) as client:
            resp = client.get("/modules")
            resp.raise_for_status()
            modules = resp.json()
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)

    table = Table(title="Registered Modules")
    table.add_column("ID", style="cyan")
    table.add_column("Version")
    table.add_column("Available", style="green")
    table.add_column("Actions")
    table.add_column("Description")

    for m in modules:
        table.add_row(
            m.get("module_id", ""),
            m.get("version", "-"),
            "yes" if m.get("available") else "[red]no[/red]",
            str(m.get("action_count", "-")),
            m.get("description", ""),
        )
    console.print(table)


@app.command("inspect")
def inspect_module(
    module_id: str = typer.Argument(help="Module ID to inspect."),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(40000),
    json_output: bool = typer.Option(False, "--json", help="Output raw JSON."),
) -> None:
    """Show the full manifest for a module."""
    import httpx

    try:
        with _client(host, port) as client:
            resp = client.get(f"/modules/{module_id}")
            resp.raise_for_status()
            manifest = resp.json()
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)

    if json_output:
        console.print(Syntax(json.dumps(manifest, indent=2), "json"))
        return

    console.print(f"[bold]{manifest['module_id']}[/bold] v{manifest['version']}")
    console.print(manifest.get("description", ""))
    console.print()

    table = Table(title="Actions")
    table.add_column("Action", style="cyan")
    table.add_column("Permission")
    table.add_column("Platforms")
    table.add_column("Description")

    for action in manifest.get("actions", []):
        table.add_row(
            action["name"],
            action.get("permission_required", ""),
            ", ".join(action.get("platforms", [])),
            action.get("description", ""),
        )
    console.print(table)
