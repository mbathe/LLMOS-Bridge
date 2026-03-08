"""Tests for DaemonToolExecutor — daemon integration bridge."""

import asyncio

import pytest

from llmos_bridge.apps.daemon_executor import (
    DaemonToolExecutor,
    _current_scope,
    _ExecutionScope,
    module_info_from_manifests,
)
from llmos_bridge.modules.manifest import (
    ActionSpec,
    ModuleManifest,
    ParamSpec,
)


# ─── Fixtures ─────────────────────────────────────────────────────

class FakeModule:
    """Minimal module that supports execute()."""

    def __init__(self, result=None, error=None, delay=0):
        self._result = result or {"success": True}
        self._error = error
        self._delay = delay
        self.last_context = None  # Track ExecutionContext

    async def execute(self, action: str, params: dict, context=None):
        self.last_context = context
        if self._delay:
            await asyncio.sleep(self._delay)
        if self._error:
            raise self._error
        return self._result


class FakeRegistry:
    """Minimal ModuleRegistry stand-in."""

    def __init__(self, modules: dict | None = None, manifests: list | None = None):
        self._modules = modules or {}
        self._manifests = manifests or []

    def get(self, module_id: str):
        if module_id not in self._modules:
            raise KeyError(f"Module '{module_id}' not found")
        return self._modules[module_id]

    def all_manifests(self):
        return self._manifests


class FakeGuard:
    """Minimal PermissionGuard stand-in."""

    def __init__(self, allowed=True):
        self._allowed = allowed

        class _Profile:
            class _P:
                value = "test_profile"
            profile = _P()
        self._profile = _Profile()

    def is_allowed(self, module_id: str, action_name: str) -> bool:
        return self._allowed

    def check_sandbox_params(self, module: str, action: str, params: dict):
        pass


class FakeEventBus:
    """Captures emitted events."""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def emit(self, topic: str, event: dict):
        self.events.append((topic, event))


class FakeSanitizer:
    """Wraps result with a marker to prove it ran."""

    def sanitize(self, output, module="", action=""):
        if isinstance(output, dict):
            output["_sanitized"] = True
        return output


# ─── Tests: execute() ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_routes_to_module():
    """Tool call is dispatched to the correct module."""
    module = FakeModule(result={"content": "hello"})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)

    result = await executor.execute("filesystem", "read_file", {"path": "/tmp/x"})
    assert result["content"] == "hello"


@pytest.mark.asyncio
async def test_execute_unknown_module_returns_error():
    """Unknown module returns error dict, doesn't raise."""
    registry = FakeRegistry(modules={})
    executor = DaemonToolExecutor(module_registry=registry)

    result = await executor.execute("nonexistent", "foo", {})
    assert "error" in result
    assert "nonexistent" in result["error"]


@pytest.mark.asyncio
async def test_execute_permission_denied():
    """Permission denied returns error dict (doesn't crash the agent loop)."""
    module = FakeModule()
    registry = FakeRegistry(modules={"os_exec": module})
    guard = FakeGuard(allowed=False)
    executor = DaemonToolExecutor(module_registry=registry, permission_guard=guard)

    result = await executor.execute("os_exec", "run_command", {"command": ["rm", "-rf", "/"]})
    assert "error" in result
    assert "PermissionDeniedError" in result["error"]


@pytest.mark.asyncio
async def test_execute_with_sanitizer():
    """Output is sanitized when sanitizer is provided."""
    module = FakeModule(result={"data": "raw"})
    registry = FakeRegistry(modules={"filesystem": module})
    sanitizer = FakeSanitizer()
    executor = DaemonToolExecutor(
        module_registry=registry, sanitizer=sanitizer,
    )

    result = await executor.execute("filesystem", "read_file", {"path": "/x"})
    assert result.get("_sanitized") is True


