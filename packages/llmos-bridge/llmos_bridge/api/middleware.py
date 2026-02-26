"""API layer — Request middleware.

- Request ID injection (X-Request-ID header)
- Structured access logging
- Global exception handler → clean ErrorResponse
"""

from __future__ import annotations

import time
import uuid
from typing import Any

from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

from llmos_bridge.api.schemas import ErrorResponse
from llmos_bridge.exceptions import (
    IMLParseError,
    IMLValidationError,
    LLMOSError,
    PermissionDeniedError,
)
from llmos_bridge.logging import get_logger

log = get_logger(__name__)


class RequestIDMiddleware(BaseHTTPMiddleware):
    """Attach a unique X-Request-ID to every request and response."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response


class AccessLogMiddleware(BaseHTTPMiddleware):
    """Log each request with timing information."""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        start = time.time()
        response = await call_next(request)
        duration_ms = round((time.time() - start) * 1000, 2)

        log.info(
            "http_request",
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_ms=duration_ms,
            request_id=getattr(request.state, "request_id", None),
        )
        return response


def build_error_handler() -> Any:
    """Return a FastAPI exception handler for LLMOSError subclasses."""

    async def handler(request: Request, exc: LLMOSError) -> JSONResponse:
        request_id = getattr(request.state, "request_id", None)

        if isinstance(exc, PermissionDeniedError):
            status_code = 403
            code = "permission_denied"
        elif isinstance(exc, IMLParseError):
            status_code = 400
            code = "parse_error"
        elif isinstance(exc, IMLValidationError):
            status_code = 422
            code = "validation_error"
        else:
            status_code = 500
            code = "internal_error"

        body = ErrorResponse(
            error=exc.message,
            code=code,
            detail=exc.context or None,
            request_id=request_id,
        )
        return JSONResponse(status_code=status_code, content=body.model_dump())

    return handler
