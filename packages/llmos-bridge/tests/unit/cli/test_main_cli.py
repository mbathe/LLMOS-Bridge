"""Unit tests — CLI main app."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from llmos_bridge.cli.main import app

runner = CliRunner()


@pytest.mark.unit
class TestMainCLI:
    def test_help_exits_zero(self) -> None:
        result = runner.invoke(app, ["--help"])
        assert result.exit_code == 0

    def test_main_no_args_shows_help(self) -> None:
        # no_args_is_help=True → shows help (exit code 0 or 2 depending on typer version)
        result = runner.invoke(app, [])
        assert result.exit_code in (0, 2)
        assert result.output is not None

    def test_daemon_subcommand_help(self) -> None:
        result = runner.invoke(app, ["daemon", "--help"])
        assert result.exit_code == 0

    def test_modules_subcommand_help(self) -> None:
        result = runner.invoke(app, ["modules", "--help"])
        assert result.exit_code == 0

    def test_plans_subcommand_help(self) -> None:
        result = runner.invoke(app, ["plans", "--help"])
        assert result.exit_code == 0

    def test_schema_subcommand_help(self) -> None:
        result = runner.invoke(app, ["schema", "--help"])
        assert result.exit_code == 0
