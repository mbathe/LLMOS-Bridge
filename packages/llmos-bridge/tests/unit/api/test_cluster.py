"""Unit tests — Cluster REST API endpoints (GET /cluster, GET /nodes)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.routes.cluster import router


def _make_settings(
    cluster_name: str = "test-cluster",
    cluster_id: str = "",
    node_id: str = "node-1",
    mode: str = "standalone",
    identity_enabled: bool = False,
):
    """Create a minimal mock Settings object."""
    settings = MagicMock()
    settings.node.cluster_name = cluster_name
    settings.node.cluster_id = cluster_id
    settings.node.node_id = node_id
    settings.node.mode = mode
    settings.node.location = "localhost"
    settings.identity.enabled = identity_enabled
    settings.security.api_token = None
    return settings


@pytest.fixture
def app() -> FastAPI:
    app = FastAPI()
    app.include_router(router)
    app.state.settings = _make_settings()
    app.state.identity_store = None
    app.state.node_registry = None
    return app


@pytest.fixture
def client(app: FastAPI) -> TestClient:
    return TestClient(app)


@pytest.mark.unit
class TestClusterEndpoint:
    """Tests for GET /cluster."""

    def test_cluster_info_standalone(self, client: TestClient) -> None:
        resp = client.get("/cluster")
        assert resp.status_code == 200
        data = resp.json()
        assert data["cluster_name"] == "test-cluster"
        assert data["node_id"] == "node-1"
        assert data["mode"] == "standalone"
        assert data["app_count"] == 0
        assert data["identity_enabled"] is False
        assert "cluster_id" in data

    def test_cluster_info_with_cluster_id(self) -> None:
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(cluster_id="custom-id")
        app.state.identity_store = None
        app.state.node_registry = None

        client = TestClient(app)
        resp = client.get("/cluster")
        assert resp.json()["cluster_id"] == "custom-id"

    def test_cluster_info_with_identity_store(self, tmp_path: Path) -> None:
        """When identity store is present, app_count reflects stored apps."""
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(identity_enabled=True)
        app.state.node_registry = None

        # Mock the identity store
        store = AsyncMock()
        store.list_applications = AsyncMock(return_value=[MagicMock(), MagicMock()])
        app.state.identity_store = store

        client = TestClient(app)
        resp = client.get("/cluster")
        assert resp.json()["app_count"] == 2


@pytest.mark.unit
class TestNodesEndpoint:
    """Tests for GET /nodes."""

    def test_nodes_standalone_returns_local_only(self, client: TestClient) -> None:
        resp = client.get("/nodes")
        assert resp.status_code == 200
        nodes = resp.json()
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "node-1"
        assert nodes[0]["is_local"] is True
        assert nodes[0]["available"] is True

    def test_nodes_with_registry(self) -> None:
        """When a NodeRegistry is available, list nodes from it."""
        app = FastAPI()
        app.include_router(router)
        app.state.settings = _make_settings(mode="orchestrator")
        app.state.identity_store = None

        # Mock the node registry
        mock_node = MagicMock()
        mock_node.is_available.return_value = True
        mock_node._location = "remote-host"
        mock_node._capabilities = ["filesystem", "os_exec"]
        mock_node._last_heartbeat = None
        mock_node._base_url = "http://remote:40000"

        registry = MagicMock()
        registry.list_nodes.return_value = ["node-1", "remote-1"]
        registry.resolve.return_value = mock_node
        app.state.node_registry = registry

        client = TestClient(app)
        resp = client.get("/nodes")
        assert resp.status_code == 200
        nodes = resp.json()
        assert len(nodes) == 2
