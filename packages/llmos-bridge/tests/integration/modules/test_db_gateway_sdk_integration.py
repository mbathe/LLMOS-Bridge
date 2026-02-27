"""Integration tests — Database Gateway ↔ SDK context chain.

Proves that when a database is connected, the LLM receives full schema context
(tables, columns, types, PKs, FKs, indexes) through the entire pipeline:

    DatabaseGatewayModule.get_context_snippet()
    → _collect_context_snippets()
    → SystemPromptGenerator._build_context_snippets()
    → GET /context
    → LLMOSToolkit.get_system_prompt()

This is the critical integration layer: if these tests pass, the LLM has
everything it needs to autonomously construct valid IML plans for any
database operation.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import sqlalchemy as sa

from llmos_bridge.api.prompt import SystemPromptGenerator
from llmos_bridge.modules.database_gateway.introspector import (
    introspect_schema,
    schema_to_context_string,
)
from llmos_bridge.modules.database_gateway.module import DatabaseGatewayModule


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_sample_db(db_path: str) -> None:
    """Create a sample database with multiple tables, FKs, and indexes."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(sa.text(
            "CREATE TABLE departments ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  name TEXT NOT NULL,"
            "  budget REAL DEFAULT 0.0"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE employees ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  name TEXT NOT NULL,"
            "  email TEXT NOT NULL,"
            "  department_id INTEGER NOT NULL,"
            "  salary REAL NOT NULL,"
            "  hire_date TEXT,"
            "  FOREIGN KEY (department_id) REFERENCES departments(id)"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE UNIQUE INDEX idx_employees_email ON employees (email)"
        ))
        conn.execute(sa.text(
            "CREATE INDEX idx_employees_dept ON employees (department_id)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE projects ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  title TEXT NOT NULL,"
            "  lead_id INTEGER,"
            "  department_id INTEGER,"
            "  status TEXT DEFAULT 'active',"
            "  FOREIGN KEY (lead_id) REFERENCES employees(id),"
            "  FOREIGN KEY (department_id) REFERENCES departments(id)"
            ")"
        ))
        # Insert seed data
        conn.execute(sa.text(
            "INSERT INTO departments (name, budget) VALUES "
            "('Engineering', 500000), ('Sales', 200000), ('HR', 100000)"
        ))
        conn.execute(sa.text(
            "INSERT INTO employees (name, email, department_id, salary) VALUES "
            "('Alice', 'alice@co.com', 1, 120000),"
            "('Bob', 'bob@co.com', 1, 110000),"
            "('Charlie', 'charlie@co.com', 2, 90000)"
        ))
        conn.commit()
    engine.dispose()


def _create_ecommerce_db(db_path: str) -> None:
    """Create an e-commerce database with rich schema."""
    engine = sa.create_engine(f"sqlite:///{db_path}")
    with engine.connect() as conn:
        conn.execute(sa.text(
            "CREATE TABLE customers ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  name TEXT NOT NULL,"
            "  email TEXT NOT NULL,"
            "  phone TEXT,"
            "  created_at TEXT DEFAULT CURRENT_TIMESTAMP"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE UNIQUE INDEX idx_customers_email ON customers (email)"
        ))
        conn.execute(sa.text(
            "CREATE TABLE products ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  name TEXT NOT NULL,"
            "  price REAL NOT NULL,"
            "  category TEXT NOT NULL,"
            "  stock INTEGER DEFAULT 0"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE orders ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  customer_id INTEGER NOT NULL,"
            "  order_date TEXT DEFAULT CURRENT_TIMESTAMP,"
            "  total REAL NOT NULL,"
            "  status TEXT DEFAULT 'pending',"
            "  FOREIGN KEY (customer_id) REFERENCES customers(id)"
            ")"
        ))
        conn.execute(sa.text(
            "CREATE TABLE order_items ("
            "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "  order_id INTEGER NOT NULL,"
            "  product_id INTEGER NOT NULL,"
            "  quantity INTEGER NOT NULL,"
            "  unit_price REAL NOT NULL,"
            "  FOREIGN KEY (order_id) REFERENCES orders(id),"
            "  FOREIGN KEY (product_id) REFERENCES products(id)"
            ")"
        ))
        conn.commit()
    engine.dispose()


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def gw() -> DatabaseGatewayModule:
    return DatabaseGatewayModule(max_connections=10, schema_cache_ttl=300)


