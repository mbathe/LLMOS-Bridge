"""Tests for security gap fixes in DaemonToolExecutor.

Covers:
  Gap 1 — AuthorizationGuard per-action RBAC (async DB lookup)
  Gap 2 — Profile elevation capping (YAML app cannot exceed daemon profile)
  Gap 3 — Sandbox path param coverage (all path-carrying params checked)
  Gap 4 — Auto-grant audit trail (OS permission grants emit audit events)
  + Edge cases: symlinks, traversal, disabled apps, sessions, concurrency,
    multi-permission handlers, cross-app isolation, invalid profiles, etc.
"""

import asyncio
import os
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.apps.daemon_executor import (
    DaemonToolExecutor,
    _current_scope,
    _ExecutionScope,
)
from llmos_bridge.identity.models import Application, IdentityContext, Role, Session


# ─── Shared fixtures ──────────────────────────────────────────────


class FakeModule:
    MODULE_ID = "test_mod"

    def __init__(self, result=None, error=None):
        self._result = result or {"ok": True}
        self._error = error
        self._security = None

    async def execute(self, action, params, context=None):
        if self._error:
            raise self._error
        return self._result


class FakeRegistry:
    def __init__(self, modules=None, manifests=None):
        self._modules = modules or {}
        self._manifests = manifests or []

    def get(self, module_id):
        if module_id not in self._modules:
            raise KeyError(module_id)
        return self._modules[module_id]

    def all_manifests(self):
        return self._manifests


class FakeGuard:
    """Minimal PermissionGuard with a real profile object."""

    def __init__(self, profile_name="local_worker", allowed=True):
        from llmos_bridge.security.profiles import PermissionProfile
        self._allowed = allowed

        class _Profile:
            profile = PermissionProfile(profile_name)
        self._profile = _Profile()

    def is_allowed(self, module_id, action_name):
        return self._allowed

    def check_sandbox_params(self, module, action, params):
        pass


class FakeEventBus:
    def __init__(self):
        self.events = []

    async def emit(self, topic, event):
        self.events.append((topic, event))


# ═══════════════════════════════════════════════════════════════════
# Gap 1: AuthorizationGuard per-action RBAC fires correctly
# ═══════════════════════════════════════════════════════════════════


class FakeIdentityStore:
    """Minimal identity store for testing."""

    def __init__(self, apps=None, sessions=None):
        self._apps = {a.app_id: a for a in (apps or [])}
        self._sessions = {s.session_id: s for s in (sessions or [])}

    async def get_application(self, app_id):
        return self._apps.get(app_id)

    async def get_session(self, session_id):
        return self._sessions.get(session_id)


@pytest.mark.asyncio
async def test_authorization_guard_blocks_disallowed_module():
    """AuthorizationGuard blocks action on a module not in allowed_modules."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(
        app_id="app-1",
        name="restricted-app",
        allowed_modules=["filesystem"],  # Only filesystem allowed
        allowed_actions={},
    )
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="app-1", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        assert "error" in result
        assert "AuthorizationDenied" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_authorization_guard_blocks_disallowed_action():
    """AuthorizationGuard blocks specific action not in allowed_actions."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(
        app_id="app-2",
        name="read-only-app",
        allowed_modules=["filesystem"],
        allowed_actions={"filesystem": ["read_file"]},  # Only read allowed
    )
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="app-2", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        result = await executor.execute("filesystem", "delete_file", {"path": "/x"})
        assert "error" in result
        assert "AuthorizationDenied" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_authorization_guard_allows_permitted_action():
    """AuthorizationGuard allows action that is in the whitelist."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(
        app_id="app-3",
        name="allowed-app",
        allowed_modules=["filesystem"],
        allowed_actions={"filesystem": ["read_file"]},
    )
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"content": "hello"})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="app-3", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        result = await executor.execute("filesystem", "read_file", {"path": "/x"})
        assert result.get("content") == "hello"
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_authorization_guard_skipped_when_no_identity():
    """No identity context = authorization check skipped."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(
        app_id="app-4",
        name="app",
        allowed_modules=["filesystem"],
    )
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    # No identity set — should skip
    result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
    assert result.get("ok") is True


@pytest.mark.asyncio
async def test_authorization_guard_empty_allowlist_permits_all():
    """Empty allowed_modules = all modules permitted."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(
        app_id="app-5",
        name="open-app",
        allowed_modules=[],  # Empty = all allowed
        allowed_actions={},
    )
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="app-5", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_authorization_guard_caches_app_on_scope():
    """Application object is cached on scope after first lookup."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(app_id="app-cache", name="cache-test")
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="app-cache", role=Role.AGENT)
    scope = _ExecutionScope(identity=identity)
    token = _current_scope.set(scope)
    try:
        await executor.execute("fs", "read", {})
        # After first call, _cached_app should be set
        assert scope._cached_app is not None
        assert scope._cached_app.app_id == "app-cache"
    finally:
        _current_scope.reset(token)


