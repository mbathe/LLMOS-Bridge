"""Unit tests — API middleware (RequestIDMiddleware, AccessLogMiddleware, error handler)."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from llmos_bridge.api.middleware import (
    AccessLogMiddleware,
    RequestIDMiddleware,
    build_error_handler,
)
from llmos_bridge.exceptions import (
    IMLParseError,
    IMLValidationError,
    LLMOSError,
    PermissionDeniedError,
)


def _make_test_app() -> FastAPI:
    """Build a minimal FastAPI app with all middleware registered."""
    app = FastAPI()
    app.add_middleware(RequestIDMiddleware)
    app.add_middleware(AccessLogMiddleware)
    handler = build_error_handler()
    # Register for each exception type (Starlette 0.50+ requires explicit subclass registration)
    for exc_cls in [LLMOSError, PermissionDeniedError, IMLParseError, IMLValidationError]:
        app.add_exception_handler(exc_cls, handler)

    @app.get("/ok")
    async def ok() -> dict:
        return {"status": "ok"}

    @app.get("/error/permission")
    async def raise_permission():
        raise PermissionDeniedError(action="delete_file", module="filesystem", profile="readonly")

    @app.get("/error/parse")
    async def raise_parse():
        raise IMLParseError("bad json input")

    @app.get("/error/validation")
    async def raise_validation():
        raise IMLValidationError("invalid field value")

    @app.get("/error/internal")
    async def raise_internal():
        raise LLMOSError("unexpected failure")

    return app


@pytest.fixture
def client() -> TestClient:
    return TestClient(_make_test_app(), raise_server_exceptions=False)


@pytest.mark.unit
class TestRequestIDMiddleware:
    def test_request_id_injected_in_response(self, client: TestClient) -> None:
        resp = client.get("/ok")
        assert resp.status_code == 200
        assert "X-Request-ID" in resp.headers

    def test_custom_request_id_echoed(self, client: TestClient) -> None:
        resp = client.get("/ok", headers={"X-Request-ID": "test-rid-123"})
        assert resp.headers["X-Request-ID"] == "test-rid-123"


@pytest.mark.unit
class TestAccessLogMiddleware:
    def test_request_does_not_crash(self, client: TestClient) -> None:
        resp = client.get("/ok")
        assert resp.status_code == 200


@pytest.mark.unit
class TestErrorHandler:
    def test_permission_denied_returns_403(self, client: TestClient) -> None:
        resp = client.get("/error/permission")
        assert resp.status_code == 403
        body = resp.json()
        assert body["code"] == "permission_denied"
        assert "not allowed" in body["error"]

    def test_parse_error_returns_400(self, client: TestClient) -> None:
        resp = client.get("/error/parse")
        assert resp.status_code == 400
        body = resp.json()
        assert body["code"] == "parse_error"

    def test_validation_error_returns_422(self, client: TestClient) -> None:
        resp = client.get("/error/validation")
        assert resp.status_code == 422
        body = resp.json()
        assert body["code"] == "validation_error"

    def test_generic_llmos_error_returns_500(self, client: TestClient) -> None:
        resp = client.get("/error/internal")
        assert resp.status_code == 500
        body = resp.json()
        assert body["code"] == "internal_error"

    def test_error_response_includes_request_id(self, client: TestClient) -> None:
        resp = client.get("/error/permission", headers={"X-Request-ID": "req-999"})
        body = resp.json()
        assert body.get("request_id") == "req-999"