@pytest.fixture()
def sample_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "sample.db")
    _create_sample_db(db_path)
    return db_path


@pytest.fixture()
def ecommerce_db(tmp_path: Path) -> str:
    db_path = str(tmp_path / "ecommerce.db")
    _create_ecommerce_db(db_path)
    return db_path


# ===========================================================================
# A. Context Snippet Generation — Module Level
# ===========================================================================


@pytest.mark.integration
class TestContextSnippetGeneration:
    """Verify DatabaseGatewayModule.get_context_snippet() returns correct info."""

    def test_no_snippet_when_no_connections(self, gw: DatabaseGatewayModule) -> None:
        """Empty module returns None — nothing to inject into LLM prompt."""
        assert gw.get_context_snippet() is None

    @pytest.mark.asyncio
    async def test_snippet_after_connect(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """After connecting, the snippet should contain table names."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "test",
        })
        snippet = gw.get_context_snippet()
        assert snippet is not None
        assert "## Database Context" in snippet
        assert "departments" in snippet
        assert "employees" in snippet
        assert "projects" in snippet
        await gw._action_disconnect({"connection_id": "test"})

    @pytest.mark.asyncio
    async def test_snippet_includes_column_names(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The LLM must see column names to write correct queries."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "cols",
        })
        snippet = gw.get_context_snippet()
        assert "name" in snippet
        assert "email" in snippet
        assert "salary" in snippet
        assert "department_id" in snippet
        assert "budget" in snippet
        await gw._action_disconnect({"connection_id": "cols"})

    @pytest.mark.asyncio
    async def test_snippet_includes_column_types(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The LLM must know data types for correct filter/value construction."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "types",
        })
        snippet = gw.get_context_snippet()
        assert "INTEGER" in snippet
        assert "TEXT" in snippet
        assert "REAL" in snippet
        await gw._action_disconnect({"connection_id": "types"})

    @pytest.mark.asyncio
    async def test_snippet_includes_primary_keys(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The LLM must see PK markers to know which columns identify records."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "pks",
        })
        snippet = gw.get_context_snippet()
        assert "PK" in snippet
        await gw._action_disconnect({"connection_id": "pks"})

    @pytest.mark.asyncio
    async def test_snippet_includes_foreign_keys(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The LLM must see FK relationships to navigate joins correctly."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "fks",
        })
        snippet = gw.get_context_snippet()
        # FK arrows like "FK → departments.id"
        assert "FK" in snippet
        assert "departments" in snippet
        await gw._action_disconnect({"connection_id": "fks"})

    @pytest.mark.asyncio
    async def test_snippet_includes_unique_indexes(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The LLM should know about unique constraints to avoid duplication."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "uniq",
        })
        snippet = gw.get_context_snippet()
        assert "unique" in snippet.lower()
        await gw._action_disconnect({"connection_id": "uniq"})

    @pytest.mark.asyncio
    async def test_snippet_disappears_after_disconnect(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """After disconnect, the context snippet should be None again."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "temp",
        })
        assert gw.get_context_snippet() is not None

        await gw._action_disconnect({"connection_id": "temp"})
        assert gw.get_context_snippet() is None

    @pytest.mark.asyncio
    async def test_snippet_multiple_connections(
        self, gw: DatabaseGatewayModule, sample_db: str, ecommerce_db: str
    ) -> None:
        """When multiple DBs are connected, ALL schemas appear in the snippet."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "hr",
        })
        await gw._action_connect({
            "driver": "sqlite",
            "database": ecommerce_db,
            "connection_id": "shop",
        })

        snippet = gw.get_context_snippet()
        assert snippet is not None

        # HR DB tables
        assert "departments" in snippet
        assert "employees" in snippet
        assert "projects" in snippet

        # E-commerce DB tables
        assert "customers" in snippet
        assert "products" in snippet
        assert "orders" in snippet
        assert "order_items" in snippet

        # Connection identifiers
        assert "hr" in snippet
        assert "shop" in snippet

        await gw._action_disconnect({"connection_id": "hr"})
        await gw._action_disconnect({"connection_id": "shop"})

    @pytest.mark.asyncio
    async def test_snippet_updates_after_schema_change(
        self, gw: DatabaseGatewayModule, tmp_path: Path
    ) -> None:
        """After adding new tables, refresh should update the context snippet."""
        db_path = str(tmp_path / "evolve.db")

        await gw._action_connect({
            "driver": "sqlite",
            "database": db_path,
            "connection_id": "evolve",
        })

        # Initially empty
        snippet = gw.get_context_snippet()
        assert snippet is None or "users" not in snippet

        # Create a table externally
        engine = sa.create_engine(f"sqlite:///{db_path}")
        with engine.connect() as conn:
            conn.execute(sa.text(
                "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"
            ))
            conn.commit()
        engine.dispose()

        # Refresh introspection
        await gw._action_introspect({
            "connection_id": "evolve",
            "refresh": True,
        })

        snippet = gw.get_context_snippet()
        assert snippet is not None
        assert "users" in snippet

        await gw._action_disconnect({"connection_id": "evolve"})


# ===========================================================================
# B. Schema Text Formatting — Introspector
# ===========================================================================


@pytest.mark.integration
class TestSchemaFormatting:
    """Verify schema_to_context_string() produces LLM-friendly text."""

    def test_basic_table_formatting(self, sample_db: str) -> None:
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        schema = introspect_schema(engine)
        text = schema_to_context_string(schema)
        engine.dispose()

        # Table headers
        assert "#### departments" in text
        assert "#### employees" in text
        assert "#### projects" in text

    def test_pk_markers_in_output(self, sample_db: str) -> None:
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        schema = introspect_schema(engine)
        text = schema_to_context_string(schema)
        engine.dispose()

        assert "(PK" in text

    def test_fk_arrows_in_output(self, sample_db: str) -> None:
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        schema = introspect_schema(engine)
        text = schema_to_context_string(schema)
        engine.dispose()

        # FK → target_table.target_column format
        assert "FK → departments.id" in text

    def test_not_null_markers(self, sample_db: str) -> None:
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        schema = introspect_schema(engine)
        text = schema_to_context_string(schema)
        engine.dispose()

        assert "not null" in text

    def test_unique_index_markers(self, sample_db: str) -> None:
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        schema = introspect_schema(engine)
        text = schema_to_context_string(schema)
        engine.dispose()

        assert "unique" in text

    def test_type_information(self, sample_db: str) -> None:
        engine = sa.create_engine(f"sqlite:///{sample_db}")
        schema = introspect_schema(engine)
        text = schema_to_context_string(schema)
        engine.dispose()

        assert "INTEGER" in text
        assert "TEXT" in text
        assert "REAL" in text

    def test_max_tables_truncation(self) -> None:
        """Large schemas get truncated to avoid prompt bloat."""
        many_tables = {
            "tables": [{"name": f"table_{i}", "columns": [], "indexes": []} for i in range(60)],
            "table_count": 60,
            "schema": "default",
        }
        text = schema_to_context_string(many_tables, max_tables=50)
        assert "... and 10 more tables" in text

    def test_max_columns_truncation(self) -> None:
        """Wide tables get truncated to avoid prompt bloat."""
        wide_table = {
            "tables": [{
                "name": "wide_table",
                "columns": [
                    {"name": f"col_{i}", "type": "TEXT", "nullable": True,
                     "primary_key": False, "autoincrement": False,
                     "default": None, "foreign_keys": []}
                    for i in range(40)
                ],
                "indexes": [],
            }],
            "table_count": 1,
            "schema": "default",
        }
        text = schema_to_context_string(wide_table, max_columns_per_table=30)
        assert "... and 10 more columns" in text

    def test_empty_schema(self) -> None:
        text = schema_to_context_string({"tables": [], "table_count": 0})
        assert "(no tables)" in text

    def test_ecommerce_fk_chain(self, ecommerce_db: str) -> None:
        """E-commerce schema should show the full FK chain:
        order_items → orders → customers, order_items → products."""
        engine = sa.create_engine(f"sqlite:///{ecommerce_db}")
        schema = introspect_schema(engine)
        text = schema_to_context_string(schema)
        engine.dispose()

        assert "FK → customers.id" in text
        assert "FK → orders.id" in text
        assert "FK → products.id" in text


# ===========================================================================
# C. SystemPromptGenerator with DB Context Snippets
# ===========================================================================


@pytest.mark.integration
class TestSystemPromptWithDbContext:
    """Verify SystemPromptGenerator includes database context in the prompt."""

    @pytest.mark.asyncio
    async def test_prompt_includes_database_context_section(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The generated prompt must include the '## Database Context' section."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "default",
        })
        snippet = gw.get_context_snippet()
        manifest = gw.get_manifest()

        gen = SystemPromptGenerator(
            manifests=[manifest],
            context_snippets={"db_gateway": snippet},
        )
        prompt = gen.generate()

        assert "## Database Context" in prompt
        assert "departments" in prompt
        assert "employees" in prompt
        await gw._action_disconnect({"connection_id": "default"})

    @pytest.mark.asyncio
    async def test_prompt_includes_db_gateway_actions(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The prompt must list all 12 db_gateway actions."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "default",
        })
        manifest = gw.get_manifest()
        snippet = gw.get_context_snippet()

        gen = SystemPromptGenerator(
            manifests=[manifest],
            context_snippets={"db_gateway": snippet},
        )
        prompt = gen.generate()

        for action_name in [
            "connect", "disconnect", "introspect",
            "find", "find_one", "count", "search",
            "create", "create_many", "update", "delete",
            "aggregate",
        ]:
            assert action_name in prompt, f"Action '{action_name}' missing from prompt"

        await gw._action_disconnect({"connection_id": "default"})

    @pytest.mark.asyncio
    async def test_prompt_includes_filter_syntax(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The prompt must explain the MongoDB-like filter syntax."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "default",
        })
        manifest = gw.get_manifest()

        gen = SystemPromptGenerator(manifests=[manifest])
        prompt = gen.generate()

        # Check filter syntax examples in param descriptions
        assert "$gte" in prompt
        assert "filter" in prompt.lower()
        await gw._action_disconnect({"connection_id": "default"})

    @pytest.mark.asyncio
    async def test_prompt_includes_aggregate_examples(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """Aggregation examples must be in the prompt so the LLM knows how."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "default",
        })
        manifest = gw.get_manifest()

        gen = SystemPromptGenerator(manifests=[manifest])
        prompt = gen.generate()

        assert "group_by" in prompt
        assert "aggregations" in prompt
        await gw._action_disconnect({"connection_id": "default"})

    @pytest.mark.asyncio
    async def test_prompt_schema_has_fk_info(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The schema in the prompt must show FK relationships."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "default",
        })
        snippet = gw.get_context_snippet()
        manifest = gw.get_manifest()

        gen = SystemPromptGenerator(
            manifests=[manifest],
            context_snippets={"db_gateway": snippet},
        )
        prompt = gen.generate()

        assert "FK → departments.id" in prompt
        await gw._action_disconnect({"connection_id": "default"})

    def test_prompt_without_db_context_has_no_database_section(self) -> None:
        """When no DB is connected, there should be no Database Context section."""
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        snippet = gw.get_context_snippet()

        gen = SystemPromptGenerator(
            manifests=[manifest],
            context_snippets={"db_gateway": snippet} if snippet else {},
        )
        prompt = gen.generate()

        assert "## Database Context" not in prompt

    @pytest.mark.asyncio
    async def test_prompt_multi_db_includes_all_schemas(
        self,
        gw: DatabaseGatewayModule,
        sample_db: str,
        ecommerce_db: str,
    ) -> None:
        """With two databases, the prompt must contain schemas for BOTH."""
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "hr_db",
        })
        await gw._action_connect({
            "driver": "sqlite",
            "database": ecommerce_db,
            "connection_id": "shop_db",
        })
        snippet = gw.get_context_snippet()
        manifest = gw.get_manifest()

        gen = SystemPromptGenerator(
            manifests=[manifest],
            context_snippets={"db_gateway": snippet},
        )
        prompt = gen.generate()

        # HR DB
        assert "employees" in prompt
        assert "departments" in prompt
        # E-commerce DB
        assert "customers" in prompt
        assert "order_items" in prompt
        # Both connection IDs
        assert "hr_db" in prompt
        assert "shop_db" in prompt

        await gw._action_disconnect({"connection_id": "hr_db"})
        await gw._action_disconnect({"connection_id": "shop_db"})


