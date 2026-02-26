"""Unit tests — CLI plans commands."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from llmos_bridge.cli.commands.plans import app

runner = CliRunner()


def _make_mock_client(
    method: str = "get",
    return_value: object = None,
    status_code: int = 200,
) -> MagicMock:
    mock_resp = MagicMock()
    mock_resp.json.return_value = return_value or {}
    mock_resp.status_code = status_code
    mock_resp.text = json.dumps(return_value or {})
    mock_resp.raise_for_status = MagicMock()

    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    getattr(mock_client, method).return_value = mock_resp
    return mock_client


@pytest.mark.unit
class TestPlansSubmit:
    def test_submit_from_file(self, tmp_path: Path) -> None:
        plan_file = tmp_path / "plan.json"
        plan_file.write_text(
            json.dumps(
                {
                    "version": "2.0",
                    "plan_id": "test_plan",
                    "actions": [],
                }
            )
        )
        submit_resp = {"plan_id": "plan_abc123", "status": "queued", "message": "Accepted"}
        mock_client = _make_mock_client("post", submit_resp)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["submit", str(plan_file)])

        assert result.exit_code == 0

    def test_submit_file_not_found(self, tmp_path: Path) -> None:
        result = runner.invoke(app, ["submit", str(tmp_path / "nonexistent.json")])
        assert result.exit_code == 1

    def test_submit_from_stdin(self) -> None:
        plan_json = json.dumps({"version": "2.0", "plan_id": "stdin_plan", "actions": []})
        submit_resp = {"plan_id": "plan_stdin", "status": "queued", "message": "OK"}
        mock_client = _make_mock_client("post", submit_resp)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["submit", "-"], input=plan_json)

        assert result.exit_code == 0

    def test_submit_invalid_json_from_stdin(self) -> None:
        with patch("httpx.Client", return_value=MagicMock()):
            result = runner.invoke(app, ["submit", "-"], input="not valid json {{{")

        assert result.exit_code == 1

    def test_submit_connection_error(self, tmp_path: Path) -> None:
        import httpx

        plan_file = tmp_path / "plan.json"
        plan_file.write_text(json.dumps({"version": "2.0", "plan_id": "x", "actions": []}))

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["submit", str(plan_file)])

        assert result.exit_code == 1


@pytest.mark.unit
class TestPlansList:
    def test_list_plans_success(self) -> None:
        plans_data = {
            "plans": [
                {"plan_id": "plan1", "status": "completed", "created_at": time.time()},
                {"plan_id": "plan2", "status": "running", "created_at": time.time()},
            ]
        }
        mock_client = _make_mock_client("get", plans_data)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 0

    def test_list_plans_with_status_filter(self) -> None:
        plans_data = {"plans": [{"plan_id": "p1", "status": "running", "created_at": 0}]}
        mock_client = _make_mock_client("get", plans_data)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["list", "--status", "running"])

        assert result.exit_code == 0

    def test_list_plans_connection_error(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["list"])

        assert result.exit_code == 1


@pytest.mark.unit
class TestPlansGet:
    def test_get_plan_table_output(self) -> None:
        plan_data = {
            "plan_id": "plan_abc",
            "status": "completed",
            "actions": [
                {"action_id": "a1", "status": "success", "error": None},
            ],
        }
        mock_client = _make_mock_client("get", plan_data)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["get", "plan_abc"])

        assert result.exit_code == 0

    def test_get_plan_json_output(self) -> None:
        plan_data = {"plan_id": "plan_abc", "status": "completed", "actions": []}
        mock_client = _make_mock_client("get", plan_data)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["get", "plan_abc", "--json"])

        assert result.exit_code == 0

    def test_get_plan_with_error_action(self) -> None:
        plan_data = {
            "plan_id": "plan_abc",
            "status": "failed",
            "actions": [
                {"action_id": "a1", "status": "failed", "error": "Something went wrong"},
            ],
        }
        mock_client = _make_mock_client("get", plan_data)

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["get", "plan_abc"])

        assert result.exit_code == 0

    def test_get_plan_connection_error(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["get", "plan_abc"])

        assert result.exit_code == 1


@pytest.mark.unit
class TestPlansCancel:
    def test_cancel_plan_success(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 204

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete.return_value = mock_resp

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["cancel", "plan_xyz"])

        assert result.exit_code == 0

    def test_cancel_plan_error_response(self) -> None:
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.text = "Not Found"

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(return_value=mock_client)
        mock_client.__exit__ = MagicMock(return_value=False)
        mock_client.delete.return_value = mock_resp

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["cancel", "plan_xyz"])

        # Exit code 0 (the command just prints the error text, doesn't exit 1)
        # Check that some output was produced
        assert result.output != ""

    def test_cancel_connection_error(self) -> None:
        import httpx

        mock_client = MagicMock()
        mock_client.__enter__ = MagicMock(side_effect=httpx.ConnectError("refused"))

        with patch("httpx.Client", return_value=mock_client):
            result = runner.invoke(app, ["cancel", "plan_xyz"])

        assert result.exit_code == 1
