"""Hub server FastAPI application factory and API endpoints."""

from __future__ import annotations

import json
import time
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

import structlog
from fastapi import APIRouter, Depends, FastAPI, File, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import Response

from llmos_hub.auth import generate_api_key, hash_api_key
from llmos_hub.config import HubServerSettings
from llmos_hub.models import ModuleRecord, PublisherRecord, VersionRecord
from llmos_hub.scanner import HubSourceScanner, ScanVerdict
from llmos_hub.storage import PackageStorage
from llmos_hub.store import HubStore
from llmos_hub.validation import validate_for_publish

log = structlog.get_logger(__name__)


# ------------------------------------------------------------------
# Dependency helpers
# ------------------------------------------------------------------

async def _verify_publisher(
    request: Request,
    x_hub_api_key: str | None = Header(None),
) -> PublisherRecord:
    if not x_hub_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Hub-API-Key header")
    store: HubStore = request.app.state.store
    key_hash = hash_api_key(x_hub_api_key)
    publisher = await store.get_publisher_by_key_hash(key_hash)
    if publisher is None:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return publisher


# ------------------------------------------------------------------
# App factory
# ------------------------------------------------------------------

def create_hub_app(settings: HubServerSettings | None = None) -> FastAPI:
    """Create the hub server FastAPI application."""
    if settings is None:
        settings = HubServerSettings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Startup
        data_dir = settings.resolved_data_dir
        data_dir.mkdir(parents=True, exist_ok=True)

        store = HubStore(str(settings.resolved_db_path))
        await store.init()
        app.state.store = store

        storage = PackageStorage(settings.resolved_packages_dir)
        settings.resolved_packages_dir.mkdir(parents=True, exist_ok=True)
        app.state.storage = storage

        app.state.settings = settings

        log.info("hub_server_started", port=settings.port, data_dir=str(data_dir))
        yield
        # Shutdown
        await store.close()
        log.info("hub_server_stopped")

    app = FastAPI(
        title="LLMOS Hub",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Register routes
    app.include_router(_router, prefix="/api/v1")

    return app


# ------------------------------------------------------------------
# Router
# ------------------------------------------------------------------

_router = APIRouter()


@_router.get("/health")
async def health():
    return {"status": "ok", "timestamp": time.time()}


# ------------------------------------------------------------------
# Publishers
# ------------------------------------------------------------------

@_router.post("/publishers/register")
async def register_publisher(request: Request):
    body = await request.json()
    name = (body.get("name") or "").strip()
    email = (body.get("email") or "").strip()
    if not name or not email:
        raise HTTPException(status_code=422, detail="name and email are required")

    store: HubStore = request.app.state.store
    publisher_id = str(uuid.uuid4())
    api_key = generate_api_key()
    key_hash = hash_api_key(api_key)

    pub = await store.create_publisher(
        publisher_id, name, key_hash,
        email=email,
        description=(body.get("description") or "").strip(),
        website=(body.get("website") or "").strip(),
    )
    return {"publisher_id": publisher_id, "name": name, "api_key": api_key}


@_router.get("/publishers/{publisher_id}")
async def get_publisher(request: Request, publisher_id: str):
    store: HubStore = request.app.state.store
    pub = await store.get_publisher(publisher_id)
    if pub is None:
        raise HTTPException(status_code=404, detail="Publisher not found")
    return _publisher_dict(pub)


@_router.get("/publishers/{publisher_id}/modules")
async def list_publisher_modules(request: Request, publisher_id: str):
    store: HubStore = request.app.state.store
    pub = await store.get_publisher(publisher_id)
    if pub is None:
        raise HTTPException(status_code=404, detail="Publisher not found")
    modules = await store.list_publisher_modules(publisher_id)
    return {"modules": [_module_dict(m) for m in modules], "total": len(modules)}


@_router.put("/publishers/{publisher_id}")
async def update_publisher(
    request: Request,
    publisher_id: str,
    x_hub_api_key: str | None = Header(None),
):
    publisher = await _verify_publisher(request, x_hub_api_key)
    if publisher.publisher_id != publisher_id:
        raise HTTPException(status_code=403, detail="Can only update own profile")

    body = await request.json()
    store: HubStore = request.app.state.store
    updated = await store.update_publisher(
        publisher_id,
        name=body.get("name"),
        email=body.get("email"),
        description=body.get("description"),
        website=body.get("website"),
    )
    if updated is None:
        raise HTTPException(status_code=404, detail="Publisher not found")
    return _publisher_dict(updated)


@_router.post("/publishers/{publisher_id}/rotate-key")
async def rotate_key(
    request: Request,
    publisher_id: str,
    x_hub_api_key: str | None = Header(None),
):
    publisher = await _verify_publisher(request, x_hub_api_key)
    if publisher.publisher_id != publisher_id:
        raise HTTPException(status_code=403, detail="Can only rotate own key")

    store: HubStore = request.app.state.store
    new_api_key = generate_api_key()
    new_hash = hash_api_key(new_api_key)
    success = await store.rotate_api_key(publisher_id, new_hash)
    if not success:
        raise HTTPException(status_code=404, detail="Publisher not found")
    return {"publisher_id": publisher_id, "new_api_key": new_api_key}


# ------------------------------------------------------------------
# Search / Browse
# ------------------------------------------------------------------

@_router.get("/modules/search")
async def search_modules(
    request: Request,
    q: str = Query("", description="Search query"),
    limit: int = Query(20, ge=1, le=100),
    tags: str = Query("", description="Comma-separated tags"),
    category: str = Query("", description="Filter by category"),
    min_rating: float = Query(0.0, ge=0, le=5, description="Minimum average rating"),
    sort_by: str = Query("downloads", description="Sort: downloads|rating|newest"),
    include_deprecated: bool = Query(False, description="Include deprecated modules"),
):
    store: HubStore = request.app.state.store
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else None
    modules = await store.search_modules(
        q, limit=limit, tags=tag_list,
        category=category, min_rating=min_rating,
        sort_by=sort_by, include_deprecated=include_deprecated,
    )
    return {
        "modules": [_module_dict(m) for m in modules],
        "total": len(modules),
        "query": q,
    }


@_router.get("/modules/{module_id}")
async def get_module(request: Request, module_id: str):
    store: HubStore = request.app.state.store
    mod = await store.get_module(module_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Module not found")
    latest = await store.get_latest_version(module_id)
    result = _module_dict(mod)
    if latest:
        result["latest"] = _version_dict(latest)
    return result


@_router.get("/modules/{module_id}/versions")
async def list_versions(request: Request, module_id: str):
    store: HubStore = request.app.state.store
    mod = await store.get_module(module_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Module not found")
    versions = await store.get_versions(module_id)
    return {
        "module_id": module_id,
        "versions": [_version_dict(v) for v in versions],
        "total": len(versions),
    }


@_router.get("/modules/{module_id}/download")
async def download_module(
    request: Request,
    module_id: str,
    version: str = Query("latest"),
):
    store: HubStore = request.app.state.store
    storage: PackageStorage = request.app.state.storage

    if version == "latest":
        ver = await store.get_latest_version(module_id)
    else:
        versions = await store.get_versions(module_id)
        ver = next((v for v in versions if v.version == version), None)

    if ver is None:
        raise HTTPException(status_code=404, detail="Version not found")

    try:
        data = await storage.load(ver.package_path)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="Package file missing")

    await store.increment_downloads(module_id)

    return Response(
        content=data,
        media_type="application/gzip",
        headers={
            "Content-Disposition": f'attachment; filename="{module_id}-{ver.version}.tar.gz"',
            "X-Checksum-SHA256": ver.checksum,
        },
    )


# ------------------------------------------------------------------
# Publish
# ------------------------------------------------------------------

@_router.post("/modules/publish")
async def publish_module(
    request: Request,
    file: UploadFile = File(...),
    x_hub_api_key: str | None = Header(None),
):
    # Auth
    publisher = await _verify_publisher(request, x_hub_api_key)
    settings: HubServerSettings = request.app.state.settings
    store: HubStore = request.app.state.store
    storage: PackageStorage = request.app.state.storage

    # Read file
    data = await file.read()
    max_bytes = settings.max_package_size_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Package too large ({len(data)} bytes). Max: {max_bytes} bytes",
        )

    # Validate structure (keep extracted so scanner can run on the temp dir).
    validation = await validate_for_publish(
        data, min_score=settings.min_publish_score, keep_extracted=True,
    )
    try:
        if not validation.hub_ready:
            raise HTTPException(
                status_code=422,
                detail={
                    "message": "Validation failed",
                    "score": validation.score,
                    "min_score": settings.min_publish_score,
                    "issues": validation.issues,
                    "warnings": validation.warnings,
                },
            )

        # Security scan — run on the extracted temp dir from validation.
        scan_verdict = ""
        scan_findings_json = "[]"
        scan_score = validation.score
        if validation.extracted_root:
            scanner = HubSourceScanner()
            scan_result = scanner.scan_directory(validation.extracted_root)
            scan_verdict = scan_result.verdict.value
            scan_findings_json = json.dumps([f.to_dict() for f in scan_result.findings])
            scan_score = scan_result.score

            if scan_result.verdict == ScanVerdict.REJECT:
                raise HTTPException(
                    status_code=422,
                    detail={
                        "message": "Source code security scan rejected",
                        "scan_score": scan_result.score,
                        "scan_verdict": "reject",
                        "findings": [f.to_dict() for f in scan_result.findings[:20]],
                    },
                )
    finally:
        # Clean up extracted temp dir.
        if validation.extracted_root:
            import shutil
            top = validation.extracted_root
            # Walk up to the tempdir root (one level above module root).
            if top.parent and top.parent != top:
                shutil.rmtree(top.parent, ignore_errors=True)
            else:
                shutil.rmtree(top, ignore_errors=True)

    # Store package
    rel_path, checksum = await storage.save(validation.module_id, validation.version, data)

    # Register version
    now = time.time()
    ver = VersionRecord(
        module_id=validation.module_id,
        version=validation.version,
        package_path=rel_path,
        checksum=checksum,
        scan_score=scan_score,
        published_at=now,
        scan_verdict=scan_verdict,
        scan_findings_json=scan_findings_json,
        min_bridge_version=validation.min_bridge_version,
        max_bridge_version=validation.max_bridge_version,
        python_requires=validation.python_requires,
    )
    await store.add_version(ver)

    # Upsert module
    mod = ModuleRecord(
        module_id=validation.module_id,
        latest_version=validation.version,
        description=validation.description,
        author=validation.author,
        tags=validation.tags,
        publisher_id=publisher.publisher_id,
        created_at=now,
        updated_at=now,
    )
    await store.upsert_module(mod)

    log.info(
        "module_published",
        module_id=validation.module_id,
        version=validation.version,
        score=validation.score,
        scan_verdict=scan_verdict,
        publisher=publisher.name,
    )

    return {
        "success": True,
        "module_id": validation.module_id,
        "version": validation.version,
        "score": validation.score,
        "checksum": checksum,
        "scan_verdict": scan_verdict,
    }


# ------------------------------------------------------------------
# Security info
# ------------------------------------------------------------------

@_router.get("/modules/{module_id}/security")
async def get_module_security(request: Request, module_id: str):
    store: HubStore = request.app.state.store
    mod = await store.get_module(module_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Module not found")

    latest = await store.get_latest_version(module_id)
    if latest is None:
        return {
            "module_id": module_id,
            "scan_score": 0,
            "scan_verdict": "",
            "scan_findings": [],
            "latest_version": "",
        }

    findings = []
    try:
        findings = json.loads(latest.scan_findings_json)
    except (json.JSONDecodeError, TypeError):
        pass

    return {
        "module_id": module_id,
        "scan_score": latest.scan_score,
        "scan_verdict": latest.scan_verdict,
        "scan_findings": findings,
        "latest_version": latest.version,
    }


# ------------------------------------------------------------------
# Ratings
# ------------------------------------------------------------------

@_router.post("/modules/{module_id}/rate")
async def rate_module(
    request: Request,
    module_id: str,
    x_hub_api_key: str | None = Header(None),
):
    publisher = await _verify_publisher(request, x_hub_api_key)
    store: HubStore = request.app.state.store

    mod = await store.get_module(module_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Module not found")

    # Prevent self-rating.
    if mod.publisher_id == publisher.publisher_id:
        raise HTTPException(status_code=403, detail="Cannot rate own module")

    body = await request.json()
    stars = body.get("stars", 0)
    if not isinstance(stars, int) or stars < 1 or stars > 5:
        raise HTTPException(status_code=422, detail="stars must be an integer 1-5")

    comment = (body.get("comment") or "").strip()
    rating = await store.add_rating(module_id, publisher.publisher_id, stars, comment)

    # Refresh module for updated averages.
    mod = await store.get_module(module_id)
    return {
        "module_id": module_id,
        "stars": rating.stars,
        "comment": rating.comment,
        "average_rating": mod.average_rating,
        "rating_count": mod.rating_count,
    }


@_router.get("/modules/{module_id}/ratings")
async def get_ratings(request: Request, module_id: str):
    store: HubStore = request.app.state.store
    mod = await store.get_module(module_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Module not found")

    ratings = await store.get_ratings(module_id)
    return {
        "module_id": module_id,
        "ratings": [
            {
                "publisher_id": r.publisher_id,
                "stars": r.stars,
                "comment": r.comment,
                "created_at": r.created_at,
            }
            for r in ratings
        ],
        "total": len(ratings),
        "average_rating": mod.average_rating,
    }


# ------------------------------------------------------------------
# Categories
# ------------------------------------------------------------------

@_router.get("/categories")
async def get_categories(request: Request):
    store: HubStore = request.app.state.store
    categories = await store.get_categories()
    return {"categories": categories}


# ------------------------------------------------------------------
# Deprecation
# ------------------------------------------------------------------

@_router.post("/modules/{module_id}/deprecate")
async def deprecate_module(
    request: Request,
    module_id: str,
    x_hub_api_key: str | None = Header(None),
):
    publisher = await _verify_publisher(request, x_hub_api_key)
    store: HubStore = request.app.state.store

    mod = await store.get_module(module_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if mod.publisher_id != publisher.publisher_id:
        raise HTTPException(status_code=403, detail="Not the module publisher")

    body = await request.json()
    message = (body.get("message") or "").strip()
    replacement_id = (body.get("replacement_module_id") or "").strip()

    await store.deprecate_module(module_id, message, replacement_id)
    return {
        "module_id": module_id,
        "deprecated": True,
        "message": message,
        "replacement_module_id": replacement_id,
    }


# ------------------------------------------------------------------
# Delete / Yank
# ------------------------------------------------------------------

@_router.delete("/modules/{module_id}")
async def delete_module(
    request: Request,
    module_id: str,
    x_hub_api_key: str | None = Header(None),
):
    publisher = await _verify_publisher(request, x_hub_api_key)
    store: HubStore = request.app.state.store
    storage: PackageStorage = request.app.state.storage

    mod = await store.get_module(module_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if mod.publisher_id != publisher.publisher_id:
        raise HTTPException(status_code=403, detail="Not the module publisher")

    await storage.delete(module_id)
    await store.delete_module(module_id)

    return {"deleted": True, "module_id": module_id}


@_router.post("/modules/{module_id}/yank/{version}")
async def yank_version(
    request: Request,
    module_id: str,
    version: str,
    x_hub_api_key: str | None = Header(None),
):
    publisher = await _verify_publisher(request, x_hub_api_key)
    store: HubStore = request.app.state.store

    mod = await store.get_module(module_id)
    if mod is None:
        raise HTTPException(status_code=404, detail="Module not found")
    if mod.publisher_id != publisher.publisher_id:
        raise HTTPException(status_code=403, detail="Not the module publisher")

    yanked = await store.yank_version(module_id, version)
    if not yanked:
        raise HTTPException(status_code=404, detail="Version not found")

    return {"yanked": True, "module_id": module_id, "version": version}


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _publisher_dict(p: PublisherRecord) -> dict:
    """Serialize a publisher for API responses — NEVER includes api_key_hash."""
    return {
        "publisher_id": p.publisher_id,
        "name": p.name,
        "email": p.email,
        "description": p.description,
        "website": p.website,
        "verified": p.verified,
        "created_at": p.created_at,
    }


def _module_dict(m: ModuleRecord) -> dict:
    d = {
        "module_id": m.module_id,
        "latest_version": m.latest_version,
        "description": m.description,
        "author": m.author,
        "license": m.license,
        "tags": m.tags,
        "downloads": m.downloads,
        "created_at": m.created_at,
        "updated_at": m.updated_at,
        "average_rating": m.average_rating,
        "rating_count": m.rating_count,
        "category": m.category,
        "deprecated": m.deprecated,
    }
    if m.deprecated:
        d["deprecated_message"] = m.deprecated_message
        d["replacement_module_id"] = m.replacement_module_id
    return d


def _version_dict(v: VersionRecord) -> dict:
    return {
        "version": v.version,
        "checksum": v.checksum,
        "scan_score": v.scan_score,
        "scan_verdict": v.scan_verdict,
        "published_at": v.published_at,
        "yanked": v.yanked,
        "min_bridge_version": v.min_bridge_version,
        "max_bridge_version": v.max_bridge_version,
        "python_requires": v.python_requires,
    }
