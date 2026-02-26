"""End-to-end tests — LangChain SDK ↔ real daemon integration.

These tests start a real FastAPI test client and then wire the LangChain SDK
(LLMOSClient, LLMOSToolkit) to use it.  This validates the full chain:

    SDK  →  HTTP  →  Parser  →  Validator  →  DAG  →  Executor  →  Module  →  Result

No mocks, no fake transports — the daemon runs for real (in-process via TestClient).
"""

from __future__ import annotations

import json
import threading
import time
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
    """Settings for an E2E daemon with filesystem + os_exec."""
    return Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        # Disable approval requirements for testing (run_command needs approval by default)
        security={"require_approval_for": []},
    )


@pytest.fixture
def test_client(daemon_settings: Settings) -> TestClient:
    """Create a TestClient that triggers real startup/shutdown events."""
    app = create_app(settings=daemon_settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture
def sdk_client(test_client: TestClient):
    """LLMOSClient wired to use the TestClient transport."""
    from langchain_llmos.client import LLMOSClient

    client = LLMOSClient.__new__(LLMOSClient)
    # Wire the SDK's httpx client to use the same ASGI transport
    client._http = test_client
    client._base_url = str(test_client.base_url)
    client._api_token = None
    client._timeout = 30.0
    return client


@pytest.fixture
def sdk_toolkit(test_client: TestClient):
    """LLMOSToolkit wired to use the TestClient transport."""
    from langchain_llmos.toolkit import LLMOSToolkit

    toolkit = LLMOSToolkit.__new__(LLMOSToolkit)
    toolkit._base_url = str(test_client.base_url)
    toolkit._api_token = None
    toolkit._timeout = 30.0
    toolkit._manifests = None
    toolkit._system_prompt = None
    toolkit._async_client = None

    # Create client wired to TestClient
    from langchain_llmos.client import LLMOSClient

    client = LLMOSClient.__new__(LLMOSClient)
    client._http = test_client
    client._base_url = str(test_client.base_url)
    client._api_token = None
    client._timeout = 30.0
    toolkit._client = client

    return toolkit


# ---------------------------------------------------------------------------
# SDK Client tests against real daemon
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSDKClientIntegration:
    """Test LLMOSClient against a real daemon."""

    def test_health(self, sdk_client) -> None:
        health = sdk_client.health()
        assert health["status"] == "ok"
        assert "version" in health
        assert health["modules_loaded"] == 2  # filesystem + os_exec

    def test_list_modules(self, sdk_client) -> None:
        modules = sdk_client.list_modules()
        ids = [m["module_id"] for m in modules]
        assert "filesystem" in ids
        assert "os_exec" in ids

    def test_get_module_manifest(self, sdk_client) -> None:
        manifest = sdk_client.get_module_manifest("filesystem")
        assert manifest["module_id"] == "filesystem"
        action_names = [a["name"] for a in manifest["actions"]]
        assert "read_file" in action_names
        assert "write_file" in action_names

    def test_get_context_full(self, sdk_client) -> None:
        context = sdk_client.get_context()
        assert isinstance(context, dict)
        assert "system_prompt" in context
        assert "modules" in context
        assert context["total_actions"] > 0
        assert "permission_profile" in context

    def test_get_system_prompt(self, sdk_client) -> None:
        prompt = sdk_client.get_system_prompt()
        assert isinstance(prompt, str)
        assert "LLMOS Bridge" in prompt
        assert "IML Protocol v2" in prompt
        assert "filesystem" in prompt
        # New sections from Fix 2
        assert "Perception" in prompt
        assert "Memory" in prompt

    def test_submit_plan_sync(self, sdk_client, tmp_path: Path) -> None:
        """Submit a real plan through the SDK and get results."""
        test_file = tmp_path / "sdk_test.txt"
        test_file.write_text("hello from sdk test")

        plan = {
            "plan_id": "sdk-test-001",
            "protocol_version": "2.0",
            "description": "SDK integration test: read a file",
            "actions": [
                {
                    "id": "read",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(test_file)},
                }
            ],
        }
        result = sdk_client.submit_plan(plan, async_execution=False)
        assert result["status"] == "completed"
        assert len(result["actions"]) == 1
        assert result["actions"][0]["status"] == "completed"


