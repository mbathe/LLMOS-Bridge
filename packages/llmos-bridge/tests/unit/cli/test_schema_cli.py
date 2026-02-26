"""Unit tests — CLI schema commands."""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest
from typer.testing import CliRunner

from llmos_bridge.cli.commands.schema import app


runner = CliRunner()


@pytest.mark.unit
class TestSchemaDumpCommand:
    def test_dump_all_schemas_to_stdout(self) -> None:
        result = runner.invoke(app, ["dump"])
        assert result.exit_code == 0

    def test_dump_module_schema_for_filesystem(self) -> None:
        result = runner.invoke(app, ["dump", "filesystem"])
        assert result.exit_code == 0

    def test_dump_all_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_file = str(Path(tmp) / "schema.json")
            result = runner.invoke(app, ["dump", "--output", output_file])
            assert result.exit_code == 0
            content = Path(output_file).read_text()
            parsed = json.loads(content)
            assert "plan_schema" in parsed or "read_file" in str(parsed)

    def test_dump_module_to_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            output_file = str(Path(tmp) / "fs_schema.json")
            result = runner.invoke(app, ["dump", "filesystem", "--output", output_file])
            assert result.exit_code == 0
            content = Path(output_file).read_text()
            parsed = json.loads(content)
            assert isinstance(parsed, dict)

    def test_dump_unknown_module_exits_ok(self) -> None:
        """Unknown module produces an empty schema, not an error."""
        result = runner.invoke(app, ["dump", "nonexistent_module"])
        assert result.exit_code == 0


@pytest.mark.unit
class TestSchemaPlanCommand:
    def test_plan_schema_to_stdout(self) -> None:
        result = runner.invoke(app, ["plan"])
        assert result.exit_code == 0
