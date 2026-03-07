"""Tests for validation — module tarball validation for publishing."""

from __future__ import annotations

import io
import tarfile
import tempfile
from pathlib import Path

import pytest

from llmos_hub.validation import validate_for_publish


def _make_tarball(files: dict[str, str], root_name: str = "test_mod") -> bytes:
    """Create an in-memory .tar.gz with the given files."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=f"{root_name}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestValidation:
    async def test_valid_module_scores_high(self):
        files = {
            "llmos-module.toml": 'module_id = "test_mod"\nversion = "1.0.0"\ndescription = "Test"\nauthor = "Alice"\nactions = "do_stuff"\n',
            "module.py": "class TestMod:\n    def _action_do_stuff(self): pass\n",
            "params.py": "class DoStuffParams: pass\n",
            "README.md": "# Test Module\n## Overview\nA test module.\n## Usage\nJust use it.\n",
            "CHANGELOG.md": "# 1.0.0\n- Initial release\n",
            "docs/actions.md": "# Actions\n## do_stuff\nDoes stuff.\n",
            "docs/integration.md": "# Integration\nHow to integrate.\n",
        }
        data = _make_tarball(files)
        result = await validate_for_publish(data, min_score=70)
        assert result.hub_ready is True
        assert result.score >= 70
        assert result.module_id == "test_mod"
        assert result.version == "1.0.0"
        assert not result.issues

    async def test_invalid_tarball(self):
        result = await validate_for_publish(b"not a tarball", min_score=70)
        assert result.hub_ready is False
        assert len(result.issues) > 0

    async def test_path_traversal_rejected(self):
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            data = b"evil"
            info = tarfile.TarInfo(name="../../../etc/passwd")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        result = await validate_for_publish(buf.getvalue())
        assert result.hub_ready is False
        assert any("traversal" in i.lower() for i in result.issues)

    async def test_missing_toml_scores_low(self):
        files = {
            "module.py": "class Mod: pass\n",
        }
        data = _make_tarball(files)
        result = await validate_for_publish(data, min_score=70)
        assert result.hub_ready is False
        assert result.score < 70
