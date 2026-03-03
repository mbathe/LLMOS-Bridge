"""Tests -- Config system: ConfigField, ModuleConfigBase, @configurable, BaseModule integration."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from llmos_bridge.modules.config import ConfigField, ModuleConfigBase, configurable
from llmos_bridge.modules.base import BaseModule
from llmos_bridge.modules.manifest import ModuleManifest


# --------------------------------------------------------------------------
# Test fixtures
# --------------------------------------------------------------------------


class SampleConfig(ModuleConfigBase):
    max_retries: int = ConfigField(
        default=3,
        description="Max retries",
        label="Max Retries",
        category="performance",
        ge=0,
        le=10,
        ui_widget="number",
    )
    debug_mode: bool = ConfigField(
        default=False,
        description="Enable debug",
        label="Debug Mode",
        ui_widget="toggle",
    )
    api_key: str = ConfigField(
        default="",
        description="API key",
        label="API Key",
        secret=True,
    )


class _TestModule(BaseModule):
    MODULE_ID = "test_config"
    VERSION = "0.1.0"
    CONFIG_MODEL = SampleConfig

    def get_manifest(self):
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Test",
            platforms=["all"],
            actions=[],
        )


class _BareModule(BaseModule):
    """Module without CONFIG_MODEL."""

    MODULE_ID = "bare"
    VERSION = "0.1.0"

    def get_manifest(self):
        return ModuleManifest(
            module_id=self.MODULE_ID,
            version=self.VERSION,
            description="Bare",
            platforms=["all"],
            actions=[],
        )


# --------------------------------------------------------------------------
# Tests
# --------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigFieldSchema:
    """to_config_schema() produces JSON Schema with x-ui-* extensions."""

    def test_config_field_generates_ui_metadata(self):
        schema = SampleConfig.to_config_schema()
        assert "properties" in schema
        props = schema["properties"]
        # Every declared field must appear in properties
        assert "max_retries" in props
        assert "debug_mode" in props
        assert "api_key" in props

    def test_config_field_label_in_schema(self):
        schema = SampleConfig.to_config_schema()
        max_retries = schema["properties"]["max_retries"]
        assert max_retries.get("x-ui-label") == "Max Retries"

    def test_config_field_category_in_schema(self):
        schema = SampleConfig.to_config_schema()
        max_retries = schema["properties"]["max_retries"]
        assert max_retries.get("x-ui-category") == "performance"

    def test_config_field_widget_in_schema(self):
        schema = SampleConfig.to_config_schema()
        max_retries = schema["properties"]["max_retries"]
        assert max_retries.get("x-ui-widget") == "number"

    def test_config_field_secret_in_schema(self):
        schema = SampleConfig.to_config_schema()
        api_key = schema["properties"]["api_key"]
        assert api_key.get("x-ui-secret") is True


@pytest.mark.unit
class TestConfigValidation:
    """ModuleConfigBase validates values through Pydantic."""

    def test_config_validation_rejects_invalid(self):
        with pytest.raises(ValidationError):
            SampleConfig.model_validate({"max_retries": 50, "debug_mode": False})

    def test_config_validation_accepts_valid(self):
        cfg = SampleConfig.model_validate({"max_retries": 5, "debug_mode": True})
        assert cfg.max_retries == 5
        assert cfg.debug_mode is True

    def test_config_rejects_extra_fields(self):
        with pytest.raises(ValidationError):
            SampleConfig.model_validate({"unknown_field": 1})


@pytest.mark.unit
class TestBaseModuleConfigIntegration:
    """BaseModule CONFIG_MODEL integration."""

    def test_base_module_config_model_none_by_default(self):
        assert _BareModule.CONFIG_MODEL is None

    @pytest.mark.asyncio
    async def test_on_config_update_validates_and_stores(self):
        module = _TestModule()
        await module.on_config_update(
            {"max_retries": 5, "debug_mode": True, "api_key": "x"}
        )
        assert module.config is not None
        assert module.config.max_retries == 5

    @pytest.mark.asyncio
    async def test_on_config_update_rejects_invalid(self):
        module = _TestModule()
        with pytest.raises(ValidationError):
            await module.on_config_update({"max_retries": 50})

    def test_collect_config_schema_returns_schema(self):
        module = _TestModule()
        schema = module._collect_config_schema()
        assert schema is not None
        assert "properties" in schema

    def test_collect_config_schema_returns_none_without_model(self):
        module = _BareModule()
        assert module._collect_config_schema() is None


@pytest.mark.unit
class TestConfigurableDecorator:
    """@configurable(Model) sets CONFIG_MODEL on the class."""

    def test_configurable_decorator_sets_config_model(self):
        @configurable(SampleConfig)
        class _Decorated(BaseModule):
            MODULE_ID = "decorated"
            VERSION = "0.1.0"

            def get_manifest(self):
                return ModuleManifest(
                    module_id=self.MODULE_ID,
                    version=self.VERSION,
                    description="Decorated",
                    platforms=["all"],
                    actions=[],
                )

        assert _Decorated.CONFIG_MODEL is SampleConfig