# ═══════════════════════════════════════════════════════════════════
# Gap 2: Profile elevation capping
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_profile_elevation_blocked():
    """YAML app requesting unrestricted on a local_worker daemon is capped."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})

    # Daemon runs with local_worker (which denies filesystem.delete_file)
    guard = FakeGuard(profile_name="local_worker", allowed=True)
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=guard)

    # App requests unrestricted profile
    scope = _ExecutionScope(security_profile="unrestricted")
    token = _current_scope.set(scope)
    try:
        # The executor should use daemon's profile (local_worker), not unrestricted
        # We can verify by checking the log or by testing that the guard used is the daemon's
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        # FakeGuard always returns allowed=True, so action passes
        # The important thing is it didn't use unrestricted profile
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_profile_downgrade_allowed():
    """YAML app requesting readonly on a power_user daemon is allowed."""
    from llmos_bridge.security.profiles import PermissionProfile, BUILTIN_PROFILES
    from llmos_bridge.security.guard import PermissionGuard

    # Daemon runs with power_user
    daemon_guard = PermissionGuard(profile=BUILTIN_PROFILES[PermissionProfile.POWER_USER])

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=daemon_guard)

    # App requests readonly profile — should be allowed but restrict actions
    scope = _ExecutionScope(security_profile="readonly")
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        # readonly does NOT allow os_exec.run_command, so should be blocked
        assert "error" in result
        assert "PermissionDeniedError" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_profile_elevation_unrestricted_on_local_worker():
    """Unrestricted YAML app on local_worker daemon stays local_worker."""
    from llmos_bridge.security.profiles import PermissionProfile, BUILTIN_PROFILES
    from llmos_bridge.security.guard import PermissionGuard

    # Daemon runs with local_worker (denies filesystem.delete_file)
    daemon_guard = PermissionGuard(profile=BUILTIN_PROFILES[PermissionProfile.LOCAL_WORKER])

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=daemon_guard)

    # App tries to escalate to unrestricted
    scope = _ExecutionScope(security_profile="unrestricted")
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "delete_file", {"path": "/x"})
        # local_worker denies filesystem.delete_file, escalation should be blocked
        assert "error" in result
        assert "PermissionDeniedError" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_profile_same_level_uses_requested():
    """YAML app requesting same profile as daemon uses that profile."""
    from llmos_bridge.security.profiles import PermissionProfile, BUILTIN_PROFILES
    from llmos_bridge.security.guard import PermissionGuard

    daemon_guard = PermissionGuard(profile=BUILTIN_PROFILES[PermissionProfile.LOCAL_WORKER])

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=daemon_guard)

    # Same profile — should pass through normally
    scope = _ExecutionScope(security_profile="local_worker")
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "read_file", {"path": "/x"})
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


# ═══════════════════════════════════════════════════════════════════
# Gap 3: Sandbox path param coverage
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_sandbox_blocks_path_param():
    """Sandbox blocks 'path' param outside allowed paths."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/project"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "read_file", {"path": "/etc/passwd"})
        assert "error" in result
        assert "outside sandbox" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_blocks_source_param():
    """Sandbox blocks 'source' param outside allowed paths."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/project"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "copy_file", {"source": "/etc/shadow"})
        assert "error" in result
        assert "outside sandbox" in result["error"]
        assert "'source'" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_blocks_destination_param():
    """Sandbox blocks 'destination' param outside allowed paths."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/project"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "copy_file", {
            "source": "/home/user/project/a.txt",
            "destination": "/tmp/evil.txt",
        })
        assert "error" in result
        assert "outside sandbox" in result["error"]
        assert "'destination'" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_blocks_file_path_param():
    """Sandbox blocks 'file_path' param outside allowed paths."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"excel": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/docs"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("excel", "open_workbook", {"file_path": "/etc/hosts"})
        assert "error" in result
        assert "outside sandbox" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_blocks_output_path_param():
    """Sandbox blocks 'output_path' param outside allowed paths."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"word": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/docs"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("word", "export_pdf", {"output_path": "/tmp/leak.pdf"})
        assert "error" in result
        assert "outside sandbox" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_allows_path_inside_sandbox():
    """Sandbox allows paths inside allowed directories."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/project"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "read_file", {
            "path": "/home/user/project/src/main.py",
        })
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_checks_all_path_params_not_just_first():
    """Sandbox checks ALL path params, not just the first one found."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/project"])
    token = _current_scope.set(scope)
    try:
        # 'path' is inside sandbox, but 'destination' is outside
        result = await executor.execute("filesystem", "copy_file", {
            "path": "/home/user/project/a.txt",
            "destination": "/tmp/escape.txt",
        })
        assert "error" in result
        assert "outside sandbox" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_blocked_commands():
    """Sandbox blocks commands matching blocked_commands patterns."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_commands=["rm -rf"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("os_exec", "run_command", {"command": ["rm", "-rf", "/"]})
        assert "error" in result
        assert "blocked by sandbox" in result["error"].lower()
    finally:
        _current_scope.reset(token)


# ═══════════════════════════════════════════════════════════════════
# Gap 4: Auto-grant OS permissions with audit trail
# ═══════════════════════════════════════════════════════════════════


class FakePermissionStore:
    """In-memory permission store."""

    def __init__(self):
        self.grants = {}

    async def is_granted(self, permission, module_id, app_id="default"):
        return (permission, module_id, app_id) in self.grants

    async def grant(self, grant_obj, app_id="default"):
        effective = grant_obj.app_id if grant_obj.app_id != "default" else app_id
        self.grants[(grant_obj.permission, grant_obj.module_id, effective)] = grant_obj


class FakeAuditLogger:
    def __init__(self):
        self.bus = FakeEventBus()


class FakePermissionManager:
    """Minimal PermissionManager for testing auto-grant."""

    def __init__(self, store=None):
        self._store = store or FakePermissionStore()
        self._audit = FakeAuditLogger()

    def _current_app_id(self):
        from llmos_bridge.security.context import get_security_app_id
        return get_security_app_id()

    async def check(self, permission, module_id):
        app_id = self._current_app_id()
        if await self._store.is_granted(permission, module_id, app_id):
            return True
        if app_id != "default":
            return await self._store.is_granted(permission, module_id, "default")
        return False

    def get_risk_level(self, permission):
        from llmos_bridge.security.models import RiskLevel
        return RiskLevel.MEDIUM

    async def _emit_permission_event(self, event_type, **data):
        record = {"event": event_type, **data}
        await self._audit.bus.emit("permissions", record)


class ModuleWithPermissions:
    """Module that has @requires_permission metadata on its actions."""
    MODULE_ID = "os_exec"

    def __init__(self):
        self._security = None

        # Simulate the _required_permissions attribute set by @requires_permission
        async def _action_run_command(self, params):
            return {"ok": True}

        _action_run_command._required_permissions = ["os.process.execute"]
        self._action_run_command = _action_run_command

    async def execute(self, action, params, context=None):
        return {"ok": True}


@pytest.mark.asyncio
async def test_auto_grant_emits_audit_event():
    """Auto-grant from capabilities emits an audit event."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant
    from llmos_bridge.security.context import set_security_app_id

    store = FakePermissionStore()
    pm = FakePermissionManager(store=store)

    module = ModuleWithPermissions()
    module._security = MagicMock()
    module._security.permission_manager = pm

    registry = FakeRegistry(modules={"os_exec": module})
    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="os_exec", actions=["run_command"]),
    ])
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    identity = IdentityContext(app_id="test-app-123")
    scope = _ExecutionScope(identity=identity, capabilities=caps)
    scope_token = _current_scope.set(scope)
    sec_token = set_security_app_id("test-app-123")
    try:
        await executor._auto_grant_permissions_for_app(module, "run_command")

        # Verify the permission was stored
        assert ("os.process.execute", "os_exec", "test-app-123") in store.grants

        # Verify audit event was emitted
        events = pm._audit.bus.events
        assert len(events) == 1
        topic, event = events[0]
        assert topic == "permissions"
        assert event["event"] == "permission_granted"
        assert event["permission"] == "os.process.execute"
        assert event["granted_by"] == "capabilities"
        assert event["app_id"] == "test-app-123"
    finally:
        from llmos_bridge.security.context import _current_app_id
        _current_app_id.reset(sec_token)
        _current_scope.reset(scope_token)


