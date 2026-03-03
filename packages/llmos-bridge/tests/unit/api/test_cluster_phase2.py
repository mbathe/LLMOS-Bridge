"""Unit tests — Cluster API Phase 2 endpoints.

Tests cover the 5 new endpoints:
- GET /nodes/{node_id}
- POST /nodes
- DELETE /nodes/{node_id}
- POST /nodes/{node_id}/heartbeat
- GET /cluster/health
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.cluster import router
from llmos_bridge.orchestration.nodes import BaseNode, LocalNode, NodeRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(mode: str = "standalone") -> MagicMock:
    settings = MagicMock()
    settings.node.cluster_name = "test-cluster"
    settings.node.cluster_id = ""
    settings.node.node_id = "local"
    settings.node.mode = mode
    settings.node.location = "localhost"
    settings.identity.enabled = False
    settings.security.api_token = None
    return settings


def _make_module_registry() -> MagicMock:
    module = MagicMock()
    module.execute = AsyncMock(return_value={})
    registry = MagicMock()
    registry.get.return_value = module
    return registry


def _make_remote_node(
    node_id: str, available: bool = True, capabilities: list | None = None
) -> MagicMock:
    from llmos_bridge.orchestration.remote_node import RemoteNode

    node = MagicMock(spec=RemoteNode)
    node.node_id = node_id
    node.is_available.return_value = available
    node._location = "remote-host"
    node._capabilities = capabilities or ["filesystem"]
    node._last_heartbeat = 1000.0
    node._base_url = f"http://{node_id}:40000"
    node.heartbeat = AsyncMock(return_value={"status": "ok" if available else "error"})
    return node


# ---------------------------------------------------------------------------
# GET /nodes/{node_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetNode:
    def test_get_local_node_standalone(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings()
        app.state.identity_store = None
        app.state.node_registry = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        client = TestClient(app)
        resp = client.get("/nodes/local")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "local"
        assert data["is_local"] is True

    def test_get_node_not_found_standalone(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings()
        app.state.identity_store = None
        app.state.node_registry = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        client = TestClient(app)
        resp = client.get("/nodes/nonexistent")
        assert resp.status_code == 404

    def test_get_remote_node(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        registry = NodeRegistry(LocalNode(_make_module_registry()))
        remote = _make_remote_node("node-1")
        registry.register(remote)
        app.state.node_registry = registry

        client = TestClient(app)
        resp = client.get("/nodes/node-1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "node-1"
        assert data["is_local"] is False
        assert data["available"] is True

    def test_get_node_not_found_with_registry(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        registry = NodeRegistry(LocalNode(_make_module_registry()))
        app.state.node_registry = registry

        client = TestClient(app)
        resp = client.get("/nodes/nonexistent")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /nodes
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterNode:
    def test_register_requires_non_standalone(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings()
        app.state.identity_store = None
        app.state.node_registry = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        client = TestClient(app)
        resp = client.post(
            "/nodes",
            json={"node_id": "node-1", "url": "http://host:40000"},
        )
        assert resp.status_code == 400

    def test_register_node_success(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.node_health_monitor = None

        registry = NodeRegistry(LocalNode(_make_module_registry()))
        app.state.node_registry = registry

        mock_node = _make_remote_node("new-node")
        discovery = MagicMock()
        discovery.register_node = AsyncMock(return_value=mock_node)
        app.state.discovery = discovery

        client = TestClient(app)
        resp = client.post(
            "/nodes",
            json={"node_id": "new-node", "url": "http://host:40000"},
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["node_id"] == "new-node"

    def test_register_duplicate_node(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.node_health_monitor = None

        registry = NodeRegistry(LocalNode(_make_module_registry()))
        existing = _make_remote_node("dup")
        registry.register(existing)
        app.state.node_registry = registry

        discovery = MagicMock()
        app.state.discovery = discovery

        client = TestClient(app)
        resp = client.post(
            "/nodes",
            json={"node_id": "dup", "url": "http://host:40000"},
        )
        assert resp.status_code == 409


# ---------------------------------------------------------------------------
# DELETE /nodes/{node_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestUnregisterNode:
    def test_unregister_requires_non_standalone(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings()
        app.state.identity_store = None
        app.state.node_registry = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        client = TestClient(app)
        resp = client.delete("/nodes/node-1")
        assert resp.status_code == 400

    def test_unregister_local_rejected(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.node_health_monitor = None
        app.state.node_registry = None

        discovery = MagicMock()
        app.state.discovery = discovery

        client = TestClient(app)
        resp = client.delete("/nodes/local")
        assert resp.status_code == 400

    def test_unregister_node_success(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.node_health_monitor = None
        app.state.node_registry = None

        discovery = MagicMock()
        discovery.unregister_node = AsyncMock(return_value=True)
        app.state.discovery = discovery

        client = TestClient(app)
        resp = client.delete("/nodes/node-1")
        assert resp.status_code == 200
        assert "unregistered" in resp.json()["detail"]

    def test_unregister_node_not_found(self) -> None:
        from llmos_bridge.exceptions import NodeNotFoundError

        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.node_health_monitor = None
        app.state.node_registry = None

        discovery = MagicMock()
        discovery.unregister_node = AsyncMock(side_effect=NodeNotFoundError("nope"))
        app.state.discovery = discovery

        client = TestClient(app)
        resp = client.delete("/nodes/nope")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /nodes/{node_id}/heartbeat
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTriggerHeartbeat:
    def test_heartbeat_requires_registry(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings()
        app.state.identity_store = None
        app.state.node_registry = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        client = TestClient(app)
        resp = client.post("/nodes/node-1/heartbeat")
        assert resp.status_code == 400

    def test_heartbeat_node_not_found(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.discovery = None

        registry = NodeRegistry(LocalNode(_make_module_registry()))
        app.state.node_registry = registry

        health_monitor = MagicMock()
        app.state.node_health_monitor = health_monitor

        client = TestClient(app)
        resp = client.post("/nodes/nonexistent/heartbeat")
        assert resp.status_code == 404

    def test_heartbeat_success_with_monitor(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.discovery = None

        registry = NodeRegistry(LocalNode(_make_module_registry()))
        remote = _make_remote_node("node-1")
        registry.register(remote)
        app.state.node_registry = registry

        health_monitor = MagicMock()
        health_monitor.check_node = AsyncMock(return_value={"status": "ok"})
        app.state.node_health_monitor = health_monitor

        client = TestClient(app)
        resp = client.post("/nodes/node-1/heartbeat")
        assert resp.status_code == 200
        data = resp.json()
        assert data["node_id"] == "node-1"
        assert data["health"]["status"] == "ok"


# ---------------------------------------------------------------------------
# GET /cluster/health
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestClusterHealth:
    def test_cluster_health_standalone(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings()
        app.state.identity_store = None
        app.state.node_registry = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        client = TestClient(app)
        resp = client.get("/cluster/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_nodes"] == 1
        assert data["available_nodes"] == 1
        assert data["unavailable_nodes"] == 0
        assert len(data["nodes"]) == 1

    def test_cluster_health_with_remote_nodes(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None
        app.state.discovery = None
        app.state.node_health_monitor = None

        registry = NodeRegistry(LocalNode(_make_module_registry()))
        healthy = _make_remote_node("node-1", available=True)
        unhealthy = _make_remote_node("node-2", available=False)
        registry.register(healthy)
        registry.register(unhealthy)
        app.state.node_registry = registry

        client = TestClient(app)
        resp = client.get("/cluster/health")
        data = resp.json()
        assert data["total_nodes"] == 3  # local + 2 remote
        assert data["available_nodes"] == 2  # local + node-1
        assert data["unavailable_nodes"] == 1  # node-2
