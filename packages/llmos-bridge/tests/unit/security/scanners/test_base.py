"""Unit tests — Scanner base abstractions (ScanVerdict, ScanResult, ScanContext, InputScanner)."""

import pytest

from llmos_bridge.security.scanners.base import (
    InputScanner,
    ScanContext,
    ScanResult,
    ScanVerdict,
)


# ---------------------------------------------------------------------------
# ScanVerdict
# ---------------------------------------------------------------------------


class TestScanVerdict:
    def test_values(self) -> None:
        assert ScanVerdict.ALLOW == "allow"
        assert ScanVerdict.WARN == "warn"
        assert ScanVerdict.REJECT == "reject"

    def test_is_str_enum(self) -> None:
        assert isinstance(ScanVerdict.ALLOW, str)


# ---------------------------------------------------------------------------
# ScanResult
# ---------------------------------------------------------------------------


class TestScanResult:
    def test_defaults(self) -> None:
        r = ScanResult(scanner_id="test", verdict=ScanVerdict.ALLOW)
        assert r.risk_score == 0.0
        assert r.threat_types == []
        assert r.details == ""
        assert r.matched_patterns == []
        assert r.scan_duration_ms == 0.0
        assert r.metadata == {}

    def test_to_dict(self) -> None:
        r = ScanResult(
            scanner_id="heuristic",
            verdict=ScanVerdict.REJECT,
            risk_score=0.9,
            threat_types=["prompt_injection"],
            details="Matched 1 pattern",
            matched_patterns=["pi_ignore_instructions"],
            scan_duration_ms=0.5,
            metadata={"extra": True},
        )
        d = r.to_dict()
        assert d["scanner_id"] == "heuristic"
        assert d["verdict"] == "reject"
        assert d["risk_score"] == 0.9
        assert d["threat_types"] == ["prompt_injection"]
        assert d["matched_patterns"] == ["pi_ignore_instructions"]
        assert d["metadata"]["extra"] is True

    def test_to_dict_verdict_serialized_as_string(self) -> None:
        r = ScanResult(scanner_id="x", verdict=ScanVerdict.WARN)
        assert r.to_dict()["verdict"] == "warn"


# ---------------------------------------------------------------------------
# ScanContext
# ---------------------------------------------------------------------------


class TestScanContext:
    def test_defaults(self) -> None:
        ctx = ScanContext()
        assert ctx.plan_id == ""
        assert ctx.plan_description == ""
        assert ctx.action_count == 0
        assert ctx.module_ids == []
        assert ctx.session_id is None
        assert ctx.extra == {}

    def test_custom_values(self) -> None:
        ctx = ScanContext(
            plan_id="plan-1",
            plan_description="Test plan",
            action_count=5,
            module_ids=["filesystem", "os_exec"],
            session_id="sess-1",
            extra={"key": "value"},
        )
        assert ctx.plan_id == "plan-1"
        assert ctx.action_count == 5
        assert ctx.module_ids == ["filesystem", "os_exec"]


# ---------------------------------------------------------------------------
# InputScanner ABC
# ---------------------------------------------------------------------------


class _StubScanner(InputScanner):
    scanner_id = "stub"
    priority = 42
    version = "0.1.0"
    description = "Test stub"

    async def scan(self, text: str, context: ScanContext | None = None) -> ScanResult:
        return ScanResult(scanner_id=self.scanner_id, verdict=ScanVerdict.ALLOW)


class TestInputScanner:
    def test_class_attrs(self) -> None:
        s = _StubScanner()
        assert s.scanner_id == "stub"
        assert s.priority == 42
        assert s.version == "0.1.0"
        assert s.description == "Test stub"

    @pytest.mark.asyncio
    async def test_scan(self) -> None:
        s = _StubScanner()
        r = await s.scan("hello")
        assert r.verdict == ScanVerdict.ALLOW

    @pytest.mark.asyncio
    async def test_close_is_noop(self) -> None:
        s = _StubScanner()
        await s.close()  # Should not raise.

    def test_status(self) -> None:
        s = _StubScanner()
        st = s.status()
        assert st["scanner_id"] == "stub"
        assert st["priority"] == 42
        assert st["version"] == "0.1.0"
        assert st["description"] == "Test stub"

    def test_cannot_instantiate_abstract(self) -> None:
        with pytest.raises(TypeError):
            InputScanner()  # type: ignore[abstract]