@pytest.mark.asyncio
async def test_auto_grant_skipped_for_default_app():
    """Auto-grant is skipped when app_id is 'default' (already handled by PermissionManager)."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant
    from llmos_bridge.security.context import set_security_app_id

    pm = FakePermissionManager()
    module = ModuleWithPermissions()
    module._security = MagicMock()
    module._security.permission_manager = pm

    registry = FakeRegistry(modules={"os_exec": module})
    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="os_exec", actions=["run_command"]),
    ])
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    scope = _ExecutionScope(capabilities=caps)
    scope_token = _current_scope.set(scope)
    # Default app_id context
    sec_token = set_security_app_id(None)  # Will resolve to "default"
    try:
        await executor._auto_grant_permissions_for_app(module, "run_command")
        # Should not have stored anything (skipped for default)
        assert len(pm._store.grants) == 0
    finally:
        from llmos_bridge.security.context import _current_app_id
        _current_app_id.reset(sec_token)
        _current_scope.reset(scope_token)


@pytest.mark.asyncio
async def test_auto_grant_skipped_without_capabilities():
    """Auto-grant is skipped when no capabilities are set."""
    pm = FakePermissionManager()
    module = ModuleWithPermissions()
    module._security = MagicMock()
    module._security.permission_manager = pm

    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(capabilities=None)
    scope_token = _current_scope.set(scope)
    try:
        await executor._auto_grant_permissions_for_app(module, "run_command")
        assert len(pm._store.grants) == 0
    finally:
        _current_scope.reset(scope_token)


@pytest.mark.asyncio
async def test_auto_grant_no_duplicate_if_already_granted():
    """Auto-grant doesn't re-grant if permission already exists."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant
    from llmos_bridge.security.context import set_security_app_id
    from llmos_bridge.security.models import PermissionGrant, PermissionScope

    store = FakePermissionStore()
    # Pre-grant the permission
    pre_grant = PermissionGrant(
        permission="os.process.execute",
        module_id="os_exec",
        scope=PermissionScope.SESSION,
        app_id="pre-granted-app",
    )
    await store.grant(pre_grant, app_id="pre-granted-app")

    pm = FakePermissionManager(store=store)
    module = ModuleWithPermissions()
    module._security = MagicMock()
    module._security.permission_manager = pm

    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="os_exec", actions=["run_command"]),
    ])
    executor = DaemonToolExecutor(module_registry=FakeRegistry(modules={"os_exec": module}))

    scope = _ExecutionScope(
        identity=IdentityContext(app_id="pre-granted-app"),
        capabilities=caps,
    )
    scope_token = _current_scope.set(scope)
    sec_token = set_security_app_id("pre-granted-app")
    try:
        await executor._auto_grant_permissions_for_app(module, "run_command")
        # No audit event should be emitted (already granted)
        assert len(pm._audit.bus.events) == 0
    finally:
        from llmos_bridge.security.context import _current_app_id
        _current_app_id.reset(sec_token)
        _current_scope.reset(scope_token)


# ═══════════════════════════════════════════════════════════════════
# Gap 1 — Edge cases: disabled apps, unknown apps, sessions
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_authorization_guard_blocks_disabled_app():
    """Disabled application is rejected by AuthorizationGuard."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(
        app_id="disabled-app",
        name="disabled",
        enabled=False,  # Disabled!
    )
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="disabled-app", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        result = await executor.execute("fs", "read", {})
        assert "error" in result
        assert "AuthorizationDenied" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_authorization_guard_unknown_app_passes():
    """Unknown app_id (not in store) silently passes — no crash."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    store = FakeIdentityStore(apps=[])  # No apps in store
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="unknown-app", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        # App not found → _cached_app stays None → check_action_allowed not called → passes
        result = await executor.execute("fs", "read", {})
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_authorization_guard_session_restricts_modules():
    """Session-level module whitelist further restricts app's whitelist."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(
        app_id="sess-app",
        name="session-test",
        allowed_modules=["filesystem", "os_exec"],  # App allows both
    )
    session = Session(
        session_id="sess-1",
        app_id="sess-app",
        allowed_modules=["filesystem"],  # Session restricts to filesystem only
    )
    store = FakeIdentityStore(apps=[app], sessions=[session])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="sess-app", session_id="sess-1", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        assert "error" in result
        assert "AuthorizationDenied" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_authorization_guard_disabled_passes_everything():
    """When AuthorizationGuard.enabled=False, all checks are no-ops."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    app = Application(
        app_id="restricted",
        name="restricted",
        allowed_modules=["filesystem"],  # Only filesystem
    )
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=False)  # DISABLED

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
    )

    identity = IdentityContext(app_id="restricted", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        assert result.get("ok") is True  # Passes because guard is disabled
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_authorization_guard_no_identity_store_passes():
    """No identity_store on executor — authorization check skipped gracefully."""
    from llmos_bridge.identity.authorization import AuthorizationGuard

    guard = AuthorizationGuard(store=None, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=None,  # No store!
    )

    identity = IdentityContext(app_id="some-app", role=Role.AGENT)
    token = _current_scope.set(_ExecutionScope(identity=identity))
    try:
        result = await executor.execute("fs", "read", {})
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


# ═══════════════════════════════════════════════════════════════════
# Gap 2 — Edge cases: all profile pairs, invalid profiles
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_profile_invalid_name_uses_daemon_profile():
    """Invalid profile name in YAML falls back to daemon profile."""
    from llmos_bridge.security.profiles import PermissionProfile, BUILTIN_PROFILES
    from llmos_bridge.security.guard import PermissionGuard

    daemon_guard = PermissionGuard(profile=BUILTIN_PROFILES[PermissionProfile.LOCAL_WORKER])

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=daemon_guard)

    # Invalid profile name
    scope = _ExecutionScope(security_profile="nonexistent_profile")
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "read_file", {"path": "/x"})
        # Should fall back to daemon's local_worker profile, which allows read_file
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_profile_readonly_cannot_escalate_to_power_user():
    """readonly daemon blocks power_user escalation."""
    from llmos_bridge.security.profiles import PermissionProfile, BUILTIN_PROFILES
    from llmos_bridge.security.guard import PermissionGuard

    daemon_guard = PermissionGuard(profile=BUILTIN_PROFILES[PermissionProfile.READONLY])

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=daemon_guard)

    scope = _ExecutionScope(security_profile="power_user")
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        # readonly doesn't allow os_exec.run_command, escalation blocked
        assert "error" in result
        assert "PermissionDeniedError" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_profile_no_security_profile_uses_daemon_default():
    """No security_profile in scope → use daemon's guard directly."""
    from llmos_bridge.security.profiles import PermissionProfile, BUILTIN_PROFILES
    from llmos_bridge.security.guard import PermissionGuard

    daemon_guard = PermissionGuard(profile=BUILTIN_PROFILES[PermissionProfile.LOCAL_WORKER])

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=daemon_guard)

    # No security_profile set
    scope = _ExecutionScope(security_profile=None)
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "read_file", {"path": "/x"})
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_profile_power_user_to_local_worker_blocks_delete():
    """power_user daemon, app requests local_worker — delete_file denied."""
    from llmos_bridge.security.profiles import PermissionProfile, BUILTIN_PROFILES
    from llmos_bridge.security.guard import PermissionGuard

    daemon_guard = PermissionGuard(profile=BUILTIN_PROFILES[PermissionProfile.POWER_USER])

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=daemon_guard)

    # local_worker explicitly denies filesystem.delete_file
    scope = _ExecutionScope(security_profile="local_worker")
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "delete_file", {"path": "/x"})
        assert "error" in result
        assert "PermissionDeniedError" in result["error"]
    finally:
        _current_scope.reset(token)


