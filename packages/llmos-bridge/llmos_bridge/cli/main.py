"""LLMOS Bridge CLI — Entry point.

Usage:
    llmos-bridge start
    llmos-bridge stop
    llmos-bridge status
    llmos-bridge modules list
    llmos-bridge modules inspect <module_id>
    llmos-bridge plans list
    llmos-bridge plans submit <file.json>
    llmos-bridge plans get <plan_id>
    llmos-bridge plans cancel <plan_id>
    llmos-bridge schema dump
"""

from __future__ import annotations

import typer
from rich.console import Console

from llmos_bridge.cli.commands import daemon, modules, plans, schema

app = typer.Typer(
    name="llmos-bridge",
    help="LLMOS Bridge — Local daemon bridging LLMs to OS, applications, and devices.",
    no_args_is_help=True,
    pretty_exceptions_enable=False,
)

console = Console()

app.add_typer(daemon.app, name="daemon")
app.add_typer(modules.app, name="modules")
app.add_typer(plans.app, name="plans")
app.add_typer(schema.app, name="schema")


@app.callback()
def main_callback() -> None:
    pass


if __name__ == "__main__":
    app()
