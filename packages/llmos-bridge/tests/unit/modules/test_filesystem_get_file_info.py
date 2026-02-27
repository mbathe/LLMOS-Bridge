"""Tests — FilesystemModule.get_file_info security decorator coverage."""
from __future__ import annotations

import pytest

from llmos_bridge.modules.filesystem.module import FilesystemModule
from llmos_bridge.security.decorators import collect_security_metadata


class TestFilesystemGetFileInfo:
    def test_get_file_info_requires_read_permission(self):
        module = FilesystemModule()
        meta = collect_security_metadata(module._action_get_file_info)
        assert "filesystem.read" in meta.get("permissions", [])
