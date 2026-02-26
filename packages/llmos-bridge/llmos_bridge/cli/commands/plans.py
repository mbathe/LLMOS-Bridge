"""CLI — Plan management commands."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.syntax import Syntax
from rich.table import Table

app = typer.Typer(help="Submit, inspect, and cancel IML plans.")
console = Console()


def _client(host: str, port: int) -> "httpx.Client":
    import httpx

    return httpx.Client(base_url=f"http://{host}:{port}", timeout=30.0)


@app.command("submit")
def submit_plan(
    plan_file: Path = typer.Argument(help="Path to the IML plan JSON file. Use - for stdin."),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(40000),
    sync: bool = typer.Option(False, "--sync", help="Wait for plan to complete."),
) -> None:
    """Submit an IML plan for execution."""
    import httpx

    if str(plan_file) == "-":
        raw = sys.stdin.read()
        try:
            plan_data = json.loads(raw)
        except json.JSONDecodeError as exc:
            console.print(f"[red]Invalid JSON from stdin: {exc}[/red]")
            raise typer.Exit(1)
    else:
        if not plan_file.exists():
            console.print(f"[red]File not found: {plan_file}[/red]")
            raise typer.Exit(1)
        plan_data = json.loads(plan_file.read_text())

    payload = {"plan": plan_data, "async_execution": not sync}

    try:
        with _client(host, port) as client:
            resp = client.post("/plans", json=payload)
            resp.raise_for_status()
            result = resp.json()
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)

    console.print(f"[green]Plan submitted:[/green] {result['plan_id']}")
    console.print(f"Status: {result['status']}")
    console.print(result.get("message", ""))


@app.command("list")
def list_plans(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(40000),
    plan_status: str | None = typer.Option(None, "--status", help="Filter by status."),
    limit: int = typer.Option(20, help="Maximum number of plans to show."),
) -> None:
    """List recent plans."""
    import httpx

    params: dict[str, object] = {"limit": limit}
    if plan_status:
        params["status"] = plan_status

    try:
        with _client(host, port) as client:
            resp = client.get("/plans", params=params)
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)

    table = Table(title="Plans")
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("Created")

    for plan in data.get("plans", []):
        table.add_row(
            plan["plan_id"],
            plan["status"],
            str(round(plan.get("created_at", 0), 0)),
        )
    console.print(table)


@app.command("get")
def get_plan(
    plan_id: str = typer.Argument(),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(40000),
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Get the status and results of a plan."""
    import httpx

    try:
        with _client(host, port) as client:
            resp = client.get(f"/plans/{plan_id}")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)

    if json_output:
        console.print(Syntax(json.dumps(data, indent=2), "json"))
        return

    console.print(f"[bold]Plan:[/bold] {data['plan_id']}")
    console.print(f"[bold]Status:[/bold] {data['status']}")

    table = Table(title="Actions")
    table.add_column("ID", style="cyan")
    table.add_column("Status")
    table.add_column("Error")

    for action in data.get("actions", []):
        table.add_row(
            action["action_id"],
            action["status"],
            action.get("error") or "",
        )
    console.print(table)


@app.command("cancel")
def cancel_plan(
    plan_id: str = typer.Argument(),
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(40000),
) -> None:
    """Cancel a running plan."""
    import httpx

    try:
        with _client(host, port) as client:
            resp = client.delete(f"/plans/{plan_id}")
            if resp.status_code == 204:
                console.print(f"[green]Plan {plan_id} cancelled.[/green]")
            else:
                console.print(f"[red]Error: {resp.text}[/red]")
    except Exception as exc:
        console.print(f"[red]Error: {exc}[/red]")
        raise typer.Exit(1)
