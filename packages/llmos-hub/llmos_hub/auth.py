"""Publisher authentication for the hub server."""

from __future__ import annotations

import hashlib
import secrets

from fastapi import Header, HTTPException

from llmos_hub.models import PublisherRecord


def hash_api_key(key: str) -> str:
    """Hash an API key using SHA-256."""
    return hashlib.sha256(key.encode()).hexdigest()


def generate_api_key() -> str:
    """Generate a new publisher API key."""
    return f"llmos_hub_{secrets.token_urlsafe(32)}"


async def verify_publisher(store, x_hub_api_key: str = Header(None)) -> PublisherRecord:
    """FastAPI dependency that authenticates a publisher via API key header.

    Raises 401 if missing/invalid.
    """
    if not x_hub_api_key:
        raise HTTPException(status_code=401, detail="Missing X-Hub-API-Key header")

    key_hash = hash_api_key(x_hub_api_key)
    publisher = await store.get_publisher_by_key_hash(key_hash)
    if publisher is None:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return publisher