@pytest.mark.asyncio
async def test_execute_emits_event():
    """EventBus receives audit event after execution."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    event_bus = FakeEventBus()
    executor = DaemonToolExecutor(
        module_registry=registry, event_bus=event_bus,
    )

    await executor.execute("filesystem", "read_file", {"path": "/x"})

    assert len(event_bus.events) == 1
    topic, event = event_bus.events[0]
    assert topic == "llmos.actions.results"
    assert event["module"] == "filesystem"
    assert event["action"] == "read_file"
    assert event["success"] is True


@pytest.mark.asyncio
async def test_execute_emits_event_on_error():
    """EventBus receives error event when module execution fails."""
    module = FakeModule(error=RuntimeError("boom"))
    registry = FakeRegistry(modules={"os_exec": module})
    event_bus = FakeEventBus()
    executor = DaemonToolExecutor(
        module_registry=registry, event_bus=event_bus,
    )

    result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})

    assert "error" in result
    assert len(event_bus.events) == 1
    _, event = event_bus.events[0]
    assert event["success"] is False


@pytest.mark.asyncio
async def test_execute_non_dict_result_normalized():
    """Non-dict results are wrapped in {'result': ...}."""
    module = FakeModule(result="plain string")
    registry = FakeRegistry(modules={"test": module})
    executor = DaemonToolExecutor(module_registry=registry)

    result = await executor.execute("test", "action", {})
    assert result == {"result": "plain string"}


@pytest.mark.asyncio
async def test_execute_with_guard_but_allowed():
    """Allowed actions pass through the guard without issue."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    guard = FakeGuard(allowed=True)
    executor = DaemonToolExecutor(
        module_registry=registry, permission_guard=guard,
    )

    result = await executor.execute("filesystem", "read_file", {"path": "/x"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_execute_no_guard_no_sanitizer():
    """Execute works with minimal config (just registry)."""
    module = FakeModule(result={"value": 42})
    registry = FakeRegistry(modules={"calc": module})
    executor = DaemonToolExecutor(module_registry=registry)

    result = await executor.execute("calc", "compute", {})
    assert result["value"] == 42


# ─── Tests: get_module_info() ──────────────────────────────────────


def test_get_module_info_from_manifests():
    """Converts ModuleManifest list to dict format for AppToolRegistry."""
    manifests = [
        ModuleManifest(
            module_id="filesystem",
            version="1.0.0",
            description="Filesystem module",
            actions=[
                ActionSpec(
                    name="read_file",
                    description="Read a file",
                    params=[
                        ParamSpec(name="path", type="string", description="File path", required=True),
                    ],
                ),
                ActionSpec(
                    name="write_file",
                    description="Write a file",
                    params=[
                        ParamSpec(name="path", type="string", description="File path", required=True),
                        ParamSpec(name="content", type="string", description="Content", required=True),
                    ],
                ),
            ],
        ),
    ]

    info = module_info_from_manifests(manifests)

    assert "filesystem" in info
    actions = info["filesystem"]["actions"]
    assert len(actions) == 2
    assert actions[0]["name"] == "read_file"
    assert actions[0]["params"]["path"]["type"] == "string"
    assert actions[0]["params"]["path"]["required"] is True


def test_get_module_info_preserves_enum():
    """Enum values on ParamSpec are preserved."""
    manifests = [
        ModuleManifest(
            module_id="test",
            version="1.0.0",
            description="Test",
            actions=[
                ActionSpec(
                    name="choose",
                    description="Choose",
                    params=[
                        ParamSpec(
                            name="mode",
                            type="string",
                            description="Mode",
                            enum=["fast", "slow"],
                        ),
                    ],
                ),
            ],
        ),
    ]

    info = module_info_from_manifests(manifests)
    assert info["test"]["actions"][0]["params"]["mode"]["enum"] == ["fast", "slow"]


def test_get_module_info_empty_registry():
    """Empty manifest list produces empty dict."""
    assert module_info_from_manifests([]) == {}


def test_get_module_info_module_no_actions():
    """Module with no actions still appears in output."""
    manifests = [
        ModuleManifest(module_id="empty", version="1.0.0", description="Empty"),
    ]
    info = module_info_from_manifests(manifests)
    assert info["empty"]["actions"] == []


def test_get_module_info_via_executor():
    """DaemonToolExecutor.get_module_info() delegates correctly."""
    manifests = [
        ModuleManifest(
            module_id="fs",
            version="1.0",
            description="FS",
            actions=[
                ActionSpec(name="read", description="Read", params=[
                    ParamSpec(name="path", type="string", description="Path"),
                ]),
            ],
        ),
    ]
    registry = FakeRegistry(modules={}, manifests=manifests)
    executor = DaemonToolExecutor(module_registry=registry)

    info = executor.get_module_info()
    assert "fs" in info
    assert info["fs"]["actions"][0]["name"] == "read"


# ─── Tests: Capabilities enforcement ──────────────────────────────


@pytest.mark.asyncio
async def test_capability_deny_blocks_action():
    """Denied actions return error without executing."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityDenial

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    caps = CapabilitiesConfig(deny=[
        CapabilityDenial(module="os_exec", action="run_command", reason="Too dangerous"),
    ])
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    result = await executor.execute("os_exec", "run_command", {"command": ["rm"]})
    assert "error" in result
    assert "Too dangerous" in result["error"]


@pytest.mark.asyncio
async def test_capability_grant_allows_listed():
    """Granted actions pass through."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="filesystem", actions=["read_file"]),
    ])
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    result = await executor.execute("filesystem", "read_file", {"path": "/x"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_capability_grant_blocks_unlisted():
    """Non-granted actions are rejected when grants are specified."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityGrant

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    caps = CapabilitiesConfig(grant=[
        CapabilityGrant(module="filesystem", actions=["read_file"]),
    ])
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    result = await executor.execute("filesystem", "delete_file", {"path": "/x"})
    assert "error" in result
    assert "not in app capability grants" in result["error"]


@pytest.mark.asyncio
async def test_capability_approval_required():
    """Actions requiring approval return error."""
    from llmos_bridge.apps.models import CapabilitiesConfig, ApprovalRule

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    caps = CapabilitiesConfig(approval_required=[
        ApprovalRule(module="os_exec", action="run_command", message="Confirm exec?"),
    ])
    executor = DaemonToolExecutor(module_registry=registry, capabilities=caps)

    result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
    assert "error" in result
    assert "APPROVAL" in result["error"]


@pytest.mark.asyncio
async def test_set_capabilities_resets_counts():
    """set_capabilities clears action counters."""
    from llmos_bridge.apps.models import CapabilitiesConfig

    from llmos_bridge.apps.daemon_executor import _current_scope, _ExecutionScope

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    executor = DaemonToolExecutor(module_registry=registry)

    # Create a scope for the test
    token = _current_scope.set(_ExecutionScope())

    try:
        # Execute once to increment counter
        await executor.execute("fs", "read", {})
        scope = _current_scope.get()
        assert scope.action_counts.get("fs.read", 0) == 1

        # Reset via set_capabilities
        executor.set_capabilities(CapabilitiesConfig())
        assert scope.action_counts == {}
    finally:
        _current_scope.reset(token)


# ─── Tests: Scanner integration ───────────────────────────────────


class FakeScanner:
    """Fake scanner for testing."""
    def __init__(self, verdict="allow", details=""):
        self.scanner_id = "fake_scanner"
        self._verdict = verdict
        self._details = details

    async def scan(self, text, context=None):
        from llmos_bridge.security.scanners.base import ScanResult, ScanVerdict
        return ScanResult(
            scanner_id=self.scanner_id,
            verdict=ScanVerdict(self._verdict),
            details=self._details,
        )


class FakeScannerRegistry:
    def list_enabled(self):
        return self._scanners

    def __init__(self, scanners=None):
        self._scanners = scanners or []


class FakePipeline:
    def __init__(self, scanners=None, enabled=True):
        self.registry = FakeScannerRegistry(scanners or [])
        self.enabled = enabled


@pytest.mark.asyncio
async def test_scanner_blocks_dangerous_params():
    """Scanner pipeline blocks tool calls with suspicious content."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    scanner = FakeScanner(verdict="reject", details="injection detected")
    pipeline = FakePipeline(scanners=[scanner])
    executor = DaemonToolExecutor(
        module_registry=registry,
        scanner_pipeline=pipeline,
    )

    result = await executor.execute("os_exec", "run_command", {"command": "DROP TABLE users"})
    assert "error" in result
    assert "Blocked by security scanner" in result["error"]


@pytest.mark.asyncio
async def test_scanner_allows_safe_params():
    """Scanner pipeline allows safe tool calls."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    scanner = FakeScanner(verdict="allow")
    pipeline = FakePipeline(scanners=[scanner])
    executor = DaemonToolExecutor(
        module_registry=registry,
        scanner_pipeline=pipeline,
    )

    result = await executor.execute("filesystem", "read_file", {"path": "/tmp/test.txt"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_scanner_disabled_skips():
    """Disabled scanner pipeline is skipped."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    pipeline = FakePipeline(enabled=False)
    executor = DaemonToolExecutor(
        module_registry=registry,
        scanner_pipeline=pipeline,
    )

    result = await executor.execute("fs", "read", {})
    assert result["ok"] is True


# ─── Tests: Tool constraints enforcement ─────────────────────────


@pytest.mark.asyncio
async def test_constraint_forbidden_command_blocks():
    """Forbidden command pattern in constraints blocks execution."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(module_registry=registry)
    executor.set_tool_constraints({
        "os_exec.run_command": {"forbidden_commands": ["rm -rf"]},
    })

    result = await executor.execute("os_exec", "run_command", {"command": "rm -rf /"})
    assert "error" in result
    assert "forbidden" in result["error"].lower()


@pytest.mark.asyncio
async def test_constraint_allowed_paths_blocks_outside():
    """Path constraint blocks access outside allowed paths."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)
    executor.set_tool_constraints({
        "filesystem.read_file": {"paths": ["/home/user/project"]},
    })

    result = await executor.execute("filesystem", "read_file", {"path": "/etc/passwd"})
    assert "error" in result
    assert "not in allowed paths" in result["error"]


@pytest.mark.asyncio
async def test_constraint_read_only_blocks_write():
    """Read-only constraint blocks write actions."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)
    executor.set_tool_constraints({
        "filesystem.write_file": {"read_only": True},
    })

    result = await executor.execute("filesystem", "write_file", {"path": "/x", "content": "y"})
    assert "error" in result
    assert "read-only" in result["error"]


@pytest.mark.asyncio
async def test_constraint_allowed_passes():
    """Actions within constraints pass through."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"filesystem": module})
    executor = DaemonToolExecutor(module_registry=registry)
    executor.set_tool_constraints({
        "filesystem.read_file": {"paths": ["/home/user"]},
    })

    result = await executor.execute("filesystem", "read_file", {"path": "/home/user/code/test.py"})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_constraint_forbidden_pattern_blocks():
    """Forbidden regex pattern in params blocks execution."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    executor = DaemonToolExecutor(module_registry=registry)
    executor.set_tool_constraints({
        "os_exec.run_command": {"forbidden_patterns": [r"DROP\s+TABLE"]},
    })

    result = await executor.execute("os_exec", "run_command", {"command": "DROP TABLE users"})
    assert "error" in result
    assert "forbidden pattern" in result["error"]


# ─── Tests: when: condition on deny/approval ─────────────────────


@pytest.mark.asyncio
async def test_deny_with_when_condition_true():
    """Deny rule with when: condition that evaluates to true blocks."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityDenial
    from llmos_bridge.apps.expression import ExpressionEngine

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    caps = CapabilitiesConfig(deny=[
        CapabilityDenial(module="os_exec", action="run_command", when="true", reason="Blocked by when"),
    ])
    executor = DaemonToolExecutor(
        module_registry=registry,
        capabilities=caps,
        expression_engine=ExpressionEngine(),
    )

    result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
    assert "error" in result
    assert "Blocked by when" in result["error"]


@pytest.mark.asyncio
async def test_deny_with_when_condition_false():
    """Deny rule with when: condition that evaluates to false passes."""
    from llmos_bridge.apps.models import CapabilitiesConfig, CapabilityDenial
    from llmos_bridge.apps.expression import ExpressionEngine

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    caps = CapabilitiesConfig(deny=[
        CapabilityDenial(module="os_exec", action="run_command", when="false", reason="Should not trigger"),
    ])
    executor = DaemonToolExecutor(
        module_registry=registry,
        capabilities=caps,
        expression_engine=ExpressionEngine(),
    )

    result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_approval_with_when_condition():
    """Approval rule with when: that evaluates to true blocks."""
    from llmos_bridge.apps.models import CapabilitiesConfig, ApprovalRule
    from llmos_bridge.apps.expression import ExpressionEngine

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    caps = CapabilitiesConfig(approval_required=[
        ApprovalRule(module="os_exec", action="run_command", when="true", message="Confirm?"),
    ])
    executor = DaemonToolExecutor(
        module_registry=registry,
        capabilities=caps,
        expression_engine=ExpressionEngine(),
    )

    result = await executor.execute("os_exec", "run_command", {"command": ["ls"]})
    assert "error" in result
    assert "APPROVAL" in result["error"]


# ─── Tests: Audit enforcement ────────────────────────────────────


@pytest.mark.asyncio
async def test_audit_level_none_skips_event():
    """Audit level 'none' suppresses all events."""
    from llmos_bridge.apps.models import CapabilitiesConfig, AuditConfig, AuditLevel

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    event_bus = FakeEventBus()
    caps = CapabilitiesConfig(audit=AuditConfig(level=AuditLevel.none))
    executor = DaemonToolExecutor(
        module_registry=registry,
        event_bus=event_bus,
        capabilities=caps,
    )
    executor.set_capabilities(caps)

    await executor.execute("fs", "read", {})
    assert len(event_bus.events) == 0


@pytest.mark.asyncio
async def test_audit_level_errors_skips_success():
    """Audit level 'errors' skips successful actions."""
    from llmos_bridge.apps.models import CapabilitiesConfig, AuditConfig, AuditLevel

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    event_bus = FakeEventBus()
    caps = CapabilitiesConfig(audit=AuditConfig(level=AuditLevel.errors))
    executor = DaemonToolExecutor(
        module_registry=registry,
        event_bus=event_bus,
        capabilities=caps,
    )
    executor.set_capabilities(caps)

    await executor.execute("fs", "read", {})
    assert len(event_bus.events) == 0


@pytest.mark.asyncio
async def test_audit_redacts_secrets():
    """Audit with redact_secrets=True hides secret-like params."""
    from llmos_bridge.apps.models import CapabilitiesConfig, AuditConfig, AuditLevel

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"http": module})
    event_bus = FakeEventBus()
    caps = CapabilitiesConfig(audit=AuditConfig(
        level=AuditLevel.full, log_params=True, redact_secrets=True,
    ))
    executor = DaemonToolExecutor(
        module_registry=registry,
        event_bus=event_bus,
        capabilities=caps,
    )
    executor.set_capabilities(caps)

    await executor.execute("http", "request", {"url": "https://x.com", "api_key": "sk-123"})
    assert len(event_bus.events) == 1
    _, event = event_bus.events[0]
    assert event["params"]["api_key"] == "***REDACTED***"
    assert event["params"]["url"] == "https://x.com"