# ---------------------------------------------------------------------------
# SDK Toolkit tests against real daemon
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSDKToolkitIntegration:
    """Test LLMOSToolkit tool generation against real daemon."""

    def test_get_tools_returns_tools(self, sdk_toolkit) -> None:
        tools = sdk_toolkit.get_tools()
        assert len(tools) > 0
        names = {t.name for t in tools}
        assert "filesystem__read_file" in names
        assert "filesystem__write_file" in names

    def test_tools_have_descriptions(self, sdk_toolkit) -> None:
        tools = sdk_toolkit.get_tools()
        for tool in tools:
            assert tool.description
            assert "[" in tool.description  # [module_id] prefix

    def test_tools_have_args_schema(self, sdk_toolkit) -> None:
        tools = sdk_toolkit.get_tools()
        read_tool = next(t for t in tools if t.name == "filesystem__read_file")
        assert read_tool.args_schema is not None
        assert "path" in read_tool.args_schema.model_fields

    def test_filter_by_module(self, sdk_toolkit) -> None:
        fs_tools = sdk_toolkit.get_tools(modules=["filesystem"])
        assert all("filesystem" in t.name for t in fs_tools)
        assert len(fs_tools) >= 2  # At least read + write

    def test_filter_by_permission(self, sdk_toolkit) -> None:
        readonly_tools = sdk_toolkit.get_tools(max_permission="readonly")
        for tool in readonly_tools:
            assert "readonly" in tool.description

    def test_get_system_prompt(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "LLMOS Bridge" in prompt
        assert "filesystem" in prompt
        assert "Perception" in prompt
        assert "Memory" in prompt

    def test_get_context(self, sdk_toolkit) -> None:
        context = sdk_toolkit.get_context()
        assert isinstance(context, dict)
        assert "system_prompt" in context
        assert context["total_actions"] > 0


# ---------------------------------------------------------------------------
# SDK Tool execution against real daemon
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSDKToolExecution:
    """Test actual tool execution (SDK tool → daemon → module → result)."""

    def test_read_file_tool(self, sdk_toolkit, tmp_path: Path) -> None:
        """Full chain: LangChain tool.invoke() → daemon → filesystem → result."""
        test_file = tmp_path / "tool_test.txt"
        test_file.write_text("content from tool test")

        tools = sdk_toolkit.get_tools()
        read_tool = next(t for t in tools if t.name == "filesystem__read_file")

        result_str = read_tool.invoke({"path": str(test_file)})
        result = json.loads(result_str)
        assert "content" in result
        assert "content from tool test" in result["content"]

    def test_write_file_tool(self, sdk_toolkit, tmp_path: Path) -> None:
        """Write a file via LangChain tool."""
        output_file = tmp_path / "tool_write.txt"

        tools = sdk_toolkit.get_tools()
        write_tool = next(t for t in tools if t.name == "filesystem__write_file")

        result_str = write_tool.invoke({
            "path": str(output_file),
            "content": "written by langchain tool",
        })
        result = json.loads(result_str)
        assert output_file.read_text() == "written by langchain tool"

    def test_run_command_tool(self, sdk_toolkit) -> None:
        """Execute a command via LangChain tool."""
        tools = sdk_toolkit.get_tools(max_permission="local_worker")
        exec_tool = next(
            (t for t in tools if t.name == "os_exec__run_command"), None
        )
        if exec_tool is None:
            pytest.skip("os_exec__run_command not available at local_worker level")

        result_str = exec_tool.invoke({"command": ["echo", "hello from llmos"]})
        result = json.loads(result_str)
        assert "hello from llmos" in result.get("stdout", "")

    def test_chained_write_then_read(self, sdk_toolkit, tmp_path: Path) -> None:
        """Simulate a LangChain agent doing write → read."""
        file_path = str(tmp_path / "chain_test.txt")

        tools = sdk_toolkit.get_tools()
        write_tool = next(t for t in tools if t.name == "filesystem__write_file")
        read_tool = next(t for t in tools if t.name == "filesystem__read_file")

        # Agent step 1: write
        write_tool.invoke({"path": file_path, "content": "chained data"})

        # Agent step 2: read back
        result_str = read_tool.invoke({"path": file_path})
        result = json.loads(result_str)
        assert "chained data" in result["content"]


# ---------------------------------------------------------------------------
# System prompt completeness validation
# ---------------------------------------------------------------------------


@pytest.mark.e2e
class TestSystemPromptCompleteness:
    """Verify the system prompt has all sections an LLM needs."""

    def test_has_identity_section(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "LLMOS Bridge" in prompt
        assert "modules" in prompt.lower()
        assert "actions" in prompt.lower()

    def test_has_protocol_section(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "IML Protocol v2" in prompt
        assert "plan_id" in prompt
        assert "protocol_version" in prompt
        assert "depends_on" in prompt

    def test_has_capabilities_section(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "Available Modules" in prompt
        assert "read_file" in prompt
        assert "write_file" in prompt

    def test_has_permission_section(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "Permission Model" in prompt
        assert "local_worker" in prompt

    def test_has_perception_section(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "Perception" in prompt
        assert "capture_before" in prompt
        assert "capture_after" in prompt
        assert "ocr_enabled" in prompt
        assert "_perception" in prompt

    def test_has_memory_section(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "Memory" in prompt
        assert "read_keys" in prompt
        assert "write_key" in prompt
        assert "{{memory." in prompt

    def test_has_guidelines_section(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "Guidelines" in prompt

    def test_has_examples_section(self, sdk_toolkit) -> None:
        prompt = sdk_toolkit.get_system_prompt()
        assert "Examples" in prompt
        assert "Read a file" in prompt


# ---------------------------------------------------------------------------
# SDK Approval flow tests (E2E)
# ---------------------------------------------------------------------------


@pytest.fixture
def approval_daemon_settings(tmp_path: Path) -> Settings:
    """Settings where os_exec.run_command requires approval."""
    return Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        security={
            "require_approval_for": ["os_exec.run_command"],
            "approval_timeout_seconds": 30,
        },
    )


@pytest.fixture
def approval_test_client(approval_daemon_settings: Settings) -> TestClient:
    app = create_app(settings=approval_daemon_settings)
    with TestClient(app, raise_server_exceptions=False) as c:
        yield c


@pytest.fixture
def approval_sdk_client(approval_test_client: TestClient):
    """LLMOSClient wired to daemon with approval requirements."""
    from langchain_llmos.client import LLMOSClient

    client = LLMOSClient.__new__(LLMOSClient)
    client._http = approval_test_client
    client._base_url = str(approval_test_client.base_url)
    client._api_token = None
    client._timeout = 30.0
    return client


@pytest.fixture
def approval_sdk_toolkit(approval_test_client: TestClient):
    """LLMOSToolkit wired to daemon with approval requirements."""
    from langchain_llmos.client import LLMOSClient
    from langchain_llmos.toolkit import LLMOSToolkit

    toolkit = LLMOSToolkit.__new__(LLMOSToolkit)
    toolkit._base_url = str(approval_test_client.base_url)
    toolkit._api_token = None
    toolkit._timeout = 30.0
    toolkit._manifests = None
    toolkit._system_prompt = None
    toolkit._async_client = None

    client = LLMOSClient.__new__(LLMOSClient)
    client._http = approval_test_client
    client._base_url = str(approval_test_client.base_url)
    client._api_token = None
    client._timeout = 30.0
    toolkit._client = client

    return toolkit


@pytest.mark.e2e
class TestSDKApprovalFlow:
    """Test the full approval chain via the SDK client methods."""

    def test_sdk_approve_action_completes_plan(
        self, approval_sdk_client, approval_test_client: TestClient
    ) -> None:
        """SDK approve_action() → action executes → plan completes."""
        plan = {
            "plan_id": "sdk-approval-001",
            "protocol_version": "2.0",
            "description": "Run echo with approval",
            "actions": [
                {
                    "id": "cmd",
                    "action": "run_command",
                    "module": "os_exec",
                    "params": {"command": ["echo", "approved-via-sdk"]},
                }
            ],
        }
        # Submit async (action will wait for approval).
        result = approval_sdk_client.submit_plan(plan, async_execution=True)
        plan_id = result["plan_id"]
        assert result["status"] in ("pending", "running")

        # Wait for AWAITING_APPROVAL.
        for _ in range(50):
            time.sleep(0.05)
            status = approval_sdk_client.get_plan(plan_id)
            actions = status.get("actions", [])
            if actions and actions[0]["status"] == "awaiting_approval":
                break
        else:
            pytest.fail("Action never reached awaiting_approval")

        # Use SDK to get pending approvals.
        pending = approval_sdk_client.get_pending_approvals(plan_id)
        assert len(pending) == 1
        assert pending[0]["action_id"] == "cmd"
        assert pending[0]["module"] == "os_exec"

        # Use SDK to approve.
        decision = approval_sdk_client.approve_action(
            plan_id, "cmd", decision="approve", approved_by="sdk_test"
        )
        assert decision["applied"] is True

        # Wait for completion.
        for _ in range(50):
            time.sleep(0.05)
            status = approval_sdk_client.get_plan(plan_id)
            if status["status"] in ("completed", "failed"):
                break

        assert status["status"] == "completed"
        assert status["actions"][0]["status"] == "completed"

    def test_sdk_reject_action_fails_plan(self, approval_sdk_client) -> None:
        """SDK reject → action fails → plan fails."""
        plan = {
            "plan_id": "sdk-reject-001",
            "protocol_version": "2.0",
            "description": "Run echo that will be rejected",
            "actions": [
                {
                    "id": "cmd",
                    "action": "run_command",
                    "module": "os_exec",
                    "params": {"command": ["echo", "reject-me"]},
                }
            ],
        }
        result = approval_sdk_client.submit_plan(plan, async_execution=True)
        plan_id = result["plan_id"]

        for _ in range(50):
            time.sleep(0.05)
            status = approval_sdk_client.get_plan(plan_id)
            actions = status.get("actions", [])
            if actions and actions[0]["status"] == "awaiting_approval":
                break

        approval_sdk_client.approve_action(
            plan_id, "cmd", decision="reject", reason="not safe"
        )

        for _ in range(50):
            time.sleep(0.05)
            status = approval_sdk_client.get_plan(plan_id)
            if status["status"] == "failed":
                break

        assert status["status"] == "failed"
        assert status["actions"][0]["status"] == "failed"

    def test_sdk_tool_invoke_with_auto_approve_thread(
        self, approval_sdk_toolkit, approval_test_client: TestClient
    ) -> None:
        """Tool invocation for an action requiring approval, auto-approved in background."""
        tools = approval_sdk_toolkit.get_tools()
        run_tool = next(
            (t for t in tools if t.name == "os_exec__run_command"), None
        )
        if run_tool is None:
            pytest.skip("os_exec__run_command not available")

        # Background thread that polls and approves.
        approved = threading.Event()

        def approver():
            for _ in range(100):
                time.sleep(0.05)
                resp = approval_test_client.get("/plans")
                if resp.status_code != 200:
                    continue
                plans = resp.json().get("plans", [])
                for p in plans:
                    plan_id = p["plan_id"]
                    pending_resp = approval_test_client.get(
                        f"/plans/{plan_id}/pending-approvals"
                    )
                    if pending_resp.status_code == 200:
                        pending = pending_resp.json()
                        for req in pending:
                            approval_test_client.post(
                                f"/plans/{plan_id}/actions/{req['action_id']}/approve",
                                json={"decision": "approve"},
                            )
                            approved.set()
                            return

        t = threading.Thread(target=approver, daemon=True)
        t.start()

        result_str = run_tool.invoke({"command": ["echo", "auto-approved"]})
        t.join(timeout=5)

        result = json.loads(result_str)
        # After approval, the action should have completed with output.
        assert "auto-approved" in result.get("stdout", result.get("content", ""))

    def test_sdk_get_pending_approvals_empty(self, approval_sdk_client) -> None:
        """get_pending_approvals returns [] when nothing is pending."""
        pending = approval_sdk_client.get_pending_approvals("nonexistent-plan")
        assert pending == []