# ===========================================================================
# D. Full API E2E — GET /context with db_gateway
# ===========================================================================


@pytest.mark.integration
class TestContextApiWithDbGateway:
    """Full E2E: create_app with db_gateway → connect → GET /context."""

    @pytest.fixture()
    def app_client(self, tmp_path: Path, sample_db: str):
        """TestClient with db_gateway enabled, connected to sample DB."""
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
            security_advanced={"enable_decorators": False},
        )
        app = create_app(settings=settings)
        with TestClient(app, raise_server_exceptions=True) as c:
            # Connect to the sample DB via the API
            c.post("/plans", json={
                "plan": {
                    "plan_id": "connect-plan",
                    "protocol_version": "2.0",
                    "description": "Connect to test database",
                    "actions": [{
                        "id": "connect",
                        "module": "db_gateway",
                        "action": "connect",
                        "params": {
                            "driver": "sqlite",
                            "database": sample_db,
                            "connection_id": "test_conn",
                        },
                    }],
                },
                "async_execution": False,
            })
            yield c

    @pytest.fixture()
    def app_client_no_db(self, tmp_path: Path):
        """TestClient with db_gateway enabled but no DB connected."""
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
            security_advanced={"enable_decorators": False},
        )
        app = create_app(settings=settings)
        with TestClient(app, raise_server_exceptions=True) as c:
            yield c

    def test_context_includes_db_schema_when_connected(self, app_client) -> None:
        """GET /context should include schema from connected database."""
        resp = app_client.get("/context")
        assert resp.status_code == 200
        data = resp.json()
        prompt = data["system_prompt"]

        assert "Database Context" in prompt
        assert "departments" in prompt
        assert "employees" in prompt

    def test_context_prompt_format_includes_schema(self, app_client) -> None:
        """GET /context?format=prompt also includes DB schema."""
        resp = app_client.get("/context", params={"format": "prompt"})
        assert resp.status_code == 200
        text = resp.text

        assert "Database Context" in text
        assert "departments" in text
        assert "FK" in text

    def test_context_no_schema_when_not_connected(self, app_client_no_db) -> None:
        """Without active connections, no Database Context section."""
        resp = app_client_no_db.get("/context")
        assert resp.status_code == 200
        data = resp.json()
        prompt = data["system_prompt"]

        assert "Database Context" not in prompt

    def test_context_has_db_gateway_module_listed(self, app_client) -> None:
        """The context response should list db_gateway as a module."""
        data = app_client.get("/context").json()
        module_ids = [m["module_id"] for m in data["modules"]]
        assert "db_gateway" in module_ids

    def test_context_db_gateway_actions_count(self, app_client) -> None:
        """db_gateway should have 12 actions."""
        data = app_client.get("/context").json()
        gw_module = next(m for m in data["modules"] if m["module_id"] == "db_gateway")
        assert gw_module["action_count"] == 12

    def test_prompt_text_has_complete_action_catalog(self, app_client) -> None:
        """Verify all 12 db_gateway actions appear in the prompt text.

        Under local_worker, allowed actions appear as **action**, while
        denied actions (like delete) appear in the 'Denied by current profile' line.
        """
        resp = app_client.get("/context", params={"format": "prompt"})
        text = resp.text

        # Allowed actions under local_worker
        allowed_actions = [
            "connect", "disconnect", "introspect",
            "find", "find_one", "count", "search",
            "create", "create_many", "update",
            "aggregate",
        ]
        for action in allowed_actions:
            assert f"**{action}**" in text, (
                f"Allowed action '{action}' not found in prompt text"
            )

        # delete is denied under local_worker — shown in denied section
        assert "Denied by current profile" in text
        assert "`delete`" in text

    def test_prompt_has_param_schemas_for_find(self, app_client) -> None:
        """The find action should have detailed param schemas."""
        resp = app_client.get("/context", params={"format": "prompt"})
        text = resp.text

        assert "`entity`" in text
        assert "`filter`" in text
        assert "`order_by`" in text
        assert "`limit`" in text

    def test_prompt_has_connect_examples(self, app_client) -> None:
        """The prompt should include connect examples."""
        resp = app_client.get("/context", params={"format": "prompt"})
        text = resp.text

        # Connect to SQLite example
        assert "sqlite" in text.lower()


