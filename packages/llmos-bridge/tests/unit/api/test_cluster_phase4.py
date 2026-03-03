"""Unit tests — Cluster API enrichments (Phase 4).

Tests cover:
- NodeResponse with latency_ms, active_actions, quarantined fields
- _node_to_response helper with routing components
- RoutingConfig defaults and validation
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock

import pytest

from llmos_bridge.api.schemas import NodeResponse
from llmos_bridge.config import RoutingConfig


# ---------------------------------------------------------------------------
# RoutingConfig
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRoutingConfig:
    def test_defaults(self) -> None:
        cfg = RoutingConfig()
        assert cfg.strategy == "local_first"
        assert cfg.node_fallback_enabled is True
        assert cfg.max_node_retries == 2
        assert cfg.quarantine_threshold == 3
        assert cfg.quarantine_duration == 60.0
        assert cfg.module_affinity == {}

    def test_custom_values(self) -> None:
        cfg = RoutingConfig(
            strategy="round_robin",
            max_node_retries=5,
            quarantine_threshold=10,
            quarantine_duration=120.0,
            module_affinity={"vision": "gpu-node"},
        )
        assert cfg.strategy == "round_robin"
        assert cfg.max_node_retries == 5
        assert cfg.quarantine_threshold == 10
        assert cfg.quarantine_duration == 120.0
        assert cfg.module_affinity == {"vision": "gpu-node"}

    def test_validation_max_retries_bounds(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RoutingConfig(max_node_retries=-1)
        with pytest.raises(ValidationError):
            RoutingConfig(max_node_retries=11)

    def test_validation_quarantine_threshold_bounds(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RoutingConfig(quarantine_threshold=0)
        with pytest.raises(ValidationError):
            RoutingConfig(quarantine_threshold=21)

    def test_validation_quarantine_duration_bounds(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RoutingConfig(quarantine_duration=1.0)  # min is 5.0
        with pytest.raises(ValidationError):
            RoutingConfig(quarantine_duration=700.0)  # max is 600.0

    def test_strategy_literals(self) -> None:
        for strategy in ("local_first", "round_robin", "least_loaded", "affinity"):
            cfg = RoutingConfig(strategy=strategy)
            assert cfg.strategy == strategy

    def test_invalid_strategy(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            RoutingConfig(strategy="invalid")


# ---------------------------------------------------------------------------
# NodeResponse
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeResponsePhase4:
    def test_new_fields_defaults(self) -> None:
        resp = NodeResponse(node_id="local")
        assert resp.latency_ms is None
        assert resp.active_actions == 0
        assert resp.quarantined is False

    def test_new_fields_populated(self) -> None:
        resp = NodeResponse(
            node_id="remote-1",
            latency_ms=12.5,
            active_actions=3,
            quarantined=True,
        )
        assert resp.latency_ms == 12.5
        assert resp.active_actions == 3
        assert resp.quarantined is True

    def test_serialization(self) -> None:
        resp = NodeResponse(
            node_id="n1",
            latency_ms=5.0,
            active_actions=1,
            quarantined=False,
        )
        data = resp.model_dump()
        assert data["latency_ms"] == 5.0
        assert data["active_actions"] == 1
        assert data["quarantined"] is False

    def test_backward_compat_no_new_fields(self) -> None:
        """Existing code creating NodeResponse without new fields still works."""
        resp = NodeResponse(
            node_id="local",
            url=None,
            location="paris",
            available=True,
            is_local=True,
        )
        assert resp.latency_ms is None
        assert resp.active_actions == 0
        assert resp.quarantined is False


# ---------------------------------------------------------------------------
# _node_to_response helper
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestNodeToResponse:
    def test_with_load_tracker_and_quarantine(self) -> None:
        from llmos_bridge.api.routes.cluster import _node_to_response
        from llmos_bridge.orchestration.routing import (
            ActiveActionCounter,
            NodeQuarantine,
        )

        node = MagicMock()
        type(node).node_id = PropertyMock(return_value="remote-1")
        node.is_available.return_value = True
        node._capabilities = ["vision"]
        node._last_heartbeat = 1000.0
        node._base_url = "http://10.0.0.1:40000"
        node._last_latency_ms = 25.3
        node._location = "lyon"

        config = MagicMock()
        config.node.location = ""

        tracker = ActiveActionCounter()
        tracker.increment("remote-1")
        tracker.increment("remote-1")

        quarantine = NodeQuarantine(threshold=1, duration=60.0)
        quarantine.record_failure("remote-1")

        resp = _node_to_response(
            "remote-1", node, config, is_local=False,
            load_tracker=tracker, quarantine=quarantine,
        )
        assert resp.latency_ms == 25.3
        assert resp.active_actions == 2
        assert resp.quarantined is True

    def test_without_routing_components(self) -> None:
        """Standalone mode — no load_tracker or quarantine."""
        from llmos_bridge.api.routes.cluster import _node_to_response

        node = MagicMock()
        type(node).node_id = PropertyMock(return_value="local")
        node.is_available.return_value = True
        node._capabilities = []
        node._last_heartbeat = None
        node._base_url = None
        node._location = "paris"
        node._last_latency_ms = None

        config = MagicMock()
        config.node.location = "paris"

        resp = _node_to_response("local", node, config, is_local=True)
        assert resp.latency_ms is None
        assert resp.active_actions == 0
        assert resp.quarantined is False

    def test_with_latency_on_node(self) -> None:
        from llmos_bridge.api.routes.cluster import _node_to_response

        node = MagicMock()
        type(node).node_id = PropertyMock(return_value="n1")
        node.is_available.return_value = True
        node._capabilities = ["fs"]
        node._last_heartbeat = 1234.0
        node._base_url = "http://host:4000"
        node._last_latency_ms = 42.0
        node._location = "lyon"

        config = MagicMock()
        config.node.location = ""

        resp = _node_to_response("n1", node, config, is_local=False)
        assert resp.latency_ms == 42.0


# ---------------------------------------------------------------------------
# Settings integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSettingsRouting:
    def test_settings_has_routing_field(self) -> None:
        from llmos_bridge.config import Settings

        s = Settings()
        assert isinstance(s.routing, RoutingConfig)
        assert s.routing.strategy == "local_first"
