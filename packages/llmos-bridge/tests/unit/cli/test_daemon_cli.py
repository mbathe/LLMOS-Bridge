"""Unit tests — CLI daemon commands."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from llmos_bridge.cli.commands.daemon import app

runner = CliRunner()


@pytest.mark.unit
class TestDaemonStart:
    def test_start_invokes_uvicorn(self) -> None:
        mock_settings = MagicMock()
        mock_settings.server.host = "127.0.0.1"
        mock_settings.server.port = 40000

        with patch("llmos_bridge.config.Settings.load", return_value=mock_settings), \
             patch("llmos_bridge.api.server.create_app", return_value=MagicMock()) as mock_create, \
             patch("llmos_bridge.cli.commands.daemon.uvicorn.run") as mock_uvicorn:
            result = runner.invoke(app, ["start", "--host", "127.0.0.1", "--port", "40001"])

        assert result.exit_code == 0
        mock_uvicorn.assert_called_once()

    def test_start_with_log_level(self) -> None:
        mock_settings = MagicMock()

        with patch("llmos_bridge.config.Settings.load", return_value=mock_settings), \
             patch("llmos_bridge.api.server.create_app", return_value=MagicMock()), \
             patch("llmos_bridge.cli.commands.daemon.uvicorn.run") as mock_uvicorn:
            result = runner.invoke(app, ["start", "--log-level", "debug"])

        assert result.exit_code == 0
        call_kwargs = mock_uvicorn.call_args[1]
        assert call_kwargs["log_level"] == "debug"

    def test_start_with_reload(self) -> None:
        mock_settings = MagicMock()

        with patch("llmos_bridge.config.Settings.load", return_value=mock_settings), \
             patch("llmos_bridge.api.server.create_app", return_value=MagicMock()), \
             patch("llmos_bridge.cli.commands.daemon.uvicorn.run") as mock_uvicorn:
            result = runner.invoke(app, ["start", "--reload"])

        assert result.exit_code == 0
        call_kwargs = mock_uvicorn.call_args[1]
        assert call_kwargs["reload"] is True


@pytest.mark.unit
class TestDaemonStatus:
    def test_status_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "running", "version": "1.0.0", "uptime_s": 42.0}

        with patch("httpx.get", return_value=mock_resp):
            result = runner.invoke(app, ["status"])

        assert result.exit_code == 0

    def test_status_unreachable_exits_1(self) -> None:
        import httpx

        with patch("httpx.get", side_effect=httpx.ConnectError("unreachable")):
            result = runner.invoke(app, ["status"])

        assert result.exit_code == 1

    def test_status_custom_host_port(self) -> None:
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"status": "running"}

        with patch("httpx.get", return_value=mock_resp) as mock_get:
            result = runner.invoke(app, ["status", "--host", "192.168.1.1", "--port", "9999"])

        assert result.exit_code == 0
        call_url = mock_get.call_args[0][0]
        assert "192.168.1.1" in call_url
        assert "9999" in call_url
