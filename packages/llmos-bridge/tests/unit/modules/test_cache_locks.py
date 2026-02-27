"""Unit tests for module cache locks — verify per-path threading.Lock protection."""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestExcelCacheLocks:
    """Test ExcelModule lock infrastructure."""

    def test_lock_creation(self) -> None:
        """_get_path_lock creates a lock on first call."""
        from llmos_bridge.modules.excel.module import ExcelModule

        with patch.object(ExcelModule, "_check_dependencies"):
            mod = ExcelModule()

        lock = mod._get_path_lock("/tmp/test.xlsx")
        assert isinstance(lock, threading.Lock)
        assert len(mod._path_locks) == 1

    def test_same_path_returns_same_lock(self) -> None:
        """Same resolved path returns the same lock instance."""
        from llmos_bridge.modules.excel.module import ExcelModule

        with patch.object(ExcelModule, "_check_dependencies"):
            mod = ExcelModule()

        lock1 = mod._get_path_lock("/tmp/test.xlsx")
        lock2 = mod._get_path_lock("/tmp/test.xlsx")
        assert lock1 is lock2

    def test_different_paths_return_different_locks(self) -> None:
        """Different files get separate locks."""
        from llmos_bridge.modules.excel.module import ExcelModule

        with patch.object(ExcelModule, "_check_dependencies"):
            mod = ExcelModule()

        lock1 = mod._get_path_lock("/tmp/file1.xlsx")
        lock2 = mod._get_path_lock("/tmp/file2.xlsx")
        assert lock1 is not lock2
        assert len(mod._path_locks) == 2

    def test_path_resolution(self) -> None:
        """Relative and absolute paths to the same file share a lock."""
        from llmos_bridge.modules.excel.module import ExcelModule

        with patch.object(ExcelModule, "_check_dependencies"):
            mod = ExcelModule()

        abs_path = str(Path("/tmp/test.xlsx").resolve())
        lock1 = mod._get_path_lock(abs_path)
        lock2 = mod._get_path_lock("/tmp/test.xlsx")
        assert lock1 is lock2

    def test_meta_lock_is_threading(self) -> None:
        """The meta-lock should be a threading.Lock, not asyncio.Lock."""
        from llmos_bridge.modules.excel.module import ExcelModule

        with patch.object(ExcelModule, "_check_dependencies"):
            mod = ExcelModule()

        assert isinstance(mod._meta_lock, type(threading.Lock()))


class TestWordCacheLocks:
    """Test WordModule lock infrastructure."""

    def test_lock_creation(self) -> None:
        from llmos_bridge.modules.word.module import WordModule

        with patch.object(WordModule, "_check_dependencies"):
            mod = WordModule()

        lock = mod._get_path_lock("/tmp/test.docx")
        assert isinstance(lock, threading.Lock)

    def test_same_path_reuses_lock(self) -> None:
        from llmos_bridge.modules.word.module import WordModule

        with patch.object(WordModule, "_check_dependencies"):
            mod = WordModule()

        assert mod._get_path_lock("/tmp/a.docx") is mod._get_path_lock("/tmp/a.docx")


class TestPowerPointCacheLocks:
    """Test PowerPointModule lock infrastructure."""

    def test_lock_creation(self) -> None:
        from llmos_bridge.modules.powerpoint.module import PowerPointModule

        with patch.object(PowerPointModule, "_check_dependencies"):
            mod = PowerPointModule()

        lock = mod._get_path_lock("/tmp/test.pptx")
        assert isinstance(lock, threading.Lock)


class TestApiHttpSessionLock:
    """Test ApiHttpModule asyncio session lock."""

    def test_session_lock_exists(self) -> None:
        import asyncio
        from llmos_bridge.modules.api_http.module import ApiHttpModule

        with patch.object(ApiHttpModule, "_check_dependencies"):
            mod = ApiHttpModule()

        assert hasattr(mod, "_session_lock")
        assert isinstance(mod._session_lock, asyncio.Lock)
