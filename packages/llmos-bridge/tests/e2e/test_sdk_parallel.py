"""End-to-end tests — SDK parallel execution (submit_plan_group).

Tests the full chain:
    SDK.submit_plan_group()  →  HTTP  →  PlanGroupExecutor  →  Module  →  aggregated Result
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llmos_bridge.api.server import create_app
from llmos_bridge.config import Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def daemon_settings(tmp_path: Path) -> Settings:
    return Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        security={"require_approval_for": []},
        security_advanced={"enable_decorators": False},
    )


@pytest.fixture
def test_client(daemon_settings: Settings) -> TestClient:
    app = create_app(settings=daemon_settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def sdk_client(test_client: TestClient):
    """LLMOSClient wired to the TestClient transport."""
    from langchain_llmos.client import LLMOSClient

    client = LLMOSClient.__new__(LLMOSClient)
    client._http = test_client
    client._base_url = str(test_client.base_url)
    client._api_token = None
    client._timeout = 30.0
    return client


# ---------------------------------------------------------------------------
# SDK submit_plan_group
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSDKSubmitPlanGroup:
    """Test LLMOSClient.submit_plan_group() against a real daemon."""

    def test_single_plan_group(self, sdk_client, tmp_path: Path) -> None:
        f = tmp_path / "parallel_single.txt"
        f.write_text("single plan group content")

        plan = {
            "plan_id": "sdk-group-single",
            "protocol_version": "2.0",
            "description": "Single plan in group",
            "actions": [
                {
                    "id": "read",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(f)},
                }
            ],
        }
        result = sdk_client.submit_plan_group([plan], timeout=30)
        assert result["status"] == "completed"
        assert result["summary"]["total"] == 1
        assert result["summary"]["completed"] == 1
        assert len(result["errors"]) == 0

    def test_multiple_plans_parallel(self, sdk_client, tmp_path: Path) -> None:
        """Submit 5 plans in parallel — all should complete."""
        plans = []
        for i in range(5):
            f = tmp_path / f"parallel_{i}.txt"
            f.write_text(f"content {i}")
            plans.append({
                "plan_id": f"sdk-parallel-{i}",
                "protocol_version": "2.0",
                "description": f"Parallel plan {i}",
                "actions": [
                    {
                        "id": "read",
                        "action": "read_file",
                        "module": "filesystem",
                        "params": {"path": str(f)},
                    }
                ],
            })

        result = sdk_client.submit_plan_group(plans, timeout=30)
        assert result["status"] == "completed"
        assert result["summary"]["total"] == 5
        assert result["summary"]["completed"] == 5
        assert len(result["results"]) == 5

    def test_custom_group_id(self, sdk_client, tmp_path: Path) -> None:
        f = tmp_path / "gid.txt"
        f.write_text("group id test")
        plan = {
            "plan_id": "sdk-gid-001",
            "protocol_version": "2.0",
            "description": "Custom group ID",
            "actions": [
                {
                    "id": "read",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(f)},
                }
            ],
        }
        result = sdk_client.submit_plan_group(
            [plan], group_id="my-sdk-group", timeout=30
        )
        assert result["group_id"] == "my-sdk-group"

    def test_max_concurrent_respected(self, sdk_client, tmp_path: Path) -> None:
        plans = []
        for i in range(8):
            f = tmp_path / f"conc_{i}.txt"
            f.write_text(f"concurrent {i}")
            plans.append({
                "plan_id": f"sdk-conc-{i}",
                "protocol_version": "2.0",
                "description": f"Concurrent plan {i}",
                "actions": [
                    {
                        "id": "read",
                        "action": "read_file",
                        "module": "filesystem",
                        "params": {"path": str(f)},
                    }
                ],
            })

        result = sdk_client.submit_plan_group(plans, max_concurrent=2, timeout=30)
        assert result["status"] == "completed"
        assert result["summary"]["completed"] == 8

    def test_write_plans_parallel(self, sdk_client, tmp_path: Path) -> None:
        """Submit parallel write plans, verify files actually created."""
        plans = []
        files = []
        for i in range(3):
            f = tmp_path / f"write_parallel_{i}.txt"
            files.append(f)
            plans.append({
                "plan_id": f"sdk-write-{i}",
                "protocol_version": "2.0",
                "description": f"Write file {i}",
                "actions": [
                    {
                        "id": "write",
                        "action": "write_file",
                        "module": "filesystem",
                        "params": {
                            "path": str(f),
                            "content": f"parallel write {i}",
                        },
                    }
                ],
            })

        result = sdk_client.submit_plan_group(plans, timeout=30)
        assert result["status"] == "completed"
        assert result["summary"]["completed"] == 3

        # Verify actual file contents
        for i, f in enumerate(files):
            assert f.exists()
            assert f.read_text() == f"parallel write {i}"


# ---------------------------------------------------------------------------
# SDK submit_plan_group — error handling
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSDKPlanGroupErrors:
    """Test error cases through the SDK."""

    def test_partial_failure(self, sdk_client, tmp_path: Path) -> None:
        good_file = tmp_path / "good.txt"
        good_file.write_text("good content")

        plans = [
            {
                "plan_id": "sdk-good",
                "protocol_version": "2.0",
                "description": "Good plan",
                "actions": [
                    {
                        "id": "read",
                        "action": "read_file",
                        "module": "filesystem",
                        "params": {"path": str(good_file)},
                    }
                ],
            },
            {
                "plan_id": "sdk-bad",
                "protocol_version": "2.0",
                "description": "Bad plan — nonexistent file",
                "actions": [
                    {
                        "id": "read",
                        "action": "read_file",
                        "module": "filesystem",
                        "params": {"path": "/does/not/exist.txt"},
                    }
                ],
            },
        ]
        result = sdk_client.submit_plan_group(plans, timeout=30)
        assert result["summary"]["total"] == 2
        # At least the good plan should succeed
        assert result["summary"]["completed"] >= 1

    def test_invalid_plan_raises_http_error(self, sdk_client) -> None:
        """Invalid plan content triggers a validation error from the daemon."""
        import httpx

        with pytest.raises(httpx.HTTPStatusError) as exc_info:
            sdk_client.submit_plan_group([{"bad": "plan"}], timeout=30)
        assert exc_info.value.response.status_code == 422


# ---------------------------------------------------------------------------
# Multi-action parallel plans
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSDKPlanGroupMultiAction:
    """Plans with multiple actions each, run in parallel."""

    def test_multi_action_plans(self, sdk_client, tmp_path: Path) -> None:
        """Each plan has write+read, 3 plans in parallel."""
        plans = []
        for i in range(3):
            f = tmp_path / f"multi_action_{i}.txt"
            plans.append({
                "plan_id": f"sdk-multi-{i}",
                "protocol_version": "2.0",
                "description": f"Multi-action plan {i}",
                "actions": [
                    {
                        "id": "write",
                        "action": "write_file",
                        "module": "filesystem",
                        "params": {
                            "path": str(f),
                            "content": f"multi-action content {i}",
                        },
                    },
                    {
                        "id": "read",
                        "action": "read_file",
                        "module": "filesystem",
                        "params": {"path": str(f)},
                        "depends_on": ["write"],
                    },
                ],
            })

        result = sdk_client.submit_plan_group(plans, timeout=30)
        assert result["status"] == "completed"
        assert result["summary"]["completed"] == 3

        # Each plan had 2 actions
        for plan_id in [f"sdk-multi-{i}" for i in range(3)]:
            assert result["results"][plan_id]["actions"] == 2


# ---------------------------------------------------------------------------
# Toolkit execute_parallel
# ---------------------------------------------------------------------------


@pytest.fixture
def sdk_toolkit(test_client: TestClient):
    """LLMOSToolkit wired to use the TestClient transport."""
    from langchain_llmos.client import LLMOSClient
    from langchain_llmos.toolkit import LLMOSToolkit

    toolkit = LLMOSToolkit.__new__(LLMOSToolkit)
    toolkit._base_url = str(test_client.base_url)
    toolkit._api_token = None
    toolkit._timeout = 30.0
    toolkit._manifests = None
    toolkit._system_prompt = None
    toolkit._async_client = None

    client = LLMOSClient.__new__(LLMOSClient)
    client._http = test_client
    client._base_url = str(test_client.base_url)
    client._api_token = None
    client._timeout = 30.0
    toolkit._client = client

    return toolkit


@pytest.mark.e2e
class TestToolkitExecuteParallel:
    """Test LLMOSToolkit.execute_parallel() against a real daemon."""

    def test_execute_parallel_basic(self, sdk_toolkit, tmp_path: Path) -> None:
        """Execute 3 write actions in parallel via toolkit."""
        actions = []
        files = []
        for i in range(3):
            f = tmp_path / f"toolkit_parallel_{i}.txt"
            files.append(f)
            actions.append({
                "module": "filesystem",
                "action": "write_file",
                "params": {"path": str(f), "content": f"toolkit content {i}"},
            })

        result = sdk_toolkit.execute_parallel(actions, timeout=30)
        assert result["status"] == "completed"
        assert result["summary"]["total"] == 3
        assert result["summary"]["completed"] == 3

        for i, f in enumerate(files):
            assert f.read_text() == f"toolkit content {i}"

    def test_execute_parallel_custom_group_id(
        self, sdk_toolkit, tmp_path: Path
    ) -> None:
        f = tmp_path / "tk_gid.txt"
        result = sdk_toolkit.execute_parallel(
            [{"module": "filesystem", "action": "write_file",
              "params": {"path": str(f), "content": "gid test"}}],
            group_id="tk-custom-group",
            timeout=30,
        )
        assert result["group_id"] == "tk-custom-group"

    def test_execute_parallel_read_actions(
        self, sdk_toolkit, tmp_path: Path
    ) -> None:
        """Read multiple files in parallel."""
        for i in range(4):
            (tmp_path / f"tk_read_{i}.txt").write_text(f"read content {i}")

        actions = [
            {
                "module": "filesystem",
                "action": "read_file",
                "params": {"path": str(tmp_path / f"tk_read_{i}.txt")},
            }
            for i in range(4)
        ]

        result = sdk_toolkit.execute_parallel(actions, max_concurrent=4, timeout=30)
        assert result["status"] == "completed"
        assert result["summary"]["completed"] == 4
