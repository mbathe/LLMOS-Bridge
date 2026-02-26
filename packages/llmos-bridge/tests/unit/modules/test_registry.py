"""Unit tests — ModuleRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from llmos_bridge.exceptions import ModuleLoadError, ModuleNotFoundError
from llmos_bridge.modules.filesystem import FilesystemModule
from llmos_bridge.modules.registry import ModuleRegistry


# ---------------------------------------------------------------------------
# Basic registration and retrieval
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleRegistryBasic:
    def test_register_and_get_module(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        module = registry.get("filesystem")
        assert module is not None

    def test_get_same_instance_on_repeated_calls(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        m1 = registry.get("filesystem")
        m2 = registry.get("filesystem")
        assert m1 is m2

    def test_get_unknown_module_raises_not_found(self) -> None:
        registry = ModuleRegistry()
        with pytest.raises(ModuleNotFoundError):
            registry.get("no_such_module")

    def test_register_no_module_id_raises(self) -> None:
        class BadModule:
            MODULE_ID = ""
        with pytest.raises(ValueError, match="no MODULE_ID"):
            registry = ModuleRegistry()
            registry.register(BadModule)  # type: ignore

    def test_list_available_includes_registered(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        available = registry.list_available()
        assert "filesystem" in available

    def test_list_modules_includes_all(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        modules = registry.list_modules()
        assert "filesystem" in modules

    def test_list_failed_empty_initially(self) -> None:
        registry = ModuleRegistry()
        assert registry.list_failed() == {}

    def test_list_platform_excluded_empty_initially(self) -> None:
        registry = ModuleRegistry()
        assert registry.list_platform_excluded() == {}

    def test_is_available_true_for_registered(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        assert registry.is_available("filesystem") is True

    def test_is_available_false_for_unknown(self) -> None:
        registry = ModuleRegistry()
        assert registry.is_available("unknown") is False

    def test_get_manifest_returns_manifest(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        manifest = registry.get_manifest("filesystem")
        assert manifest.module_id == "filesystem"

    def test_all_manifests_returns_list(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        manifests = registry.all_manifests()
        assert len(manifests) == 1
        assert manifests[0].module_id == "filesystem"

    def test_status_report_contains_available_key(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        report = registry.status_report()
        assert "available" in report
        assert "failed" in report
        assert "platform_excluded" in report
        assert "filesystem" in report["available"]

    def test_unregister_removes_module(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        registry.unregister("filesystem")
        assert "filesystem" not in registry.list_available()

    def test_register_duplicate_logs_warning(self) -> None:
        registry = ModuleRegistry()
        registry.register(FilesystemModule)
        # Second registration should log a warning but not raise
        registry.register(FilesystemModule)
        assert "filesystem" in registry.list_available()


# ---------------------------------------------------------------------------
# Module instantiation failure
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestModuleRegistryFailure:
    def test_module_load_failure_raises_module_load_error(self) -> None:
        class FailingModule:
            MODULE_ID = "failing"
            VERSION = "1.0.0"
            SUPPORTED_PLATFORMS = []

            def __init__(self) -> None:
                raise RuntimeError("intentional load failure")

        registry = ModuleRegistry()
        registry.register(FailingModule)  # type: ignore
        with pytest.raises(ModuleLoadError, match="failing"):
            registry.get("failing")

    def test_failed_module_recorded(self) -> None:
        class FailingModule:
            MODULE_ID = "fail_mod"
            VERSION = "1.0.0"
            SUPPORTED_PLATFORMS = []

            def __init__(self) -> None:
                raise ImportError("missing dependency")

        registry = ModuleRegistry()
        registry.register(FailingModule)  # type: ignore
        try:
            registry.get("fail_mod")
        except ModuleLoadError:
            pass
        assert "fail_mod" in registry.list_failed()

    def test_failed_module_is_not_available(self) -> None:
        class FailingModule:
            MODULE_ID = "fail_check"
            VERSION = "1.0.0"
            SUPPORTED_PLATFORMS = []

            def __init__(self) -> None:
                raise RuntimeError("crash")

        registry = ModuleRegistry()
        registry.register(FailingModule)  # type: ignore
        assert registry.is_available("fail_check") is False

    def test_platform_excluded_raises_module_load_error(self) -> None:
        registry = ModuleRegistry()
        registry._platform_excluded["iot"] = "Not supported on this platform"
        with pytest.raises(ModuleLoadError):
            registry.get("iot")

    def test_platform_excluded_is_not_available(self) -> None:
        registry = ModuleRegistry()
        registry._platform_excluded["iot"] = "Not supported"
        assert registry.is_available("iot") is False

    def test_failed_module_raises_on_second_get(self) -> None:
        registry = ModuleRegistry()
        registry._failed["broken"] = "crashed during init"
        with pytest.raises(ModuleLoadError):
            registry.get("broken")
