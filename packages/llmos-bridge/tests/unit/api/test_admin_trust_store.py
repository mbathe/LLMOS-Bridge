"""Tests for admin trust store API endpoints (Phase 2).

Covers:
  - GET  /admin/modules/trust-store   → list keys
  - POST /admin/modules/trust-store   → add key
  - DELETE /admin/modules/trust-store/{fp} → remove key
  - POST /admin/modules/verify-all    → verify checksums
  - POST /admin/modules/{id}/verify   → verify single
  - POST /admin/modules/bulk-rescan   → rescan all
"""

from __future__ import annotations

import base64
import hashlib
import json
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from llmos_bridge.api.routes.admin_modules import router
from llmos_bridge.hub.index import InstalledModule, ModuleIndex
from llmos_bridge.hub.trust_store import TrustStoreManager


@pytest.fixture()
async def index(tmp_path):
    idx = ModuleIndex(tmp_path / "test.db")
    await idx.init()
    yield idx
    await idx.close()


@pytest.fixture()
async def trust_store(tmp_path):
    store = TrustStoreManager(tmp_path / "trust_store", bootstrap=False)
    await store.init()
    return store


@pytest.fixture()
def app(index, trust_store):
    """Create a minimal FastAPI app with admin_modules routes."""
    from llmos_bridge.api.dependencies import verify_api_token, get_module_registry

    app = FastAPI()

    async def mock_auth():
        return None

    app.dependency_overrides[verify_api_token] = mock_auth

    async def mock_registry():
        reg = MagicMock()
        reg.is_available.return_value = True
        mm = MagicMock()
        reg.get.return_value = mm
        return reg

    app.dependency_overrides[get_module_registry] = mock_registry

    mock_settings = MagicMock()
    mock_settings.security.api_token = None
    app.state.settings = mock_settings
    app.state.module_index = index
    app.state.module_installer = MagicMock()
    app.state.trust_store = trust_store

    app.include_router(router)
    return app


@pytest.fixture()
async def client(app):
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


async def _add_test_module(index, module_id="test_mod", **kwargs):
    defaults = dict(
        module_id=module_id,
        version="1.0.0",
        install_path=f"/tmp/{module_id}",
        module_class_path=f"{module_id}.module:Mod",
        requirements=[],
        installed_at=time.time(),
        updated_at=time.time(),
        enabled=True,
        sandbox_level="basic",
        trust_tier="unverified",
        scan_score=90.0,
        scan_result_json=json.dumps({
            "verdict": "allow",
            "score": 90.0,
            "findings": [],
            "files_scanned": 2,
            "scan_duration_ms": 5.0,
        }),
        signature_status="unsigned",
        checksum="abc123",
    )
    defaults.update(kwargs)
    await index.add(InstalledModule(**defaults))


# ---------------------------------------------------------------------------
# Trust store CRUD
# ---------------------------------------------------------------------------


class TestListTrustStore:
    @pytest.mark.asyncio
    async def test_empty_store_returns_zero(self, client):
        resp = await client.get("/admin/modules/trust-store")
        assert resp.status_code == 200
        data = resp.json()
        assert data["keys"] == []
        assert data["total"] == 0

    @pytest.mark.asyncio
    async def test_lists_added_keys(self, client, trust_store):
        trust_store.add_key("Key A", b"\x01" * 32)
        resp = await client.get("/admin/modules/trust-store")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["keys"][0]["label"] == "Key A"
        assert data["keys"][0]["source"] == "manual"

    @pytest.mark.asyncio
    async def test_no_trust_store_returns_empty(self, app, client):
        app.state.trust_store = None
        resp = await client.get("/admin/modules/trust-store")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


