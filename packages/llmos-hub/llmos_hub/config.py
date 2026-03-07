"""Hub server configuration via pydantic-settings."""

from __future__ import annotations

from pathlib import Path

from pydantic_settings import BaseSettings


class HubServerSettings(BaseSettings):
    """Configuration for the LLMOS Hub server.

    All fields can be set via environment variables with the ``LLMOS_HUB_``
    prefix (e.g. ``LLMOS_HUB_PORT=9090``).
    """

    host: str = "0.0.0.0"
    port: int = 8080
    data_dir: str = "~/.llmos-hub"
    db_path: str = ""  # Default: {data_dir}/hub.db
    packages_dir: str = ""  # Default: {data_dir}/packages
    min_publish_score: int = 70
    max_package_size_mb: int = 50
    require_signatures: bool = False
    log_level: str = "info"

    model_config = {"env_prefix": "LLMOS_HUB_"}

    @property
    def resolved_data_dir(self) -> Path:
        return Path(self.data_dir).expanduser()

    @property
    def resolved_db_path(self) -> Path:
        if self.db_path:
            return Path(self.db_path).expanduser()
        return self.resolved_data_dir / "hub.db"

    @property
    def resolved_packages_dir(self) -> Path:
        if self.packages_dir:
            return Path(self.packages_dir).expanduser()
        return self.resolved_data_dir / "packages"
