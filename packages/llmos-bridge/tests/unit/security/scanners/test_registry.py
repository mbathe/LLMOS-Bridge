"""Unit tests — ScannerRegistry."""

import pytest

from llmos_bridge.security.scanners.base import (
    InputScanner,
    ScanContext,
    ScanResult,
    ScanVerdict,
)
from llmos_bridge.security.scanners.registry import ScannerRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeScanner(InputScanner):
    def __init__(self, sid: str = "fake", priority: int = 50) -> None:
        self.scanner_id = sid  # type: ignore[misc]
        self.priority = priority  # type: ignore[misc]

    async def scan(self, text: str, context: ScanContext | None = None) -> ScanResult:
        return ScanResult(scanner_id=self.scanner_id, verdict=ScanVerdict.ALLOW)


class _FailCloseScanner(_FakeScanner):
    async def close(self) -> None:
        raise RuntimeError("close failed")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestScannerRegistry:
    def test_register_and_get(self) -> None:
        reg = ScannerRegistry()
        s = _FakeScanner("foo")
        reg.register(s)
        assert reg.get("foo") is s

    def test_get_nonexistent(self) -> None:
        reg = ScannerRegistry()
        assert reg.get("missing") is None

    def test_register_overwrites(self) -> None:
        reg = ScannerRegistry()
        s1 = _FakeScanner("foo", priority=10)
        s2 = _FakeScanner("foo", priority=20)
        reg.register(s1)
        reg.register(s2)
        assert reg.get("foo") is s2

    def test_unregister_existing(self) -> None:
        reg = ScannerRegistry()
        reg.register(_FakeScanner("foo"))
        assert reg.unregister("foo") is True
        assert reg.get("foo") is None

    def test_unregister_nonexistent(self) -> None:
        reg = ScannerRegistry()
        assert reg.unregister("nope") is False

    def test_enable_disable(self) -> None:
        reg = ScannerRegistry()
        reg.register(_FakeScanner("s1"))
        assert reg.is_enabled("s1") is True

        reg.disable("s1")
        assert reg.is_enabled("s1") is False

        reg.enable("s1")
        assert reg.is_enabled("s1") is True

    def test_enable_nonexistent_returns_false(self) -> None:
        reg = ScannerRegistry()
        assert reg.enable("nope") is False

    def test_disable_nonexistent_returns_false(self) -> None:
        reg = ScannerRegistry()
        assert reg.disable("nope") is False

    def test_list_all_sorted_by_priority(self) -> None:
        reg = ScannerRegistry()
        reg.register(_FakeScanner("s2", priority=20))
        reg.register(_FakeScanner("s1", priority=10))
        reg.register(_FakeScanner("s3", priority=30))
        all_s = reg.list_all()
        ids = [s.scanner_id for s in all_s]
        assert ids == ["s1", "s2", "s3"]

    def test_list_enabled_excludes_disabled(self) -> None:
        reg = ScannerRegistry()
        reg.register(_FakeScanner("s1", priority=10))
        reg.register(_FakeScanner("s2", priority=20))
        reg.disable("s2")
        enabled = reg.list_enabled()
        ids = [s.scanner_id for s in enabled]
        assert ids == ["s1"]

    def test_list_enabled_sorted_by_priority(self) -> None:
        reg = ScannerRegistry()
        reg.register(_FakeScanner("s3", priority=30))
        reg.register(_FakeScanner("s1", priority=10))
        enabled = reg.list_enabled()
        ids = [s.scanner_id for s in enabled]
        assert ids == ["s1", "s3"]

    def test_to_dict_list(self) -> None:
        reg = ScannerRegistry()
        reg.register(_FakeScanner("s1", priority=10))
        reg.register(_FakeScanner("s2", priority=20))
        reg.disable("s2")
        dicts = reg.to_dict_list()
        assert len(dicts) == 2
        assert dicts[0]["scanner_id"] == "s1"
        assert dicts[0]["enabled"] is True
        assert dicts[1]["scanner_id"] == "s2"
        assert dicts[1]["enabled"] is False

    def test_on_change_callback(self) -> None:
        reg = ScannerRegistry()
        calls: list[str] = []
        reg.set_on_change(lambda: calls.append("changed"))

        reg.register(_FakeScanner("s1"))
        assert len(calls) == 1

        reg.enable("s1")
        assert len(calls) == 2

        reg.disable("s1")
        assert len(calls) == 3

        reg.unregister("s1")
        assert len(calls) == 4

    def test_on_change_not_called_on_noop_unregister(self) -> None:
        reg = ScannerRegistry()
        calls: list[str] = []
        reg.set_on_change(lambda: calls.append("changed"))
        reg.unregister("nope")
        assert calls == []

    @pytest.mark.asyncio
    async def test_close_all(self) -> None:
        reg = ScannerRegistry()
        s1 = _FakeScanner("s1")
        s2 = _FakeScanner("s2")
        reg.register(s1)
        reg.register(s2)
        await reg.close_all()  # Should not raise.

    @pytest.mark.asyncio
    async def test_close_all_tolerates_errors(self) -> None:
        reg = ScannerRegistry()
        reg.register(_FailCloseScanner("fail"))
        reg.register(_FakeScanner("ok"))
        await reg.close_all()  # Should not raise.

    def test_is_enabled_nonexistent(self) -> None:
        reg = ScannerRegistry()
        assert reg.is_enabled("nope") is False

    def test_set_on_change_none(self) -> None:
        reg = ScannerRegistry()
        calls: list[str] = []
        reg.set_on_change(lambda: calls.append("x"))
        reg.register(_FakeScanner("s1"))
        assert len(calls) == 1

        reg.set_on_change(None)
        reg.register(_FakeScanner("s2"))
        assert len(calls) == 1  # No new calls.