class TestAddTrustedKey:
    @pytest.mark.asyncio
    async def test_add_valid_key(self, client):
        pub_bytes = b"\xaa" * 32
        b64_key = base64.b64encode(pub_bytes).decode()
        resp = await client.post(
            "/admin/modules/trust-store",
            json={"label": "Publisher X", "public_key_b64": b64_key},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["label"] == "Publisher X"
        expected_fp = hashlib.sha256(pub_bytes).hexdigest()
        assert data["fingerprint"] == expected_fp

    @pytest.mark.asyncio
    async def test_reject_invalid_base64(self, client):
        resp = await client.post(
            "/admin/modules/trust-store",
            json={"label": "Bad", "public_key_b64": "!!!not-base64!!!"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_reject_wrong_key_length(self, client):
        b64_key = base64.b64encode(b"\x00" * 16).decode()  # 16 bytes, not 32
        resp = await client.post(
            "/admin/modules/trust-store",
            json={"label": "Short", "public_key_b64": b64_key},
        )
        assert resp.status_code == 400
        assert "32-byte" in resp.json()["detail"]

    @pytest.mark.asyncio
    async def test_503_when_no_trust_store(self, app, client):
        app.state.trust_store = None
        b64_key = base64.b64encode(b"\x00" * 32).decode()
        resp = await client.post(
            "/admin/modules/trust-store",
            json={"label": "X", "public_key_b64": b64_key},
        )
        assert resp.status_code == 503


class TestRemoveTrustedKey:
    @pytest.mark.asyncio
    async def test_remove_existing_key(self, client, trust_store):
        key = trust_store.add_key("Removable", b"\xbb" * 32)
        resp = await client.delete(f"/admin/modules/trust-store/{key.fingerprint}")
        assert resp.status_code == 200
        assert resp.json()["removed"] is True
        # Verify it's actually gone
        assert trust_store.get_key(key.fingerprint) is None

    @pytest.mark.asyncio
    async def test_404_for_unknown_fingerprint(self, client):
        resp = await client.delete("/admin/modules/trust-store/deadbeef123")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_503_when_no_trust_store(self, app, client):
        app.state.trust_store = None
        resp = await client.delete("/admin/modules/trust-store/deadbeef123")
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# Verify endpoints
# ---------------------------------------------------------------------------


class TestVerifyAll:
    @pytest.mark.asyncio
    async def test_verify_all_no_modules(self, client):
        resp = await client.post("/admin/modules/verify-all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["verified"] == 0
        assert data["tampered"] == 0
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_verify_all_detects_tampered(self, client, index, tmp_path):
        # Create a real module dir with known content
        mod_dir = tmp_path / "mod_a"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "module.py").write_text("class A: pass\n")

        # Compute real checksum
        from llmos_bridge.modules.signing import ModuleSigner
        real_hash = ModuleSigner.compute_module_hash(mod_dir)

        # Register one module with correct checksum, one with wrong
        await _add_test_module(index, module_id="good_mod", install_path=str(mod_dir), checksum=real_hash)
        await _add_test_module(index, module_id="bad_mod", install_path=str(mod_dir), checksum="wrong_hash_123")

        resp = await client.post("/admin/modules/verify-all")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 2
        assert data["verified"] == 1
        assert data["tampered"] == 1

    @pytest.mark.asyncio
    async def test_verify_all_marks_tampered_in_index(self, client, index, tmp_path):
        mod_dir = tmp_path / "mod_tampered"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")

        await _add_test_module(
            index, module_id="tampered_mod",
            install_path=str(mod_dir), checksum="invalid_checksum",
        )

        resp = await client.post("/admin/modules/verify-all")
        assert resp.status_code == 200

        # Check that index was updated
        mod = await index.get("tampered_mod")
        assert mod.signature_status == "tampered"


class TestVerifySingle:
    @pytest.mark.asyncio
    async def test_verify_valid_module(self, client, index, tmp_path):
        mod_dir = tmp_path / "valid_mod"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "module.py").write_text("class V: pass\n")

        from llmos_bridge.modules.signing import ModuleSigner
        real_hash = ModuleSigner.compute_module_hash(mod_dir)

        await _add_test_module(
            index, module_id="valid_mod",
            install_path=str(mod_dir), checksum=real_hash,
        )

        resp = await client.post("/admin/modules/valid_mod/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module_id"] == "valid_mod"
        assert data["checksum_valid"] is True

    @pytest.mark.asyncio
    async def test_verify_tampered_module(self, client, index, tmp_path):
        mod_dir = tmp_path / "tampered_single"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")

        await _add_test_module(
            index, module_id="tampered_single",
            install_path=str(mod_dir), checksum="wrong_hash",
        )

        resp = await client.post("/admin/modules/tampered_single/verify")
        assert resp.status_code == 200
        data = resp.json()
        assert data["checksum_valid"] is False
        assert data["signature_status"] == "tampered"

    @pytest.mark.asyncio
    async def test_verify_404_for_missing(self, client):
        resp = await client.post("/admin/modules/nonexistent/verify")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Bulk rescan
# ---------------------------------------------------------------------------


class TestBulkRescan:
    @pytest.mark.asyncio
    async def test_bulk_rescan_empty(self, client):
        resp = await client.post("/admin/modules/bulk-rescan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 0
        assert data["scanned"] == 0

    @pytest.mark.asyncio
    async def test_bulk_rescan_clean_module(self, client, index, tmp_path):
        mod_dir = tmp_path / "clean_mod"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "module.py").write_text("class Clean: pass\n")

        await _add_test_module(
            index, module_id="clean_mod",
            install_path=str(mod_dir),
        )

        resp = await client.post("/admin/modules/bulk-rescan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["scanned"] == 1
        assert data["passed"] >= 1

    @pytest.mark.asyncio
    async def test_bulk_rescan_missing_path_counts_as_failed(self, client, index):
        await _add_test_module(
            index, module_id="missing_mod",
            install_path="/nonexistent/dir",
        )

        resp = await client.post("/admin/modules/bulk-rescan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["failed"] == 1
        assert data["scanned"] == 0
