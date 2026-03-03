"""Tests for hub.index security extensions — trust tier, scan score, etc."""

from __future__ import annotations

import json
import time

import pytest

from llmos_bridge.hub.index import InstalledModule, ModuleIndex


@pytest.fixture()
async def index(tmp_path):
    idx = ModuleIndex(tmp_path / "test_modules.db")
    await idx.init()
    yield idx
    await idx.close()


def _make_module(module_id: str = "test_mod", **overrides) -> InstalledModule:
    defaults = dict(
        module_id=module_id,
        version="1.0.0",
        install_path=f"/tmp/{module_id}",
        module_class_path=f"{module_id}.module:TestModule",
        requirements=["requests>=2.0"],
        installed_at=time.time(),
        updated_at=time.time(),
        enabled=True,
        sandbox_level="basic",
    )
    defaults.update(overrides)
    return InstalledModule(**defaults)


class TestSecurityFieldDefaults:
    @pytest.mark.asyncio
    async def test_default_trust_tier(self, index):
        await index.add(_make_module())
        m = await index.get("test_mod")
        assert m.trust_tier == "unverified"

    @pytest.mark.asyncio
    async def test_default_scan_score(self, index):
        await index.add(_make_module())
        m = await index.get("test_mod")
        assert m.scan_score == -1.0

    @pytest.mark.asyncio
    async def test_default_signature_status(self, index):
        await index.add(_make_module())
        m = await index.get("test_mod")
        assert m.signature_status == "unsigned"

    @pytest.mark.asyncio
    async def test_default_checksum_empty(self, index):
        await index.add(_make_module())
        m = await index.get("test_mod")
        assert m.checksum == ""

    @pytest.mark.asyncio
    async def test_default_publisher_id_empty(self, index):
        await index.add(_make_module())
        m = await index.get("test_mod")
        assert m.publisher_id == ""


class TestSecurityFieldPersistence:
    @pytest.mark.asyncio
    async def test_trust_tier_preserved(self, index):
        mod = _make_module(trust_tier="verified")
        await index.add(mod)
        m = await index.get("test_mod")
        assert m.trust_tier == "verified"

    @pytest.mark.asyncio
    async def test_scan_score_preserved(self, index):
        mod = _make_module(scan_score=85.5)
        await index.add(mod)
        m = await index.get("test_mod")
        assert m.scan_score == 85.5

    @pytest.mark.asyncio
    async def test_scan_result_json_preserved(self, index):
        findings = json.dumps([{"rule_id": "sc_eval", "severity": 0.8}])
        mod = _make_module(scan_result_json=findings)
        await index.add(mod)
        m = await index.get("test_mod")
        assert m.scan_result_json == findings

    @pytest.mark.asyncio
    async def test_signature_status_preserved(self, index):
        mod = _make_module(signature_status="verified")
        await index.add(mod)
        m = await index.get("test_mod")
        assert m.signature_status == "verified"

    @pytest.mark.asyncio
    async def test_checksum_preserved(self, index):
        mod = _make_module(checksum="abc123def456")
        await index.add(mod)
        m = await index.get("test_mod")
        assert m.checksum == "abc123def456"


class TestUpdateSecurityData:
    @pytest.mark.asyncio
    async def test_update_all_fields(self, index):
        await index.add(_make_module())
        await index.update_security_data(
            "test_mod",
            trust_tier="trusted",
            scan_score=92.0,
            scan_result_json='{"verdict": "allow"}',
            signature_status="verified",
            checksum="sha256abc",
        )
        m = await index.get("test_mod")
        assert m.trust_tier == "trusted"
        assert m.scan_score == 92.0
        assert m.scan_result_json == '{"verdict": "allow"}'
        assert m.signature_status == "verified"
        assert m.checksum == "sha256abc"

    @pytest.mark.asyncio
    async def test_update_partial_fields(self, index):
        await index.add(_make_module())
        await index.update_security_data("test_mod", trust_tier="verified")
        m = await index.get("test_mod")
        assert m.trust_tier == "verified"
        assert m.scan_score == -1.0  # Unchanged

    @pytest.mark.asyncio
    async def test_update_sets_updated_at(self, index):
        mod = _make_module()
        await index.add(mod)
        old_updated = (await index.get("test_mod")).updated_at
        import asyncio
        await asyncio.sleep(0.01)
        await index.update_security_data("test_mod", trust_tier="verified")
        new_updated = (await index.get("test_mod")).updated_at
        assert new_updated > old_updated

    @pytest.mark.asyncio
    async def test_update_noop_if_no_fields(self, index):
        await index.add(_make_module())
        # No fields specified → should not error
        await index.update_security_data("test_mod")
        m = await index.get("test_mod")
        assert m.trust_tier == "unverified"  # Unchanged


class TestUpdateTrustTier:
    @pytest.mark.asyncio
    async def test_update_trust_tier(self, index):
        await index.add(_make_module())
        await index.update_trust_tier("test_mod", "trusted")
        m = await index.get("test_mod")
        assert m.trust_tier == "trusted"

    @pytest.mark.asyncio
    async def test_update_trust_tier_back_to_unverified(self, index):
        mod = _make_module(trust_tier="verified")
        await index.add(mod)
        await index.update_trust_tier("test_mod", "unverified")
        m = await index.get("test_mod")
        assert m.trust_tier == "unverified"


class TestGetSecurityData:
    @pytest.mark.asyncio
    async def test_returns_dict(self, index):
        mod = _make_module(trust_tier="verified", scan_score=80.0, checksum="abc")
        await index.add(mod)
        data = await index.get_security_data("test_mod")
        assert data is not None
        assert data["module_id"] == "test_mod"
        assert data["trust_tier"] == "verified"
        assert data["scan_score"] == 80.0
        assert data["checksum"] == "abc"

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self, index):
        data = await index.get_security_data("nonexistent")
        assert data is None

    @pytest.mark.asyncio
    async def test_contains_all_security_fields(self, index):
        await index.add(_make_module())
        data = await index.get_security_data("test_mod")
        expected_keys = {
            "module_id", "trust_tier", "scan_score",
            "scan_result_json", "signature_status", "publisher_id", "checksum",
        }
        assert set(data.keys()) == expected_keys


class TestSchemaMigration:
    @pytest.mark.asyncio
    async def test_double_init_is_safe(self, tmp_path):
        """Calling init() twice should not error (migrations are idempotent)."""
        idx = ModuleIndex(tmp_path / "test.db")
        await idx.init()
        await idx.init()  # Should not raise
        await idx.add(_make_module())
        m = await idx.get("test_mod")
        assert m is not None
        assert m.trust_tier == "unverified"
        await idx.close()

    @pytest.mark.asyncio
    async def test_list_all_includes_security_fields(self, index):
        mod = _make_module(trust_tier="trusted", scan_score=95.0)
        await index.add(mod)
        all_mods = await index.list_all()
        assert len(all_mods) == 1
        assert all_mods[0].trust_tier == "trusted"
        assert all_mods[0].scan_score == 95.0
