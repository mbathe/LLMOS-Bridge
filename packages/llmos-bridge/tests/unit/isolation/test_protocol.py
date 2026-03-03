"""Tests for isolation.protocol — JSON-RPC 2.0 message types and error codes."""

from __future__ import annotations

import json

import pytest

from llmos_bridge.isolation.protocol import (
    ACTION_EXECUTION_ERROR,
    ACTION_NOT_FOUND_ERROR,
    BINARY_SCREENSHOT_KEY,
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_EXECUTE,
    METHOD_GET_MANIFEST,
    METHOD_HEALTH_CHECK,
    METHOD_INITIALIZE,
    METHOD_NOT_FOUND,
    METHOD_SHUTDOWN,
    MODULE_LOAD_ERROR,
    NOTIFICATION_WORKER_LOG,
    NOTIFICATION_WORKER_READY,
    PARSE_ERROR,
    PERMISSION_ERROR,
    TIMEOUT_ERROR,
    JsonRpcError,
    JsonRpcNotification,
    JsonRpcRequest,
    JsonRpcResponse,
)


# ---------------------------------------------------------------------------
# JsonRpcRequest
# ---------------------------------------------------------------------------


class TestJsonRpcRequest:
    def test_minimal(self):
        req = JsonRpcRequest(method="health_check")
        assert req.jsonrpc == "2.0"
        assert req.method == "health_check"
        assert req.params == {}
        assert len(req.id) == 12

    def test_with_params(self):
        req = JsonRpcRequest(method="execute", params={"action": "click"}, id="abc")
        assert req.method == "execute"
        assert req.params == {"action": "click"}
        assert req.id == "abc"

    def test_auto_id_is_unique(self):
        ids = {JsonRpcRequest(method="ping").id for _ in range(50)}
        assert len(ids) == 50

    def test_json_round_trip(self):
        req = JsonRpcRequest(method="initialize", params={"env_vars": {"A": "1"}}, id="x1")
        data = json.loads(req.model_dump_json())
        assert data["jsonrpc"] == "2.0"
        assert data["method"] == "initialize"
        assert data["params"]["env_vars"]["A"] == "1"
        assert data["id"] == "x1"

    def test_from_dict(self):
        raw = {"jsonrpc": "2.0", "method": "execute", "params": {}, "id": "r1"}
        req = JsonRpcRequest.model_validate(raw)
        assert req.method == "execute"
        assert req.id == "r1"


# ---------------------------------------------------------------------------
# JsonRpcResponse
# ---------------------------------------------------------------------------


class TestJsonRpcResponse:
    def test_success_response(self):
        resp = JsonRpcResponse(id="r1", result={"status": "ok"})
        assert resp.jsonrpc == "2.0"
        assert resp.result == {"status": "ok"}
        assert resp.error is None
        assert resp.id == "r1"

    def test_error_response(self):
        err = JsonRpcError(code=INTERNAL_ERROR, message="boom")
        resp = JsonRpcResponse(id="r2", error=err)
        assert resp.result is None
        assert resp.error.code == INTERNAL_ERROR
        assert resp.error.message == "boom"

    def test_error_with_data(self):
        err = JsonRpcError(code=ACTION_EXECUTION_ERROR, message="fail", data={"traceback": "..."})
        resp = JsonRpcResponse(id="r3", error=err)
        assert resp.error.data == {"traceback": "..."}

    def test_json_round_trip(self):
        resp = JsonRpcResponse(id="r4", result=[1, 2, 3])
        data = json.loads(resp.model_dump_json())
        assert data["result"] == [1, 2, 3]
        assert data["id"] == "r4"
        assert data["error"] is None


# ---------------------------------------------------------------------------
# JsonRpcNotification
# ---------------------------------------------------------------------------


class TestJsonRpcNotification:
    def test_worker_ready(self):
        n = JsonRpcNotification(method=NOTIFICATION_WORKER_READY, params={"pid": 12345})
        assert n.jsonrpc == "2.0"
        assert n.method == "worker.ready"
        assert n.params["pid"] == 12345

    def test_worker_log(self):
        n = JsonRpcNotification(
            method=NOTIFICATION_WORKER_LOG,
            params={"level": "info", "message": "loaded"},
        )
        assert n.method == "worker.log"
        assert n.params["level"] == "info"

    def test_no_id_field(self):
        n = JsonRpcNotification(method="test")
        data = n.model_dump()
        assert "id" not in data

    def test_json_round_trip(self):
        n = JsonRpcNotification(method="worker.ready", params={"module_id": "vision"})
        data = json.loads(n.model_dump_json())
        assert data["method"] == "worker.ready"
        assert "id" not in data


# ---------------------------------------------------------------------------
# JsonRpcError
# ---------------------------------------------------------------------------


class TestJsonRpcError:
    def test_basic(self):
        err = JsonRpcError(code=-32603, message="Internal error")
        assert err.code == -32603
        assert err.message == "Internal error"
        assert err.data is None

    def test_with_data(self):
        err = JsonRpcError(code=-32001, message="load fail", data={"module": "vision"})
        assert err.data == {"module": "vision"}


# ---------------------------------------------------------------------------
# Error code constants
# ---------------------------------------------------------------------------


class TestErrorCodes:
    def test_standard_codes(self):
        assert PARSE_ERROR == -32700
        assert INVALID_REQUEST == -32600
        assert METHOD_NOT_FOUND == -32601
        assert INVALID_PARAMS == -32602
        assert INTERNAL_ERROR == -32603

    def test_llmos_codes(self):
        assert MODULE_LOAD_ERROR == -32001
        assert ACTION_NOT_FOUND_ERROR == -32002
        assert ACTION_EXECUTION_ERROR == -32003
        assert PERMISSION_ERROR == -32004
        assert TIMEOUT_ERROR == -32005

    def test_codes_are_negative(self):
        codes = [
            PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS,
            INTERNAL_ERROR, MODULE_LOAD_ERROR, ACTION_NOT_FOUND_ERROR,
            ACTION_EXECUTION_ERROR, PERMISSION_ERROR, TIMEOUT_ERROR,
        ]
        for code in codes:
            assert code < 0


# ---------------------------------------------------------------------------
# Method & notification constants
# ---------------------------------------------------------------------------


class TestMethodConstants:
    def test_methods(self):
        assert METHOD_INITIALIZE == "initialize"
        assert METHOD_EXECUTE == "execute"
        assert METHOD_GET_MANIFEST == "get_manifest"
        assert METHOD_HEALTH_CHECK == "health_check"
        assert METHOD_SHUTDOWN == "shutdown"

    def test_notifications(self):
        assert NOTIFICATION_WORKER_READY == "worker.ready"
        assert NOTIFICATION_WORKER_LOG == "worker.log"

    def test_binary_key(self):
        assert BINARY_SCREENSHOT_KEY == "_screenshot_bytes_b64"
