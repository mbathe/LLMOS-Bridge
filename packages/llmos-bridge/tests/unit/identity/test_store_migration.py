"""Unit tests — PlanStateStore and PermissionStore app_id migration."""

from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from llmos_bridge.orchestration.state import PlanStateStore, ExecutionState
from llmos_bridge.protocol.models import ActionStatus, IMLAction, IMLPlan, PlanStatus
from llmos_bridge.security.permission_store import PermissionStore
from llmos_bridge.security.models import PermissionGrant, PermissionScope


@pytest.mark.unit
class TestPlanStateStoreAppId:
    """Tests for PlanStateStore app_id column."""

    @pytest.fixture
    async def store(self, tmp_path: Path):
        s = PlanStateStore(tmp_path / "state.db")
        await s.init()
        yield s
        await s.close()

    async def test_create_with_default_app_id(self, store: PlanStateStore) -> None:
        plan = IMLPlan(
            plan_id="p1",
            description="test",
            actions=[IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/tmp/x"})],
        )
        state = ExecutionState.from_plan(plan)
        await store.create(state)

        plans = await store.list_plans()
        assert len(plans) == 1
        assert plans[0]["app_id"] == "default"

    async def test_create_with_custom_app_id(self, store: PlanStateStore) -> None:
        plan = IMLPlan(
            plan_id="p2",
            description="test",
            actions=[IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/tmp/x"})],
        )
        state = ExecutionState.from_plan(plan)
        await store.create(state, app_id="myapp")

        plans = await store.list_plans()
        assert len(plans) == 1
        assert plans[0]["app_id"] == "myapp"

    async def test_list_plans_filter_by_app_id(self, store: PlanStateStore) -> None:
        for i, app in enumerate(["app1", "app1", "app2"]):
            plan = IMLPlan(
                plan_id=f"p{i}",
                description="test",
                actions=[IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/tmp/x"})],
            )
            state = ExecutionState.from_plan(plan)
            await store.create(state, app_id=app)

        all_plans = await store.list_plans()
        assert len(all_plans) == 3

        app1_plans = await store.list_plans(app_id="app1")
        assert len(app1_plans) == 2

        app2_plans = await store.list_plans(app_id="app2")
        assert len(app2_plans) == 1

    async def test_list_plans_combined_filters(self, store: PlanStateStore) -> None:
        plan = IMLPlan(
            plan_id="p1",
            description="test",
            actions=[IMLAction(id="a1", action="read_file", module="filesystem", params={"path": "/tmp/x"})],
        )
        state = ExecutionState.from_plan(plan)
        await store.create(state, app_id="myapp")
        await store.update_plan_status("p1", PlanStatus.COMPLETED)

        # Filter by both status and app_id
        completed = await store.list_plans(status=PlanStatus.COMPLETED, app_id="myapp")
        assert len(completed) == 1

        running = await store.list_plans(status=PlanStatus.RUNNING, app_id="myapp")
        assert len(running) == 0

    async def test_migration_adds_app_id_to_existing_db(self, tmp_path: Path) -> None:
        """Simulate a v1 database without app_id, then open with new code."""
        db_path = tmp_path / "legacy.db"
        # Create the old schema (no app_id column).
        conn = await aiosqlite.connect(str(db_path))
        await conn.executescript("""
            CREATE TABLE plans (
                plan_id TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                data TEXT NOT NULL
            );
            CREATE TABLE actions (
                plan_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                status TEXT NOT NULL,
                started_at REAL,
                finished_at REAL,
                result TEXT,
                error TEXT,
                attempt INTEGER NOT NULL DEFAULT 0,
                module TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                PRIMARY KEY (plan_id, action_id)
            );
        """)
        # Insert a legacy plan.
        import time, json
        await conn.execute(
            "INSERT INTO plans VALUES (?, ?, ?, ?, ?)",
            ("legacy-plan", "completed", time.time(), time.time(), json.dumps({})),
        )
        await conn.commit()
        await conn.close()

        # Open with new code — should migrate.
        store = PlanStateStore(db_path)
        await store.init()
        try:
            plans = await store.list_plans()
            assert len(plans) == 1
            assert plans[0]["app_id"] == "default"  # Migration default
        finally:
            await store.close()


@pytest.mark.unit
class TestPermissionStoreAppIdMigration:
    """Tests for PermissionStore app_id migration."""

    async def test_migration_adds_app_id_column(self, tmp_path: Path) -> None:
        """Simulate a legacy PermissionStore DB without app_id."""
        db_path = tmp_path / "legacy_perms.db"
        conn = await aiosqlite.connect(str(db_path))
        await conn.executescript("""
            CREATE TABLE permission_grants (
                permission TEXT NOT NULL,
                module_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'session',
                granted_at REAL NOT NULL,
                granted_by TEXT NOT NULL DEFAULT 'user',
                reason TEXT NOT NULL DEFAULT '',
                expires_at REAL,
                PRIMARY KEY (permission, module_id)
            );
        """)
        import time
        await conn.execute(
            "INSERT INTO permission_grants (permission, module_id, granted_at) VALUES (?, ?, ?)",
            ("filesystem.write", "filesystem", time.time()),
        )
        await conn.commit()
        await conn.close()

        store = PermissionStore(db_path)
        await store.init()
        try:
            grants = await store.get_all()
            assert len(grants) == 0  # Session grants cleared on init
        finally:
            await store.close()

        # Verify column was added.
        conn2 = await aiosqlite.connect(str(db_path))
        async with conn2.execute("PRAGMA table_info(permission_grants)") as cursor:
            columns = {row[1] for row in await cursor.fetchall()}
        await conn2.close()
        assert "app_id" in columns
