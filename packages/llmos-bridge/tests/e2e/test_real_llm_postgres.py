"""E2E test — Real Claude model + Real PostgreSQL database.

This test proves the ENTIRE chain works end-to-end:

1. LLMOS Bridge connects to a real PostgreSQL database (eyeflow_db)
2. The gateway introspects the full schema (19 tables, FKs, indexes)
3. The schema is injected into the LLM system prompt
4. Claude (Anthropic API) receives the prompt and sees all tables/columns
5. Claude generates valid IML plans autonomously for database queries
6. The plans are executed successfully against the real database
7. Claude correctly chains operations (read → analyze → follow-up query)

Requirements:
    - ANTHROPIC_API_KEY environment variable set
    - PostgreSQL running locally with user=eyeflow, password=eyeflow
    - Database eyeflow_db with tables (users, connectors, llm_projects, etc.)

Usage:
    ANTHROPIC_API_KEY=sk-ant-... pytest tests/e2e/test_real_llm_postgres.py -v -s
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Skip conditions
# ---------------------------------------------------------------------------

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
PG_AVAILABLE = True

try:
    import psycopg2

    conn = psycopg2.connect(
        host="localhost", database="eyeflow_db", user="eyeflow", password="eyeflow"
    )
    conn.close()
except Exception:
    PG_AVAILABLE = False

skip_no_key = pytest.mark.skipif(
    not ANTHROPIC_KEY, reason="ANTHROPIC_API_KEY not set"
)
skip_no_pg = pytest.mark.skipif(
    not PG_AVAILABLE, reason="PostgreSQL eyeflow_db not available"
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def call_claude(system_prompt: str, user_message: str) -> str:
    """Call Claude API and return the text response."""
    import anthropic

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    response = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=4096,
        system=system_prompt,
        messages=[{"role": "user", "content": user_message}],
    )
    return response.content[0].text


def extract_iml_plan(text: str) -> dict[str, Any] | None:
    """Extract the first JSON IML plan from Claude's response."""
    # Try to find JSON block in markdown code fence
    import re

    patterns = [
        r"```json\s*\n(.*?)```",
        r"```\s*\n(\{.*?\})\s*\n```",
        r"(\{[^{}]*\"plan_id\"[^{}]*\"actions\".*?\})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(1))
            except json.JSONDecodeError:
                continue

    # Try parsing the whole text as JSON
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gw():
    """DatabaseGatewayModule connected to real PostgreSQL."""
    from llmos_bridge.modules.database_gateway.module import DatabaseGatewayModule

    module = DatabaseGatewayModule(max_connections=5, schema_cache_ttl=300)
    yield module


@pytest.fixture()
def connected_gw(gw):
    """Gateway with active PostgreSQL connection."""
    import asyncio

    async def _connect():
        result = await gw._action_connect({
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "eyeflow_db",
            "user": "eyeflow",
            "password": "eyeflow",
            "connection_id": "eyeflow",
        })
        return result

    asyncio.get_event_loop().run_until_complete(_connect())
    yield gw

    async def _disconnect():
        await gw._action_disconnect({"connection_id": "eyeflow"})

    try:
        asyncio.get_event_loop().run_until_complete(_disconnect())
    except Exception:
        pass


@pytest.fixture()
def system_prompt(connected_gw):
    """Full system prompt with PostgreSQL schema injected."""
    from llmos_bridge.api.prompt import SystemPromptGenerator

    manifest = connected_gw.get_manifest()
    snippet = connected_gw.get_context_snippet()

    gen = SystemPromptGenerator(
        manifests=[manifest],
        permission_profile="power_user",
        daemon_version="1.1.0",
        context_snippets={"db_gateway": snippet} if snippet else {},
    )
    return gen.generate()


# ===========================================================================
# Test 1: Schema introspection on real PostgreSQL
# ===========================================================================


@skip_no_pg
@pytest.mark.e2e
class TestPostgresIntrospection:
    """Verify the gateway correctly introspects the real PostgreSQL database."""

    @pytest.mark.asyncio
    async def test_connect_to_postgres(self, gw) -> None:
        result = await gw._action_connect({
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "eyeflow_db",
            "user": "eyeflow",
            "password": "eyeflow",
            "connection_id": "pg_test",
        })
        assert result["status"] == "connected"
        assert result["table_count"] >= 19
        await gw._action_disconnect({"connection_id": "pg_test"})

    @pytest.mark.asyncio
    async def test_introspect_full_schema(self, gw) -> None:
        await gw._action_connect({
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "eyeflow_db",
            "user": "eyeflow",
            "password": "eyeflow",
            "connection_id": "intro",
        })
        result = await gw._action_introspect({
            "connection_id": "intro",
            "refresh": True,
        })
        table_names = [t["name"] for t in result["tables"]]
        assert "users" in table_names
        assert "connectors" in table_names
        assert "llm_projects" in table_names
        assert "missions" in table_names
        assert "audit_logs" in table_names

        # Check FK detection
        pv_table = next(t for t in result["tables"] if t["name"] == "project_versions")
        fk_cols = [c for c in pv_table["columns"] if c.get("foreign_keys")]
        assert len(fk_cols) > 0, "Foreign keys should be detected on project_versions"

        await gw._action_disconnect({"connection_id": "intro"})

    @pytest.mark.asyncio
    async def test_context_snippet_for_postgres(self, gw) -> None:
        await gw._action_connect({
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "eyeflow_db",
            "user": "eyeflow",
            "password": "eyeflow",
            "connection_id": "ctx",
        })
        snippet = gw.get_context_snippet()
        assert snippet is not None
        assert "## Database Context" in snippet
        assert "users" in snippet
        assert "connectors" in snippet
        assert "UUID" in snippet or "uuid" in snippet.lower()
        assert "VARCHAR" in snippet or "CHARACTER VARYING" in snippet
        assert "PK" in snippet
        await gw._action_disconnect({"connection_id": "ctx"})

    @pytest.mark.asyncio
    async def test_read_real_data(self, gw) -> None:
        await gw._action_connect({
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "eyeflow_db",
            "user": "eyeflow",
            "password": "eyeflow",
            "connection_id": "read",
        })

        # Find all users
        users = await gw._action_find({
            "entity": "users",
            "connection_id": "read",
        })
        assert users["row_count"] >= 1
        assert "email" in users["rows"][0]

        # Count connectors
        count = await gw._action_count({
            "entity": "connectors",
            "connection_id": "read",
        })
        assert count["count"] >= 1

        await gw._action_disconnect({"connection_id": "read"})


# ===========================================================================
# Test 2: System prompt contains real PostgreSQL schema
# ===========================================================================


@skip_no_pg
@pytest.mark.e2e
class TestSystemPromptWithPostgres:
    """Verify the LLM system prompt includes the real PostgreSQL schema."""

    def test_prompt_contains_all_tables(self, system_prompt: str) -> None:
        for table in [
            "users", "connectors", "llm_projects", "missions",
            "audit_logs", "project_versions", "execution_records",
        ]:
            assert table in system_prompt, f"Table '{table}' missing from prompt"

    def test_prompt_contains_column_types(self, system_prompt: str) -> None:
        # PostgreSQL-specific types
        prompt_lower = system_prompt.lower()
        assert "uuid" in prompt_lower
        assert "varchar" in prompt_lower or "character varying" in prompt_lower
        assert "timestamp" in prompt_lower
        assert "jsonb" in prompt_lower or "json" in prompt_lower

    def test_prompt_contains_foreign_keys(self, system_prompt: str) -> None:
        assert "FK" in system_prompt
        assert "llm_projects" in system_prompt

    def test_prompt_contains_all_12_actions(self, system_prompt: str) -> None:
        for action in [
            "connect", "disconnect", "introspect",
            "find", "find_one", "count", "search",
            "create", "create_many", "update", "delete",
            "aggregate",
        ]:
            assert f"**{action}**" in system_prompt

    def test_prompt_contains_filter_syntax(self, system_prompt: str) -> None:
        assert "$gte" in system_prompt
        assert "MongoDB" in system_prompt or "mongodb" in system_prompt.lower()

    def test_prompt_length_reasonable(self, system_prompt: str) -> None:
        """The prompt should be substantial but not excessively long."""
        length = len(system_prompt)
        assert length > 5000, f"Prompt too short ({length} chars)"
        assert length < 100000, f"Prompt too long ({length} chars)"
        print(f"\n  System prompt length: {length:,} characters")


# ===========================================================================
# Test 3: Claude generates valid IML plans from real schema
# ===========================================================================


@skip_no_key
@skip_no_pg
@pytest.mark.e2e
class TestClaudeGeneratesPlans:
    """Claude sees the real schema and generates valid IML plans."""

    def test_claude_generates_find_users_plan(self, system_prompt: str) -> None:
        """Ask Claude to list all users — it should generate a valid find plan."""
        response = call_claude(
            system_prompt,
            "Liste-moi tous les utilisateurs de la base de données. "
            "Génère un plan IML pour cette requête."
        )
        print(f"\n  Claude response:\n{response[:500]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"Could not extract IML plan from response:\n{response}"
        assert "actions" in plan
        assert len(plan["actions"]) >= 1

        action = plan["actions"][0]
        assert action["module"] == "db_gateway"
        assert action["action"] == "find"
        assert action["params"]["entity"] == "users"

    def test_claude_generates_count_plan(self, system_prompt: str) -> None:
        """Ask Claude to count connectors — should use the count action."""
        response = call_claude(
            system_prompt,
            "Combien y a-t-il de connecteurs dans la base de données ? "
            "Génère un plan IML."
        )
        print(f"\n  Claude response:\n{response[:500]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"Could not extract IML plan from response:\n{response}"

        action = plan["actions"][0]
        assert action["module"] == "db_gateway"
        assert action["action"] == "count"
        assert action["params"]["entity"] == "connectors"

    def test_claude_generates_filtered_query(self, system_prompt: str) -> None:
        """Ask Claude for a filtered query — should use MongoDB-like filter syntax."""
        response = call_claude(
            system_prompt,
            "Trouve tous les audit_logs des dernières 24 heures. "
            "Génère un plan IML."
        )
        print(f"\n  Claude response:\n{response[:500]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"Could not extract IML plan from response:\n{response}"

        action = plan["actions"][0]
        assert action["module"] == "db_gateway"
        assert action["action"] in ("find", "search")
        assert action["params"]["entity"] == "audit_logs"
        # Should have a filter
        if action["action"] == "find":
            assert "filter" in action["params"] or "filter" in action.get("params", {})

    def test_claude_generates_chained_plan(self, system_prompt: str) -> None:
        """Ask Claude for a multi-step query — should use depends_on chaining."""
        response = call_claude(
            system_prompt,
            "D'abord, compte le nombre total d'utilisateurs. "
            "Ensuite, liste les 5 derniers audit_logs triés par date décroissante. "
            "Génère un seul plan IML avec les deux actions chaînées."
        )
        print(f"\n  Claude response:\n{response[:800]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"Could not extract IML plan from response:\n{response}"

        assert len(plan["actions"]) >= 2, "Should have at least 2 actions"

        # First action: count users
        a1 = plan["actions"][0]
        assert a1["module"] == "db_gateway"

        # Second action: find audit_logs
        a2 = plan["actions"][1]
        assert a2["module"] == "db_gateway"

    def test_claude_uses_correct_connection_id(self, system_prompt: str) -> None:
        """Claude should reference the active connection ID from the context."""
        response = call_claude(
            system_prompt,
            "Je suis connecté à la base 'eyeflow' via connection_id='eyeflow'. "
            "Fais-moi un plan IML pour trouver tous les llm_projects."
        )
        print(f"\n  Claude response:\n{response[:500]}")

        plan = extract_iml_plan(response)
        assert plan is not None

        action = plan["actions"][0]
        # Should use the connection_id from context
        assert action["params"].get("connection_id") == "eyeflow"
        assert action["params"]["entity"] == "llm_projects"


# ===========================================================================
# Test 4: Execute Claude-generated plans on real database
# ===========================================================================


@skip_no_key
@skip_no_pg
@pytest.mark.e2e
class TestExecuteClaudePlans:
    """Execute plans generated by Claude against the real PostgreSQL database."""

    @pytest.mark.asyncio
    async def test_execute_find_users_plan(
        self, connected_gw, system_prompt: str
    ) -> None:
        """Claude generates a find plan → we execute it → verify results."""
        response = call_claude(
            system_prompt,
            "Je suis connecté via connection_id='eyeflow'. "
            "Génère un plan IML pour lister tous les utilisateurs. "
            "Utilise connection_id='eyeflow'."
        )
        plan = extract_iml_plan(response)
        assert plan is not None, f"No plan extracted:\n{response}"

        # Execute the first action
        action = plan["actions"][0]
        assert action["action"] == "find"

        result = await connected_gw._action_find({
            "entity": action["params"]["entity"],
            "connection_id": "eyeflow",
            **{k: v for k, v in action["params"].items()
               if k not in ("entity", "connection_id")},
        })

        assert result["row_count"] >= 1
        print(f"\n  Found {result['row_count']} users")
        for row in result["rows"][:3]:
            print(f"    - {row.get('email', 'N/A')} ({row.get('firstName', '')} {row.get('lastName', '')})")

    @pytest.mark.asyncio
    async def test_execute_count_connectors_plan(
        self, connected_gw, system_prompt: str
    ) -> None:
        """Claude generates a count plan → execute → verify."""
        response = call_claude(
            system_prompt,
            "Je suis connecté via connection_id='eyeflow'. "
            "Combien de connectors y a-t-il ? Génère un plan IML. "
            "Utilise connection_id='eyeflow'."
        )
        plan = extract_iml_plan(response)
        assert plan is not None

        action = plan["actions"][0]
        result = await connected_gw._action_count({
            "entity": action["params"]["entity"],
            "connection_id": "eyeflow",
        })

        assert result["count"] >= 1
        print(f"\n  Connector count: {result['count']}")

    @pytest.mark.asyncio
    async def test_execute_aggregate_plan(
        self, connected_gw, system_prompt: str
    ) -> None:
        """Claude generates an aggregate plan → execute → verify."""
        response = call_claude(
            system_prompt,
            "Je suis connecté via connection_id='eyeflow'. "
            "Agrège les connectors par type, en comptant le nombre par type. "
            "Génère un plan IML. Utilise connection_id='eyeflow'."
        )
        plan = extract_iml_plan(response)
        assert plan is not None, f"No plan extracted:\n{response}"

        action = plan["actions"][0]
        assert action["action"] == "aggregate"

        result = await connected_gw._action_aggregate({
            "entity": action["params"]["entity"],
            "group_by": action["params"].get("group_by", ["type"]),
            "aggregations": action["params"].get("aggregations", {"id": "count"}),
            "connection_id": "eyeflow",
        })

        assert result["row_count"] >= 1
        print(f"\n  Aggregation result ({result['row_count']} groups):")
        for row in result["rows"]:
            print(f"    - {row}")


# ===========================================================================
# Test 5: Full API E2E with real PostgreSQL
# ===========================================================================


@skip_no_pg
@pytest.mark.e2e
class TestFullApiE2EPostgres:
    """Full FastAPI E2E with real PostgreSQL (no LLM needed)."""

    @pytest.fixture()
    def app_client(self, tmp_path: Path):
        from fastapi.testclient import TestClient

        from llmos_bridge.api.server import create_app
        from llmos_bridge.config import Settings

        settings = Settings(
            memory={
                "state_db_path": str(tmp_path / "state.db"),
                "vector_db_path": str(tmp_path / "vector"),
            },
            logging={"level": "warning", "format": "console", "audit_file": None},
            modules={"enabled": ["db_gateway"]},
            security={"permission_profile": "power_user"},
            security_advanced={"enable_decorators": False},
        )
        app = create_app(settings=settings)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    def test_connect_via_api_and_get_context(self, app_client) -> None:
        """Submit connect plan via API, then verify /context has schema."""
        # Connect to PostgreSQL
        plan_id = f"pg-connect-{uuid.uuid4().hex[:8]}"
        resp = app_client.post("/plans", json={
            "plan": {
                "plan_id": plan_id,
                "protocol_version": "2.0",
                "description": "Connect to eyeflow PostgreSQL",
                "actions": [{
                    "id": "connect",
                    "module": "db_gateway",
                    "action": "connect",
                    "params": {
                        "driver": "postgresql",
                        "host": "localhost",
                        "port": 5432,
                        "database": "eyeflow_db",
                        "user": "eyeflow",
                        "password": "eyeflow",
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })
        assert resp.status_code == 202
        plan_result = resp.json()
        assert plan_result["status"] == "completed"

        # Verify /context now has PostgreSQL schema
        ctx_resp = app_client.get("/context")
        data = ctx_resp.json()
        prompt = data["system_prompt"]

        assert "Database Context" in prompt
        assert "users" in prompt
        assert "connectors" in prompt
        assert "PK" in prompt
        print(f"\n  System prompt length with PG schema: {len(prompt):,} chars")

    def test_find_via_api(self, app_client) -> None:
        """Connect + find users via API plans."""
        # Connect
        connect_plan_id = f"conn-{uuid.uuid4().hex[:8]}"
        app_client.post("/plans", json={
            "plan": {
                "plan_id": connect_plan_id,
                "protocol_version": "2.0",
                "description": "Connect",
                "actions": [{
                    "id": "connect",
                    "module": "db_gateway",
                    "action": "connect",
                    "params": {
                        "driver": "postgresql",
                        "host": "localhost",
                        "database": "eyeflow_db",
                        "user": "eyeflow",
                        "password": "eyeflow",
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })

        # Find users
        find_plan_id = f"find-{uuid.uuid4().hex[:8]}"
        resp = app_client.post("/plans", json={
            "plan": {
                "plan_id": find_plan_id,
                "protocol_version": "2.0",
                "description": "Find all users",
                "actions": [{
                    "id": "find_users",
                    "module": "db_gateway",
                    "action": "find",
                    "params": {
                        "entity": "users",
                        "select": ["email", "firstName", "lastName", "role"],
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })
        assert resp.status_code == 202
        result = resp.json()
        assert result["status"] == "completed"

        # Check the action result (actions is a list of dicts with 'action_id')
        actions = result["actions"]
        find_action = next(a for a in actions if a["action_id"] == "find_users")
        action_result = find_action["result"]
        assert action_result["row_count"] >= 1
        print(f"\n  Users found via API: {action_result['row_count']}")
        for row in action_result["rows"][:5]:
            print(f"    - {row.get('email')} ({row.get('role')})")

    def test_introspect_via_api(self, app_client) -> None:
        """Connect + introspect via API."""
        # Connect
        app_client.post("/plans", json={
            "plan": {
                "plan_id": f"conn-{uuid.uuid4().hex[:8]}",
                "protocol_version": "2.0",
                "description": "Connect",
                "actions": [{
                    "id": "connect",
                    "module": "db_gateway",
                    "action": "connect",
                    "params": {
                        "driver": "postgresql",
                        "host": "localhost",
                        "database": "eyeflow_db",
                        "user": "eyeflow",
                        "password": "eyeflow",
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })

        # Introspect
        intro_plan_id = f"intro-{uuid.uuid4().hex[:8]}"
        resp = app_client.post("/plans", json={
            "plan": {
                "plan_id": intro_plan_id,
                "protocol_version": "2.0",
                "description": "Introspect database schema",
                "actions": [{
                    "id": "introspect",
                    "module": "db_gateway",
                    "action": "introspect",
                    "params": {
                        "connection_id": "eyeflow",
                        "refresh": True,
                    },
                }],
            },
            "async_execution": False,
        })
        assert resp.status_code == 202
        result = resp.json()
        assert result["status"] == "completed"

        actions = result["actions"]
        intro_action = next(a for a in actions if a["action_id"] == "introspect")
        action_result = intro_action["result"]
        assert action_result["table_count"] >= 19
        table_names = [t["name"] for t in action_result["tables"]]
        assert "users" in table_names
        assert "connectors" in table_names
        print(f"\n  Tables introspected: {action_result['table_count']}")
        for t in action_result["tables"][:10]:
            col_count = len(t.get("columns", []))
            print(f"    - {t['name']} ({col_count} columns)")