# ═══════════════════════════════════════════════════════════════════
# Gap 3 — Edge cases: traversal attacks, symlinks, multiple sandbox paths
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_sandbox_blocks_dot_dot_traversal():
    """Sandbox blocks path traversal via '..' components."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/project"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "read_file", {
            "path": "/home/user/project/../../etc/passwd",
        })
        assert "error" in result
        assert "outside sandbox" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_symlink_traversal():
    """Sandbox blocks symlink that resolves outside allowed path."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    # Create a temp directory structure with a symlink pointing outside
    with tempfile.TemporaryDirectory() as sandbox_dir:
        with tempfile.TemporaryDirectory() as outside_dir:
            secret_file = os.path.join(outside_dir, "secret.txt")
            with open(secret_file, "w") as f:
                f.write("secret")
            symlink_path = os.path.join(sandbox_dir, "escape_link")
            os.symlink(outside_dir, symlink_path)

            scope = _ExecutionScope(sandbox_paths=[sandbox_dir])
            token = _current_scope.set(scope)
            try:
                # The symlink is inside sandbox_dir, but resolves to outside_dir
                result = await executor.execute("filesystem", "read_file", {
                    "path": os.path.join(symlink_path, "secret.txt"),
                })
                assert "error" in result
                assert "outside sandbox" in result["error"]
            finally:
                _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_multiple_allowed_paths():
    """Sandbox allows paths in any of the listed sandbox_paths."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=["/home/user/project", "/tmp/workspace"])
    token = _current_scope.set(scope)
    try:
        # Both paths should be allowed
        r1 = await executor.execute("filesystem", "read_file", {"path": "/home/user/project/a.py"})
        assert r1.get("ok") is True

        r2 = await executor.execute("filesystem", "read_file", {"path": "/tmp/workspace/b.py"})
        assert r2.get("ok") is True

        # But /etc is still blocked
        r3 = await executor.execute("filesystem", "read_file", {"path": "/etc/passwd"})
        assert "error" in r3
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_empty_paths_allows_all():
    """Empty sandbox_paths = no restriction on paths."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_paths=[])  # Empty = no sandbox
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("filesystem", "read_file", {"path": "/etc/passwd"})
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_all_14_path_param_keys():
    """Every known path param key is checked against sandbox."""
    _ALL_PATH_KEYS = (
        "path", "file_path", "source", "destination", "output_path",
        "image_path", "theme_path", "screenshot_path", "database",
        "directory", "working_directory", "cwd", "dir", "target_path",
    )
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"test": module})
    executor = DaemonToolExecutor(module_registry=registry)

    for key in _ALL_PATH_KEYS:
        scope = _ExecutionScope(sandbox_paths=["/safe"])
        token = _current_scope.set(scope)
        try:
            result = await executor.execute("test", "action", {key: "/dangerous/path"})
            assert "error" in result, f"Sandbox did not block param '{key}'"
            assert "outside sandbox" in result["error"], f"Wrong error for param '{key}'"
            assert f"'{key}'" in result["error"], f"Error message doesn't mention param '{key}'"
        finally:
            _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_command_as_string():
    """Sandbox blocked_commands works with string commands (not just lists)."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(module_registry=registry)

    scope = _ExecutionScope(sandbox_commands=["sudo"])
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("os_exec", "run_command", {"command": "sudo rm -rf /"})
        assert "error" in result
        assert "blocked by sandbox" in result["error"].lower()
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_sandbox_no_scope_passes():
    """No execution scope = sandbox check passes (no restrictions)."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    # No scope set at all
    result = await executor.execute("filesystem", "read_file", {"path": "/etc/passwd"})
    assert result.get("ok") is True


# ═══════════════════════════════════════════════════════════════════
# Gap 4 — Edge cases: module without security, no handler, multi-perms
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_auto_grant_module_no_security_attr():
    """Auto-grant gracefully handles modules without _security attribute."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant

    module = FakeModule(result={"ok": True})
    # FakeModule has _security = None by default
    assert module._security is None

    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="test_mod", actions=["action"]),
    ])
    executor = DaemonToolExecutor(module_registry=FakeRegistry(modules={"test_mod": module}))

    scope = _ExecutionScope(
        identity=IdentityContext(app_id="app-no-sec"),
        capabilities=caps,
    )
    token = _current_scope.set(scope)
    try:
        # Should not raise
        await executor._auto_grant_permissions_for_app(module, "action")
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_auto_grant_action_without_permissions_metadata():
    """Auto-grant skips actions that don't have @requires_permission."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant
    from llmos_bridge.security.context import set_security_app_id

    pm = FakePermissionManager()

    class ModuleNoPerms:
        MODULE_ID = "simple"
        _security = MagicMock()

        async def _action_do_thing(self, params):
            return {"ok": True}

        async def execute(self, action, params, context=None):
            return {"ok": True}

    module = ModuleNoPerms()
    module._security.permission_manager = pm

    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="simple", actions=["do_thing"]),
    ])
    executor = DaemonToolExecutor(module_registry=FakeRegistry(modules={"simple": module}))

    scope = _ExecutionScope(
        identity=IdentityContext(app_id="app-no-perms"),
        capabilities=caps,
    )
    scope_token = _current_scope.set(scope)
    sec_token = set_security_app_id("app-no-perms")
    try:
        await executor._auto_grant_permissions_for_app(module, "do_thing")
        # No _required_permissions → nothing to grant
        assert len(pm._store.grants) == 0
        assert len(pm._audit.bus.events) == 0
    finally:
        from llmos_bridge.security.context import _current_app_id
        _current_app_id.reset(sec_token)
        _current_scope.reset(scope_token)


