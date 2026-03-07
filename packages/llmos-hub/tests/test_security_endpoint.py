"""Tests for the GET /modules/{id}/security endpoint (Phase 4)."""

from __future__ import annotations

import io
import json
import tarfile
import time
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from llmos_hub.auth import generate_api_key, hash_api_key
from llmos_hub.config import HubServerSettings
from llmos_hub.models import ModuleRecord, VersionRecord
from llmos_hub.scanner import HubScanFinding, HubScanResult, ScanVerdict
from llmos_hub.storage import PackageStorage
from llmos_hub.store import HubStore


@pytest.fixture()
async def hub_app(tmp_path):
    settings = HubServerSettings(
        data_dir=str(tmp_path / "hub"),
        min_publish_score=0,
    )
    store = HubStore(str(settings.resolved_db_path))
    settings.resolved_data_dir.mkdir(parents=True, exist_ok=True)
    settings.resolved_packages_dir.mkdir(parents=True, exist_ok=True)
    await store.init()

    storage = PackageStorage(settings.resolved_packages_dir)

    app = FastAPI()
    app.state.store = store
    app.state.storage = storage
    app.state.settings = settings

    from llmos_hub.api import _router
    app.include_router(_router, prefix="/api/v1")

    yield app, store, storage

    await store.close()


@pytest.fixture()
async def client(hub_app):
    app, _, _ = hub_app
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as c:
        yield c


@pytest.fixture()
async def publisher_key(hub_app):
    _, store, _ = hub_app
    key = generate_api_key()
    await store.create_publisher("test-pub", "Test Publisher", hash_api_key(key), email="test@example.com")
    return key


def _make_tarball(module_id: str = "test_mod", version: str = "1.0.0", module_code: str = "") -> bytes:
    """Build a minimal tarball suitable for publishing."""
    if not module_code:
        module_code = "class Mod:\n    def _action_do_something(self): pass\n"
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        files = {
            "llmos-module.toml": f'module_id = "{module_id}"\nversion = "{version}"\ndescription = "Test"\nauthor = "Tester"\nactions = "do_something"\n',
            "module.py": module_code,
        }
        for name, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(name=f"{module_id}/{name}")
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


class TestSecurityEndpoint:
    async def test_security_info_clean(self, client, hub_app):
        """Security info for a clean module shows allow verdict."""
        _, store, _ = hub_app
        await store.upsert_module(ModuleRecord(module_id="clean_mod", latest_version="1.0.0"))
        await store.add_version(VersionRecord(
            module_id="clean_mod",
            version="1.0.0",
            package_path="p",
            checksum="c",
            scan_score=100.0,
            scan_verdict="allow",
            scan_findings_json="[]",
            published_at=time.time(),
        ))

        resp = await client.get("/api/v1/modules/clean_mod/security")
        assert resp.status_code == 200
        data = resp.json()
        assert data["module_id"] == "clean_mod"
        assert data["scan_verdict"] == "allow"
        assert data["scan_score"] == 100.0
        assert data["scan_findings"] == []
        assert data["latest_version"] == "1.0.0"

    async def test_security_info_with_findings(self, client, hub_app):
        """Security info includes deserialized findings from the version record."""
        _, store, _ = hub_app
        findings = [
            {"rule_id": "sc_eval", "category": "dangerous_builtins", "severity": 8.0,
             "file_path": "module.py", "line_number": 5, "description": "eval usage"},
        ]
        await store.upsert_module(ModuleRecord(module_id="risky_mod", latest_version="1.0.0"))
        await store.add_version(VersionRecord(
            module_id="risky_mod",
            version="1.0.0",
            package_path="p",
            checksum="c",
            scan_score=82.0,
            scan_verdict="warn",
            scan_findings_json=json.dumps(findings),
            published_at=time.time(),
        ))

        resp = await client.get("/api/v1/modules/risky_mod/security")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scan_verdict"] == "warn"
        assert data["scan_score"] == 82.0
        assert len(data["scan_findings"]) == 1
        assert data["scan_findings"][0]["rule_id"] == "sc_eval"

    async def test_security_info_404(self, client):
        resp = await client.get("/api/v1/modules/nonexistent/security")
        assert resp.status_code == 404

    async def test_publish_rejected_by_scanner(self, client, publisher_key):
        """Publishing a module that the scanner rejects should return 422."""
        reject_result = HubScanResult(
            verdict=ScanVerdict.REJECT,
            score=10.0,
            findings=[
                HubScanFinding(
                    rule_id="sc_eval", category="dangerous_builtins", severity=8.0,
                    file_path="module.py", line_number=3, description="eval usage",
                ),
            ],
            files_scanned=1,
        )

        tarball = _make_tarball("evil_mod", "1.0.0")
        with patch("llmos_hub.api.HubSourceScanner") as MockScanner:
            MockScanner.return_value.scan_directory.return_value = reject_result
            resp = await client.post(
                "/api/v1/modules/publish",
                files={"file": ("evil_mod-1.0.0.tar.gz", tarball, "application/gzip")},
                headers={"X-Hub-API-Key": publisher_key},
            )
        assert resp.status_code == 422
        detail = resp.json()["detail"]
        assert detail["scan_verdict"] == "reject"
        assert len(detail["findings"]) > 0

    async def test_publish_stores_scan_verdict(self, client, hub_app, publisher_key):
        """A successfully published module stores the scan verdict in the version record."""
        _, store, _ = hub_app
        # Clean module code.
        tarball = _make_tarball("safe_mod", "1.0.0")
        resp = await client.post(
            "/api/v1/modules/publish",
            files={"file": ("safe_mod-1.0.0.tar.gz", tarball, "application/gzip")},
            headers={"X-Hub-API-Key": publisher_key},
        )
        assert resp.status_code == 200
        pub_data = resp.json()
        assert pub_data["scan_verdict"] in ("allow", "warn", "")

        # Verify via security endpoint.
        resp2 = await client.get("/api/v1/modules/safe_mod/security")
        assert resp2.status_code == 200
        sec_data = resp2.json()
        assert sec_data["scan_verdict"] in ("allow", "warn", "")
        assert sec_data["latest_version"] == "1.0.0"