# ─── Tests: Perception ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_perception_disabled_skips():
    """Perception disabled = no capture calls."""
    from llmos_bridge.apps.models import PerceptionAppConfig

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    executor = DaemonToolExecutor(
        module_registry=registry,
        perception_config=PerceptionAppConfig(enabled=False),
    )

    result = await executor.execute("fs", "read", {})
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_perception_enabled_no_module_degrades():
    """Perception enabled but perception module not available degrades gracefully."""
    from llmos_bridge.apps.models import PerceptionAppConfig

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})  # No perception module
    executor = DaemonToolExecutor(
        module_registry=registry,
        perception_config=PerceptionAppConfig(enabled=True, capture_after=True),
    )

    result = await executor.execute("fs", "read", {})
    assert result["ok"] is True  # Should not crash


# ─── Tests: Rate Limiting (IML ActionRateLimiter) ─────────────────


class FakeRateLimiter:
    """Minimal ActionRateLimiter stand-in."""

    def __init__(self, should_raise=False):
        self._should_raise = should_raise
        self.checked: list[tuple[str, int | None, int | None]] = []

    def check_or_raise(self, action_key, *, calls_per_minute=None, calls_per_hour=None):
        self.checked.append((action_key, calls_per_minute, calls_per_hour))
        if self._should_raise:
            from llmos_bridge.exceptions import RateLimitExceededError
            raise RateLimitExceededError(
                action_key=action_key, limit=calls_per_minute or 0, window="minute",
            )