@pytest.mark.asyncio
async def test_auto_grant_multiple_permissions_on_one_handler():
    """Auto-grant handles actions requiring multiple permissions."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant
    from llmos_bridge.security.context import set_security_app_id

    store = FakePermissionStore()
    pm = FakePermissionManager(store=store)

    class ModuleMultiPerms:
        MODULE_ID = "dangerous"
        _security = MagicMock()

        async def execute(self, action, params, context=None):
            return {"ok": True}

    module = ModuleMultiPerms()
    module._security.permission_manager = pm
    # Simulate handler with 2 required permissions
    handler = AsyncMock()
    handler._required_permissions = ["os.process.execute", "os.process.kill"]
    module._action_danger = handler

    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="dangerous", actions=["danger"]),
    ])
    executor = DaemonToolExecutor(module_registry=FakeRegistry(modules={"dangerous": module}))

    scope = _ExecutionScope(
        identity=IdentityContext(app_id="multi-perm-app"),
        capabilities=caps,
    )
    scope_token = _current_scope.set(scope)
    sec_token = set_security_app_id("multi-perm-app")
    try:
        await executor._auto_grant_permissions_for_app(module, "danger")
        # Both permissions should be granted
        assert ("os.process.execute", "dangerous", "multi-perm-app") in store.grants
        assert ("os.process.kill", "dangerous", "multi-perm-app") in store.grants
        # Both should emit audit events
        assert len(pm._audit.bus.events) == 2
    finally:
        from llmos_bridge.security.context import _current_app_id
        _current_app_id.reset(sec_token)
        _current_scope.reset(scope_token)


@pytest.mark.asyncio
async def test_auto_grant_cross_app_isolation():
    """Permissions granted for app A don't leak to app B."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant
    from llmos_bridge.security.context import set_security_app_id

    store = FakePermissionStore()
    pm = FakePermissionManager(store=store)

    module = ModuleWithPermissions()
    module._security = MagicMock()
    module._security.permission_manager = pm

    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="os_exec", actions=["run_command"]),
    ])
    executor = DaemonToolExecutor(module_registry=FakeRegistry(modules={"os_exec": module}))

    # Grant for app-A
    scope_a = _ExecutionScope(
        identity=IdentityContext(app_id="app-A"),
        capabilities=caps,
    )
    scope_token = _current_scope.set(scope_a)
    sec_token = set_security_app_id("app-A")
    try:
        await executor._auto_grant_permissions_for_app(module, "run_command")
        assert ("os.process.execute", "os_exec", "app-A") in store.grants
    finally:
        from llmos_bridge.security.context import _current_app_id
        _current_app_id.reset(sec_token)
        _current_scope.reset(scope_token)

    # Verify app-B does NOT have the permission
    assert ("os.process.execute", "os_exec", "app-B") not in store.grants

    # Verify check for app-B fails (can't use app-A's grant)
    sec_token_b = set_security_app_id("app-B")
    try:
        has_perm = await pm.check("os.process.execute", "os_exec")
        assert has_perm is False
    finally:
        _current_app_id.reset(sec_token_b)


@pytest.mark.asyncio
async def test_auto_grant_nonexistent_action_handler():
    """Auto-grant handles action names that don't have a handler method."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant
    from llmos_bridge.security.context import set_security_app_id

    pm = FakePermissionManager()
    module = ModuleWithPermissions()
    module._security = MagicMock()
    module._security.permission_manager = pm

    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="os_exec", actions=["nonexistent_action"]),
    ])
    executor = DaemonToolExecutor(module_registry=FakeRegistry(modules={"os_exec": module}))

    scope = _ExecutionScope(
        identity=IdentityContext(app_id="ghost-app"),
        capabilities=caps,
    )
    scope_token = _current_scope.set(scope)
    sec_token = set_security_app_id("ghost-app")
    try:
        # Should not raise
        await executor._auto_grant_permissions_for_app(module, "nonexistent_action")
        assert len(pm._store.grants) == 0
    finally:
        from llmos_bridge.security.context import _current_app_id
        _current_app_id.reset(sec_token)
        _current_scope.reset(scope_token)


# ═══════════════════════════════════════════════════════════════════
# Integration: multiple layers working together
# ═══════════════════════════════════════════════════════════════════


@pytest.mark.asyncio
async def test_integration_authorization_plus_capabilities():
    """Authorization whitelist AND capabilities grant must both agree."""
    from llmos_bridge.identity.authorization import AuthorizationGuard
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant

    app = Application(
        app_id="combo-app",
        name="combo",
        allowed_modules=["filesystem"],  # RBAC: only filesystem
    )
    store = FakeIdentityStore(apps=[app])
    guard = AuthorizationGuard(store=store, enabled=True)

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})

    # Capabilities grant os_exec (but RBAC denies it)
    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="os_exec", actions=["run_command"]),
    ])
    executor = DaemonToolExecutor(
        module_registry=registry,
        authorization_guard=guard,
        identity_store=store,
        capabilities=caps,
    )

    identity = IdentityContext(app_id="combo-app", role=Role.AGENT)
    scope = _ExecutionScope(identity=identity, capabilities=caps)
    token = _current_scope.set(scope)
    try:
        result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
        # RBAC runs first (step 4) and blocks os_exec → error
        assert "error" in result
        assert "AuthorizationDenied" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_integration_profile_cap_plus_sandbox():
    """Profile capping + sandbox both enforce their rules."""
    from llmos_bridge.security.profiles import PermissionProfile, BUILTIN_PROFILES
    from llmos_bridge.security.guard import PermissionGuard

    daemon_guard = PermissionGuard(profile=BUILTIN_PROFILES[PermissionProfile.LOCAL_WORKER])

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=daemon_guard)

    # App tries unrestricted profile + sandbox
    scope = _ExecutionScope(
        security_profile="unrestricted",  # Will be capped to local_worker
        sandbox_paths=["/home/user/project"],
    )
    token = _current_scope.set(scope)
    try:
        # Path outside sandbox — blocked by sandbox (step 5b)
        result = await executor.execute("filesystem", "read_file", {"path": "/etc/passwd"})
        assert "error" in result
        assert "outside sandbox" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_integration_deny_takes_precedence_over_grant():
    """Deny rules take precedence over grant rules in capabilities."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant, CapabilityDenial

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})

    caps = CapabilitiesConfig(
        grant=[
            CapabilityGrant(module="os_exec", actions=["run_command"]),
        ],
        deny=[
            CapabilityDenial(module="os_exec", action="run_command", reason="Explicitly denied"),
        ],
    )
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
    assert "error" in result
    assert "Explicitly denied" in result["error"]


# ═══════════════════════════════════════════════════════════════════
# Gap 5: Risk-based automatic approval
# ═══════════════════════════════════════════════════════════════════


def _make_action(perms=None, risk=None, irreversible=False):
    """Create a fake action coroutine with decorator metadata on the function."""
    async def _action(self, params):
        return self._result

    if perms is not None:
        _action._required_permissions = perms
    if risk is not None:
        _action._risk_level = risk
    if irreversible:
        _action._irreversible = True
    return _action


class FakeModuleWithRisk:
    """Module whose action handlers carry decorator metadata."""
    MODULE_ID = "risky_mod"

    def __init__(self, result=None):
        from llmos_bridge.security.models import RiskLevel

        self._result = result or {"ok": True}
        self._security = None

        # Bind decorated functions as methods — metadata lives on the function
        import types
        self._action_low_op = types.MethodType(
            _make_action(perms=["filesystem.read"]), self
        )
        self._action_medium_op = types.MethodType(
            _make_action(perms=["os.process.execute"], risk=RiskLevel.MEDIUM), self
        )
        self._action_high_op = types.MethodType(
            _make_action(perms=["filesystem.delete"], risk=RiskLevel.HIGH, irreversible=True), self
        )
        self._action_critical_op = types.MethodType(
            _make_action(perms=["device.keyboard"], risk=RiskLevel.CRITICAL), self
        )
        self._action_no_meta_op = types.MethodType(
            _make_action(), self  # no metadata at all
        )
        self._action_multi_perm_op = types.MethodType(
            _make_action(perms=["filesystem.read", "filesystem.delete"]), self
        )

    async def execute(self, action, params, context=None):
        return self._result


