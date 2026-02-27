"""Unit tests — Negotiation Protocol (_suggest_alternatives + enriched errors)."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
import pytest_asyncio

from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.registry import ModuleRegistry
from llmos_bridge.orchestration.executor import PlanExecutor
from llmos_bridge.orchestration.state import ActionState, ActionStatus, PlanStateStore
from llmos_bridge.security.audit import AuditLogger
from llmos_bridge.security.guard import PermissionGuard
from llmos_bridge.security.profiles import PermissionProfile, get_profile_config


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture
async def state_store(tmp_path: Path):
    store = PlanStateStore(tmp_path / "state.db")
    await store.init()
    yield store
    await store.close()


@pytest.fixture
def audit_logger() -> AuditLogger:
    bus = MagicMock()
    bus.emit = AsyncMock()
    return AuditLogger(bus=bus)


@pytest.fixture
def guard() -> PermissionGuard:
    return PermissionGuard(
        profile=get_profile_config(PermissionProfile.UNRESTRICTED)
    )


@pytest.fixture
def registry() -> ModuleRegistry:
    reg = ModuleRegistry()
    reg.register(FilesystemModule)
    return reg


def _make_executor(
    registry, guard, state_store, audit_logger,
    fallbacks: dict[str, list[str]] | None = None,
) -> PlanExecutor:
    return PlanExecutor(
        module_registry=registry,
        guard=guard,
        state_store=state_store,
        audit_logger=audit_logger,
        fallback_chains=fallbacks or {},
    )


# ---------------------------------------------------------------------------
# Tests — _suggest_alternatives
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestSuggestAlternatives:
    """Test the Negotiation Protocol alternative suggestions."""

    def test_no_alternatives_when_no_fallbacks(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        executor = _make_executor(registry, guard, state_store, audit_logger)
        alts = executor._suggest_alternatives("excel", "read_cell", "some error")
        assert alts == []

    def test_suggests_fallback_module_with_matching_action(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """If a fallback module has the same action name, suggest it."""
        executor = _make_executor(
            registry, guard, state_store, audit_logger,
            fallbacks={"filesystem": ["filesystem"]},  # self-fallback for testing
        )
        alts = executor._suggest_alternatives("filesystem", "read_file", "some error")
        assert any("filesystem.read_file" in a for a in alts)

    def test_no_suggestion_when_fallback_lacks_action(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """If the fallback module doesn't have the action, don't suggest it."""
        executor = _make_executor(
            registry, guard, state_store, audit_logger,
            fallbacks={"filesystem": ["filesystem"]},
        )
        alts = executor._suggest_alternatives(
            "filesystem", "nonexistent_action", "some error"
        )
        assert not any("filesystem.nonexistent_action" in a for a in alts)

    def test_not_found_hint(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        executor = _make_executor(registry, guard, state_store, audit_logger)
        alts = executor._suggest_alternatives("fs", "read", "File not found: /x.txt")
        assert any("file path exists" in a.lower() for a in alts)

    def test_no_such_file_hint(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        executor = _make_executor(registry, guard, state_store, audit_logger)
        alts = executor._suggest_alternatives("fs", "read", "No such file or directory")
        assert any("file path exists" in a.lower() for a in alts)

    def test_permission_denied_hint(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        executor = _make_executor(registry, guard, state_store, audit_logger)
        alts = executor._suggest_alternatives("fs", "write", "Permission denied: /root/x")
        assert any("permission" in a.lower() for a in alts)

    def test_timeout_hint(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        executor = _make_executor(registry, guard, state_store, audit_logger)
        alts = executor._suggest_alternatives("api", "call", "Request timeout after 30s")
        assert any("timeout" in a.lower() for a in alts)

    def test_multiple_hints_combined(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """Multiple patterns can match at once."""
        executor = _make_executor(
            registry, guard, state_store, audit_logger,
            fallbacks={"filesystem": ["filesystem"]},
        )
        alts = executor._suggest_alternatives(
            "filesystem", "read_file", "File not found: /x.txt"
        )
        # Should have both fallback suggestion and "verify path" hint
        assert len(alts) >= 2

    def test_unrecognized_error_no_hints(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """Generic errors with no matching patterns → no hints."""
        executor = _make_executor(registry, guard, state_store, audit_logger)
        alts = executor._suggest_alternatives("fs", "act", "Unexpected internal error")
        assert alts == []


# ---------------------------------------------------------------------------
# Tests — _fail_action enrichment
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFailActionEnrichment:
    """Verify _fail_action stores structured alternatives on ActionState."""

    @pytest.mark.asyncio
    async def test_fail_action_stores_structured_alternatives(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        executor = _make_executor(registry, guard, state_store, audit_logger)
        action_state = ActionState(action_id="a1")

        await executor._fail_action(
            plan_id="p1",
            action_id="a1",
            action_state=action_state,
            error="File not found: /missing.txt",
            module_id="filesystem",
            action_name="read_file",
        )

        assert action_state.status == ActionStatus.FAILED
        assert action_state.error == "File not found: /missing.txt"
        # Alternatives are stored as structured list, NOT embedded in error
        assert len(action_state.alternatives) > 0
        assert any("file path" in a.lower() for a in action_state.alternatives)

    @pytest.mark.asyncio
    async def test_fail_action_no_alternatives(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """When there are no alternatives, the list stays empty."""
        executor = _make_executor(registry, guard, state_store, audit_logger)
        action_state = ActionState(action_id="a1")

        await executor._fail_action(
            plan_id="p1",
            action_id="a1",
            action_state=action_state,
            error="Something unexpected happened",
            module_id="filesystem",
            action_name="read_file",
        )

        assert action_state.error == "Something unexpected happened"
        assert action_state.alternatives == []

    @pytest.mark.asyncio
    async def test_fail_action_without_module_context(
        self, registry, guard, state_store, audit_logger
    ) -> None:
        """_fail_action still works with empty module_id/action_name."""
        executor = _make_executor(registry, guard, state_store, audit_logger)
        action_state = ActionState(action_id="a1")

        await executor._fail_action(
            plan_id="p1",
            action_id="a1",
            action_state=action_state,
            error="Generic error",
        )

        assert action_state.status == ActionStatus.FAILED
        assert action_state.error == "Generic error"
