"""Unit tests — Settings.load, get_settings, override_settings."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from llmos_bridge.config import Settings, get_settings, override_settings


@pytest.mark.unit
class TestSettingsLoad:
    def test_load_defaults_when_no_files(self, tmp_path: Path) -> None:
        """Load with no existing config files returns defaults."""
        # Patch candidates so no file exists
        with patch.object(Path, "exists", return_value=False):
            settings = Settings.load()
        assert settings.server.port == 40000
        assert settings.server.host == "127.0.0.1"

    def test_load_from_custom_config_file(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text("server:\n  port: 9999\n")

        settings = Settings.load(config_file=config_file)
        assert settings.server.port == 9999

    def test_load_without_config_file_arg(self) -> None:
        """load() with config_file=None works without error."""
        with patch.object(Path, "exists", return_value=False):
            settings = Settings.load(config_file=None)
        assert isinstance(settings, Settings)

    def test_active_modules_excludes_disabled(self, tmp_path: Path) -> None:
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "modules:\n"
            "  enabled:\n    - filesystem\n    - excel\n    - word\n"
            "  disabled:\n    - excel\n"
        )
        settings = Settings.load(config_file=config_file)
        active = settings.active_modules()
        assert "filesystem" in active
        assert "excel" not in active
        assert "word" in active


@pytest.mark.unit
class TestGetSettings:
    def test_get_settings_returns_settings_instance(self) -> None:
        import llmos_bridge.config as cfg_module

        original = cfg_module._settings
        try:
            cfg_module._settings = None
            with patch.object(Path, "exists", return_value=False):
                settings = get_settings()
            assert isinstance(settings, Settings)
        finally:
            cfg_module._settings = original

    def test_get_settings_returns_cached_instance(self) -> None:
        import llmos_bridge.config as cfg_module

        original = cfg_module._settings
        try:
            mock_settings = Settings()
            cfg_module._settings = mock_settings
            result = get_settings()
            assert result is mock_settings
        finally:
            cfg_module._settings = original


@pytest.mark.unit
class TestOverrideSettings:
    def test_override_sets_singleton(self) -> None:
        import llmos_bridge.config as cfg_module

        original = cfg_module._settings
        try:
            new_settings = Settings(server={"port": 12345})
            override_settings(new_settings)
            assert cfg_module._settings is new_settings
            assert get_settings() is new_settings
        finally:
            cfg_module._settings = original
