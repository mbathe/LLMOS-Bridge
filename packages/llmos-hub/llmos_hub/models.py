"""Data models for the hub server."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class PublisherRecord:
    """A registered publisher (module author)."""

    publisher_id: str
    name: str
    api_key_hash: str
    created_at: float
    enabled: bool = True
    email: str = ""
    description: str = ""
    website: str = ""
    verified: bool = False


@dataclass
class ModuleRecord:
    """Metadata for a published module."""

    module_id: str
    latest_version: str
    description: str = ""
    author: str = ""
    license: str = ""
    tags: list[str] = field(default_factory=list)
    downloads: int = 0
    publisher_id: str = ""
    created_at: float = 0.0
    updated_at: float = 0.0
    average_rating: float = 0.0
    rating_count: int = 0
    category: str = ""
    deprecated: bool = False
    deprecated_message: str = ""
    replacement_module_id: str = ""


@dataclass
class VersionRecord:
    """A single published version of a module."""

    module_id: str
    version: str
    package_path: str  # Relative path in storage
    checksum: str  # SHA-256 of the .tar.gz
    scan_score: float = 0.0
    published_at: float = 0.0
    yanked: bool = False
    scan_verdict: str = ""
    scan_findings_json: str = ""
    min_bridge_version: str = ""
    max_bridge_version: str = ""
    python_requires: str = ""


@dataclass
class RatingRecord:
    """A publisher's rating for a module."""

    id: int
    module_id: str
    publisher_id: str
    stars: int
    comment: str
    created_at: float