@pytest.mark.asyncio
async def test_rate_limiter_blocks_when_exceeded():
    """Rate limiter blocks action when limit exceeded."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    limiter = FakeRateLimiter(should_raise=True)
    executor = DaemonToolExecutor(module_registry=registry, rate_limiter=limiter)

    # Set rate limit via tool constraints
    token = _current_scope.set(_ExecutionScope(
        tool_constraints={"fs.read_file": {"rate_limit_per_minute": 5}},
    ))
    try:
        result = await executor.execute("fs", "read_file", {"path": "/x"})
        assert "error" in result
        assert "RateLimitExceeded" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_rate_limiter_passes_when_within_limit():
    """Rate limiter allows action within limits."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    limiter = FakeRateLimiter(should_raise=False)
    executor = DaemonToolExecutor(module_registry=registry, rate_limiter=limiter)

    token = _current_scope.set(_ExecutionScope(
        tool_constraints={"fs.read_file": {"rate_limit_per_minute": 100}},
    ))
    try:
        result = await executor.execute("fs", "read_file", {"path": "/x"})
        assert result["ok"] is True
        assert len(limiter.checked) == 1
        assert limiter.checked[0] == ("fs.read_file", 100, None)
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_rate_limiter_skipped_when_no_config():
    """No rate limit config = rate limiter not invoked."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    limiter = FakeRateLimiter()
    executor = DaemonToolExecutor(module_registry=registry, rate_limiter=limiter)

    result = await executor.execute("fs", "read_file", {"path": "/x"})
    assert result["ok"] is True
    assert len(limiter.checked) == 0  # Not called


# ─── Tests: Intent Verification (IML IntentVerifier) ──────────────


class FakeIntentVerifier:
    """Minimal IntentVerifier stand-in."""

    def __init__(self, safe=True, strict=False):
        self._safe = safe
        self.enabled = True
        self.strict = strict
        self.verified: list[str] = []

    async def verify_action(self, action, *, plan_id="", plan_description=""):
        self.verified.append(f"{action.module}.{action.action}")
        from llmos_bridge.security.intent_verifier import VerificationResult, VerificationVerdict
        if self._safe:
            return VerificationResult(
                verdict=VerificationVerdict.APPROVE,
                reasoning="Looks safe",
            )
        return VerificationResult(
            verdict=VerificationVerdict.REJECT,
            reasoning="Suspicious activity detected",
            risk_level="high",
        )


@pytest.mark.asyncio
async def test_intent_verifier_blocks_suspicious():
    """IntentVerifier rejects suspicious actions."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"os_exec": module})
    verifier = FakeIntentVerifier(safe=False)
    executor = DaemonToolExecutor(
        module_registry=registry, intent_verifier=verifier,
    )

    result = await executor.execute("os_exec", "run_command", {"command": ["rm", "-rf", "/"]})
    assert "error" in result
    assert "IntentVerificationFailed" in result["error"]
    assert "Suspicious activity" in result["error"]


