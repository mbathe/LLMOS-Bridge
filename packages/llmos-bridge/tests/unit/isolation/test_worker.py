"""Tests for isolation.worker — subprocess-side JSON-RPC worker."""

from __future__ import annotations

import asyncio
import json
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.isolation import protocol as proto
from llmos_bridge.isolation.worker import (
    _dispatch,
    _emit,
    _emit_error,
    _emit_notification,
    _emit_response,
    _handle_execute,
    _handle_get_manifest,
    _handle_health_check,
    _handle_initialize,
    _import_module_class,
    _WorkerMethodError,
)


# ---------------------------------------------------------------------------
# _import_module_class
# ---------------------------------------------------------------------------


class TestImportModuleClass:
    def test_valid_path(self):
        cls = _import_module_class("llmos_bridge.modules.base:BaseModule")
        from llmos_bridge.modules.base import BaseModule
        assert cls is BaseModule

    def test_missing_colon(self):
        with pytest.raises(ValueError, match="module.path:ClassName"):
            _import_module_class("llmos_bridge.modules.base.BaseModule")

    def test_invalid_module(self):
        with pytest.raises(ModuleNotFoundError):
            _import_module_class("nonexistent.module:FakeClass")

    def test_invalid_class(self):
        with pytest.raises(AttributeError):
            _import_module_class("llmos_bridge.modules.base:NonExistentClass")


# ---------------------------------------------------------------------------
# Emit helpers
# ---------------------------------------------------------------------------


class TestEmit:
    def test_emit_writes_json_line(self):
        output = []
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = lambda s: output.append(s)
            mock_stdout.flush = lambda: None
            _emit({"key": "value"})
        assert len(output) == 1
        assert json.loads(output[0].strip()) == {"key": "value"}

    def test_emit_response(self):
        output = []
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = lambda s: output.append(s)
            mock_stdout.flush = lambda: None
            _emit_response("r1", {"status": "ok"})
        data = json.loads(output[0])
        assert data["id"] == "r1"
        assert data["result"] == {"status": "ok"}
        assert data["error"] is None

    def test_emit_error(self):
        output = []
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = lambda s: output.append(s)
            mock_stdout.flush = lambda: None
            _emit_error("r2", -32603, "boom")
        data = json.loads(output[0])
        assert data["id"] == "r2"
        assert data["error"]["code"] == -32603
        assert data["error"]["message"] == "boom"
        assert data["result"] is None

    def test_emit_notification(self):
        output = []
        with patch("sys.stdout") as mock_stdout:
            mock_stdout.write = lambda s: output.append(s)
            mock_stdout.flush = lambda: None
            _emit_notification("worker.ready", {"pid": 123})
        data = json.loads(output[0])
        assert data["method"] == "worker.ready"
        assert data["params"]["pid"] == 123
        assert "id" not in data


# ---------------------------------------------------------------------------
# _handle_initialize
# ---------------------------------------------------------------------------


class TestHandleInitialize:
    @pytest.mark.asyncio
    async def test_success(self):
        import llmos_bridge.isolation.worker as w

        manifest = MagicMock()
        manifest.to_dict.return_value = {"module_id": "test", "actions": []}

        mock_cls = MagicMock()
        mock_instance = MagicMock()
        mock_instance.MODULE_ID = "test"
        mock_instance.VERSION = "1.0.0"
        mock_instance.get_manifest.return_value = manifest
        mock_cls.return_value = mock_instance

        old_class_path = w._module_class_path
        try:
            w._module_class_path = "fake.path:FakeModule"
            with patch.object(w, "_import_module_class", return_value=mock_cls):
                result = await _handle_initialize({"env_vars": {"KEY": "VAL"}})

            assert result["module_id"] == "test"
            assert result["version"] == "1.0.0"
            assert result["manifest"]["module_id"] == "test"
            assert os.environ.get("KEY") == "VAL"
            assert w._module_instance is mock_instance
        finally:
            w._module_class_path = old_class_path
            w._module_instance = None
            os.environ.pop("KEY", None)

    @pytest.mark.asyncio
    async def test_import_failure(self):
        import llmos_bridge.isolation.worker as w
        old_class_path = w._module_class_path
        try:
            w._module_class_path = "bad.path:Missing"
            with pytest.raises(_WorkerMethodError) as exc_info:
                await _handle_initialize({})
            assert exc_info.value.code == proto.MODULE_LOAD_ERROR
        finally:
            w._module_class_path = old_class_path


# ---------------------------------------------------------------------------
# _handle_execute
# ---------------------------------------------------------------------------


