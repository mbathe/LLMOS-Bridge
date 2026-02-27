"""Unit tests for BaseModule security integration.

Tests cover:
  - ``_security`` is None by default after ``__init__``
  - ``set_security()`` stores the security manager
  - ``_collect_security_metadata()`` returns empty dict for undecorated module
  - ``_collect_security_metadata()`` returns correct metadata for decorated actions
  - ``execute()`` passes through ``PermissionNotGrantedError``
  - ``execute()`` passes through ``RateLimitExceededError``
  - ``execute()`` wraps other exceptions as ``ActionExecutionError``
  - ``execute()`` dispatches to the correct ``_action_`` handler
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.exceptions import (
    ActionExecutionError,
    PermissionNotGrantedError,
    RateLimitExceededError,
)
from llmos_bridge.modules.base import BaseModule
from llmos_bridge.modules.manifest import ModuleManifest
from llmos_bridge.security.decorators import requires_permission, sensitive_action
from llmos_bridge.security.models import RiskLevel


# ---------------------------------------------------------------------------
# Concrete test modules
# ---------------------------------------------------------------------------


class DummyModule(BaseModule):
    """Module with decorated and undecorated actions for testing."""

    MODULE_ID = "dummy"
    VERSION = "1.0.0"

    @requires_permission("filesystem.write", reason="Test reason")
    @sensitive_action(RiskLevel.HIGH, irreversible=True)
    async def _action_decorated(self, params):
        return {"ok": True}

    async def _action_plain(self, params):
        return {"plain": True}

    async def _action_error(self, params):
        raise ValueError("boom")

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id="dummy", version="1.0.0", description="Test module"
        )


class PlainModule(BaseModule):
    """Module with no decorated actions at all."""

    MODULE_ID = "plain"
    VERSION = "1.0.0"

    async def _action_noop(self, params):
        return {"noop": True}

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(
            module_id="plain", version="1.0.0", description="Plain module"
        )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestBaseModuleSecurity:
    """Tests for BaseModule security integration."""

    # ------------------------------------------------------------------
    # 1. _security is None by default after __init__
    # ------------------------------------------------------------------
    async def test_security_is_none_by_default(self) -> None:
        mod = DummyModule()
        assert mod._security is None

    # ------------------------------------------------------------------
    # 2. set_security() stores the security manager
    # ------------------------------------------------------------------
    async def test_set_security_stores_manager(self) -> None:
        mod = DummyModule()
        mock_sm = MagicMock(name="SecurityManager")
        mod.set_security(mock_sm)
        assert mod._security is mock_sm

    # ------------------------------------------------------------------
    # 3. _collect_security_metadata() returns empty dict for undecorated
    # ------------------------------------------------------------------
    async def test_collect_metadata_empty_for_undecorated_module(self) -> None:
        mod = PlainModule()
        meta = mod._collect_security_metadata()
        assert meta == {}

    # ------------------------------------------------------------------
    # 4. _collect_security_metadata() returns correct metadata for
    #    module with decorated actions
    # ------------------------------------------------------------------
    async def test_collect_metadata_for_decorated_actions(self) -> None:
        mod = DummyModule()
        meta = mod._collect_security_metadata()

        # Only "decorated" should have metadata; "plain" and "error" are bare
        assert "decorated" in meta
        assert "plain" not in meta
        assert "error" not in meta

        decorated_meta = meta["decorated"]
        assert decorated_meta["permissions"] == ["filesystem.write"]
        assert decorated_meta["permission_reason"] == "Test reason"
        assert decorated_meta["risk_level"] == "high"
        assert decorated_meta["irreversible"] is True
        assert decorated_meta["requires_confirmation"] is True

    # ------------------------------------------------------------------
    # 5. execute() passes through PermissionNotGrantedError
    # ------------------------------------------------------------------
    async def test_execute_passes_through_permission_not_granted(self) -> None:
        mod = DummyModule()
        err = PermissionNotGrantedError(
            permission="filesystem.write",
            module_id="dummy",
            action="decorated",
        )

        async def _raise(_params):
            raise err

        with patch.object(mod, "_get_handler", return_value=_raise):
            with pytest.raises(PermissionNotGrantedError) as exc_info:
                await mod.execute("decorated", {})
            assert exc_info.value is err

    # ------------------------------------------------------------------
    # 6. execute() passes through RateLimitExceededError
    # ------------------------------------------------------------------
    async def test_execute_passes_through_rate_limit_exceeded(self) -> None:
        mod = DummyModule()
        err = RateLimitExceededError(
            action_key="dummy.decorated", limit=10, window="minute"
        )

        async def _raise(_params):
            raise err

        with patch.object(mod, "_get_handler", return_value=_raise):
            with pytest.raises(RateLimitExceededError) as exc_info:
                await mod.execute("decorated", {})
            assert exc_info.value is err

    # ------------------------------------------------------------------
    # 7. execute() wraps other exceptions as ActionExecutionError
    # ------------------------------------------------------------------
    async def test_execute_wraps_unexpected_exception(self) -> None:
        mod = DummyModule()

        with pytest.raises(ActionExecutionError) as exc_info:
            await mod.execute("error", {})

        assert exc_info.value.cause.__class__ is ValueError
        assert "boom" in str(exc_info.value.cause)

    # ------------------------------------------------------------------
    # 8. execute() dispatches to the correct _action_ handler
    # ------------------------------------------------------------------
    async def test_execute_dispatches_to_correct_handler(self) -> None:
        mod = DummyModule()

        result_decorated = await mod.execute("decorated", {})
        assert result_decorated == {"ok": True}

        result_plain = await mod.execute("plain", {})
        assert result_plain == {"plain": True}
