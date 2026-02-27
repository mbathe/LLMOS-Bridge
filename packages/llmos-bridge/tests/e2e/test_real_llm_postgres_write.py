"""E2E tests — Claude generates WRITE and DELETE plans on real PostgreSQL.

These tests prove the LLM can autonomously:
1. INSERT new records (create, create_many)
2. UPDATE existing records
3. DELETE records
4. Execute a full CRUD lifecycle (create → read → update → verify → delete → verify)

Safety:
    All tests use a dedicated ``_e2e_test_products`` table created at test time
    and dropped in cleanup. No existing data is ever touched.

Requirements:
    - ANTHROPIC_API_KEY environment variable set
    - PostgreSQL running locally with user=eyeflow, password=eyeflow
    - Database eyeflow_db

Usage:
    ANTHROPIC_API_KEY=sk-ant-... pytest tests/e2e/test_real_llm_postgres_write.py -v -s
"""

from __future__ import annotations

import json
import os
import re
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

TEST_TABLE = "_e2e_test_products"


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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return None


def _create_test_table() -> None:
    """Create the _e2e_test_products table in PostgreSQL."""
    conn = psycopg2.connect(
        host="localhost", database="eyeflow_db", user="eyeflow", password="eyeflow"
    )
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    cur.execute(f"""
        CREATE TABLE {TEST_TABLE} (
            id SERIAL PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            price NUMERIC(10,2) NOT NULL,
            category VARCHAR(50),
            in_stock BOOLEAN DEFAULT true,
            created_at TIMESTAMP DEFAULT now()
        )
    """)
    conn.commit()
    cur.close()
    conn.close()


def _drop_test_table() -> None:
    """Drop the _e2e_test_products table."""
    conn = psycopg2.connect(
        host="localhost", database="eyeflow_db", user="eyeflow", password="eyeflow"
    )
    cur = conn.cursor()
    cur.execute(f"DROP TABLE IF EXISTS {TEST_TABLE}")
    conn.commit()
    cur.close()
    conn.close()


def _count_rows() -> int:
    """Direct SQL count of rows in the test table."""
    conn = psycopg2.connect(
        host="localhost", database="eyeflow_db", user="eyeflow", password="eyeflow"
    )
    cur = conn.cursor()
    cur.execute(f"SELECT COUNT(*) FROM {TEST_TABLE}")
    count = cur.fetchone()[0]
    cur.close()
    conn.close()
    return count


def _get_all_rows() -> list[dict]:
    """Direct SQL select all from test table."""
    conn = psycopg2.connect(
        host="localhost", database="eyeflow_db", user="eyeflow", password="eyeflow"
    )
    cur = conn.cursor()
    cur.execute(f"SELECT id, name, price, category, in_stock FROM {TEST_TABLE} ORDER BY id")
    cols = ["id", "name", "price", "category", "in_stock"]
    rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    cur.close()
    conn.close()
    return rows


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gw():
    """DatabaseGatewayModule instance."""
    from llmos_bridge.modules.database_gateway.module import DatabaseGatewayModule

    return DatabaseGatewayModule(max_connections=5, schema_cache_ttl=300)


@pytest.fixture()
def connected_gw_with_test_table(gw):
    """Gateway connected to PostgreSQL with the test table created and introspected."""
    import asyncio

    # Create the test table
    _create_test_table()

    async def _setup():
        await gw._action_connect({
            "driver": "postgresql",
            "host": "localhost",
            "port": 5432,
            "database": "eyeflow_db",
            "user": "eyeflow",
            "password": "eyeflow",
            "connection_id": "eyeflow",
        })
        # Refresh introspection so the gateway sees the new test table
        await gw._action_introspect({
            "connection_id": "eyeflow",
            "refresh": True,
        })

    asyncio.get_event_loop().run_until_complete(_setup())
    yield gw

    async def _teardown():
        try:
            await gw._action_disconnect({"connection_id": "eyeflow"})
        except Exception:
            pass

    asyncio.get_event_loop().run_until_complete(_teardown())
    # Always drop the test table
    _drop_test_table()


@pytest.fixture()
def system_prompt_with_test_table(connected_gw_with_test_table):
    """System prompt that includes the test table schema."""
    from llmos_bridge.api.prompt import SystemPromptGenerator

    gw = connected_gw_with_test_table
    manifest = gw.get_manifest()
    snippet = gw.get_context_snippet()

    gen = SystemPromptGenerator(
        manifests=[manifest],
        permission_profile="power_user",
        daemon_version="1.1.0",
        context_snippets={"db_gateway": snippet} if snippet else {},
    )
    prompt = gen.generate()
    # Verify the test table is visible
    assert TEST_TABLE in prompt, f"Test table {TEST_TABLE} not found in system prompt"
    return prompt