# ── _get_action_risk_level tests ──────────────────────────────────


@pytest.mark.asyncio
async def test_risk_level_resolves_low_from_permission():
    """Action with only LOW-risk permission resolves to 'low'."""
    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    executor = DaemonToolExecutor(module_registry=registry)

    assert executor._get_action_risk_level("risky_mod", "low_op") == "low"


@pytest.mark.asyncio
async def test_risk_level_resolves_medium_from_sensitive_decorator():
    """@sensitive_action(MEDIUM) resolves to 'medium'."""
    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    executor = DaemonToolExecutor(module_registry=registry)

    assert executor._get_action_risk_level("risky_mod", "medium_op") == "medium"


@pytest.mark.asyncio
async def test_risk_level_resolves_high():
    """HIGH risk from @sensitive_action + permission."""
    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    executor = DaemonToolExecutor(module_registry=registry)

    assert executor._get_action_risk_level("risky_mod", "high_op") == "high"


@pytest.mark.asyncio
async def test_risk_level_resolves_critical():
    """CRITICAL risk resolves correctly."""
    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    executor = DaemonToolExecutor(module_registry=registry)

    assert executor._get_action_risk_level("risky_mod", "critical_op") == "critical"


@pytest.mark.asyncio
async def test_risk_level_no_metadata_defaults_to_low():
    """Action without any risk metadata defaults to 'low'."""
    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    executor = DaemonToolExecutor(module_registry=registry)

    assert executor._get_action_risk_level("risky_mod", "no_meta_op") == "low"


@pytest.mark.asyncio
async def test_risk_level_unknown_action_defaults_to_low():
    """Unknown action name defaults to 'low'."""
    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    executor = DaemonToolExecutor(module_registry=registry)

    assert executor._get_action_risk_level("risky_mod", "nonexistent") == "low"


@pytest.mark.asyncio
async def test_risk_level_unknown_module_defaults_to_low():
    """Unknown module defaults to 'low'."""
    registry = FakeRegistry(modules={})
    executor = DaemonToolExecutor(module_registry=registry)

    assert executor._get_action_risk_level("no_such_module", "action") == "low"


@pytest.mark.asyncio
async def test_risk_level_multi_permission_takes_highest():
    """Multiple permissions on one action: highest risk wins."""
    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    executor = DaemonToolExecutor(module_registry=registry)

    # filesystem.read=LOW, filesystem.delete=HIGH → result should be HIGH
    assert executor._get_action_risk_level("risky_mod", "multi_perm_op") == "high"


# ── _check_risk_approval tests ────────────────────────────────────


@pytest.mark.asyncio
async def test_risk_approval_medium_threshold_blocks_medium_action():
    """Default threshold 'medium': MEDIUM action triggers approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    msg = executor._check_risk_approval("risky_mod", "medium_op", {})
    assert msg is not None
    assert "MEDIUM" in msg


@pytest.mark.asyncio
async def test_risk_approval_medium_threshold_allows_low_action():
    """Default threshold 'medium': LOW action passes without approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    msg = executor._check_risk_approval("risky_mod", "low_op", {})
    assert msg is None


@pytest.mark.asyncio
async def test_risk_approval_high_threshold_allows_medium():
    """Threshold 'high': MEDIUM action passes without approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="high")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    msg = executor._check_risk_approval("risky_mod", "medium_op", {})
    assert msg is None


@pytest.mark.asyncio
async def test_risk_approval_high_threshold_blocks_high():
    """Threshold 'high': HIGH action triggers approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="high")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    msg = executor._check_risk_approval("risky_mod", "high_op", {})
    assert msg is not None
    assert "HIGH" in msg


@pytest.mark.asyncio
async def test_risk_approval_critical_threshold_allows_high():
    """Threshold 'critical': HIGH action passes without approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="critical")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    msg = executor._check_risk_approval("risky_mod", "high_op", {})
    assert msg is None


@pytest.mark.asyncio
async def test_risk_approval_critical_threshold_blocks_critical():
    """Threshold 'critical': CRITICAL action triggers approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="critical")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    msg = executor._check_risk_approval("risky_mod", "critical_op", {})
    assert msg is not None
    assert "CRITICAL" in msg


@pytest.mark.asyncio
async def test_risk_approval_none_disables_all_risk_approval():
    """Threshold 'none': disables risk-based approval entirely."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="none")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    # Even CRITICAL should not trigger risk-based approval
    msg = executor._check_risk_approval("risky_mod", "critical_op", {})
    assert msg is None


@pytest.mark.asyncio
async def test_risk_approval_low_threshold_blocks_everything():
    """Threshold 'low': even LOW risk actions need approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="low")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    msg = executor._check_risk_approval("risky_mod", "low_op", {})
    assert msg is not None
    assert "LOW" in msg


@pytest.mark.asyncio
async def test_risk_approval_no_capabilities_skips():
    """No capabilities in scope → risk approval is skipped."""
    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    executor = DaemonToolExecutor(module_registry=registry)

    # Clear scope
    token = _current_scope.set(None)
    try:
        msg = executor._check_risk_approval("risky_mod", "critical_op", {})
        assert msg is None
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_risk_approval_default_is_medium():
    """Default auto_approve_risk is 'medium' — MEDIUM action triggers approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    # Use default CapabilitiesConfig (no explicit auto_approve_risk)
    caps = CapabilitiesConfig()
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    assert caps.auto_approve_risk == "medium"
    msg = executor._check_risk_approval("risky_mod", "medium_op", {})
    assert msg is not None


# ── Integration: risk approval + YAML rules ──────────────────────


@pytest.mark.asyncio
async def test_yaml_rule_overrides_risk_message():
    """YAML approval_required rule provides the approval message even if risk also triggers."""
    from llmos_bridge.apps.models import CapabilitiesConfig, ApprovalRule

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(
        auto_approve_risk="medium",
        approval_required=[
            ApprovalRule(
                module="risky_mod",
                action="medium_op",
                message="Custom YAML approval message",
            ),
        ],
    )
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    # Both risk-based and YAML should trigger, but YAML message wins (more specific)
    yaml_msg = executor._check_approval_required("risky_mod", "medium_op", {})
    risk_msg = executor._check_risk_approval("risky_mod", "medium_op", {})
    assert yaml_msg == "Custom YAML approval message"
    assert risk_msg is not None
    # In the pipeline, yaml_msg takes priority
    final_msg = yaml_msg or risk_msg
    assert final_msg == "Custom YAML approval message"


@pytest.mark.asyncio
async def test_risk_none_with_yaml_rule_still_requires_approval():
    """auto_approve_risk='none' disables risk-based, but YAML rules still apply."""
    from llmos_bridge.apps.models import CapabilitiesConfig, ApprovalRule

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(
        auto_approve_risk="none",
        approval_required=[
            ApprovalRule(
                module="risky_mod",
                action="high_op",
                message="YAML says approve this",
            ),
        ],
    )
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    # Risk-based should not trigger
    risk_msg = executor._check_risk_approval("risky_mod", "high_op", {})
    assert risk_msg is None

    # But YAML rule should
    yaml_msg = executor._check_approval_required("risky_mod", "high_op", {})
    assert yaml_msg == "YAML says approve this"


@pytest.mark.asyncio
async def test_risk_approval_triggers_handle_approval_in_pipeline():
    """Full pipeline: MEDIUM risk action with no approval gate returns error."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    # No approval gate → _handle_approval returns immediate error
    result = await executor.execute("risky_mod", "medium_op", {})
    assert "error" in result
    assert "[APPROVAL REQUIRED]" in result["error"]
    assert result.get("_approval") == "no_gate"


