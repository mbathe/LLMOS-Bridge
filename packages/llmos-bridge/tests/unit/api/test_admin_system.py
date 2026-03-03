"""Unit tests — Admin system REST API endpoints."""

from __future__ import annotations

from collections import deque
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.admin_system import router


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_module_manager():
    mm = AsyncMock()
    mm._action_get_system_status = AsyncMock(return_value={"status": "ok", "modules": 5})
    mm._action_list_services = AsyncMock(return_value={"services": [], "count": 0})
    return mm


@pytest.fixture
def mock_registry(mock_module_manager):
    registry = MagicMock()
    registry.is_available.return_value = True
    registry.get.return_value = mock_module_manager
    return registry


@pytest.fixture
def admin_app(mock_registry) -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    # Settings with an api_token to test redaction.
    settings_mock = MagicMock()
    settings_mock.security.api_token = None
    settings_mock.model_dump.return_value = {
        "security": {"api_token": "my_secret_token_value", "profile": "local_worker"},
        "server": {"host": "127.0.0.1", "port": 9090},
    }
    app.state.settings = settings_mock
    app.state.module_registry = mock_registry
    # Mock lifecycle_manager with event bus ring buffer.
    bus_mock = MagicMock()
    bus_mock._recent_events = deque([
        {"_topic": "llmos.plans", "event": "plan_started", "_timestamp": 1.0},
        {"_topic": "llmos.actions", "event": "action_done", "_timestamp": 2.0},
    ])
    lm_mock = MagicMock()
    lm_mock._event_bus = bus_mock
    app.state.lifecycle_manager = lm_mock
    # Mock executor with policy enforcer.
    pe_mock = MagicMock()
    pe_mock.status.return_value = {"fs": {"cooldown": 0.0}, "os_exec": {"cooldown": 1.0}}
    executor_mock = MagicMock()
    executor_mock._policy_enforcer = pe_mock
    app.state.plan_executor = executor_mock
    return app


@pytest.fixture
def client(admin_app) -> TestClient:
    return TestClient(admin_app)


# ---------------------------------------------------------------------------
# Tests — GET /admin/system/status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetSystemStatus:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/admin/system/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["modules"] == 5

    def test_delegates_to_module_manager(
        self, client: TestClient, mock_module_manager: AsyncMock
    ) -> None:
        client.get("/admin/system/status")
        mock_module_manager._action_get_system_status.assert_awaited_once_with(
            {"include_health": True}
        )

    def test_503_when_module_manager_not_available(
        self, admin_app: FastAPI, mock_registry: MagicMock
    ) -> None:
        mock_registry.is_available.return_value = False
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/status")
        assert resp.status_code == 200
        body = resp.json()
        assert "error" in body
        assert "not available" in body["error"]

    def test_registry_queried_for_module_manager(
        self, client: TestClient, mock_registry: MagicMock
    ) -> None:
        client.get("/admin/system/status")
        mock_registry.is_available.assert_called_with("module_manager")
        mock_registry.get.assert_called_with("module_manager")


# ---------------------------------------------------------------------------
# Tests — GET /admin/system/services
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListServices:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/admin/system/services")
        assert resp.status_code == 200
        body = resp.json()
        assert body["services"] == []
        assert body["count"] == 0

    def test_delegates_to_module_manager(
        self, client: TestClient, mock_module_manager: AsyncMock
    ) -> None:
        client.get("/admin/system/services")
        mock_module_manager._action_list_services.assert_awaited_once_with({})

    def test_returns_error_when_module_manager_unavailable(
        self, admin_app: FastAPI, mock_registry: MagicMock
    ) -> None:
        mock_registry.is_available.return_value = False
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/services")
        assert resp.status_code == 200
        body = resp.json()
        assert body["services"] == []
        assert "error" in body

    def test_services_with_populated_list(
        self, admin_app: FastAPI, mock_registry: MagicMock
    ) -> None:
        mm = AsyncMock()
        mm._action_list_services = AsyncMock(return_value={
            "services": [{"name": "vision"}, {"name": "audio"}],
            "count": 2,
        })
        mock_registry.get.return_value = mm
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/services")
        body = resp.json()
        assert body["count"] == 2
        assert len(body["services"]) == 2