@pytest.mark.asyncio
async def test_intent_verifier_allows_safe():
    """IntentVerifier approves safe actions."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    verifier = FakeIntentVerifier(safe=True)
    executor = DaemonToolExecutor(
        module_registry=registry, intent_verifier=verifier,
    )

    result = await executor.execute("fs", "read_file", {"path": "/tmp/safe.txt"})
    assert result["ok"] is True
    assert len(verifier.verified) == 1


@pytest.mark.asyncio
async def test_intent_verifier_disabled_skips():
    """Disabled IntentVerifier is skipped."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    verifier = FakeIntentVerifier(safe=False)
    verifier.enabled = False  # Disabled
    executor = DaemonToolExecutor(
        module_registry=registry, intent_verifier=verifier,
    )

    result = await executor.execute("fs", "read_file", {"path": "/x"})
    assert result["ok"] is True  # Should pass despite verifier saying unsafe


# ─── Tests: ExecutionContext passed to modules ────────────────────


@pytest.mark.asyncio
async def test_execution_context_passed():
    """Module receives ExecutionContext with tracing info."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    executor = DaemonToolExecutor(module_registry=registry)

    await executor.execute("fs", "read_file", {"path": "/x"})

    assert module.last_context is not None
    assert module.last_context.action_id == "fs.read_file"
    assert module.last_context.extra["source"] == "yaml_app"


@pytest.mark.asyncio
async def test_execution_context_includes_identity():
    """ExecutionContext includes identity info when available."""
    from llmos_bridge.identity.models import IdentityContext

    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fs": module})
    executor = DaemonToolExecutor(module_registry=registry)

    token = _current_scope.set(_ExecutionScope(
        identity=IdentityContext(app_id="my-app", agent_id="agent-1", session_id="sess-1"),
    ))
    try:
        await executor.execute("fs", "read_file", {"path": "/x"})
        ctx = module.last_context
        assert ctx.session_id == "sess-1"
        assert ctx.extra["app_id"] == "my-app"
        assert ctx.extra["agent_id"] == "agent-1"
    finally:
        _current_scope.reset(token)


# ─── Tests: Per-tool timeout ─────────────────────────────────────


@pytest.mark.asyncio
async def test_timeout_triggers_on_slow_module():
    """Per-tool timeout fires when module exceeds configured timeout."""
    module = FakeModule(result={"ok": True}, delay=5)  # Would take 5s
    registry = FakeRegistry(modules={"slow": module})
    executor = DaemonToolExecutor(module_registry=registry)

    token = _current_scope.set(_ExecutionScope(
        tool_constraints={"slow.action": {"timeout": "0.1s"}},  # 100ms timeout
    ))
    try:
        result = await executor.execute("slow", "action", {})
        assert "error" in result
        assert "timed out" in result["error"] or "TimeoutError" in result["error"]
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_no_timeout_when_not_configured():
    """No timeout wrapping when timeout is not configured."""
    module = FakeModule(result={"ok": True})
    registry = FakeRegistry(modules={"fast": module})
    executor = DaemonToolExecutor(module_registry=registry)

    result = await executor.execute("fast", "action", {})
    assert result["ok"] is True


# ─── Tests: Per-tool retry ───────────────────────────────────────


@pytest.mark.asyncio
async def test_retry_on_failure():
    """Per-tool retry retries on failure up to max_retries."""
    call_count = 0
    original_result = {"ok": True}

    class RetryModule:
        async def execute(self, action, params, context=None):
            nonlocal call_count
            call_count += 1
            if call_count < 3:
                raise RuntimeError("transient failure")
            return original_result

    registry = FakeRegistry(modules={"flaky": RetryModule()})
    executor = DaemonToolExecutor(module_registry=registry)

    token = _current_scope.set(_ExecutionScope(
        tool_constraints={"flaky.action": {"max_retries": 3, "retry_backoff": "fixed"}},
    ))
    try:
        result = await executor.execute("flaky", "action", {})
        assert result["ok"] is True
        assert call_count == 3  # 1 initial + 2 retries
    finally:
        _current_scope.reset(token)


@pytest.mark.asyncio
async def test_retry_exhausted_returns_error():
    """When retries are exhausted, error is returned."""
    class AlwaysFails:
        async def execute(self, action, params, context=None):
            raise RuntimeError("permanent failure")

    registry = FakeRegistry(modules={"fail": AlwaysFails()})
    executor = DaemonToolExecutor(module_registry=registry)

    token = _current_scope.set(_ExecutionScope(
        tool_constraints={"fail.action": {"max_retries": 1, "retry_backoff": "fixed"}},
    ))
    try:
        result = await executor.execute("fail", "action", {})
        assert "error" in result
        assert "permanent failure" in result["error"]
    finally:
        _current_scope.reset(token)


# ─── Tests: YAML ToolConstraints rate limit fields ───────────────


def test_tool_constraints_rate_limit_fields():
    """ToolConstraints model has rate limit and retry fields."""
    from llmos_bridge.apps.models import ToolConstraints

    tc = ToolConstraints(
        rate_limit_per_minute=30,
        rate_limit_per_hour=500,
        max_retries=3,
        retry_backoff="linear",
        timeout="10s",
    )
    assert tc.rate_limit_per_minute == 30
    assert tc.rate_limit_per_hour == 500
    assert tc.max_retries == 3
    assert tc.retry_backoff == "linear"
    assert tc.timeout == "10s"


def test_tool_constraints_defaults():
    """ToolConstraints defaults: no rate limit, no retry."""
    from llmos_bridge.apps.models import ToolConstraints

    tc = ToolConstraints()
    assert tc.rate_limit_per_minute is None
    assert tc.rate_limit_per_hour is None
    assert tc.max_retries == 0
    assert tc.retry_backoff == "exponential"
    assert tc.timeout == ""


# ─── Tests: _parse_duration ──────────────────────────────────────


def test_parse_duration():
    """_parse_duration handles various time formats."""
    from llmos_bridge.apps.daemon_executor import _parse_duration

    assert _parse_duration("30s") == 30.0
    assert _parse_duration("5m") == 300.0
    assert _parse_duration("1h") == 3600.0
    assert _parse_duration("100ms") == 0.1
    assert _parse_duration("") == 0.0
    assert _parse_duration("invalid") == 0.0
