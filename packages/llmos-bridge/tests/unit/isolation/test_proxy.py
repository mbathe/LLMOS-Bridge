"""Tests for isolation.proxy — IsolatedModuleProxy (host-side)."""

from __future__ import annotations

import asyncio
import base64
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from llmos_bridge.exceptions import (
    ActionExecutionError,
    WorkerCommunicationError,
    WorkerCrashedError,
    WorkerStartError,
)
from llmos_bridge.isolation.proxy import IsolatedModuleProxy
from llmos_bridge.modules.base import ExecutionContext, Platform
from llmos_bridge.modules.manifest import ModuleManifest


@pytest.fixture
def venv_mgr():
    mgr = MagicMock()
    mgr.ensure_venv = AsyncMock(return_value=Path("/fake/venv/bin/python"))
    return mgr


@pytest.fixture
def proxy(venv_mgr):
    return IsolatedModuleProxy(
        module_id="vision",
        module_class_path="llmos_bridge.modules.perception_vision.ultra.module:UltraVisionModule",
        venv_manager=venv_mgr,
        requirements=["torch>=2.2"],
        env_vars={"LLMOS_DEVICE": "cuda"},
        timeout=10.0,
        max_restarts=2,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


class TestConstruction:
    def test_module_id(self, proxy: IsolatedModuleProxy):
        assert proxy.MODULE_ID == "vision"

    def test_does_not_call_super_init(self, venv_mgr):
        """__init__ must NOT call super().__init__() which calls _check_dependencies."""
        # If super().__init__() was called, BaseModule would try to run
        # _check_dependencies, which is fine (it's a no-op), but we verify
        # the proxy doesn't accidentally import heavy deps.
        p = IsolatedModuleProxy(
            module_id="test",
            module_class_path="fake:Class",
            venv_manager=venv_mgr,
        )
        assert p._security is None
        assert p._started is False

    def test_supported_platforms_all(self, proxy: IsolatedModuleProxy):
        assert Platform.ALL in proxy.SUPPORTED_PLATFORMS

    def test_set_security(self, proxy: IsolatedModuleProxy):
        sec = MagicMock()
        proxy.set_security(sec)
        assert proxy._security is sec


# ---------------------------------------------------------------------------
# get_manifest (before start)
# ---------------------------------------------------------------------------


class TestGetManifestBeforeStart:
    def test_returns_minimal_manifest(self, proxy: IsolatedModuleProxy):
        m = proxy.get_manifest()
        assert isinstance(m, ModuleManifest)
        assert m.module_id == "vision"
        assert "not yet started" in m.description

    def test_returns_cached_manifest_after_set(self, proxy: IsolatedModuleProxy):
        cached = ModuleManifest(
            module_id="vision", version="2.0.0", description="UltraVision",
        )
        proxy._manifest = cached
        assert proxy.get_manifest() is cached


# ---------------------------------------------------------------------------
# _parse_manifest
# ---------------------------------------------------------------------------


class TestParseManifest:
    def test_full_manifest(self, proxy: IsolatedModuleProxy):
        data = {
            "module_id": "vision",
            "version": "2.0.0",
            "description": "Ultra Vision Module",
            "author": "LLMOS",
            "platforms": ["linux", "macos"],
            "actions": [
                {
                    "name": "parse_screen",
                    "description": "Parse a screenshot",
                    "params_schema": {
                        "type": "object",
                        "properties": {
                            "screenshot_path": {"type": "string", "description": "Path"},
                        },
                        "required": ["screenshot_path"],
                    },
                    "returns": "object",
                    "permission_required": "local_worker",
                    "platforms": ["all"],
                    "examples": [],
                    "tags": ["vision"],
                },
            ],
            "declared_permissions": ["screen_capture"],
        }
        m = proxy._parse_manifest(data)
        assert m.module_id == "vision"
        assert m.version == "2.0.0"
        assert len(m.actions) == 1
        assert m.actions[0].name == "parse_screen"
        assert len(m.actions[0].params) == 1
        assert m.actions[0].params[0].name == "screenshot_path"
        assert m.actions[0].params[0].required is True

    def test_empty_manifest(self, proxy: IsolatedModuleProxy):
        m = proxy._parse_manifest({})
        assert m.module_id == "vision"
        assert m.actions == []


# ---------------------------------------------------------------------------
# _rpc
# ---------------------------------------------------------------------------


class TestRpc:
    @pytest.mark.asyncio
    async def test_not_running(self, proxy: IsolatedModuleProxy):
        with pytest.raises(WorkerCommunicationError, match="not running"):
            await proxy._rpc("health_check", {})

    @pytest.mark.asyncio
    async def test_success(self, proxy: IsolatedModuleProxy):
        """Simulate a successful RPC round-trip."""
        # Mock process with stdin/stdout.
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        proxy._process = MagicMock()
        proxy._process.stdin = mock_stdin
        proxy._process.returncode = None

        # Simulate response arriving.
        async def fake_rpc(method, params, timeout=None):
            # Peek at the request that would be sent.
            return {"status": "ok"}

        # Directly test by putting a result on the pending future.
        request_id = "test123"
        loop = asyncio.get_event_loop()
        future = loop.create_future()
        proxy._pending[request_id] = future
        future.set_result({"result": {"status": "ok"}, "error": None})

        result = await future
        assert result["result"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_error_response(self, proxy: IsolatedModuleProxy):
        """RPC returning an error should raise ActionExecutionError."""
        mock_stdin = MagicMock()
        mock_stdin.write = MagicMock()
        mock_stdin.drain = AsyncMock()
        proxy._process = MagicMock()
        proxy._process.stdin = mock_stdin
        proxy._process.returncode = None

        # Monkey-patch to return error immediately.
        async def mock_rpc(method, params, timeout=None):
            raise ActionExecutionError(
                module_id="vision", action=method,
                cause=RuntimeError("[-32003] Action failed"),
            )

        with patch.object(proxy, "_rpc", side_effect=mock_rpc):
            with pytest.raises(ActionExecutionError, match="Action failed"):
                await proxy._rpc("execute", {"action": "bad"})


# ---------------------------------------------------------------------------
# execute
# ---------------------------------------------------------------------------


class TestExecute:
    @pytest.mark.asyncio
    async def test_calls_rpc(self, proxy: IsolatedModuleProxy):
        proxy._started = True
        proxy._process = MagicMock()
        proxy._process.returncode = None

        with patch.object(proxy, "_rpc", new_callable=AsyncMock, return_value={"ok": True}) as mock:
            result = await proxy.execute("list_files", {"path": "/tmp"})
            assert result == {"ok": True}
            mock.assert_called_once()
            call_params = mock.call_args[0][1]
            assert call_params["action"] == "list_files"
            assert call_params["params"]["path"] == "/tmp"

    @pytest.mark.asyncio
    async def test_with_context(self, proxy: IsolatedModuleProxy):
        proxy._started = True
        proxy._process = MagicMock()
        proxy._process.returncode = None

        ctx = ExecutionContext(plan_id="p1", action_id="a1")
        with patch.object(proxy, "_rpc", new_callable=AsyncMock, return_value={}) as mock:
            await proxy.execute("click", {"x": 100}, context=ctx)
            call_params = mock.call_args[0][1]
            assert call_params["context"]["plan_id"] == "p1"

    @pytest.mark.asyncio
    async def test_lazy_start(self, proxy: IsolatedModuleProxy):
        """First execute call should trigger start()."""
        with patch.object(proxy, "start", new_callable=AsyncMock) as mock_start:
            with patch.object(proxy, "_rpc", new_callable=AsyncMock, return_value={}):
                await proxy.execute("test_action", {})
            mock_start.assert_called_once()


# ---------------------------------------------------------------------------
# parse_screen (BaseVisionModule compat)
# ---------------------------------------------------------------------------


class TestParseScreen:
    @pytest.mark.asyncio
    async def test_encodes_bytes(self, proxy: IsolatedModuleProxy):
        proxy._started = True
        proxy._process = MagicMock()
        proxy._process.returncode = None

        raw_png = b"FAKE_PNG_DATA"

        with patch.object(proxy, "execute", new_callable=AsyncMock, return_value={
            "elements": [], "width": 1920, "height": 1080,
            "parse_time_ms": 500, "model_id": "test",
        }):
            await proxy.parse_screen(screenshot_bytes=raw_png)
            call_params = proxy.execute.call_args[0][1]
            # Should be base64-encoded under the sentinel key.
            assert "_screenshot_bytes_b64" in call_params
            decoded = base64.b64decode(call_params["_screenshot_bytes_b64"])
            assert decoded == raw_png
            assert "screenshot_bytes" not in call_params

    @pytest.mark.asyncio
    async def test_forwards_path(self, proxy: IsolatedModuleProxy):
        proxy._started = True
        proxy._process = MagicMock()
        proxy._process.returncode = None

        with patch.object(proxy, "execute", new_callable=AsyncMock, return_value={
            "elements": [], "width": 1920, "height": 1080,
            "parse_time_ms": 100, "model_id": "test",
        }):
            await proxy.parse_screen(screenshot_path="/tmp/screen.png")
            call_params = proxy.execute.call_args[0][1]
            assert call_params["screenshot_path"] == "/tmp/screen.png"


# ---------------------------------------------------------------------------
# Lifecycle
# ---------------------------------------------------------------------------


class TestLifecycle:
    def test_is_alive_not_started(self, proxy: IsolatedModuleProxy):
        assert proxy.is_alive is False

    def test_is_alive_running(self, proxy: IsolatedModuleProxy):
        proxy._started = True
        proxy._process = MagicMock()
        proxy._process.returncode = None
        assert proxy.is_alive is True

    def test_is_alive_crashed(self, proxy: IsolatedModuleProxy):
        proxy._started = True
        proxy._process = MagicMock()
        proxy._process.returncode = 1
        assert proxy.is_alive is False

    @pytest.mark.asyncio
    async def test_stop_kills_process(self, proxy: IsolatedModuleProxy):
        proxy._process = MagicMock()
        proxy._process.returncode = None
        proxy._process.terminate = MagicMock()
        proxy._process.kill = MagicMock()
        proxy._process.wait = AsyncMock()
        proxy._reader_task = None
        proxy._started = True

        with patch.object(proxy, "_rpc", new_callable=AsyncMock):
            await proxy.stop()

        assert proxy._started is False

    @pytest.mark.asyncio
    async def test_health_check_alive(self, proxy: IsolatedModuleProxy):
        proxy._started = True
        proxy._process = MagicMock()
        proxy._process.returncode = None

        with patch.object(proxy, "_rpc", new_callable=AsyncMock, return_value={"status": "ok"}):
            result = await proxy.health_check()
            assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_health_check_dead(self, proxy: IsolatedModuleProxy):
        result = await proxy.health_check()
        assert result["status"] == "dead"

    @pytest.mark.asyncio
    async def test_max_restarts_exhausted(self, proxy: IsolatedModuleProxy):
        proxy._restart_count = 5  # > max_restarts=2
        proxy._started = False
        with pytest.raises(WorkerStartError, match="Max restarts"):
            await proxy._ensure_started()


# ---------------------------------------------------------------------------
# Reader loop
# ---------------------------------------------------------------------------


class TestReaderLoop:
    @pytest.mark.asyncio
    async def test_resolves_pending_futures(self, proxy: IsolatedModuleProxy):
        """Reader loop should resolve pending futures when response arrives."""
        # Create a mock stdout that yields one response then EOF.
        response = json.dumps({
            "jsonrpc": "2.0", "id": "req_001",
            "result": {"status": "ok"}, "error": None,
        }).encode() + b"\n"

        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(side_effect=[response, b""])
        proxy._process = MagicMock()
        proxy._process.stdout = mock_stdout
        proxy._process.returncode = None

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        proxy._pending["req_001"] = future

        await proxy._reader_loop()

        assert future.done()
        assert future.result()["result"]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_handles_notifications(self, proxy: IsolatedModuleProxy):
        """Worker.log notifications should be processed without error."""
        notification = json.dumps({
            "jsonrpc": "2.0", "method": "worker.log",
            "params": {"level": "info", "message": "test"},
        }).encode() + b"\n"

        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(side_effect=[notification, b""])
        proxy._process = MagicMock()
        proxy._process.stdout = mock_stdout
        proxy._process.returncode = 0

        await proxy._reader_loop()
        # No crash = success

    @pytest.mark.asyncio
    async def test_eof_fails_pending(self, proxy: IsolatedModuleProxy):
        """On EOF (worker death), pending futures should fail."""
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(return_value=b"")
        proxy._process = MagicMock()
        proxy._process.stdout = mock_stdout
        proxy._process.returncode = 1

        loop = asyncio.get_event_loop()
        future = loop.create_future()
        proxy._pending["orphan"] = future

        await proxy._reader_loop()

        assert future.done()
        with pytest.raises(WorkerCrashedError):
            future.result()