class TestHandleExecute:
    @pytest.mark.asyncio
    async def test_not_initialized(self):
        import llmos_bridge.isolation.worker as w
        old = w._module_instance
        try:
            w._module_instance = None
            with pytest.raises(_WorkerMethodError) as exc_info:
                await _handle_execute({"action": "read_file"})
            assert exc_info.value.code == proto.MODULE_LOAD_ERROR
        finally:
            w._module_instance = old

    @pytest.mark.asyncio
    async def test_missing_action(self):
        import llmos_bridge.isolation.worker as w
        old = w._module_instance
        try:
            w._module_instance = MagicMock()
            with pytest.raises(_WorkerMethodError) as exc_info:
                await _handle_execute({})
            assert exc_info.value.code == proto.ACTION_NOT_FOUND_ERROR
        finally:
            w._module_instance = old

    @pytest.mark.asyncio
    async def test_success_dict_result(self):
        import llmos_bridge.isolation.worker as w
        mock_module = MagicMock()
        mock_module.execute = AsyncMock(return_value={"files": ["a.txt"]})
        old = w._module_instance
        try:
            w._module_instance = mock_module
            result = await _handle_execute({"action": "list_files", "params": {"path": "/tmp"}})
            assert result == {"files": ["a.txt"]}
            mock_module.execute.assert_called_once_with("list_files", {"path": "/tmp"}, None)
        finally:
            w._module_instance = old

    @pytest.mark.asyncio
    async def test_pydantic_model_dump(self):
        import llmos_bridge.isolation.worker as w
        pydantic_result = MagicMock()
        pydantic_result.model_dump.return_value = {"width": 1920, "elements": []}
        mock_module = MagicMock()
        mock_module.execute = AsyncMock(return_value=pydantic_result)
        old = w._module_instance
        try:
            w._module_instance = mock_module
            result = await _handle_execute({"action": "parse_screen", "params": {}})
            assert result == {"width": 1920, "elements": []}
        finally:
            w._module_instance = old

    @pytest.mark.asyncio
    async def test_base64_decoding(self):
        """_screenshot_bytes_b64 should be decoded to screenshot_bytes."""
        import base64
        import llmos_bridge.isolation.worker as w

        mock_module = MagicMock()
        mock_module.execute = AsyncMock(return_value={})
        old = w._module_instance
        try:
            w._module_instance = mock_module
            raw = base64.b64encode(b"PNG_DATA").decode()
            await _handle_execute({
                "action": "parse_screen",
                "params": {"_screenshot_bytes_b64": raw},
            })
            call_params = mock_module.execute.call_args[0][1]
            assert call_params["screenshot_bytes"] == b"PNG_DATA"
            assert "_screenshot_bytes_b64" not in call_params
        finally:
            w._module_instance = old

    @pytest.mark.asyncio
    async def test_action_exception(self):
        import llmos_bridge.isolation.worker as w
        mock_module = MagicMock()
        mock_module.execute = AsyncMock(side_effect=RuntimeError("crash"))
        old = w._module_instance
        try:
            w._module_instance = mock_module
            with pytest.raises(_WorkerMethodError) as exc_info:
                await _handle_execute({"action": "bad_action", "params": {}})
            assert exc_info.value.code == proto.ACTION_EXECUTION_ERROR
        finally:
            w._module_instance = old


# ---------------------------------------------------------------------------
# _handle_get_manifest
# ---------------------------------------------------------------------------


class TestHandleGetManifest:
    def test_not_initialized(self):
        import llmos_bridge.isolation.worker as w
        old = w._module_instance
        try:
            w._module_instance = None
            with pytest.raises(_WorkerMethodError):
                _handle_get_manifest()
        finally:
            w._module_instance = old

    def test_returns_dict(self):
        import llmos_bridge.isolation.worker as w
        manifest = MagicMock()
        manifest.to_dict.return_value = {"module_id": "test", "actions": []}
        mock_module = MagicMock()
        mock_module.get_manifest.return_value = manifest
        old = w._module_instance
        try:
            w._module_instance = mock_module
            result = _handle_get_manifest()
            assert result == {"module_id": "test", "actions": []}
        finally:
            w._module_instance = old


# ---------------------------------------------------------------------------
# _handle_health_check
# ---------------------------------------------------------------------------


class TestHandleHealthCheck:
    def test_returns_status(self):
        import llmos_bridge.isolation.worker as w
        old_id = w._module_id
        old_time = w._start_time
        try:
            w._module_id = "test_mod"
            w._start_time = 0  # Will give large uptime
            result = _handle_health_check()
            assert result["status"] == "ok"
            assert result["module_id"] == "test_mod"
            assert "uptime_seconds" in result
            assert "pid" in result
        finally:
            w._module_id = old_id
            w._start_time = old_time


# ---------------------------------------------------------------------------
# _dispatch
# ---------------------------------------------------------------------------


class TestDispatch:
    @pytest.mark.asyncio
    async def test_unknown_method(self):
        with pytest.raises(_WorkerMethodError) as exc_info:
            await _dispatch("nonexistent_method", {})
        assert exc_info.value.code == proto.METHOD_NOT_FOUND

    @pytest.mark.asyncio
    async def test_health_check(self):
        import llmos_bridge.isolation.worker as w
        old_id, old_time = w._module_id, w._start_time
        try:
            w._module_id = "dispatch_test"
            w._start_time = 0
            result = await _dispatch("health_check", {})
            assert result["status"] == "ok"
        finally:
            w._module_id = old_id
            w._start_time = old_time

    @pytest.mark.asyncio
    async def test_shutdown(self):
        """Shutdown should return status and schedule exit."""
        with patch("sys.exit"):
            result = await _dispatch("shutdown", {})
        assert result["status"] == "shutting_down"


# ---------------------------------------------------------------------------
# _WorkerMethodError
# ---------------------------------------------------------------------------


class TestWorkerMethodError:
    def test_stores_code(self):
        err = _WorkerMethodError(-32001, "load failed")
        assert err.code == -32001
        assert str(err) == "load failed"
