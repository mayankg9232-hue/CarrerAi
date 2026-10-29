"""Domain exceptions and standardized global exception handlers."""

import uuid
from typing import Any, Optional
from fastapi import Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.logging import logger
from app.schemas.common import ErrorDetail, ErrorResponse


class AppException(Exception):
    """Base application domain exception."""

    def __init__(
        self,
        message: str,
        code: str = "INTERNAL_SERVER_ERROR",
        status_code: int = status.HTTP_500_INTERNAL_SERVER_ERROR,
        field: Optional[str] = None,
        details: Optional[Any] = None,
    ):
        super().__init__(message)
        self.message = message
        self.code = code
        self.status_code = status_code
        self.field = field
        self.details = details


class BadRequestError(AppException):
    """Malformed syntax or unparseable input (400)."""

    def __init__(self, message: str = "Bad request", field: Optional[str] = None, details: Optional[Any] = None):
        super().__init__(
            message=message,
            code="BAD_REQUEST",
            status_code=status.HTTP_400_BAD_REQUEST,
            field=field,
            details=details,
        )


class AuthenticationError(AppException):
    """Missing or invalid credentials (401)."""

    def __init__(self, message: str = "Authentication required"):
        super().__init__(
            message=message,
            code="UNAUTHORIZED",
            status_code=status.HTTP_401_UNAUTHORIZED,
        )


class AuthorizationError(AppException):
    """Forbidden resource access (403)."""

    def __init__(
        self,
        message: str = "Access forbidden",
        code: str = "FORBIDDEN",
        error_code: Optional[str] = None,
        field: Optional[str] = None,
        details: Optional[Any] = None,
    ):
        super().__init__(
            message=message,
            code=error_code or code,
            status_code=status.HTTP_403_FORBIDDEN,
            field=field,
            details=details,
        )


class NotFoundError(AppException):
    """Resource not found (404)."""

    def __init__(self, message: str = "Resource not found", field: Optional[str] = None):
        super().__init__(
            message=message,
            code="NOT_FOUND",
            status_code=status.HTTP_404_NOT_FOUND,
            field=field,
        )


class PayloadTooLargeError(AppException):
    """Uploaded payload exceeds maximum size limit (413)."""

    def __init__(self, message: str = "Payload exceeds size limit"):
        super().__init__(
            message=message,
            code="PAYLOAD_TOO_LARGE",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
        )


class UnsupportedMediaTypeError(AppException):
    """Unsupported content MIME type (415)."""

    def __init__(self, message: str = "Unsupported media type"):
        super().__init__(
            message=message,
            code="UNSUPPORTED_MEDIA_TYPE",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )


class ConflictError(AppException):
    """Resource conflict, e.g. duplicate user registration (409)."""

    def __init__(self, message: str = "Resource conflict", field: Optional[str] = None):
        super().__init__(
            message=message,
            code="CONFLICT",
            status_code=status.HTTP_409_CONFLICT,
            field=field,
        )


class ValidationAppError(AppException):
    """Business validation violation (422)."""

    def __init__(self, message: str, field: Optional[str] = None, details: Optional[Any] = None):
        super().__init__(
            message=message,
            code="VALIDATION_ERROR",
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            field=field,
            details=details,
        )


class RateLimitError(AppException):
    """Rate limit exceeded (429)."""

    def __init__(self, message: str = "Rate limit exceeded"):
        super().__init__(
            message=message,
            code="RATE_LIMIT_EXCEEDED",
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
        )


class ExternalProviderUnavailableError(AppException):
    """Upstream service / provider unavailable (502)."""

    def __init__(self, message: str = "Upstream provider unavailable"):
        super().__init__(
            message=message,
            code="BAD_GATEWAY",
            status_code=status.HTTP_502_BAD_GATEWAY,
        )


class ExternalTimeoutError(AppException):
    """Upstream provider timeout exceeded (504)."""

    def __init__(self, message: str = "Upstream request timed out"):
        super().__init__(
            message=message,
            code="GATEWAY_TIMEOUT",
            status_code=status.HTTP_504_GATEWAY_TIMEOUT,
        )


async def app_exception_handler(request: Request, exc: AppException) -> JSONResponse:
    """Handles domain-specific AppException."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.warning(f"AppException [{exc.code}] on {request.method} {request.url.path}: {exc.message}")

    payload = ErrorResponse(
        success=False,
        error=ErrorDetail(
            code=exc.code,
            message=exc.message,
            field=exc.field,
            details=exc.details,
        ),
        request_id=request_id,
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))


async def validation_exception_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    """Handles FastAPI/Pydantic RequestValidationError."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    errors = exc.errors()
    first_field = ".".join(str(loc) for loc in errors[0].get("loc", [])) if errors else None
    first_msg = errors[0].get("msg", "Invalid request body") if errors else "Validation failed"

    logger.info(f"ValidationError on {request.method} {request.url.path}: {first_msg} (field: {first_field})")

    payload = ErrorResponse(
        success=False,
        error=ErrorDetail(
            code="VALIDATION_ERROR",
            message=f"The request body failed validation constraints: {first_msg}",
            field=first_field,
            details=[{"loc": err.get("loc"), "msg": err.get("msg"), "type": err.get("type")} for err in errors],
        ),
        request_id=request_id,
    )
    return JSONResponse(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, content=payload.model_dump(mode="json"))


async def http_exception_handler(request: Request, exc: StarletteHTTPException) -> JSONResponse:
    """Handles Starlette / FastAPI HTTPExceptions (404, 405, etc.)."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    code_map = {
        400: "BAD_REQUEST",
        401: "UNAUTHORIZED",
        403: "FORBIDDEN",
        404: "NOT_FOUND",
        405: "METHOD_NOT_ALLOWED",
        413: "PAYLOAD_TOO_LARGE",
        415: "UNSUPPORTED_MEDIA_TYPE",
        429: "RATE_LIMIT_EXCEEDED",
    }
    code = code_map.get(exc.status_code, f"HTTP_{exc.status_code}")

    payload = ErrorResponse(
        success=False,
        error=ErrorDetail(
            code=code,
            message=str(exc.detail),
        ),
        request_id=request_id,
    )
    return JSONResponse(status_code=exc.status_code, content=payload.model_dump(mode="json"))


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions. Prevents leaking stack traces or internal paths."""
    request_id = getattr(request.state, "request_id", str(uuid.uuid4()))
    logger.exception(f"Unhandled server error on {request.method} {request.url.path} (request_id: {request_id})")

    payload = ErrorResponse(
        success=False,
        error=ErrorDetail(
            code="INTERNAL_SERVER_ERROR",
            message="An unexpected server error occurred. Please contact support with the request ID.",
        ),
        request_id=request_id,
    )
    return JSONResponse(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, content=payload.model_dump(mode="json"))
