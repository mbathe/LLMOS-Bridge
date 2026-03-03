"""Hub client — HTTP client for the remote module registry.

Provides the interface for searching, downloading, and querying the
LLMOS Module Hub.  The actual hub server is a separate project; this
client is the daemon-side consumer.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


@dataclass
class HubModuleInfo:
    """Module metadata from the hub."""

    module_id: str
    version: str
    description: str
    author: str
    downloads: int = 0
    license: str = ""
    tags: list[str] = field(default_factory=list)
    icon: str = ""
    min_bridge_version: str = ""
    module_type: str = "user"


class HubClient:
    """HTTP client for the LLMOS Module Hub API.

    Uses ``httpx.AsyncClient`` for non-blocking HTTP requests.
    """

    def __init__(self, base_url: str, timeout: float = 30.0) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: Any | None = None

    async def _ensure_client(self) -> Any:
        """Lazily create the httpx client."""
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def search(self, query: str, limit: int = 20) -> list[HubModuleInfo]:
        """Search the hub for modules matching the query."""
        client = await self._ensure_client()
        response = await client.get(
            f"{self._base_url}/modules/search",
            params={"q": query, "limit": limit},
        )
        response.raise_for_status()
        data = response.json()
        return [
            HubModuleInfo(
                module_id=m["module_id"],
                version=m.get("version", ""),
                description=m.get("description", ""),
                author=m.get("author", ""),
                downloads=m.get("downloads", 0),
                license=m.get("license", ""),
                tags=m.get("tags", []),
            )
            for m in data.get("modules", [])
        ]

    async def get_module_info(self, module_id: str) -> HubModuleInfo | None:
        """Get detailed info about a specific module."""
        client = await self._ensure_client()
        response = await client.get(
            f"{self._base_url}/modules/{module_id}"
        )
        if response.status_code == 404:
            return None
        response.raise_for_status()
        m = response.json()
        return HubModuleInfo(
            module_id=m["module_id"],
            version=m.get("version", ""),
            description=m.get("description", ""),
            author=m.get("author", ""),
            downloads=m.get("downloads", 0),
            license=m.get("license", ""),
            tags=m.get("tags", []),
        )

    async def download_package(
        self, module_id: str, version: str, dest: Path
    ) -> Path:
        """Download a module package to a local directory."""
        client = await self._ensure_client()
        response = await client.get(
            f"{self._base_url}/modules/{module_id}/download",
            params={"version": version},
        )
        response.raise_for_status()

        # Write the tarball.
        tarball_path = dest / f"{module_id}-{version}.tar.gz"
        tarball_path.write_bytes(response.content)
        return tarball_path

    async def get_versions(self, module_id: str) -> list[str]:
        """Get available versions for a module."""
        client = await self._ensure_client()
        response = await client.get(
            f"{self._base_url}/modules/{module_id}/versions"
        )
        response.raise_for_status()
        return response.json().get("versions", [])

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client is not None:
            await self._client.aclose()
            self._client = None
