"""Security middleware and tracing helpers."""

import uuid
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.exceptions import global_exception_handler


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
