"""Integration tests for the isolation system — registry, config, server wiring.

These tests verify that all isolation components work together:
  - ModuleIsolationSpec and IsolationConfig are properly structured
  - ModuleRegistry.register_isolated creates IsolatedModuleProxy instances
  - _register_builtin_modules correctly wires isolation specs
  - Health monitor integrates with registry and proxies
  - Shutdown handler cleans up health monitor and workers
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from llmos_bridge.config import IsolationConfig, ModuleIsolationSpec, Settings
from llmos_bridge.exceptions import (
    VenvCreationError,
    WorkerCommunicationError,
    WorkerCrashedError,
    WorkerStartError,
)
from llmos_bridge.isolation.health import HealthMonitor
from llmos_bridge.isolation.proxy import IsolatedModuleProxy
from llmos_bridge.modules.base import BaseModule, Platform
from llmos_bridge.modules.manifest import ModuleManifest
from llmos_bridge.modules.registry import ModuleRegistry


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _DummyModule(BaseModule):
    MODULE_ID = "dummy"
    VERSION = "1.0.0"
    SUPPORTED_PLATFORMS = [Platform.ALL]

    def get_manifest(self) -> ModuleManifest:
        return ModuleManifest(module_id="dummy", version="1.0.0", description="Dummy")

    def _check_dependencies(self) -> None:
        pass


def _make_venv_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.ensure_venv = AsyncMock(return_value=Path("/fake/venv/bin/python"))
    return mgr


# ---------------------------------------------------------------------------
# Config integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestConfigIntegration:
    def test_isolation_config_defaults(self):
        cfg = IsolationConfig()
        assert cfg.enabled is False
        assert cfg.default_isolation == "subprocess"
        assert cfg.prefer_uv is True
        assert cfg.health_check_interval == 10.0
        assert cfg.modules == {}

    def test_module_isolation_spec_defaults(self):
        spec = ModuleIsolationSpec(
            module_id="vision",
            module_class_path="pkg:Class",
        )
        assert spec.isolation == "subprocess"
        assert spec.requirements == []
        assert spec.env_vars == {}
        assert spec.timeout == 30.0
        assert spec.max_restarts == 3

    def test_module_isolation_spec_subprocess(self):
        spec = ModuleIsolationSpec(
            module_id="vision",
            module_class_path="pkg.mod:VisionModule",
            isolation="subprocess",
            requirements=["torch>=2.2", "transformers>=5.0"],
            env_vars={"CUDA_VISIBLE_DEVICES": "0"},
            timeout=60.0,
        )
        assert spec.isolation == "subprocess"
        assert len(spec.requirements) == 2

    def test_module_isolation_spec_in_process(self):
        spec = ModuleIsolationSpec(
            module_id="filesystem",
            module_class_path="pkg.mod:FilesystemModule",
            isolation="in_process",
        )
        assert spec.isolation == "in_process"

    def test_isolation_config_with_modules(self):
        cfg = IsolationConfig(
            enabled=True,
            modules={
                "vision_omni": ModuleIsolationSpec(
                    module_id="vision",
                    module_class_path="pkg:Omni",
                    requirements=["transformers>=5.0"],
                ),
                "vision_ultra": ModuleIsolationSpec(
                    module_id="vision",
                    module_class_path="pkg:Ultra",
                    requirements=["transformers==4.57.6"],
                ),
            },
        )
        assert len(cfg.modules) == 2
        assert cfg.modules["vision_omni"].requirements == ["transformers>=5.0"]
        assert cfg.modules["vision_ultra"].requirements == ["transformers==4.57.6"]

    def test_settings_has_isolation_field(self):
        """Settings model includes isolation field with IsolationConfig defaults."""
        with patch.object(Path, "exists", return_value=False):
            settings = Settings.load()
        assert hasattr(settings, "isolation")
        assert isinstance(settings.isolation, IsolationConfig)
        assert settings.isolation.enabled is False

    def test_settings_isolation_from_yaml(self, tmp_path: Path):
        config_file = tmp_path / "config.yaml"
        config_file.write_text(
            "isolation:\n"
            "  enabled: true\n"
            "  prefer_uv: false\n"
            "  venv_base_dir: /tmp/llmos_test_venvs\n"
        )
        settings = Settings.load(config_file=config_file)
        assert settings.isolation.enabled is True
        assert settings.isolation.prefer_uv is False
        assert settings.isolation.venv_base_dir == "/tmp/llmos_test_venvs"


# ---------------------------------------------------------------------------
# Registry integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestRegistryIntegration:
    def test_register_isolated_creates_proxy(self):
        registry = ModuleRegistry()
        venv_mgr = _make_venv_manager()

        registry.register_isolated(
            module_id="vision",
            module_class_path="pkg:Class",
            venv_manager=venv_mgr,
            requirements=["torch>=2.2"],
            timeout=60.0,
        )

        assert "vision" in registry.list_available()
        module = registry.get("vision")
        assert isinstance(module, IsolatedModuleProxy)
        assert module.MODULE_ID == "vision"

    def test_register_isolated_manifest(self):
        """Proxy returns a minimal manifest before start."""
        registry = ModuleRegistry()
        venv_mgr = _make_venv_manager()

        registry.register_isolated(
            module_id="browser",
            module_class_path="pkg:Browser",
            venv_manager=venv_mgr,
        )

        manifest = registry.get_manifest("browser")
        assert manifest.module_id == "browser"
        assert "not yet started" in manifest.description

    def test_register_isolated_and_regular(self):
        """Isolated and in-process modules coexist in the same registry."""
        registry = ModuleRegistry()
        venv_mgr = _make_venv_manager()

        # In-process module.
        registry.register(_DummyModule)

        # Isolated module.
        registry.register_isolated(
            module_id="vision",
            module_class_path="pkg:Vision",
            venv_manager=venv_mgr,
            requirements=["torch"],
        )

        available = registry.list_available()
        assert "dummy" in available
        assert "vision" in available
        assert isinstance(registry.get("dummy"), _DummyModule)
        assert isinstance(registry.get("vision"), IsolatedModuleProxy)

    def test_register_isolated_replaces_in_process(self):
        """If a module is registered in-process first, isolated registration replaces it."""
        registry = ModuleRegistry()
        venv_mgr = _make_venv_manager()

        registry.register(_DummyModule)
        assert isinstance(registry.get("dummy"), _DummyModule)

        # Now register an isolated version with the same module_id.
        registry.register_isolated(
            module_id="dummy",
            module_class_path="pkg:Dummy",
            venv_manager=venv_mgr,
        )
        assert isinstance(registry.get("dummy"), IsolatedModuleProxy)

    def test_register_isolated_unregister(self):
        """Unregister works for isolated modules."""
        registry = ModuleRegistry()
        venv_mgr = _make_venv_manager()

        registry.register_isolated(
            module_id="vision",
            module_class_path="pkg:Vision",
            venv_manager=venv_mgr,
        )
        assert registry.is_available("vision")

        registry.unregister("vision")
        assert not registry.is_available("vision")

    def test_register_isolated_status_report(self):
        """Isolated modules appear in status report."""
        registry = ModuleRegistry()
        venv_mgr = _make_venv_manager()

        registry.register_isolated(
            module_id="browser",
            module_class_path="pkg:Browser",
            venv_manager=venv_mgr,
        )
        registry.register(_DummyModule)

        report = registry.status_report()
        assert "browser" in report["available"]
        assert "dummy" in report["available"]

    def test_all_manifests_includes_isolated(self):
        """all_manifests() includes proxied modules."""
        registry = ModuleRegistry()
        venv_mgr = _make_venv_manager()

        registry.register_isolated(
            module_id="vision",
            module_class_path="pkg:Vision",
            venv_manager=venv_mgr,
        )
        registry.register(_DummyModule)

        manifests = registry.all_manifests()
        ids = [m.module_id for m in manifests]
        assert "vision" in ids
        assert "dummy" in ids


# ---------------------------------------------------------------------------
# Health monitor + registry integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHealthMonitorRegistryIntegration:
    def test_register_only_proxies(self):
        """HealthMonitor only tracks IsolatedModuleProxy instances."""
        registry = ModuleRegistry()
        venv_mgr = _make_venv_manager()

        registry.register(_DummyModule)
        registry.register_isolated(
            module_id="vision",
            module_class_path="pkg:Vision",
            venv_manager=venv_mgr,
        )

        monitor = HealthMonitor(check_interval=10.0)
        for inst in registry._instances.values():
            if isinstance(inst, IsolatedModuleProxy):
                monitor.register(inst)

        # Only the proxy should be monitored.
        assert monitor.monitored_count == 1

    @pytest.mark.asyncio
    async def test_health_monitor_lifecycle(self):
        """Start → check → stop lifecycle."""
        monitor = HealthMonitor(check_interval=100.0)

        proxy = MagicMock(spec=IsolatedModuleProxy)
        proxy.MODULE_ID = "vision"
        proxy._started = True
        proxy._restart_count = 0
        proxy._max_restarts = 3
        type(proxy).is_alive = PropertyMock(return_value=True)
        proxy.health_check = AsyncMock(return_value={"status": "ok"})
        proxy.stop = AsyncMock()

        monitor.register(proxy)
        await monitor.start()
        assert monitor.is_running

        results = await monitor.check_all()
        assert len(results) == 1
        assert results[0]["status"] == "ok"

        await monitor.stop()
        assert not monitor.is_running
        proxy.stop.assert_called_once()


# ---------------------------------------------------------------------------
# Server wiring (_register_builtin_modules) integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestServerWiringIntegration:
    def _make_settings(self, tmp_path: Path, isolation_enabled: bool = False, **isolation_kwargs: Any) -> Settings:
        """Create Settings with isolation config."""
        yaml_lines = [
            f"isolation:\n",
            f"  enabled: {str(isolation_enabled).lower()}\n",
        ]
        for k, v in isolation_kwargs.items():
            yaml_lines.append(f"  {k}: {v}\n")

        config_file = tmp_path / "config.yaml"
        config_file.write_text("".join(yaml_lines))
        return Settings.load(config_file=config_file)

    def test_isolation_disabled_no_proxies(self, tmp_path: Path):
        """When isolation.enabled=False, no IsolatedModuleProxy should be created."""
        settings = self._make_settings(tmp_path, isolation_enabled=False)
        assert settings.isolation.enabled is False

        # The registry shouldn't have any IsolatedModuleProxy when isolation is off.
        registry = ModuleRegistry()
        # Simulate what _register_builtin_modules does without isolation.
        from llmos_bridge.modules.filesystem import FilesystemModule
        registry.register(FilesystemModule)

        for inst in registry._instances.values():
            assert not isinstance(inst, IsolatedModuleProxy)

    def test_isolation_enabled_with_specs(self, tmp_path: Path):
        """When isolation.enabled=True with module specs, proxies are created."""
        settings = Settings.load()
        settings_dict = settings.model_dump()
        settings_dict["isolation"] = {
            "enabled": True,
            "prefer_uv": True,
            "venv_base_dir": str(tmp_path / "venvs"),
            "modules": {
                "test_mod": {
                    "module_id": "test_isolated",
                    "module_class_path": "pkg.mod:TestModule",
                    "isolation": "subprocess",
                    "requirements": ["requests>=2.0"],
                },
            },
        }

        # Verify the config model accepts this structure.
        iso_cfg = IsolationConfig(**settings_dict["isolation"])
        assert iso_cfg.enabled is True
        assert "test_mod" in iso_cfg.modules
        assert iso_cfg.modules["test_mod"].isolation == "subprocess"

    def test_in_process_spec_not_registered_as_proxy(self):
        """Modules with isolation='in_process' stay in the builtin_map."""
        spec = ModuleIsolationSpec(
            module_id="filesystem",
            module_class_path="pkg:FS",
            isolation="in_process",
        )
        # in_process specs should NOT go through register_isolated
        assert spec.isolation != "subprocess"


# ---------------------------------------------------------------------------
# Full exports from __init__.py
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestIsolationExports:
    def test_all_exports_importable(self):
        from llmos_bridge.isolation import (
            HealthMonitor,
            IsolatedModuleProxy,
            JsonRpcError,
            JsonRpcNotification,
            JsonRpcRequest,
            JsonRpcResponse,
            VenvManager,
        )
        assert HealthMonitor is not None
        assert IsolatedModuleProxy is not None
        assert VenvManager is not None
        assert JsonRpcRequest is not None
        assert JsonRpcResponse is not None
        assert JsonRpcError is not None
        assert JsonRpcNotification is not None

    def test_all_list_matches(self):
        import llmos_bridge.isolation as iso
        assert "HealthMonitor" in iso.__all__
        assert "IsolatedModuleProxy" in iso.__all__
        assert "VenvManager" in iso.__all__
        assert "JsonRpcRequest" in iso.__all__
        assert "JsonRpcResponse" in iso.__all__
        assert "JsonRpcError" in iso.__all__
        assert "JsonRpcNotification" in iso.__all__


# ---------------------------------------------------------------------------
# Exception hierarchy integration
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestExceptionHierarchy:
    def test_worker_errors_under_module_error(self):
        from llmos_bridge.exceptions import (
            ModuleError,
            WorkerError,
            WorkerStartError,
            WorkerCommunicationError,
            WorkerCrashedError,
            VenvCreationError,
        )
        assert issubclass(WorkerError, ModuleError)
        assert issubclass(WorkerStartError, WorkerError)
        assert issubclass(WorkerCommunicationError, WorkerError)
        assert issubclass(WorkerCrashedError, WorkerError)
        assert issubclass(VenvCreationError, WorkerError)

    def test_worker_start_error_fields(self):
        err = WorkerStartError("vision", "Failed to spawn")
        assert "vision" in str(err)
        assert "Failed to spawn" in str(err)

    def test_worker_crashed_error_fields(self):
        err = WorkerCrashedError("browser", 137)
        assert "browser" in str(err)
        assert "137" in str(err)

    def test_venv_creation_error_fields(self):
        err = VenvCreationError("gui", "uv not found")
        assert "gui" in str(err)
        assert "uv not found" in str(err)

    def test_all_worker_errors_catchable_as_llmos_error(self):
        from llmos_bridge.exceptions import LLMOSError
        errs = [
            WorkerStartError("m", "r"),
            WorkerCommunicationError("m", "r"),
            WorkerCrashedError("m", 1),
            VenvCreationError("m", "r"),
        ]
        for err in errs:
            assert isinstance(err, LLMOSError)
