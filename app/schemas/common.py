"""Common API response and error schemas conforming to docs/api-contracts.md."""

from datetime import datetime, timezone
from typing import Any, Generic, Optional, TypeVar
from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")


class StandardResponse(BaseModel, Generic[T]):
    """Standard success envelope for API responses."""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    success: bool = True
    data: T
    message: Optional[str] = "Operation completed successfully"
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ErrorDetail(BaseModel):
    """Detailed error object embedded in ErrorResponse."""

    code: str
    message: str
    field: Optional[str] = None
    details: Optional[Any] = None


class ErrorResponse(BaseModel):
    """Standard error response envelope returned on 4xx and 5xx errors."""

    success: bool = False
    error: ErrorDetail
    request_id: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HealthResponse(BaseModel):
    """Payload for system health diagnostic endpoint."""

    status: str = "healthy"
    version: str = "0.1.0"
    environment: str = "development"
    services: dict[str, str] = Field(default_factory=lambda: {"api": "up", "database": "up"})