# ===========================================================================
# Test 1: Claude generates CREATE plans
# ===========================================================================


@skip_no_key
@skip_no_pg
@pytest.mark.e2e
class TestClaudeCreatesData:
    """Claude generates IML plans to INSERT data into the test table."""

    @pytest.mark.asyncio
    async def test_claude_creates_single_record(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Claude generates a create plan → execute → verify row exists."""
        gw = connected_gw_with_test_table
        prompt = system_prompt_with_test_table

        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Insère un nouveau produit dans la table '{TEST_TABLE}': "
            f"nom='Clavier Mécanique', prix=89.99, catégorie='informatique', en stock=true. "
            f"Génère un plan IML. Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:600]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"No IML plan extracted:\n{response}"

        action = plan["actions"][0]
        assert action["module"] == "db_gateway"
        assert action["action"] == "create"
        assert action["params"]["entity"] == TEST_TABLE

        # Execute the plan
        result = await gw._action_create({
            "entity": TEST_TABLE,
            "data": action["params"]["data"],
            "connection_id": "eyeflow",
        })
        assert result["created"] is True
        assert result["inserted_id"] is not None
        print(f"  Inserted ID: {result['inserted_id']}")

        # Verify via direct SQL
        rows = _get_all_rows()
        assert len(rows) == 1
        assert rows[0]["name"] == "Clavier Mécanique"
        assert float(rows[0]["price"]) == 89.99
        print(f"  Verified: {rows[0]}")

    @pytest.mark.asyncio
    async def test_claude_creates_many_records(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Claude generates a create_many plan → execute → verify all rows."""
        gw = connected_gw_with_test_table
        prompt = system_prompt_with_test_table

        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Insère 4 produits d'un coup dans '{TEST_TABLE}' (batch insert) :\n"
            f"1. Souris Gaming, 49.99€, catégorie=informatique, en stock\n"
            f"2. Écran 27 pouces, 349.99€, catégorie=informatique, en stock\n"
            f"3. Câble HDMI, 12.50€, catégorie=accessoires, en stock\n"
            f"4. Webcam HD, 79.00€, catégorie=informatique, pas en stock\n"
            f"Génère un plan IML avec create_many. Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:800]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"No IML plan extracted:\n{response}"

        # Find the create_many action
        create_action = None
        for a in plan["actions"]:
            if a["action"] == "create_many":
                create_action = a
                break

        assert create_action is not None, (
            f"No create_many action found. Actions: "
            f"{[a['action'] for a in plan['actions']]}"
        )
        assert create_action["params"]["entity"] == TEST_TABLE
        records = create_action["params"]["records"]
        assert len(records) == 4

        # Execute
        result = await gw._action_create_many({
            "entity": TEST_TABLE,
            "records": records,
            "connection_id": "eyeflow",
        })
        assert result["inserted_count"] == 4
        print(f"  Inserted {result['inserted_count']} records")

        # Verify via direct SQL
        assert _count_rows() == 4
        rows = _get_all_rows()
        names = [r["name"] for r in rows]
        print(f"  Products: {names}")
        # At least verify count (names may be slightly different in French)
        assert len(rows) == 4

    @pytest.mark.asyncio
    async def test_execute_create_then_read(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Claude generates a chained create → find plan."""
        gw = connected_gw_with_test_table
        prompt = system_prompt_with_test_table

        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Fais deux actions dans un seul plan IML :\n"
            f"1. D'abord, crée un produit dans '{TEST_TABLE}': "
            f"nom='Casque Audio', prix=159.99, catégorie='audio', en stock=true\n"
            f"2. Ensuite, lis tous les produits de la table '{TEST_TABLE}'\n"
            f"La seconde action doit dépendre de la première (depends_on). "
            f"Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:800]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"No IML plan extracted:\n{response}"
        assert len(plan["actions"]) >= 2, (
            f"Expected 2+ actions, got {len(plan['actions'])}"
        )

        # Action 1: create
        a1 = plan["actions"][0]
        assert a1["action"] == "create"
        result1 = await gw._action_create({
            "entity": TEST_TABLE,
            "data": a1["params"]["data"],
            "connection_id": "eyeflow",
        })
        assert result1["created"] is True
        print(f"  Created: ID={result1['inserted_id']}")

        # Action 2: find
        a2 = plan["actions"][1]
        assert a2["action"] == "find"
        # Verify depends_on was set
        assert "depends_on" in a2 and len(a2["depends_on"]) > 0, (
            "Second action should depend on the first"
        )

        result2 = await gw._action_find({
            "entity": TEST_TABLE,
            "connection_id": "eyeflow",
        })
        assert result2["row_count"] == 1
        assert result2["rows"][0]["name"] == "Casque Audio"
        print(f"  Found: {result2['rows'][0]['name']} @ {result2['rows'][0]['price']}")


# ===========================================================================
# Test 2: Claude generates UPDATE plans
# ===========================================================================


@skip_no_key
@skip_no_pg
@pytest.mark.e2e
class TestClaudeUpdatesData:
    """Claude generates IML plans to UPDATE data in the test table."""

    @pytest.mark.asyncio
    async def test_claude_updates_price(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Insert seed data → Claude generates update plan → verify change."""
        gw = connected_gw_with_test_table

        # Seed data
        await gw._action_create_many({
            "entity": TEST_TABLE,
            "records": [
                {"name": "Laptop Pro", "price": 1299.99, "category": "informatique", "in_stock": True},
                {"name": "Tablet Air", "price": 599.99, "category": "informatique", "in_stock": True},
                {"name": "Phone Mini", "price": 399.99, "category": "mobile", "in_stock": False},
            ],
            "connection_id": "eyeflow",
        })
        assert _count_rows() == 3

        prompt = system_prompt_with_test_table
        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"La table '{TEST_TABLE}' contient des produits. "
            f"Mets à jour le prix du 'Laptop Pro' à 1199.99 (promotion). "
            f"Génère un plan IML. Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:600]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"No IML plan extracted:\n{response}"

        action = plan["actions"][0]
        assert action["module"] == "db_gateway"
        assert action["action"] == "update"
        assert action["params"]["entity"] == TEST_TABLE

        # Execute
        result = await gw._action_update({
            "entity": TEST_TABLE,
            "filter": action["params"]["filter"],
            "values": action["params"]["values"],
            "connection_id": "eyeflow",
        })
        assert result["rows_affected"] >= 1
        print(f"  Updated {result['rows_affected']} row(s)")

        # Verify via direct SQL
        rows = _get_all_rows()
        laptop = next(r for r in rows if r["name"] == "Laptop Pro")
        assert float(laptop["price"]) == 1199.99
        print(f"  Laptop Pro price now: {laptop['price']}")

    @pytest.mark.asyncio
    async def test_claude_updates_stock_status(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Claude sets out-of-stock products back in stock."""
        gw = connected_gw_with_test_table

        # Seed: 2 in stock, 2 out of stock
        await gw._action_create_many({
            "entity": TEST_TABLE,
            "records": [
                {"name": "Widget A", "price": 10.00, "category": "tools", "in_stock": True},
                {"name": "Widget B", "price": 20.00, "category": "tools", "in_stock": False},
                {"name": "Widget C", "price": 30.00, "category": "tools", "in_stock": False},
                {"name": "Widget D", "price": 40.00, "category": "tools", "in_stock": True},
            ],
            "connection_id": "eyeflow",
        })

        prompt = system_prompt_with_test_table
        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Remets en stock tous les produits qui sont actuellement en rupture "
            f"(in_stock=false) dans la table '{TEST_TABLE}'. "
            f"Génère un plan IML avec une seule action 'update'. "
            f"Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:600]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"No IML plan extracted:\n{response}"

        # Find the update action (Claude may add preparatory steps)
        update_action = next(
            (a for a in plan["actions"] if a["action"] == "update"), None
        )
        assert update_action is not None, (
            f"No 'update' action found. Actions: {[a['action'] for a in plan['actions']]}"
        )

        result = await gw._action_update({
            "entity": TEST_TABLE,
            "filter": update_action["params"]["filter"],
            "values": update_action["params"]["values"],
            "connection_id": "eyeflow",
        })
        assert result["rows_affected"] == 2
        print(f"  Updated {result['rows_affected']} rows back in stock")

        # Verify all are now in stock
        rows = _get_all_rows()
        assert all(r["in_stock"] for r in rows), (
            f"Not all in stock: {[(r['name'], r['in_stock']) for r in rows]}"
        )

    @pytest.mark.asyncio
    async def test_claude_updates_category(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Claude renames a category across multiple products."""
        gw = connected_gw_with_test_table

        await gw._action_create_many({
            "entity": TEST_TABLE,
            "records": [
                {"name": "Prod A", "price": 10.00, "category": "ancien", "in_stock": True},
                {"name": "Prod B", "price": 20.00, "category": "ancien", "in_stock": True},
                {"name": "Prod C", "price": 30.00, "category": "nouveau", "in_stock": True},
            ],
            "connection_id": "eyeflow",
        })

        prompt = system_prompt_with_test_table
        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Dans la table '{TEST_TABLE}', renomme la catégorie 'ancien' "
            f"en 'classique' pour tous les produits concernés. "
            f"Génère un plan IML avec une seule action 'update'. "
            f"Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:600]}")

        plan = extract_iml_plan(response)
        assert plan is not None

        # Find the update action
        update_action = next(
            (a for a in plan["actions"] if a["action"] == "update"), None
        )
        assert update_action is not None, (
            f"No 'update' action found. Actions: {[a['action'] for a in plan['actions']]}"
        )

        result = await gw._action_update({
            "entity": TEST_TABLE,
            "filter": update_action["params"]["filter"],
            "values": update_action["params"]["values"],
            "connection_id": "eyeflow",
        })
        assert result["rows_affected"] == 2
        print(f"  Renamed {result['rows_affected']} products from 'ancien' to 'classique'")

        rows = _get_all_rows()
        categories = {r["category"] for r in rows}
        assert "ancien" not in categories
        assert "classique" in categories
        assert "nouveau" in categories


# ===========================================================================
# Test 3: Claude generates DELETE plans
# ===========================================================================


@skip_no_key
@skip_no_pg
@pytest.mark.e2e
class TestClaudeDeletesData:
    """Claude generates IML plans to DELETE data from the test table."""

    @pytest.mark.asyncio
    async def test_claude_deletes_single_product(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Claude deletes a specific product by name."""
        gw = connected_gw_with_test_table

        await gw._action_create_many({
            "entity": TEST_TABLE,
            "records": [
                {"name": "Keep Me", "price": 10.00, "category": "safe", "in_stock": True},
                {"name": "Delete Me", "price": 99.99, "category": "doomed", "in_stock": False},
                {"name": "Keep Me Too", "price": 20.00, "category": "safe", "in_stock": True},
            ],
            "connection_id": "eyeflow",
        })
        assert _count_rows() == 3

        prompt = system_prompt_with_test_table
        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Supprime le produit 'Delete Me' de la table '{TEST_TABLE}'. "
            f"Confirme la suppression (confirm=true). "
            f"Génère un plan IML. Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:600]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"No IML plan extracted:\n{response}"

        action = plan["actions"][0]
        assert action["module"] == "db_gateway"
        assert action["action"] == "delete"
        assert action["params"]["entity"] == TEST_TABLE

        # Execute
        result = await gw._action_delete({
            "entity": TEST_TABLE,
            "filter": action["params"]["filter"],
            "confirm": True,
            "connection_id": "eyeflow",
        })
        assert result["deleted"] is True
        assert result["rows_deleted"] == 1
        print(f"  Deleted {result['rows_deleted']} row")

        # Verify
        assert _count_rows() == 2
        rows = _get_all_rows()
        names = [r["name"] for r in rows]
        assert "Delete Me" not in names
        assert "Keep Me" in names
        assert "Keep Me Too" in names
        print(f"  Remaining: {names}")

    @pytest.mark.asyncio
    async def test_claude_deletes_by_filter(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Claude deletes all out-of-stock products."""
        gw = connected_gw_with_test_table

        await gw._action_create_many({
            "entity": TEST_TABLE,
            "records": [
                {"name": "In Stock 1", "price": 10.00, "category": "a", "in_stock": True},
                {"name": "Out Stock 1", "price": 20.00, "category": "a", "in_stock": False},
                {"name": "Out Stock 2", "price": 30.00, "category": "b", "in_stock": False},
                {"name": "In Stock 2", "price": 40.00, "category": "b", "in_stock": True},
                {"name": "Out Stock 3", "price": 50.00, "category": "c", "in_stock": False},
            ],
            "connection_id": "eyeflow",
        })
        assert _count_rows() == 5

        prompt = system_prompt_with_test_table
        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Supprime tous les produits en rupture de stock (in_stock=false) "
            f"de la table '{TEST_TABLE}'. Confirme la suppression (confirm=true). "
            f"Génère un plan IML avec une seule action 'delete'. "
            f"Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:600]}")

        plan = extract_iml_plan(response)
        assert plan is not None

        # Find the delete action
        delete_action = next(
            (a for a in plan["actions"] if a["action"] == "delete"), None
        )
        assert delete_action is not None, (
            f"No 'delete' action found. Actions: {[a['action'] for a in plan['actions']]}"
        )

        result = await gw._action_delete({
            "entity": TEST_TABLE,
            "filter": delete_action["params"]["filter"],
            "confirm": True,
            "connection_id": "eyeflow",
        })
        assert result["rows_deleted"] == 3
        print(f"  Deleted {result['rows_deleted']} out-of-stock products")

        # Verify only in-stock remain
        assert _count_rows() == 2
        rows = _get_all_rows()
        assert all(r["in_stock"] for r in rows)
        print(f"  Remaining: {[r['name'] for r in rows]}")

    @pytest.mark.asyncio
    async def test_claude_deletes_by_price_range(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Claude deletes products cheaper than a threshold using $lt filter."""
        gw = connected_gw_with_test_table

        await gw._action_create_many({
            "entity": TEST_TABLE,
            "records": [
                {"name": "Cheap 1", "price": 5.00, "category": "x", "in_stock": True},
                {"name": "Cheap 2", "price": 8.00, "category": "x", "in_stock": True},
                {"name": "Medium", "price": 25.00, "category": "x", "in_stock": True},
                {"name": "Expensive", "price": 150.00, "category": "x", "in_stock": True},
            ],
            "connection_id": "eyeflow",
        })

        prompt = system_prompt_with_test_table
        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Supprime tous les produits dont le prix est inférieur à 10€ "
            f"dans la table '{TEST_TABLE}'. Utilise un filtre $lt. "
            f"Confirme la suppression. "
            f"Génère un plan IML. Utilise connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:600]}")

        plan = extract_iml_plan(response)
        assert plan is not None

        action = plan["actions"][0]
        assert action["action"] == "delete"
        # Verify Claude used a comparison operator
        filt = action["params"]["filter"]
        assert "price" in filt
        price_filter = filt["price"]
        assert isinstance(price_filter, dict), (
            f"Expected a comparison filter, got {price_filter}"
        )
        assert "$lt" in price_filter or "$lte" in price_filter

        result = await gw._action_delete({
            "entity": TEST_TABLE,
            "filter": filt,
            "confirm": True,
            "connection_id": "eyeflow",
        })
        assert result["rows_deleted"] == 2
        print(f"  Deleted {result['rows_deleted']} cheap products")

        remaining = _get_all_rows()
        assert len(remaining) == 2
        assert all(float(r["price"]) >= 10.0 for r in remaining)
        print(f"  Remaining: {[(r['name'], float(r['price'])) for r in remaining]}")


# ===========================================================================
# Test 4: Full CRUD lifecycle — Claude orchestrates everything
# ===========================================================================


@skip_no_key
@skip_no_pg
@pytest.mark.e2e
class TestClaudeFullCRUDLifecycle:
    """Claude generates a multi-step plan: create → read → update → read → delete → read."""

    @pytest.mark.asyncio
    async def test_full_lifecycle_step_by_step(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Ask Claude for each CRUD step, execute, verify the chain."""
        gw = connected_gw_with_test_table
        prompt = system_prompt_with_test_table

        # --- Step 1: CREATE ---
        print("\n  === Step 1: CREATE ===")
        resp1 = call_claude(
            prompt,
            f"connection_id='eyeflow'. "
            f"Insère 3 produits dans '{TEST_TABLE}':\n"
            f"- 'Alpha', 100€, catégorie='premium', en stock\n"
            f"- 'Beta', 50€, catégorie='standard', en stock\n"
            f"- 'Gamma', 25€, catégorie='budget', pas en stock\n"
            f"Plan IML avec create_many. connection_id='eyeflow'."
        )
        plan1 = extract_iml_plan(resp1)
        assert plan1 is not None, f"Step 1 failed:\n{resp1}"

        create_action = next(
            a for a in plan1["actions"] if a["action"] == "create_many"
        )
        r1 = await gw._action_create_many({
            "entity": TEST_TABLE,
            "records": create_action["params"]["records"],
            "connection_id": "eyeflow",
        })
        assert r1["inserted_count"] == 3
        print(f"  Created {r1['inserted_count']} products")

        # --- Step 2: READ (count) ---
        print("  === Step 2: COUNT ===")
        r2 = await gw._action_count({
            "entity": TEST_TABLE,
            "connection_id": "eyeflow",
        })
        assert r2["count"] == 3
        print(f"  Count: {r2['count']}")

        # --- Step 3: UPDATE ---
        print("  === Step 3: UPDATE ===")
        resp3 = call_claude(
            prompt,
            f"connection_id='eyeflow'. "
            f"Le produit 'Gamma' dans '{TEST_TABLE}' est maintenant en stock "
            f"et son prix a augmenté à 30€. Mets à jour. "
            f"Plan IML. connection_id='eyeflow'."
        )
        plan3 = extract_iml_plan(resp3)
        assert plan3 is not None, f"Step 3 failed:\n{resp3}"

        update_action = next(
            a for a in plan3["actions"] if a["action"] == "update"
        )
        r3 = await gw._action_update({
            "entity": TEST_TABLE,
            "filter": update_action["params"]["filter"],
            "values": update_action["params"]["values"],
            "connection_id": "eyeflow",
        })
        assert r3["rows_affected"] == 1
        print(f"  Updated {r3['rows_affected']} row")

        # Verify update
        gamma = await gw._action_find_one({
            "entity": TEST_TABLE,
            "filter": {"name": "Gamma"},
            "connection_id": "eyeflow",
        })
        assert gamma["found"] is True
        assert float(gamma["record"]["price"]) == 30.0
        assert gamma["record"]["in_stock"] is True
        print(f"  Gamma now: price={gamma['record']['price']}, in_stock={gamma['record']['in_stock']}")

        # --- Step 4: DELETE ---
        print("  === Step 4: DELETE ===")
        resp4 = call_claude(
            prompt,
            f"connection_id='eyeflow'. "
            f"Supprime le produit 'Beta' de '{TEST_TABLE}'. "
            f"Confirme. Plan IML. connection_id='eyeflow'."
        )
        plan4 = extract_iml_plan(resp4)
        assert plan4 is not None, f"Step 4 failed:\n{resp4}"

        delete_action = next(
            a for a in plan4["actions"] if a["action"] == "delete"
        )
        r4 = await gw._action_delete({
            "entity": TEST_TABLE,
            "filter": delete_action["params"]["filter"],
            "confirm": True,
            "connection_id": "eyeflow",
        })
        assert r4["rows_deleted"] == 1
        print(f"  Deleted {r4['rows_deleted']} row")

        # --- Step 5: Final verification ---
        print("  === Step 5: FINAL CHECK ===")
        final = await gw._action_find({
            "entity": TEST_TABLE,
            "order_by": ["name"],
            "connection_id": "eyeflow",
        })
        assert final["row_count"] == 2
        names = [r["name"] for r in final["rows"]]
        assert "Alpha" in names
        assert "Gamma" in names
        assert "Beta" not in names
        print(f"  Final products: {names}")
        print("  === CRUD lifecycle COMPLETE ===")

    @pytest.mark.asyncio
    async def test_claude_multi_action_plan(
        self, connected_gw_with_test_table, system_prompt_with_test_table,
    ) -> None:
        """Ask Claude to generate a SINGLE plan with create + update + delete chained."""
        gw = connected_gw_with_test_table
        prompt = system_prompt_with_test_table

        response = call_claude(
            prompt,
            f"Je suis connecté via connection_id='eyeflow'. "
            f"Génère un seul plan IML avec 3 actions chaînées sur '{TEST_TABLE}':\n"
            f"1. Crée un produit: nom='Temporaire', prix=9.99, catégorie='test', en stock=true\n"
            f"2. Mets à jour le prix de 'Temporaire' à 19.99 (depends_on action 1)\n"
            f"3. Supprime 'Temporaire' (depends_on action 2, confirm=true)\n"
            f"Chaque action doit avoir un id unique et les depends_on corrects. "
            f"connection_id='eyeflow'."
        )
        print(f"\n  Claude response:\n{response[:1000]}")

        plan = extract_iml_plan(response)
        assert plan is not None, f"No IML plan extracted:\n{response}"
        assert len(plan["actions"]) == 3, (
            f"Expected 3 actions, got {len(plan['actions'])}: "
            f"{[a['action'] for a in plan['actions']]}"
        )

        actions = plan["actions"]
        a1, a2, a3 = actions[0], actions[1], actions[2]

        # Verify action types
        assert a1["action"] == "create"
        assert a2["action"] == "update"
        assert a3["action"] == "delete"

        # Verify dependency chain
        assert "depends_on" in a2 and a1["id"] in a2["depends_on"]
        assert "depends_on" in a3 and a2["id"] in a3["depends_on"]

        # Execute the chain
        r1 = await gw._action_create({
            "entity": TEST_TABLE,
            "data": a1["params"]["data"],
            "connection_id": "eyeflow",
        })
        assert r1["created"] is True
        print(f"  Step 1: Created 'Temporaire' (id={r1['inserted_id']})")

        r2 = await gw._action_update({
            "entity": TEST_TABLE,
            "filter": a2["params"]["filter"],
            "values": a2["params"]["values"],
            "connection_id": "eyeflow",
        })
        assert r2["rows_affected"] == 1
        print(f"  Step 2: Updated price to 19.99")

        # Verify intermediate state
        check = await gw._action_find_one({
            "entity": TEST_TABLE,
            "filter": {"name": "Temporaire"},
            "connection_id": "eyeflow",
        })
        assert float(check["record"]["price"]) == 19.99

        r3 = await gw._action_delete({
            "entity": TEST_TABLE,
            "filter": a3["params"]["filter"],
            "confirm": True,
            "connection_id": "eyeflow",
        })
        assert r3["rows_deleted"] == 1
        print(f"  Step 3: Deleted 'Temporaire'")

        # Table should be empty
        final_count = _count_rows()
        assert final_count == 0
        print(f"  Final count: {final_count} (table empty)")


# ===========================================================================
# Test 5: Full API E2E — Write operations through FastAPI
# ===========================================================================


@skip_no_pg
@pytest.mark.e2e
class TestWriteViaApi:
    """Write and delete operations through the full FastAPI stack."""

    @pytest.fixture(autouse=True)
    def _setup_teardown_table(self):
        """Create test table before each test, drop after."""
        _create_test_table()
        yield
        _drop_test_table()

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

    def _connect_pg(self, client) -> None:
        """Helper to submit a connect plan."""
        client.post("/plans", json={
            "plan": {
                "plan_id": f"conn-{uuid.uuid4().hex[:8]}",
                "protocol_version": "2.0",
                "description": "Connect to PG",
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
        # Refresh introspection to see test table
        client.post("/plans", json={
            "plan": {
                "plan_id": f"intro-{uuid.uuid4().hex[:8]}",
                "protocol_version": "2.0",
                "description": "Refresh schema",
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

    def test_create_via_api(self, app_client) -> None:
        """Insert a product via the Plans API."""
        self._connect_pg(app_client)

        plan_id = f"create-{uuid.uuid4().hex[:8]}"
        resp = app_client.post("/plans", json={
            "plan": {
                "plan_id": plan_id,
                "protocol_version": "2.0",
                "description": "Create a test product",
                "actions": [{
                    "id": "create_product",
                    "module": "db_gateway",
                    "action": "create",
                    "params": {
                        "entity": TEST_TABLE,
                        "data": {
                            "name": "API Product",
                            "price": 42.00,
                            "category": "api_test",
                            "in_stock": True,
                        },
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })
        assert resp.status_code == 202
        result = resp.json()
        assert result["status"] == "completed"

        action = next(a for a in result["actions"] if a["action_id"] == "create_product")
        assert action["result"]["created"] is True
        print(f"\n  Created via API: ID={action['result']['inserted_id']}")

        # Verify
        assert _count_rows() == 1
        rows = _get_all_rows()
        assert rows[0]["name"] == "API Product"

    def test_create_many_via_api(self, app_client) -> None:
        """Batch insert via the Plans API."""
        self._connect_pg(app_client)

        plan_id = f"batch-{uuid.uuid4().hex[:8]}"
        resp = app_client.post("/plans", json={
            "plan": {
                "plan_id": plan_id,
                "protocol_version": "2.0",
                "description": "Batch insert products",
                "actions": [{
                    "id": "batch_create",
                    "module": "db_gateway",
                    "action": "create_many",
                    "params": {
                        "entity": TEST_TABLE,
                        "records": [
                            {"name": "Batch 1", "price": 10.00, "category": "batch", "in_stock": True},
                            {"name": "Batch 2", "price": 20.00, "category": "batch", "in_stock": True},
                            {"name": "Batch 3", "price": 30.00, "category": "batch", "in_stock": False},
                        ],
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })
        assert resp.status_code == 202
        result = resp.json()
        assert result["status"] == "completed"

        action = next(a for a in result["actions"] if a["action_id"] == "batch_create")
        assert action["result"]["inserted_count"] == 3
        print(f"\n  Batch created: {action['result']['inserted_count']} rows")
        assert _count_rows() == 3

    def test_update_via_api(self, app_client) -> None:
        """Update a product via the Plans API."""
        self._connect_pg(app_client)

        # First create seed data
        app_client.post("/plans", json={
            "plan": {
                "plan_id": f"seed-{uuid.uuid4().hex[:8]}",
                "protocol_version": "2.0",
                "description": "Seed data",
                "actions": [{
                    "id": "seed",
                    "module": "db_gateway",
                    "action": "create",
                    "params": {
                        "entity": TEST_TABLE,
                        "data": {"name": "Update Target", "price": 100.00, "category": "test", "in_stock": True},
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })

        # Now update
        plan_id = f"update-{uuid.uuid4().hex[:8]}"
        resp = app_client.post("/plans", json={
            "plan": {
                "plan_id": plan_id,
                "protocol_version": "2.0",
                "description": "Update product price",
                "actions": [{
                    "id": "update_price",
                    "module": "db_gateway",
                    "action": "update",
                    "params": {
                        "entity": TEST_TABLE,
                        "filter": {"name": "Update Target"},
                        "values": {"price": 79.99, "category": "promo"},
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })
        assert resp.status_code == 202
        result = resp.json()
        assert result["status"] == "completed"

        action = next(a for a in result["actions"] if a["action_id"] == "update_price")
        assert action["result"]["rows_affected"] == 1
        print(f"\n  Updated {action['result']['rows_affected']} row via API")

        rows = _get_all_rows()
        assert float(rows[0]["price"]) == 79.99
        assert rows[0]["category"] == "promo"

    def test_delete_via_api(self, app_client) -> None:
        """Delete a product via the Plans API."""
        self._connect_pg(app_client)

        # Seed
        app_client.post("/plans", json={
            "plan": {
                "plan_id": f"seed-{uuid.uuid4().hex[:8]}",
                "protocol_version": "2.0",
                "description": "Seed data",
                "actions": [{
                    "id": "seed",
                    "module": "db_gateway",
                    "action": "create_many",
                    "params": {
                        "entity": TEST_TABLE,
                        "records": [
                            {"name": "Keep", "price": 10.00, "category": "safe", "in_stock": True},
                            {"name": "Remove", "price": 20.00, "category": "doomed", "in_stock": True},
                        ],
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })
        assert _count_rows() == 2

        # Delete
        plan_id = f"delete-{uuid.uuid4().hex[:8]}"
        resp = app_client.post("/plans", json={
            "plan": {
                "plan_id": plan_id,
                "protocol_version": "2.0",
                "description": "Delete a product",
                "actions": [{
                    "id": "delete_product",
                    "module": "db_gateway",
                    "action": "delete",
                    "params": {
                        "entity": TEST_TABLE,
                        "filter": {"name": "Remove"},
                        "confirm": True,
                        "connection_id": "eyeflow",
                    },
                }],
            },
            "async_execution": False,
        })
        assert resp.status_code == 202
        result = resp.json()
        assert result["status"] == "completed"

        action = next(a for a in result["actions"] if a["action_id"] == "delete_product")
        assert action["result"]["deleted"] is True
        assert action["result"]["rows_deleted"] == 1
        print(f"\n  Deleted {action['result']['rows_deleted']} row via API")

        # Verify
        assert _count_rows() == 1
        rows = _get_all_rows()
        assert rows[0]["name"] == "Keep"

    def test_full_crud_chain_via_api(self, app_client) -> None:
        """Full CRUD chain in a single multi-action plan via API."""
        self._connect_pg(app_client)

        plan_id = f"crud-{uuid.uuid4().hex[:8]}"
        resp = app_client.post("/plans", json={
            "plan": {
                "plan_id": plan_id,
                "protocol_version": "2.0",
                "description": "Full CRUD lifecycle",
                "actions": [
                    {
                        "id": "step_create",
                        "module": "db_gateway",
                        "action": "create",
                        "params": {
                            "entity": TEST_TABLE,
                            "data": {"name": "Lifecycle Item", "price": 55.00, "category": "lifecycle", "in_stock": True},
                            "connection_id": "eyeflow",
                        },
                    },
                    {
                        "id": "step_read",
                        "module": "db_gateway",
                        "action": "find",
                        "depends_on": ["step_create"],
                        "params": {
                            "entity": TEST_TABLE,
                            "filter": {"name": "Lifecycle Item"},
                            "connection_id": "eyeflow",
                        },
                    },
                    {
                        "id": "step_update",
                        "module": "db_gateway",
                        "action": "update",
                        "depends_on": ["step_read"],
                        "params": {
                            "entity": TEST_TABLE,
                            "filter": {"name": "Lifecycle Item"},
                            "values": {"price": 44.99, "in_stock": False},
                            "connection_id": "eyeflow",
                        },
                    },
                    {
                        "id": "step_verify",
                        "module": "db_gateway",
                        "action": "find_one",
                        "depends_on": ["step_update"],
                        "params": {
                            "entity": TEST_TABLE,
                            "filter": {"name": "Lifecycle Item"},
                            "connection_id": "eyeflow",
                        },
                    },
                    {
                        "id": "step_delete",
                        "module": "db_gateway",
                        "action": "delete",
                        "depends_on": ["step_verify"],
                        "params": {
                            "entity": TEST_TABLE,
                            "filter": {"name": "Lifecycle Item"},
                            "confirm": True,
                            "connection_id": "eyeflow",
                        },
                    },
                ],
            },
            "async_execution": False,
        })
        assert resp.status_code == 202
        result = resp.json()
        assert result["status"] == "completed"

        actions = {a["action_id"]: a["result"] for a in result["actions"]}

        # Verify each step
        assert actions["step_create"]["created"] is True
        print(f"\n  Step 1 (create): ID={actions['step_create']['inserted_id']}")

        assert actions["step_read"]["row_count"] == 1
        print(f"  Step 2 (read): found {actions['step_read']['row_count']} row")

        assert actions["step_update"]["rows_affected"] == 1
        print(f"  Step 3 (update): affected {actions['step_update']['rows_affected']} row")

        assert actions["step_verify"]["found"] is True
        assert float(actions["step_verify"]["record"]["price"]) == 44.99
        assert actions["step_verify"]["record"]["in_stock"] is False
        print(f"  Step 4 (verify): price={actions['step_verify']['record']['price']}, in_stock={actions['step_verify']['record']['in_stock']}")

        assert actions["step_delete"]["deleted"] is True
        assert actions["step_delete"]["rows_deleted"] == 1
        print(f"  Step 5 (delete): removed {actions['step_delete']['rows_deleted']} row")

        # Table should be empty
        assert _count_rows() == 0
        print("  Final: table empty — full CRUD lifecycle via API PASSED")