# ---------------------------------------------------------------------------
# Tests — GET /admin/system/config
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetConfig:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/admin/system/config")
        assert resp.status_code == 200

    def test_redacts_long_api_token(self, client: TestClient) -> None:
        resp = client.get("/admin/system/config")
        body = resp.json()
        token = body["security"]["api_token"]
        assert token == "my_s***"
        assert "my_secret_token_value" not in str(body)

    def test_no_api_token_returns_none(self, admin_app: FastAPI) -> None:
        settings_mock = MagicMock()
        settings_mock.security.api_token = None
        settings_mock.model_dump.return_value = {
            "security": {"api_token": None, "profile": "readonly"},
            "server": {"host": "0.0.0.0", "port": 8080},
        }
        admin_app.state.settings = settings_mock
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/config")
        body = resp.json()
        assert body["security"]["api_token"] is None

    def test_short_api_token_fully_redacted(self, admin_app: FastAPI) -> None:
        settings_mock = MagicMock()
        settings_mock.security.api_token = None
        settings_mock.model_dump.return_value = {
            "security": {"api_token": "abc", "profile": "readonly"},
            "server": {"host": "0.0.0.0", "port": 8080},
        }
        admin_app.state.settings = settings_mock
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/config")
        body = resp.json()
        assert body["security"]["api_token"] == "***"

    def test_exactly_four_char_token_fully_redacted(self, admin_app: FastAPI) -> None:
        settings_mock = MagicMock()
        settings_mock.security.api_token = None
        settings_mock.model_dump.return_value = {
            "security": {"api_token": "abcd", "profile": "readonly"},
            "server": {"host": "0.0.0.0", "port": 8080},
        }
        admin_app.state.settings = settings_mock
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/config")
        body = resp.json()
        # len("abcd") == 4, NOT > 4, so it should be fully redacted.
        assert body["security"]["api_token"] == "***"

    def test_config_preserves_non_sensitive_fields(self, client: TestClient) -> None:
        resp = client.get("/admin/system/config")
        body = resp.json()
        assert body["server"]["host"] == "127.0.0.1"
        assert body["server"]["port"] == 9090


# ---------------------------------------------------------------------------
# Tests — GET /admin/system/events
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetEvents:
    def test_returns_recent_events(self, client: TestClient) -> None:
        resp = client.get("/admin/system/events")
        assert resp.status_code == 200
        body = resp.json()
        assert body["count"] == 2
        assert len(body["events"]) == 2

    def test_topic_filter(self, client: TestClient) -> None:
        resp = client.get("/admin/system/events", params={"topic": "llmos.plans"})
        body = resp.json()
        assert body["count"] == 1
        assert body["events"][0]["event"] == "plan_started"

    def test_topic_filter_no_match(self, client: TestClient) -> None:
        resp = client.get("/admin/system/events", params={"topic": "llmos.nonexistent"})
        body = resp.json()
        assert body["count"] == 0
        assert body["events"] == []

    def test_limit(self, client: TestClient) -> None:
        resp = client.get("/admin/system/events", params={"limit": 1})
        body = resp.json()
        # Limit takes the last N events from the list.
        assert body["count"] == 1
        assert body["events"][0]["event"] == "action_done"

    def test_no_lifecycle_manager(self, admin_app: FastAPI) -> None:
        del admin_app.state.lifecycle_manager
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/events")
        body = resp.json()
        assert body["events"] == []
        assert body["count"] == 0

    def test_no_event_bus(self, admin_app: FastAPI) -> None:
        admin_app.state.lifecycle_manager = MagicMock(spec=[])
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/events")
        body = resp.json()
        assert body["events"] == []
        assert body["count"] == 0

    def test_default_limit_is_50(self, admin_app: FastAPI) -> None:
        """When more than 50 events exist and no limit is given, default caps at 50."""
        bus_mock = MagicMock()
        bus_mock._recent_events = deque(
            [{"_topic": "llmos.plans", "event": f"evt_{i}", "_timestamp": float(i)}
             for i in range(100)]
        )
        admin_app.state.lifecycle_manager._event_bus = bus_mock
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/events")
        body = resp.json()
        assert body["count"] == 50


# ---------------------------------------------------------------------------
# Tests — GET /admin/system/policies
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetPolicies:
    def test_returns_200(self, client: TestClient) -> None:
        resp = client.get("/admin/system/policies")
        assert resp.status_code == 200
        body = resp.json()
        assert "policies" in body
        assert body["policies"]["fs"]["cooldown"] == 0.0
        assert body["policies"]["os_exec"]["cooldown"] == 1.0

    def test_no_executor(self, admin_app: FastAPI) -> None:
        del admin_app.state.plan_executor
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/policies")
        body = resp.json()
        assert body["policies"] == {}
        assert "error" in body

    def test_no_policy_enforcer(self, admin_app: FastAPI) -> None:
        admin_app.state.plan_executor = MagicMock(spec=[])
        tc = TestClient(admin_app)
        resp = tc.get("/admin/system/policies")
        body = resp.json()
        assert body["policies"] == {}
        assert "note" in body
        assert "not configured" in body["note"]
