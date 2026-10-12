"""Security middleware, request size capping, and tracing helpers (Developer A)."""

import uuid
from fastapi import status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.exceptions import global_exception_handler
from app.schemas.common import ErrorDetail, ErrorResponse

# Default max payload size: 5 MB (5 * 1024 * 1024 bytes)
DEFAULT_MAX_PAYLOAD_SIZE = 5 * 1024 * 1024


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Adds security headers, request ID tracking, and catches uncaught exceptions safely."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
        request.state.request_id = request_id

        try:
            response = await call_next(request)
        except Exception as exc:
            response = await global_exception_handler(request, exc)

        response.headers["X-Request-ID"] = request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
        return response


class PayloadSizeLimitMiddleware(BaseHTTPMiddleware):
    """Rejects incoming HTTP requests exceeding the maximum allowed body size (413)."""

    def __init__(self, app, max_payload_bytes: int = DEFAULT_MAX_PAYLOAD_SIZE):
        super().__init__(app)
        self.max_payload_bytes = max_payload_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        content_length = request.headers.get("content-length")
        if content_length:
            try:
                length = int(content_length)
                if length > self.max_payload_bytes:
                    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
                    error_payload = ErrorResponse(
                        success=False,
                        error=ErrorDetail(
                            code="PAYLOAD_TOO_LARGE",
                            message=f"Request payload exceeds maximum allowed limit of {self.max_payload_bytes} bytes",
                        ),
                        request_id=request_id,
                    )
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content=error_payload.model_dump(mode="json"),
                    )
            except ValueError:
                pass

        return await call_next(request)
