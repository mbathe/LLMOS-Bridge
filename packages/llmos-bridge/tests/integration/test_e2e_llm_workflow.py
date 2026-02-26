"""Integration tests — End-to-end LLM workflow simulation.

These tests simulate the full workflow as a real LLM would use LLMOS Bridge:
  1. Discover available modules via GET /modules
  2. Inspect action schemas via GET /modules/{id}/actions/{action}/schema
  3. Submit IML plans with realistic workflows
  4. Verify execution results including template chains, dependency resolution,
     cascade failure, and complete action response fields.

All tests use a real FastAPI TestClient with in-memory SQLite.
No external services or network calls required.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from llmos_bridge.api.server import create_app
from llmos_bridge.config import Settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """TestClient with filesystem + os_exec, unrestricted security profile."""
    settings = Settings(
        memory={
            "state_db_path": str(tmp_path / "state.db"),
            "vector_db_path": str(tmp_path / "vector"),
        },
        logging={"level": "warning", "format": "console", "audit_file": None},
        modules={"enabled": ["filesystem", "os_exec"]},
        security={"permission_profile": "unrestricted", "require_approval_for": []},
    )
    app = create_app(settings=settings)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


def _submit_sync(client: TestClient, plan: dict) -> dict:
    """Submit a plan synchronously and assert HTTP 202."""
    resp = client.post("/plans", json={"plan": plan, "async_execution": False})
    assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"
    return resp.json()


def _get_plan(client: TestClient, plan_id: str) -> dict:
    """GET /plans/{plan_id} and assert HTTP 200."""
    resp = client.get(f"/plans/{plan_id}")
    assert resp.status_code == 200
    return resp.json()


# ---------------------------------------------------------------------------
# Module discovery workflow (simulates first call by an LLM)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestLLMDiscovery:
    """Validate the module discovery flow a real LLM would run before building plans."""

    def test_list_modules_returns_filesystem_and_os_exec(
        self, client: TestClient
    ) -> None:
        resp = client.get("/modules")
        assert resp.status_code == 200
        modules = resp.json()
        assert isinstance(modules, list)
        ids = [m["module_id"] for m in modules]
        assert "filesystem" in ids
        assert "os_exec" in ids

    def test_module_list_has_required_fields(self, client: TestClient) -> None:
        resp = client.get("/modules")
        data = resp.json()
        for module in data:
            assert "module_id" in module
            assert "available" in module
            assert "version" in module

    def test_get_filesystem_manifest(self, client: TestClient) -> None:
        resp = client.get("/modules/filesystem")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module_id"] == "filesystem"
        assert "actions" in data
        assert len(data["actions"]) >= 1
        action_names = [a["name"] for a in data["actions"]]
        assert "read_file" in action_names
        assert "write_file" in action_names

    def test_get_action_schema_for_read_file(self, client: TestClient) -> None:
        resp = client.get("/modules/filesystem/actions/read_file/schema")
        assert resp.status_code == 200
        schema = resp.json()
        assert "properties" in schema
        assert "path" in schema["properties"]

    def test_get_action_schema_for_write_file(self, client: TestClient) -> None:
        resp = client.get("/modules/filesystem/actions/write_file/schema")
        assert resp.status_code == 200
        schema = resp.json()
        assert "properties" in schema
        assert "path" in schema["properties"]
        assert "content" in schema["properties"]

    def test_get_nonexistent_module_returns_404(self, client: TestClient) -> None:
        resp = client.get("/modules/does_not_exist")
        assert resp.status_code == 404

    def test_get_nonexistent_action_schema_returns_404(
        self, client: TestClient
    ) -> None:
        resp = client.get("/modules/filesystem/actions/nonexistent_action/schema")
        assert resp.status_code == 404

    def test_discover_then_build_and_submit_plan(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Full LLM flow: discover → inspect schema → build plan → submit → verify."""
        # Step 1: Discover modules
        modules_resp = client.get("/modules")
        assert "filesystem" in [m["module_id"] for m in modules_resp.json()]

        # Step 2: Inspect schema (LLM learns required params)
        schema_resp = client.get("/modules/filesystem/actions/write_file/schema")
        schema = schema_resp.json()
        assert "path" in schema["properties"]
        assert "content" in schema["properties"]

        # Step 3: Build and submit plan based on discovered schema
        output_path = tmp_path / "discovered.txt"
        plan = {
            "protocol_version": "2.0",
            "description": "Plan built from module discovery",
            "actions": [
                {
                    "id": "write",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(output_path),
                        "content": "discovered by LLM",
                    },
                }
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"
        assert output_path.read_text() == "discovered by LLM"


# ---------------------------------------------------------------------------
# Template chain — {{result.action_id.field}}
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestTemplateChain:
    """Validate that {{result.*}} templates are correctly resolved across actions."""

    def test_read_then_write_via_content_template(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Read a file, then write a second file using the content from the first."""
        source_path = tmp_path / "source.txt"
        source_path.write_text("template_value_42")
        dest_path = tmp_path / "dest.txt"

        plan = {
            "protocol_version": "2.0",
            "description": "Template chain: read → write with {{result.read.content}}",
            "actions": [
                {
                    "id": "read",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(source_path)},
                },
                {
                    "id": "write",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(dest_path),
                        "content": "{{result.read.content}}",
                    },
                    "depends_on": ["read"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"
        assert dest_path.read_text() == "template_value_42"

    def test_three_action_chain_a_to_b_to_c(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """A (read) → B (write with A's content) → C (write with A's content + suffix)."""
        a_path = tmp_path / "a.txt"
        a_path.write_text("chain_content")
        b_path = tmp_path / "b.txt"
        c_path = tmp_path / "c.txt"

        plan = {
            "protocol_version": "2.0",
            "description": "Three-step template chain",
            "actions": [
                {
                    "id": "a",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(a_path)},
                },
                {
                    "id": "b",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(b_path),
                        "content": "{{result.a.content}}",
                    },
                    "depends_on": ["a"],
                },
                {
                    "id": "c",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(c_path),
                        "content": "{{result.a.content}}_processed",
                    },
                    "depends_on": ["b"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"
        assert b_path.read_text() == "chain_content"
        assert c_path.read_text() == "chain_content_processed"

    def test_template_numeric_field_size_bytes(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Use size_bytes (integer) from read_file result inside a template string."""
        source_path = tmp_path / "data.txt"
        source_path.write_text("hello")  # 5 bytes in UTF-8
        info_path = tmp_path / "info.txt"

        plan = {
            "protocol_version": "2.0",
            "description": "Use size_bytes numeric field in template",
            "actions": [
                {
                    "id": "read",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(source_path)},
                },
                {
                    "id": "record",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(info_path),
                        "content": "size={{result.read.size_bytes}}",
                    },
                    "depends_on": ["read"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"
        assert info_path.read_text() == "size=5"

    def test_template_path_field_from_write_result(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """write_file returns {path, bytes_written} — use path field in template."""
        out_path = tmp_path / "out.txt"
        report_path = tmp_path / "report.txt"

        plan = {
            "protocol_version": "2.0",
            "description": "Use path field from write_file result",
            "actions": [
                {
                    "id": "write",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(out_path),
                        "content": "initial",
                    },
                },
                {
                    "id": "report",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(report_path),
                        "content": "wrote_to={{result.write.path}}",
                    },
                    "depends_on": ["write"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"
        assert str(out_path) in report_path.read_text()


# ---------------------------------------------------------------------------
# Dependency chains — depends_on
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestDependencyChain:
    """Verify sequential and parallel execution with depends_on."""

    def test_sequential_write_append_read(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Actions run in order: write → append → read."""
        path = tmp_path / "seq.txt"

        plan = {
            "protocol_version": "2.0",
            "description": "Sequential write → append → read",
            "actions": [
                {
                    "id": "write",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {"path": str(path), "content": "line1"},
                },
                {
                    "id": "append",
                    "action": "append_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(path),
                        "content": "line2",
                        "newline": True,
                    },
                    "depends_on": ["write"],
                },
                {
                    "id": "read",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(path)},
                    "depends_on": ["append"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"

        plan_state = _get_plan(client, result["plan_id"])
        statuses = {a["action_id"]: a["status"] for a in plan_state["actions"]}
        assert statuses["write"] == "completed"
        assert statuses["append"] == "completed"
        assert statuses["read"] == "completed"

        content = path.read_text()
        assert "line1" in content
        assert "line2" in content

    def test_parallel_independent_actions(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Three independent actions run concurrently (same DAG wave)."""
        data_dir = tmp_path / "data"
        data_dir.mkdir()
        for i in range(3):
            (data_dir / f"file{i}.txt").write_text(f"content{i}")

        plan = {
            "protocol_version": "2.0",
            "description": "Three parallel reads — no dependencies",
            "actions": [
                {
                    "id": f"read{i}",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(data_dir / f"file{i}.txt")},
                }
                for i in range(3)
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"

        plan_state = _get_plan(client, result["plan_id"])
        for i in range(3):
            action = next(
                a for a in plan_state["actions"] if a["action_id"] == f"read{i}"
            )
            assert action["status"] == "completed"
            assert action["result"]["content"] == f"content{i}"

    def test_diamond_dependency_fan_out_fan_in(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Diamond pattern: a → b, a → c (parallel), b + c → d."""
        a_path = tmp_path / "a.txt"
        a_path.write_text("source")
        b_path = tmp_path / "b.txt"
        c_path = tmp_path / "c.txt"
        d_path = tmp_path / "d.txt"

        plan = {
            "protocol_version": "2.0",
            "description": "Diamond dependency: fan-out then fan-in",
            "actions": [
                {
                    "id": "a",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(a_path)},
                },
                {
                    "id": "b",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {"path": str(b_path), "content": "branch_b"},
                    "depends_on": ["a"],
                },
                {
                    "id": "c",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {"path": str(c_path), "content": "branch_c"},
                    "depends_on": ["a"],
                },
                {
                    "id": "d",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {"path": str(d_path), "content": "merged"},
                    "depends_on": ["b", "c"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"
        assert b_path.read_text() == "branch_b"
        assert c_path.read_text() == "branch_c"
        assert d_path.read_text() == "merged"

    def test_multi_file_create_then_verify(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Create N files in parallel, then verify each in a second wave."""
        data_dir = tmp_path / "output"
        data_dir.mkdir()
        n = 4

        actions = [
            {
                "id": f"write{i}",
                "action": "write_file",
                "module": "filesystem",
                "params": {
                    "path": str(data_dir / f"file{i}.txt"),
                    "content": f"data{i}",
                },
            }
            for i in range(n)
        ]
        actions += [
            {
                "id": f"verify{i}",
                "action": "get_file_info",
                "module": "filesystem",
                "params": {"path": str(data_dir / f"file{i}.txt")},
                "depends_on": [f"write{i}"],
            }
            for i in range(n)
        ]

        plan = {
            "protocol_version": "2.0",
            "description": "Create N files then verify each",
            "actions": actions,
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"

        plan_state = _get_plan(client, result["plan_id"])
        statuses = {a["action_id"]: a["status"] for a in plan_state["actions"]}
        for i in range(n):
            assert statuses[f"write{i}"] == "completed"
            assert statuses[f"verify{i}"] == "completed"


# ---------------------------------------------------------------------------
# Cascade failure — on_error=abort cascades SKIPPED to descendants
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestCascadeFailure:
    """Verify that on_error=abort cascades SKIPPED status to all descendants."""

    def test_abort_cascades_to_direct_dependents(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Failing action with on_error=abort: dependents must be SKIPPED."""
        plan = {
            "protocol_version": "2.0",
            "description": "Direct cascade: fail → dep1 (SKIPPED)",
            "actions": [
                {
                    "id": "fail",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "does_not_exist.txt")},
                    "on_error": "abort",
                },
                {
                    "id": "dep1",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(tmp_path / "dep1.txt"),
                        "content": "should not be written",
                    },
                    "depends_on": ["fail"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "failed"

        plan_state = _get_plan(client, result["plan_id"])
        statuses = {a["action_id"]: a["status"] for a in plan_state["actions"]}
        assert statuses["fail"] == "failed"
        assert statuses["dep1"] == "skipped"
        assert not (tmp_path / "dep1.txt").exists()

    def test_abort_cascades_transitively(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Transitive cascade: fail → dep1 (SKIPPED) → dep2 (SKIPPED)."""
        plan = {
            "protocol_version": "2.0",
            "description": "Transitive cascade failure",
            "actions": [
                {
                    "id": "fail",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "nope.txt")},
                    "on_error": "abort",
                },
                {
                    "id": "dep1",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(tmp_path / "dep1.txt"),
                        "content": "x",
                    },
                    "depends_on": ["fail"],
                },
                {
                    "id": "dep2",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(tmp_path / "dep2.txt"),
                        "content": "y",
                    },
                    "depends_on": ["dep1"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "failed"

        plan_state = _get_plan(client, result["plan_id"])
        statuses = {a["action_id"]: a["status"] for a in plan_state["actions"]}
        assert statuses["fail"] == "failed"
        assert statuses["dep1"] == "skipped"
        assert statuses["dep2"] == "skipped"
        assert not (tmp_path / "dep1.txt").exists()
        assert not (tmp_path / "dep2.txt").exists()

    def test_abort_does_not_affect_independent_actions(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Cascade only affects descendants, not unrelated parallel actions."""
        independent_path = tmp_path / "independent.txt"

        plan = {
            "protocol_version": "2.0",
            "execution_mode": "parallel",  # parallel mode: fail and independent are in the same wave
            "description": "Cascade does not spread to unrelated actions",
            "actions": [
                {
                    "id": "fail",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "nope.txt")},
                    "on_error": "abort",
                },
                {
                    "id": "dep",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(tmp_path / "dep.txt"),
                        "content": "x",
                    },
                    "depends_on": ["fail"],
                },
                {
                    "id": "independent",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(independent_path),
                        "content": "I ran independently",
                    },
                    # No depends_on — runs in the same wave as "fail"
                },
            ],
        }

        result = _submit_sync(client, plan)
        plan_state = _get_plan(client, result["plan_id"])
        statuses = {a["action_id"]: a["status"] for a in plan_state["actions"]}

        assert statuses["fail"] == "failed"
        assert statuses["dep"] == "skipped"
        # Independent action ran to completion in its own wave
        assert statuses["independent"] == "completed"
        assert independent_path.read_text() == "I ran independently"

    def test_continue_on_error_does_not_cascade(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """on_error=continue: dependents still execute despite the failure."""
        dep_path = tmp_path / "dep.txt"

        plan = {
            "protocol_version": "2.0",
            "description": "Continue on error — dependents still run",
            "actions": [
                {
                    "id": "fail",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "nope.txt")},
                    "on_error": "continue",
                },
                {
                    "id": "dep",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(dep_path),
                        "content": "ran_despite_failure",
                    },
                    "depends_on": ["fail"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        plan_id = result["plan_id"]
        plan_state = _get_plan(client, plan_id)
        statuses = {a["action_id"]: a["status"] for a in plan_state["actions"]}

        assert statuses["fail"] == "failed"
        # With on_error=continue, the cascade skip does NOT trigger
        # dep depends on fail (ordering only), has no template from fail's result
        assert statuses["dep"] == "completed"
        assert dep_path.read_text() == "ran_despite_failure"


# ---------------------------------------------------------------------------
# ActionResponse fields — module and action populated (Sprint 1.7 fix)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestActionResponseFields:
    """Verify that ActionResponse.module and ActionResponse.action are populated.

    These fields were empty strings before the Sprint 1.7 fix.
    A LLM polling GET /plans/{id} needs these fields to understand what ran.
    """

    def test_completed_action_has_module_and_action_fields(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        path = tmp_path / "test.txt"
        path.write_text("hello")

        plan = {
            "protocol_version": "2.0",
            "description": "Verify ActionResponse fields on success",
            "actions": [
                {
                    "id": "read",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(path)},
                }
            ],
        }

        result = _submit_sync(client, plan)
        plan_state = _get_plan(client, result["plan_id"])

        assert len(plan_state["actions"]) == 1
        action = plan_state["actions"][0]
        assert action["module"] == "filesystem", "module field must not be empty"
        assert action["action"] == "read_file", "action field must not be empty"

    def test_multi_action_response_fields_per_action(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        path = tmp_path / "m.txt"
        path.write_text("data")

        plan = {
            "protocol_version": "2.0",
            "description": "Multiple actions — each has correct module/action",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(path)},
                },
                {
                    "id": "a2",
                    "action": "get_file_info",
                    "module": "filesystem",
                    "params": {"path": str(path)},
                },
            ],
        }

        result = _submit_sync(client, plan)
        plan_state = _get_plan(client, result["plan_id"])
        by_id = {a["action_id"]: a for a in plan_state["actions"]}

        assert by_id["a1"]["module"] == "filesystem"
        assert by_id["a1"]["action"] == "read_file"
        assert by_id["a2"]["module"] == "filesystem"
        assert by_id["a2"]["action"] == "get_file_info"

    def test_failed_action_still_has_module_and_action(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """module/action fields must be populated even when the action fails."""
        plan = {
            "protocol_version": "2.0",
            "description": "Failed action — fields still populated",
            "actions": [
                {
                    "id": "fail",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": "/totally/nonexistent/file.txt"},
                }
            ],
        }

        result = _submit_sync(client, plan)
        plan_state = _get_plan(client, result["plan_id"])
        action = plan_state["actions"][0]

        assert action["status"] == "failed"
        assert action["module"] == "filesystem"
        assert action["action"] == "read_file"

    def test_skipped_action_has_module_and_action(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """SKIPPED actions (cascade failure) also have module/action populated."""
        plan = {
            "protocol_version": "2.0",
            "description": "Skipped action — fields still populated",
            "actions": [
                {
                    "id": "fail",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "nope.txt")},
                    "on_error": "abort",
                },
                {
                    "id": "skipped",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(tmp_path / "skip.txt"),
                        "content": "x",
                    },
                    "depends_on": ["fail"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        plan_state = _get_plan(client, result["plan_id"])
        by_id = {a["action_id"]: a for a in plan_state["actions"]}

        assert by_id["skipped"]["status"] == "skipped"
        assert by_id["skipped"]["module"] == "filesystem"
        assert by_id["skipped"]["action"] == "write_file"


# ---------------------------------------------------------------------------
# Node routing — target_node field (Sprint 1.7 distributed foundations)
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestNodeRouting:
    """Verify target_node=None (default) routes to local node (standalone guarantee)."""

    def test_default_target_node_routes_locally(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Plans without target_node run on the local node — backward compatible."""
        path = tmp_path / "node_test.txt"
        path.write_text("local_content")

        plan = {
            "protocol_version": "2.0",
            "description": "No target_node → local execution",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(path)},
                    # No target_node field — defaults to None → local
                }
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"

        plan_state = _get_plan(client, result["plan_id"])
        assert plan_state["actions"][0]["result"]["content"] == "local_content"

    def test_explicit_null_target_node_routes_locally(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Explicit target_node=null also routes to local node."""
        path = tmp_path / "null_node.txt"
        path.write_text("null_target_content")

        plan = {
            "protocol_version": "2.0",
            "description": "Explicit null target_node",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(path)},
                    "target_node": None,
                }
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"

        plan_state = _get_plan(client, result["plan_id"])
        assert plan_state["actions"][0]["result"]["content"] == "null_target_content"


# ---------------------------------------------------------------------------
# Full multi-step LLM workflow scenarios
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestFullLLMWorkflow:
    """Simulate realistic LLM-driven automation scenarios end-to-end."""

    def test_read_process_write_report(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """LLM reads source data, builds a report using templates, writes it out."""
        input_path = tmp_path / "input.txt"
        input_path.write_text("Q1: 1000000\nQ2: 1200000")
        output_path = tmp_path / "report.txt"

        plan = {
            "protocol_version": "2.0",
            "description": "Read source → build report → write report → verify",
            "actions": [
                {
                    "id": "read_input",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(input_path)},
                },
                {
                    "id": "write_report",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(output_path),
                        "content": "=== REPORT ===\n{{result.read_input.content}}",
                    },
                    "depends_on": ["read_input"],
                },
                {
                    "id": "verify",
                    "action": "get_file_info",
                    "module": "filesystem",
                    "params": {"path": str(output_path)},
                    "depends_on": ["write_report"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"

        plan_state = _get_plan(client, result["plan_id"])
        statuses = {a["action_id"]: a["status"] for a in plan_state["actions"]}
        assert all(s == "completed" for s in statuses.values())

        report_content = output_path.read_text()
        assert "=== REPORT ===" in report_content
        assert "Q1: 1000000" in report_content
        assert "Q2: 1200000" in report_content

    def test_create_directory_structure_and_files(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """LLM creates a directory hierarchy and multiple files in parallel."""
        base = tmp_path / "project"

        plan = {
            "protocol_version": "2.0",
            "description": "Create project directory structure",
            "actions": [
                {
                    "id": "mkdir_src",
                    "action": "create_directory",
                    "module": "filesystem",
                    "params": {"path": str(base / "src"), "parents": True},
                },
                {
                    "id": "mkdir_tests",
                    "action": "create_directory",
                    "module": "filesystem",
                    "params": {"path": str(base / "tests"), "parents": True},
                },
                {
                    "id": "write_readme",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(base / "README.md"),
                        "content": "# My Project",
                        "create_dirs": True,
                    },
                },
                {
                    "id": "write_main",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(base / "src" / "main.py"),
                        "content": "# main module",
                    },
                    "depends_on": ["mkdir_src"],
                },
                {
                    "id": "write_test",
                    "action": "write_file",
                    "module": "filesystem",
                    "params": {
                        "path": str(base / "tests" / "test_main.py"),
                        "content": "# tests",
                    },
                    "depends_on": ["mkdir_tests"],
                },
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "completed"

        assert (base / "README.md").read_text() == "# My Project"
        assert (base / "src" / "main.py").read_text() == "# main module"
        assert (base / "tests" / "test_main.py").read_text() == "# tests"

    def test_plan_id_stable_across_submission_and_get(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """plan_id from POST /plans matches the id in GET /plans/{id}."""
        (tmp_path / "f.txt").write_text("x")

        plan = {
            "protocol_version": "2.0",
            "plan_id": "stable-id-e2e-001",
            "description": "ID stability check",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "f.txt")},
                }
            ],
        }

        submit_result = _submit_sync(client, plan)
        plan_id = submit_result["plan_id"]
        assert plan_id == "stable-id-e2e-001"

        get_result = _get_plan(client, plan_id)
        assert get_result["plan_id"] == "stable-id-e2e-001"

    def test_list_plans_shows_submitted_plan(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """GET /plans list includes the plan after submission."""
        (tmp_path / "f.txt").write_text("x")

        plan = {
            "protocol_version": "2.0",
            "plan_id": "list-test-e2e-001",
            "description": "Should appear in list",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "f.txt")},
                }
            ],
        }

        _submit_sync(client, plan)

        resp = client.get("/plans")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_async_submit_then_get_status(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Submit async, then GET /plans/{id} returns valid status."""
        (tmp_path / "async_input.txt").write_text("async_content")

        plan = {
            "protocol_version": "2.0",
            "description": "Async submit then poll",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "async_input.txt")},
                }
            ],
        }

        resp = client.post("/plans", json={"plan": plan, "async_execution": True})
        assert resp.status_code == 202
        plan_id = resp.json()["plan_id"]

        plan_state = _get_plan(client, plan_id)
        assert plan_state["status"] in ("completed", "failed", "pending", "running")
        assert "actions" in plan_state


# ---------------------------------------------------------------------------
# Error handling — malformed plans and invalid operations
# ---------------------------------------------------------------------------


@pytest.mark.integration
class TestErrorHandling:
    """Verify robust error handling for malformed plans and invalid operations."""

    def test_completely_malformed_plan_returns_error(
        self, client: TestClient
    ) -> None:
        """A completely invalid plan body must be rejected."""
        resp = client.post(
            "/plans",
            json={
                "plan": {"this_is": "completely_invalid"},
                "async_execution": True,
            },
        )
        assert resp.status_code in (400, 422)

    def test_circular_dependency_rejected(self, client: TestClient) -> None:
        """Circular dependency must be detected — results in an error response."""
        plan = {
            "protocol_version": "2.0",
            "description": "Circular dependency",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": "/tmp/x.txt"},
                    "depends_on": ["a2"],
                },
                {
                    "id": "a2",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": "/tmp/y.txt"},
                    "depends_on": ["a1"],
                },
            ],
        }
        resp = client.post(
            "/plans", json={"plan": plan, "async_execution": True}
        )
        # DAGCycleError may be caught as 400/422 (validator) or 500 (executor)
        assert resp.status_code in (400, 422, 500)

    def test_empty_actions_list_rejected(self, client: TestClient) -> None:
        plan = {
            "protocol_version": "2.0",
            "description": "No actions",
            "actions": [],
        }
        resp = client.post(
            "/plans", json={"plan": plan, "async_execution": True}
        )
        assert resp.status_code in (400, 422)

    def test_plan_with_unknown_module_fails(
        self, client: TestClient
    ) -> None:
        plan = {
            "protocol_version": "2.0",
            "description": "Unknown module",
            "actions": [
                {
                    "id": "a1",
                    "action": "do_something",
                    "module": "does_not_exist",
                    "params": {},
                }
            ],
        }
        resp = client.post(
            "/plans", json={"plan": plan, "async_execution": False}
        )
        # Either rejected at validation (400/422) or fails at execution (202 + failed)
        if resp.status_code == 202:
            plan_state = _get_plan(client, resp.json()["plan_id"])
            assert plan_state["status"] == "failed"
        else:
            assert resp.status_code in (400, 422)

    def test_get_nonexistent_plan_returns_404(
        self, client: TestClient
    ) -> None:
        resp = client.get("/plans/plan-that-does-not-exist-xyz")
        assert resp.status_code == 404

    def test_file_not_found_causes_action_failure(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        """Reading a non-existent file produces a failed action with an error message."""
        plan = {
            "protocol_version": "2.0",
            "description": "File not found → action fails",
            "actions": [
                {
                    "id": "a1",
                    "action": "read_file",
                    "module": "filesystem",
                    "params": {"path": str(tmp_path / "nonexistent.txt")},
                }
            ],
        }

        result = _submit_sync(client, plan)
        assert result["status"] == "failed"

        plan_state = _get_plan(client, result["plan_id"])
        action = plan_state["actions"][0]
        assert action["status"] == "failed"
        assert action["error"] is not None
        assert len(action["error"]) > 0
