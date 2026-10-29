"""Health and diagnostic verification endpoints."""

from fastapi import APIRouter, status
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.schemas.common import HealthResponse, StandardResponse

router = APIRouter(tags=["health"])
settings = get_settings()


class EchoRequest(BaseModel):
    """Test schema for verifying Pydantic body validation in Phase 1."""

    message: str = Field(min_length=3, max_length=100)
    tag: str = Field(pattern="^[a-zA-Z0-9_-]+$")


@router.get(
    "/health",
    response_model=StandardResponse[HealthResponse],
    status_code=status.HTTP_200_OK,
    summary="Application Health Check",
)
async def check_health() -> StandardResponse[HealthResponse]:
    """Returns application health status, version, environment, and dynamic service connectivity."""
    from app.db.connection import get_db_manager

    db_status = "down"
    if settings.REPOSITORY_BACKEND == "memory":
        db_status = "up"
    else:
        try:
            db_mgr = get_db_manager()
            conn_res = await db_mgr.check_connectivity()
            if conn_res.get("status") == "connected":
                db_status = "up"
        except Exception:
            db_status = "down"

    is_healthy = (db_status == "up")
    health_data = HealthResponse(
        status="healthy" if is_healthy else "degraded",
        version=settings.APP_VERSION,
        environment=settings.APP_ENV,
        services={
            "api": "up",
            "database": db_status,
        },
    )
    return StandardResponse(
        data=health_data,
        message="System is operating normally" if is_healthy else "System operating in degraded mode (database down)",
    )


@router.post(
    "/test/echo",
    response_model=StandardResponse[EchoRequest],
    status_code=status.HTTP_200_OK,
    summary="Echo endpoint for schema validation testing",
)
async def echo_test(payload: EchoRequest) -> StandardResponse[EchoRequest]:
    """Echoes back validated payload, used for Phase 1 contract and validation tests."""
    return StandardResponse(
        data=payload,
        message="Payload successfully validated",
    )