@pytest.mark.asyncio
async def test_risk_approval_low_action_passes_pipeline():
    """Full pipeline: LOW risk action passes through without approval."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    result = await executor.execute("risky_mod", "low_op", {})
    assert result.get("ok") is True


@pytest.mark.asyncio
async def test_risk_none_allows_all_in_pipeline():
    """Full pipeline: auto_approve_risk='none' lets all actions through."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="none")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    # Even HIGH risk passes without approval
    result = await executor.execute("risky_mod", "high_op", {})
    assert result.get("ok") is True


@pytest.mark.asyncio
async def test_risk_approval_with_gate_auto_approved():
    """Full pipeline: approval gate with APPROVE_ALWAYS lets action through."""
    from llmos_bridge.apps.models import CapabilitiesConfig
    from llmos_bridge.orchestration.approval import ApprovalGate

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    gate = ApprovalGate()
    # Pre-mark this action as auto-approved (format: "module.action")
    gate._auto_approve = {"test-run": {"risky_mod.medium_op"}}
    executor.set_approval_gate(gate)

    token = _current_scope.set(_ExecutionScope(run_id="test-run", capabilities=caps))
    try:
        result = await executor.execute("risky_mod", "medium_op", {})
        assert result.get("ok") is True
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_handle_approval_uses_resolved_risk_level():
    """ApprovalRequest.risk_level is resolved from module metadata, not hardcoded."""
    from llmos_bridge.apps.models import CapabilitiesConfig
    from llmos_bridge.orchestration.approval import ApprovalGate, ApprovalDecision, ApprovalResponse

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    gate = ApprovalGate()
    executor.set_approval_gate(gate)

    # Capture the request that goes to the gate
    captured_requests = []
    original_request_approval = gate.request_approval

    async def mock_request_approval(request, timeout=300, timeout_behavior="reject"):
        captured_requests.append(request)
        return ApprovalResponse(decision=ApprovalDecision.APPROVE, approved_by="test")

    gate.request_approval = mock_request_approval

    token = _current_scope.set(_ExecutionScope(run_id="risk-test", capabilities=caps))
    try:
        result = await executor.execute("risky_mod", "high_op", {})
        assert result.get("ok") is True
        assert len(captured_requests) == 1
        # Risk level should be 'high', not hardcoded 'medium'
        assert captured_requests[0].risk_level == "high"
    finally:
        _current_scope.reset(token)


# ═══════════════════════════════════════════════════════════════════
# Gap 6: Approval enrichment, MESSAGE decision, session-scoped APPROVE_ALWAYS
# ═══════════════════════════════════════════════════════════════════


# ── Agent awareness: enriched tool results ────────────────────────


@pytest.mark.asyncio
async def test_approval_reject_returns_enriched_error():
    """Rejected approval returns structured error the agent can understand."""
    from llmos_bridge.apps.models import CapabilitiesConfig
    from llmos_bridge.orchestration.approval import (
        ApprovalGate, ApprovalDecision, ApprovalResponse,
    )

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    gate = ApprovalGate()
    executor.set_approval_gate(gate)

    async def mock_reject(request, timeout=300, timeout_behavior="reject"):
        return ApprovalResponse(
            decision=ApprovalDecision.REJECT,
            reason="This is dangerous",
        )
    gate.request_approval = mock_reject

    token = _current_scope.set(_ExecutionScope(run_id="rej-test", capabilities=caps))
    try:
        result = await executor.execute("risky_mod", "medium_op", {})
        assert "error" in result
        assert "[APPROVAL REJECTED]" in result["error"]
        assert "This is dangerous" in result["error"]
        assert result.get("_approval") == "rejected"
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_approval_skip_returns_enriched_error():
    """Skipped approval returns structured error with skip context."""
    from llmos_bridge.apps.models import CapabilitiesConfig
    from llmos_bridge.orchestration.approval import (
        ApprovalGate, ApprovalDecision, ApprovalResponse,
    )

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    gate = ApprovalGate()
    executor.set_approval_gate(gate)

    async def mock_skip(request, timeout=300, timeout_behavior="reject"):
        return ApprovalResponse(decision=ApprovalDecision.SKIP, reason="Not now")
    gate.request_approval = mock_skip

    token = _current_scope.set(_ExecutionScope(run_id="skip-test", capabilities=caps))
    try:
        result = await executor.execute("risky_mod", "medium_op", {})
        assert "error" in result
        assert "[APPROVAL SKIPPED]" in result["error"]
        assert "Not now" in result["error"]
        assert result.get("_approval") == "skipped"
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_approval_no_gate_returns_enriched_error():
    """No approval gate returns structured error with context."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)
    # No gate set

    result = await executor.execute("risky_mod", "medium_op", {})
    assert "error" in result
    assert "[APPROVAL REQUIRED]" in result["error"]
    assert result.get("_approval") == "no_gate"


# ── MESSAGE decision ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approval_message_returns_user_feedback():
    """MESSAGE decision blocks action and returns user's feedback to agent."""
    from llmos_bridge.apps.models import CapabilitiesConfig
    from llmos_bridge.orchestration.approval import (
        ApprovalGate, ApprovalDecision, ApprovalResponse,
    )

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    gate = ApprovalGate()
    executor.set_approval_gate(gate)

    async def mock_message(request, timeout=300, timeout_behavior="reject"):
        return ApprovalResponse(
            decision=ApprovalDecision.MESSAGE,
            reason="Don't run commands, just read the file instead",
        )
    gate.request_approval = mock_message

    token = _current_scope.set(_ExecutionScope(run_id="msg-test", capabilities=caps))
    try:
        result = await executor.execute("risky_mod", "medium_op", {})
        assert "error" in result
        assert "[USER FEEDBACK]" in result["error"]
        assert "Don't run commands, just read the file instead" in result["error"]
        assert result.get("_approval") == "message"
        assert result.get("_user_message") == "Don't run commands, just read the file instead"
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_approval_message_default_text():
    """MESSAGE without reason provides a default feedback text."""
    from llmos_bridge.apps.models import CapabilitiesConfig
    from llmos_bridge.orchestration.approval import (
        ApprovalGate, ApprovalDecision, ApprovalResponse,
    )

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    gate = ApprovalGate()
    executor.set_approval_gate(gate)

    async def mock_message(request, timeout=300, timeout_behavior="reject"):
        return ApprovalResponse(decision=ApprovalDecision.MESSAGE, reason="")
    gate.request_approval = mock_message

    token = _current_scope.set(_ExecutionScope(run_id="msg-default", capabilities=caps))
    try:
        result = await executor.execute("risky_mod", "medium_op", {})
        assert "error" in result
        assert "[USER FEEDBACK]" in result["error"]
        assert "do something different" in result["error"]
    finally:
        _current_scope.reset(token)


