"""Tests for admin module security API endpoints."""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from llmos_bridge.api.routes.admin_modules import router
from llmos_bridge.hub.index import InstalledModule, ModuleIndex


@pytest.fixture()
async def index(tmp_path):
    idx = ModuleIndex(tmp_path / "test.db")
    await idx.init()
    yield idx
    await idx.close()


@pytest.fixture()
def app(index):
    """Create a minimal FastAPI app with admin_modules routes."""
    from llmos_bridge.api.dependencies import verify_api_token, get_module_registry

    app = FastAPI()

    # Override auth to no-op
    async def mock_auth():
        return None

    app.dependency_overrides[verify_api_token] = mock_auth

    # Override registry
    async def mock_registry():
        reg = MagicMock()
        reg.is_available.return_value = True
        mm = MagicMock()
        reg.get.return_value = mm
        return reg

    app.dependency_overrides[get_module_registry] = mock_registry

    # Set up app.state (settings needed for auth bypass)
    mock_settings = MagicMock()
    mock_settings.security.api_token = None
    app.state.settings = mock_settings
    app.state.module_index = index
    app.state.module_installer = MagicMock()

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
        scan_score=85.0,
        scan_result_json=json.dumps({
            "verdict": "allow",
            "score": 85.0,
            "findings": [
                {"rule_id": "sc_requests_call", "severity": 0.3, "file_path": "mod.py", "line_number": 5}
            ],
            "files_scanned": 3,
            "scan_duration_ms": 12.5,
        }),
        signature_status="unsigned",
        checksum="abc123",
    )
    defaults.update(kwargs)
    await index.add(InstalledModule(**defaults))


class TestGetModuleSecurity:
    @pytest.mark.asyncio
    async def test_returns_security_data(self, client, index):
        await _add_test_module(index)
        resp = await client.get("/admin/modules/test_mod/security")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module_id"] == "test_mod"
        assert data["trust_tier"] == "unverified"
        assert data["scan_score"] == 85.0
        assert data["signature_status"] == "unsigned"
        assert data["checksum"] == "abc123"
        assert data["findings_count"] == 1

    @pytest.mark.asyncio
    async def test_404_for_missing_module(self, client):
        resp = await client.get("/admin/modules/nonexistent/security")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_findings_count_zero_when_no_json(self, client, index):
        await _add_test_module(index, scan_result_json="")
        resp = await client.get("/admin/modules/test_mod/security")
        assert resp.status_code == 200
        assert resp.json()["findings_count"] == 0


class TestRescanModule:
    @pytest.mark.asyncio
    async def test_rescan_updates_data(self, client, index, tmp_path):
        # Create a real module directory
        mod_dir = tmp_path / "test_mod"
        mod_dir.mkdir()
        (mod_dir / "__init__.py").write_text("")
        (mod_dir / "module.py").write_text("class Mod: pass\n")

        await _add_test_module(index, install_path=str(mod_dir))
        resp = await client.post("/admin/modules/test_mod/rescan")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module_id"] == "test_mod"
        assert "scan_score" in data
        assert "verdict" in data
        assert "trust_tier" in data

    @pytest.mark.asyncio
    async def test_rescan_404_for_missing(self, client):
        resp = await client.post("/admin/modules/nonexistent/rescan")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_rescan_422_for_missing_path(self, client, index):
        await _add_test_module(index, install_path="/nonexistent/path")
        resp = await client.post("/admin/modules/test_mod/rescan")
        assert resp.status_code == 422


class TestSetModuleTrust:
    @pytest.mark.asyncio
    async def test_set_trust_tier(self, client, index):
        await _add_test_module(index)
        resp = await client.put(
            "/admin/modules/test_mod/trust",
            json={"trust_tier": "verified", "reason": "Manual review passed"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["trust_tier"] == "verified"
        assert data["previous_tier"] == "unverified"

        # Verify in index
        m = await index.get("test_mod")
        assert m.trust_tier == "verified"

    @pytest.mark.asyncio
    async def test_reject_official_tier(self, client, index):
        await _add_test_module(index)
        resp = await client.put(
            "/admin/modules/test_mod/trust",
            json={"trust_tier": "official"},
        )
        assert resp.status_code == 403

    @pytest.mark.asyncio
    async def test_reject_invalid_tier(self, client, index):
        await _add_test_module(index)
        resp = await client.put(
            "/admin/modules/test_mod/trust",
            json={"trust_tier": "super_trusted"},
        )
        assert resp.status_code == 400

    @pytest.mark.asyncio
    async def test_404_for_missing_module(self, client):
        resp = await client.put(
            "/admin/modules/nonexistent/trust",
            json={"trust_tier": "verified"},
        )
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_set_trusted_tier(self, client, index):
        await _add_test_module(index)
        resp = await client.put(
            "/admin/modules/test_mod/trust",
            json={"trust_tier": "trusted"},
        )
        assert resp.status_code == 200
        assert resp.json()["trust_tier"] == "trusted"


class TestGetScanReport:
    @pytest.mark.asyncio
    async def test_returns_full_report(self, client, index):
        await _add_test_module(index)
        resp = await client.get("/admin/modules/test_mod/scan-report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module_id"] == "test_mod"
        assert data["scan_score"] == 85.0
        assert data["verdict"] == "allow"
        assert len(data["findings"]) == 1
        assert data["files_scanned"] == 3

    @pytest.mark.asyncio
    async def test_empty_report_when_no_scan(self, client, index):
        await _add_test_module(index, scan_result_json="", scan_score=-1.0)
        resp = await client.get("/admin/modules/test_mod/scan-report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["findings"] == []
        assert data["verdict"] == "unknown"

    @pytest.mark.asyncio
    async def test_404_for_missing_module(self, client):
        resp = await client.get("/admin/modules/nonexistent/scan-report")
        assert resp.status_code == 404

    @pytest.mark.asyncio
    async def test_handles_malformed_json(self, client, index):
        await _add_test_module(index, scan_result_json="not-json{{{")
        resp = await client.get("/admin/modules/test_mod/scan-report")
        assert resp.status_code == 200
        data = resp.json()
        assert data["findings"] == []


class TestInstalledModulesIncludesSecurityFields:
    @pytest.mark.asyncio
    async def test_list_includes_trust_and_scan(self, client, index):
        await _add_test_module(index, trust_tier="verified", scan_score=92.0, signature_status="signed")
        resp = await client.get("/admin/modules/installed")
        assert resp.status_code == 200
        modules = resp.json()["modules"]
        assert len(modules) == 1
        m = modules[0]
        assert m["trust_tier"] == "verified"
        assert m["scan_score"] == 92.0
        assert m["signature_status"] == "signed"