# ===========================================================================
# E. LLM Readability — End-to-End Context Verification
# ===========================================================================


@pytest.mark.integration
class TestLlmReadability:
    """Verify the LLM gets enough information to autonomously operate."""

    @pytest.mark.asyncio
    async def test_llm_has_enough_for_crud(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """An LLM reading the prompt can construct CRUD plans.

        The prompt must contain:
        - Table names (so the LLM knows 'entity' param value)
        - Column names (so the LLM knows field names for 'data'/'filter')
        - Column types (so the LLM uses correct value types)
        - PK info (so the LLM knows which field to filter by for updates)
        """
        await gw._action_connect({
            "driver": "sqlite",
            "database": sample_db,
            "connection_id": "default",
        })
        snippet = gw.get_context_snippet()
        manifest = gw.get_manifest()

        gen = SystemPromptGenerator(
            manifests=[manifest],
            context_snippets={"db_gateway": snippet},
        )
        prompt = gen.generate()

        # 1. Table names for entity param
        for table in ["departments", "employees", "projects"]:
            assert table in prompt

        # 2. Column names for data/filter
        for col in ["name", "email", "salary", "department_id", "budget", "title", "status"]:
            assert col in prompt

        # 3. Types so LLM knows to use strings vs numbers
        assert "INTEGER" in prompt
        assert "TEXT" in prompt
        assert "REAL" in prompt

        # 4. PK marker so LLM knows 'id' is the primary key
        assert "PK" in prompt

        # 5. Actions available
        assert "create" in prompt
        assert "find" in prompt
        assert "update" in prompt
        assert "delete" in prompt

        # 6. Filter syntax examples
        assert "$gte" in prompt
        assert "filter" in prompt.lower()

        await gw._action_disconnect({"connection_id": "default"})

    @pytest.mark.asyncio
    async def test_llm_has_fk_info_for_joins(
        self, gw: DatabaseGatewayModule, ecommerce_db: str
    ) -> None:
        """An LLM should understand FK relationships to navigate across tables.

        For example:
        - order_items.order_id → orders.id
        - order_items.product_id → products.id
        - orders.customer_id → customers.id
        """
        await gw._action_connect({
            "driver": "sqlite",
            "database": ecommerce_db,
            "connection_id": "default",
        })
        snippet = gw.get_context_snippet()
        manifest = gw.get_manifest()

        gen = SystemPromptGenerator(
            manifests=[manifest],
            context_snippets={"db_gateway": snippet},
        )
        prompt = gen.generate()

        # FK relationships
        assert "FK → customers.id" in prompt
        assert "FK → orders.id" in prompt
        assert "FK → products.id" in prompt

        await gw._action_disconnect({"connection_id": "default"})

    @pytest.mark.asyncio
    async def test_llm_has_connection_id_concept(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The prompt must explain connection_id for multi-database scenarios."""
        manifest = gw.get_manifest()
        gen = SystemPromptGenerator(manifests=[manifest])
        prompt = gen.generate()

        assert "connection_id" in prompt

    @pytest.mark.asyncio
    async def test_prompt_explains_semantic_approach(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The module description should explain entity-based (no SQL) approach."""
        manifest = gw.get_manifest()
        gen = SystemPromptGenerator(manifests=[manifest])
        prompt = gen.generate()

        # Module description should mention semantic/entity-based approach
        assert "entity" in prompt.lower()
        # Should mention MongoDB-like filters
        assert "mongodb" in prompt.lower() or "mongo" in prompt.lower()

    @pytest.mark.asyncio
    async def test_prompt_iml_protocol_complete(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The IML Protocol section must be present so the LLM can
        construct valid plan JSON structures."""
        manifest = gw.get_manifest()
        gen = SystemPromptGenerator(manifests=[manifest])
        prompt = gen.generate()

        # IML plan structure
        assert "plan_id" in prompt
        assert "protocol_version" in prompt
        assert '"2.0"' in prompt
        assert "actions" in prompt
        assert "depends_on" in prompt
        assert "{{result." in prompt

    @pytest.mark.asyncio
    async def test_prompt_has_error_handling_guidance(
        self, gw: DatabaseGatewayModule, sample_db: str
    ) -> None:
        """The LLM should know about on_error and retry options."""
        manifest = gw.get_manifest()
        gen = SystemPromptGenerator(manifests=[manifest])
        prompt = gen.generate()

        assert "on_error" in prompt
        assert "retry" in prompt.lower()


# ===========================================================================
# F. Manifest Quality — db_gateway action specs
# ===========================================================================


@pytest.mark.integration
class TestManifestQuality:
    """Verify the db_gateway manifest has rich, complete action specs."""

    def test_manifest_has_12_actions(self) -> None:
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        assert len(manifest.actions) == 12

    def test_all_actions_have_descriptions(self) -> None:
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        for action in manifest.actions:
            assert action.description, f"Action '{action.name}' has no description"
            assert len(action.description) > 10, (
                f"Action '{action.name}' description is too short"
            )

    def test_all_actions_have_permissions(self) -> None:
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        for action in manifest.actions:
            assert action.permission_required in [
                "readonly", "local_worker", "power_user", "unrestricted"
            ], f"Action '{action.name}' has invalid permission"

    def test_read_actions_are_readonly(self) -> None:
        """Read-only actions should require only 'readonly' permission."""
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        readonly_actions = {"introspect", "find", "find_one", "count", "aggregate", "search"}
        for action in manifest.actions:
            if action.name in readonly_actions:
                assert action.permission_required == "readonly", (
                    f"Read action '{action.name}' should be 'readonly', "
                    f"got '{action.permission_required}'"
                )

    def test_write_actions_require_worker(self) -> None:
        """Write actions should require at least 'local_worker' permission."""
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        write_actions = {"create", "create_many", "update", "connect", "disconnect"}
        for action in manifest.actions:
            if action.name in write_actions:
                assert action.permission_required in {"local_worker", "power_user"}, (
                    f"Write action '{action.name}' should require at least local_worker"
                )

    def test_delete_requires_power_user(self) -> None:
        """Delete is destructive and should require power_user."""
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        delete_action = next(a for a in manifest.actions if a.name == "delete")
        assert delete_action.permission_required == "power_user"

    def test_connect_has_examples(self) -> None:
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        connect_action = next(a for a in manifest.actions if a.name == "connect")
        assert len(connect_action.examples) > 0

    def test_find_has_examples(self) -> None:
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        find_action = next(a for a in manifest.actions if a.name == "find")
        assert len(find_action.examples) > 0

    def test_aggregate_has_examples(self) -> None:
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        agg_action = next(a for a in manifest.actions if a.name == "aggregate")
        assert len(agg_action.examples) > 0

    def test_module_description_mentions_extensibility(self) -> None:
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        desc = manifest.description.lower()
        assert "extensible" in desc or "custom" in desc

    def test_module_tags_include_database_types(self) -> None:
        gw = DatabaseGatewayModule()
        manifest = gw.get_manifest()
        tags = manifest.tags
        assert "database" in tags
        assert "sqlite" in tags
        assert "postgresql" in tags
        assert "mysql" in tags