# ── Session-scoped APPROVE_ALWAYS ─────────────────────────────────


@pytest.mark.asyncio
async def test_approve_always_scoped_to_run_id():
    """APPROVE_ALWAYS only applies within the same run_id session."""
    from llmos_bridge.orchestration.approval import ApprovalGate

    gate = ApprovalGate()

    # Simulate APPROVE_ALWAYS for run-1
    from llmos_bridge.orchestration.approval import ApprovalDecision, ApprovalResponse, ApprovalRequest

    req = ApprovalRequest(
        plan_id="run-1", action_id="test.1", module="os_exec", action_name="run_command",
        params={},
    )
    entry_key = ("run-1", "test.1")
    from llmos_bridge.orchestration.approval import _PendingEntry
    entry = _PendingEntry(req)
    gate._pending[entry_key] = entry
    gate.submit_decision("run-1", "test.1", ApprovalResponse(
        decision=ApprovalDecision.APPROVE_ALWAYS,
    ))

    # Same run: auto-approved
    assert gate.is_auto_approved("os_exec", "run_command", run_id="run-1") is True

    # Different run: NOT auto-approved
    assert gate.is_auto_approved("os_exec", "run_command", run_id="run-2") is False

    # No run_id: NOT auto-approved
    assert gate.is_auto_approved("os_exec", "run_command") is False


@pytest.mark.asyncio
async def test_approve_always_different_actions_same_session():
    """APPROVE_ALWAYS for one action doesn't affect another in same session."""
    from llmos_bridge.orchestration.approval import (
        ApprovalGate, ApprovalDecision, ApprovalResponse, ApprovalRequest, _PendingEntry,
    )

    gate = ApprovalGate()

    req = ApprovalRequest(
        plan_id="run-x", action_id="test.1", module="os_exec", action_name="run_command",
        params={},
    )
    entry = _PendingEntry(req)
    gate._pending[("run-x", "test.1")] = entry
    gate.submit_decision("run-x", "test.1", ApprovalResponse(
        decision=ApprovalDecision.APPROVE_ALWAYS,
    ))

    assert gate.is_auto_approved("os_exec", "run_command", run_id="run-x") is True
    assert gate.is_auto_approved("os_exec", "kill_process", run_id="run-x") is False


@pytest.mark.asyncio
async def test_clear_auto_approvals_per_run():
    """clear_auto_approvals with run_id only clears that session."""
    from llmos_bridge.orchestration.approval import ApprovalGate

    gate = ApprovalGate()
    gate._auto_approve = {
        "run-1": {"os_exec.run_command"},
        "run-2": {"filesystem.delete"},
    }

    gate.clear_auto_approvals(run_id="run-1")
    assert gate.is_auto_approved("os_exec", "run_command", run_id="run-1") is False
    assert gate.is_auto_approved("filesystem", "delete", run_id="run-2") is True


@pytest.mark.asyncio
async def test_clear_auto_approvals_all():
    """clear_auto_approvals without run_id clears everything."""
    from llmos_bridge.orchestration.approval import ApprovalGate

    gate = ApprovalGate()
    gate._auto_approve = {
        "run-1": {"os_exec.run_command"},
        "run-2": {"filesystem.delete"},
    }

    gate.clear_auto_approvals()
    assert gate._auto_approve == {}


# ── Integration: APPROVE_ALWAYS in full pipeline ──────────────────


@pytest.mark.asyncio
async def test_approve_always_works_in_pipeline_same_session():
    """Full pipeline: APPROVE_ALWAYS in one call auto-approves the next in same run."""
    from llmos_bridge.apps.models import CapabilitiesConfig
    from llmos_bridge.orchestration.approval import (
        ApprovalGate, ApprovalDecision, ApprovalResponse,
    )

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    gate = ApprovalGate()
    executor.set_approval_gate(gate)
    call_count = 0

    async def mock_approve_always(request, timeout=300, timeout_behavior="reject"):
        nonlocal call_count
        call_count += 1
        # Simulate what submit_decision does: register auto-approve for this session
        action_key = f"{request.module}.{request.action_name}"
        session_set = gate._auto_approve.setdefault(request.plan_id, set())
        session_set.add(action_key)
        return ApprovalResponse(decision=ApprovalDecision.APPROVE_ALWAYS)
    gate.request_approval = mock_approve_always

    token = _current_scope.set(_ExecutionScope(run_id="always-test", capabilities=caps))
    try:
        # First call: goes through approval gate
        result1 = await executor.execute("risky_mod", "medium_op", {})
        assert result1.get("ok") is True
        assert call_count == 1

        # Second call same run: auto-approved, gate NOT called again
        result2 = await executor.execute("risky_mod", "medium_op", {})
        assert result2.get("ok") is True
        assert call_count == 1  # Not incremented — auto-approved
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_approve_always_not_shared_across_runs():
    """Full pipeline: APPROVE_ALWAYS in run-1 does NOT apply in run-2."""
    from llmos_bridge.apps.models import CapabilitiesConfig
    from llmos_bridge.orchestration.approval import (
        ApprovalGate, ApprovalDecision, ApprovalResponse,
    )

    module = FakeModuleWithRisk()
    registry = FakeRegistry(modules={"risky_mod": module})
    caps = CapabilitiesConfig(auto_approve_risk="medium")
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    gate = ApprovalGate()
    executor.set_approval_gate(gate)
    call_count = 0

    async def mock_approve_always(request, timeout=300, timeout_behavior="reject"):
        nonlocal call_count
        call_count += 1
        # Simulate submit_decision: register auto-approve for this session only
        action_key = f"{request.module}.{request.action_name}"
        session_set = gate._auto_approve.setdefault(request.plan_id, set())
        session_set.add(action_key)
        return ApprovalResponse(decision=ApprovalDecision.APPROVE_ALWAYS)
    gate.request_approval = mock_approve_always

    # Run 1: approve always
    token1 = _current_scope.set(_ExecutionScope(run_id="run-1", capabilities=caps))
    try:
        await executor.execute("risky_mod", "medium_op", {})
        assert call_count == 1
    finally:
        _current_scope.reset(token1)

    # Run 2: must go through approval again (different session)
    token2 = _current_scope.set(_ExecutionScope(run_id="run-2", capabilities=caps))
    try:
        await executor.execute("risky_mod", "medium_op", {})
        assert call_count == 2  # Called again for run-2
    finally:
        _current_scope.reset(token2)
