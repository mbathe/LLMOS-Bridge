"""Unit tests — Intent Verifier REST API routes.

Tests all five endpoints in llmos_bridge.api.routes.intent_verifier:
  - GET  /intent-verifier/status
  - POST /intent-verifier/verify
  - GET  /intent-verifier/categories
  - POST /intent-verifier/categories
  - DELETE /intent-verifier/categories/{category_id}
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, PropertyMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.dependencies import get_intent_verifier
from llmos_bridge.api.routes.intent_verifier import router
from llmos_bridge.security.intent_verifier import (
    IntentVerifier,
    ThreatType,
    VerificationResult,
    VerificationVerdict,
)
from llmos_bridge.security.threat_categories import (
    ThreatCategory,
    ThreatCategoryRegistry,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_app(intent_verifier=None) -> FastAPI:
    """Build a minimal FastAPI app with the intent-verifier router."""
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_intent_verifier] = lambda: intent_verifier
    return app


def _make_mock_verifier(
    *,
    status_dict: dict | None = None,
    registry: ThreatCategoryRegistry | None = None,
    verify_result: VerificationResult | None = None,
) -> MagicMock:
    """Create a mock IntentVerifier with sensible defaults."""
    verifier = MagicMock(spec=IntentVerifier)

    # status() returns a dict
    verifier.status.return_value = status_dict or {
        "enabled": True,
        "strict": False,
        "model": "test-model",
        "timeout": 30.0,
        "cache_size": 256,
        "cache_entries": 0,
        "has_prompt_composer": False,
        "threat_categories": [],
    }

    # category_registry is a property
    type(verifier).category_registry = PropertyMock(return_value=registry)

    # verify_plan is async
    result = verify_result or VerificationResult(
        verdict=VerificationVerdict.APPROVE,
        reasoning="Plan looks safe.",
    )
    verifier.verify_plan = AsyncMock(return_value=result)

    return verifier


def _valid_plan_payload() -> dict:
    """Return a minimal valid IML plan dict."""
    return {
        "plan": {
            "description": "Test plan",
            "actions": [
                {
                    "id": "a1",
                    "module": "filesystem",
                    "action": "read_file",
                    "params": {"path": "/tmp/test.txt"},
                }
            ],
        }
    }


# ---------------------------------------------------------------------------
# Tests — GET /intent-verifier/status
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestGetStatus:
    """GET /intent-verifier/status"""

    def test_status_when_verifier_is_none(self):
        """When no IntentVerifier is configured, return enabled=False."""
        client = TestClient(_make_app(intent_verifier=None))
        resp = client.get("/intent-verifier/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body["enabled"] is False
        assert "reason" in body

    def test_status_when_verifier_exists(self):
        """When IntentVerifier is configured, return its status dict."""
        expected = {
            "enabled": True,
            "strict": True,
            "model": "claude-test",
            "timeout": 15.0,
            "cache_size": 128,
            "cache_entries": 5,
            "has_prompt_composer": True,
            "threat_categories": [],
        }
        verifier = _make_mock_verifier(status_dict=expected)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.get("/intent-verifier/status")

        assert resp.status_code == 200
        body = resp.json()
        assert body == expected
        verifier.status.assert_called_once()


# ---------------------------------------------------------------------------
# Tests — POST /intent-verifier/verify
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVerifyPlan:
    """POST /intent-verifier/verify"""

    def test_verify_returns_result(self):
        """Valid plan returns the VerificationResult from the verifier."""
        result = VerificationResult(
            verdict=VerificationVerdict.APPROVE,
            risk_level="low",
            reasoning="All clear.",
            recommendations=["none"],
        )
        verifier = _make_mock_verifier(verify_result=result)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.post("/intent-verifier/verify", json=_valid_plan_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "approve"
        assert body["reasoning"] == "All clear."
        verifier.verify_plan.assert_awaited_once()

    def test_verify_when_verifier_is_none(self):
        """When no IntentVerifier is configured, return 503."""
        client = TestClient(_make_app(intent_verifier=None))

        resp = client.post("/intent-verifier/verify", json=_valid_plan_payload())

        assert resp.status_code == 503
        assert "not configured" in resp.json()["detail"].lower()

    def test_verify_with_invalid_plan(self):
        """When the plan JSON is not a valid IMLPlan, return 422."""
        verifier = _make_mock_verifier()
        client = TestClient(_make_app(intent_verifier=verifier))

        # Missing required "description" field in the plan
        resp = client.post(
            "/intent-verifier/verify",
            json={"plan": {"actions": []}},
        )

        assert resp.status_code == 422
        assert "invalid iml plan" in resp.json()["detail"].lower()

    def test_verify_with_reject_verdict(self):
        """Verify that a REJECT verdict is faithfully returned."""
        result = VerificationResult(
            verdict=VerificationVerdict.REJECT,
            risk_level="critical",
            reasoning="Suspicious exfiltration pattern detected.",
        )
        verifier = _make_mock_verifier(verify_result=result)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.post("/intent-verifier/verify", json=_valid_plan_payload())

        assert resp.status_code == 200
        body = resp.json()
        assert body["verdict"] == "reject"
        assert body["risk_level"] == "critical"


# ---------------------------------------------------------------------------
# Tests — GET /intent-verifier/categories
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestListCategories:
    """GET /intent-verifier/categories"""

    def test_categories_when_verifier_is_none(self):
        """When no IntentVerifier is configured, return empty list."""
        client = TestClient(_make_app(intent_verifier=None))

        resp = client.get("/intent-verifier/categories")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_categories_when_registry_is_none(self):
        """When verifier exists but has no registry, return empty list."""
        verifier = _make_mock_verifier(registry=None)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.get("/intent-verifier/categories")

        assert resp.status_code == 200
        assert resp.json() == []

    def test_categories_with_populated_registry(self):
        """When registry has categories, return them as a list of dicts."""
        registry = ThreatCategoryRegistry()
        registry.register(
            ThreatCategory(
                id="prompt_injection",
                name="Prompt Injection",
                description="Detect prompt injection in parameters.",
                threat_type=ThreatType.PROMPT_INJECTION,
                builtin=True,
            )
        )
        registry.register(
            ThreatCategory(
                id="custom_rule",
                name="Custom Rule",
                description="User-defined detection rule.",
                threat_type=ThreatType.CUSTOM,
                builtin=False,
            )
        )
        verifier = _make_mock_verifier(registry=registry)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.get("/intent-verifier/categories")

        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        ids = {cat["id"] for cat in body}
        assert ids == {"prompt_injection", "custom_rule"}


# ---------------------------------------------------------------------------
# Tests — POST /intent-verifier/categories
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegisterCategory:
    """POST /intent-verifier/categories"""

    def test_register_custom_category(self):
        """Registering a custom category returns 201 and the category dict."""
        registry = ThreatCategoryRegistry()
        verifier = _make_mock_verifier(registry=registry)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.post(
            "/intent-verifier/categories",
            json={
                "id": "data_retention",
                "name": "Data Retention Violations",
                "description": "Detect plans that violate data retention policies.",
                "threat_type": "custom",
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["id"] == "data_retention"
        assert body["name"] == "Data Retention Violations"
        assert body["builtin"] is False
        assert body["enabled"] is True
        # Verify it was actually added to the registry
        assert registry.get("data_retention") is not None

    def test_register_when_verifier_is_none(self):
        """When no IntentVerifier is configured, return 503."""
        client = TestClient(_make_app(intent_verifier=None))

        resp = client.post(
            "/intent-verifier/categories",
            json={
                "id": "test",
                "name": "Test",
                "description": "Test category.",
            },
        )

        assert resp.status_code == 503

    def test_register_when_registry_is_none(self):
        """When verifier exists but has no registry, return 503."""
        verifier = _make_mock_verifier(registry=None)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.post(
            "/intent-verifier/categories",
            json={
                "id": "test",
                "name": "Test",
                "description": "Test category.",
            },
        )

        assert resp.status_code == 503
        assert "registry" in resp.json()["detail"].lower()

    def test_register_with_invalid_threat_type_falls_back_to_custom(self):
        """An unknown threat_type string falls back to ThreatType.CUSTOM."""
        registry = ThreatCategoryRegistry()
        verifier = _make_mock_verifier(registry=registry)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.post(
            "/intent-verifier/categories",
            json={
                "id": "weird_rule",
                "name": "Weird Rule",
                "description": "Some detection guidance.",
                "threat_type": "totally_unknown_type",
            },
        )

        assert resp.status_code == 201
        body = resp.json()
        assert body["threat_type"] == "custom"


# ---------------------------------------------------------------------------
# Tests — DELETE /intent-verifier/categories/{category_id}
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRemoveCategory:
    """DELETE /intent-verifier/categories/{category_id}"""

    def test_remove_custom_category(self):
        """Removing a custom (non-builtin) category succeeds."""
        registry = ThreatCategoryRegistry()
        registry.register(
            ThreatCategory(
                id="my_custom",
                name="My Custom",
                description="Custom rule.",
                threat_type=ThreatType.CUSTOM,
                builtin=False,
            )
        )
        verifier = _make_mock_verifier(registry=registry)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.delete("/intent-verifier/categories/my_custom")

        assert resp.status_code == 200
        assert resp.json() == {"removed": "my_custom"}
        # Verify it was actually removed
        assert registry.get("my_custom") is None

    def test_remove_builtin_category_returns_400(self):
        """Attempting to remove a built-in category returns 400."""
        registry = ThreatCategoryRegistry()
        registry.register(
            ThreatCategory(
                id="prompt_injection",
                name="Prompt Injection",
                description="Built-in detection.",
                threat_type=ThreatType.PROMPT_INJECTION,
                builtin=True,
            )
        )
        verifier = _make_mock_verifier(registry=registry)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.delete("/intent-verifier/categories/prompt_injection")

        assert resp.status_code == 400
        assert "built-in" in resp.json()["detail"].lower()
        # Verify it was NOT removed
        assert registry.get("prompt_injection") is not None

    def test_remove_nonexistent_category_returns_404(self):
        """Attempting to remove a category that does not exist returns 404."""
        registry = ThreatCategoryRegistry()
        verifier = _make_mock_verifier(registry=registry)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.delete("/intent-verifier/categories/no_such_thing")

        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_remove_when_verifier_is_none(self):
        """When no IntentVerifier is configured, return 503."""
        client = TestClient(_make_app(intent_verifier=None))

        resp = client.delete("/intent-verifier/categories/anything")

        assert resp.status_code == 503

    def test_remove_when_registry_is_none(self):
        """When verifier exists but has no registry, return 503."""
        verifier = _make_mock_verifier(registry=None)
        client = TestClient(_make_app(intent_verifier=verifier))

        resp = client.delete("/intent-verifier/categories/anything")

        assert resp.status_code == 503
