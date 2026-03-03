"""Module package format — standardized structure for distributable modules.

A module package is a directory (or tarball) with this layout::

    my_module/
        llmos-module.toml       # Package metadata (required)
        my_module/
            __init__.py
            module.py           # BaseModule subclass
        tests/
            test_module.py

The ``llmos-module.toml`` is the single source of truth for module identity,
entry point, Python dependencies, module-to-module dependencies, and platform
support.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from llmos_bridge.logging import get_logger

log = get_logger(__name__)


class ActionDeclaration(BaseModel):
    """Lightweight action declaration in llmos-module.toml for hub listing."""
    name: str
    description: str = ""
    risk_level: str = "low"
    permission: str = "local_worker"
    category: str = ""


class CapabilityDeclaration(BaseModel):
    """Module capability overview for hub and dashboard."""
    permissions: list[str] = Field(default_factory=list)
    side_effects: list[str] = Field(default_factory=list)
    events_emitted: list[str] = Field(default_factory=list)
    events_subscribed: list[str] = Field(default_factory=list)
    services_provided: list[str] = Field(default_factory=list)
    services_consumed: list[str] = Field(default_factory=list)


class DocsConfig(BaseModel):
    """Documentation file paths relative to module root."""
    readme: str = "README.md"
    changelog: str = "CHANGELOG.md"
    actions: str = "docs/actions.md"
    integration: str = "docs/integration.md"


class ModulePackageConfig(BaseModel):
    """Parsed ``llmos-module.toml`` schema."""

    module_id: str
    version: str
    description: str = ""
    author: str = ""
    license: str = ""
    homepage: str = ""
    module_class_path: str  # e.g. "my_module.module:MyModule"
    platforms: list[str] = Field(default_factory=lambda: ["all"])
    requirements: list[str] = Field(default_factory=list)
    module_dependencies: dict[str, str] = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)
    sandbox_level: str = "basic"
    module_type: str = "user"
    min_bridge_version: str = ""
    python_version: str = ""  # e.g. "3.11", "3.12". Empty = use host Python.
    icon: str = ""
    actions: list[ActionDeclaration] = Field(default_factory=list)
    capabilities: CapabilityDeclaration | None = None
    docs: DocsConfig | None = None

    @classmethod
    def from_toml(cls, path: Path) -> ModulePackageConfig:
        """Parse an ``llmos-module.toml`` file."""
        try:
            import tomllib
        except ImportError:
            import tomli as tomllib  # type: ignore[no-redef]

        text = path.read_text(encoding="utf-8")
        data = tomllib.loads(text)
        module_data = data.get("module", data)
        return cls.model_validate(module_data)


@dataclass
class ModulePackage:
    """Represents a module package on disk."""

    config: ModulePackageConfig
    path: Path

    @classmethod
    def from_directory(cls, directory: Path) -> ModulePackage:
        """Load a module package from a directory."""
        config_path = directory / "llmos-module.toml"
        if not config_path.exists():
            raise FileNotFoundError(f"No llmos-module.toml in {directory}")
        config = ModulePackageConfig.from_toml(config_path)
        return cls(config=config, path=directory)
