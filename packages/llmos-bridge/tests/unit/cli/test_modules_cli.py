"""Unit tests — CLI modules commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from llmos_bridge.cli.commands.modules import app

runner = CliRunner()


def _make_mock_client(get_return_value: object) -> MagicMock:
    """Return a mock httpx.Client context manager."""
    mock_resp = MagicMock()
    mock_resp.json.return_value = get_return_value
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.get.return_value = mock_resp
    return mock_client


@pytest.mark.unit
class TestModulesList:
    def test_list_modules_success(self) -> None:
        modules_data = [
            {
                "module_id": "filesystem",
                "version": "1.0.0",
                "available": True,
                "action_count": 12,
                "description": "File I/O",
            }
        ]
        mock_client = _make_mock_client(modules_data)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 0

    def test_list_modules_empty(self) -> None:
        mock_client = _make_mock_client([])

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 0

    def test_list_modules_connection_error(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 1

    def test_list_unavailable_module(self) -> None:
        modules_data = [
            {
                "module_id": "broken",
                "version": "0.1.0",
                "available": False,
                "action_count": 0,
                "description": "broken module",
            }
        ]
        mock_client = _make_mock_client(modules_data)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 0


@pytest.mark.unit
class TestModulesInspect:
    def test_inspect_module_table_output(self) -> None:
        manifest = {
            "module_id": "filesystem",
            "version": "1.0.0",
            "description": "File operations",
            "actions": [
                {
                    "name": "read_file",
                    "permission_required": "readonly",
                    "platforms": ["all"],
                    "description": "Read a file",
                }
            ],
        }
        mock_client = _make_mock_client(manifest)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["inspect", "filesystem"])

        assert result.exit_code == 0

    def test_inspect_module_json_output(self) -> None:
        manifest = {
            "module_id": "filesystem",
            "version": "1.0.0",
            "description": "File operations",
            "actions": [],
        }
        mock_client = _make_mock_client(manifest)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["inspect", "filesystem", "--json"])

        assert result.exit_code == 0

    def test_inspect_module_connection_error(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["inspect", "filesystem"])

        assert result.exit_code == 1
