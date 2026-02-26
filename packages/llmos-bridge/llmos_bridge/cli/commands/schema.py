"""CLI — Schema inspection and export commands."""

from __future__ import annotations

import json

import typer
from rich.console import Console
from rich.syntax import Syntax

app = typer.Typer(help="Inspect and export IML protocol schemas.")
console = Console()


@app.command("dump")
def dump_schema(
    module_id: str | None = typer.Argument(
        default=None, help="Dump schema for a specific module. Dumps all if not specified."
    ),
    output: str | None = typer.Option(None, "--output", "-o", help="Output file path."),
) -> None:
    """Dump the IML JSONSchema to stdout or a file."""
    from llmos_bridge.protocol.schema import get_schema_registry

    registry = get_schema_registry()

    if module_id:
        schema = registry.get_module_schema(module_id)
    else:
        schema = registry.get_all_schemas()

    json_str = json.dumps(schema, indent=2, default=str)

    if output:
        import pathlib

        pathlib.Path(output).write_text(json_str)
        console.print(f"[green]Schema written to {output}[/green]")
    else:
        console.print(Syntax(json_str, "json"))


@app.command("plan")
def plan_schema() -> None:
    """Dump the IMLPlan JSONSchema."""
    from llmos_bridge.protocol.schema import get_schema_registry

    registry = get_schema_registry()
    json_str = json.dumps(registry.get_plan_schema(), indent=2, default=str)
    console.print(Syntax(json_str, "json"))
