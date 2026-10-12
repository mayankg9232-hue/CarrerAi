"""FastAPI application initialization and lifecycle orchestration."""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncGenerator
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.v1.router import api_v1_router
from app.core.config import get_settings
from app.core.exceptions import (
    AppException,
    app_exception_handler,
    global_exception_handler,
    http_exception_handler,
    validation_exception_handler,
)
from app.core.logging import logger
from app.core.security import DEFAULT_MAX_PAYLOAD_SIZE, PayloadSizeLimitMiddleware, SecurityHeadersMiddleware

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan context manager for startup and shutdown routines."""
    logger.info(f"Starting {settings.APP_NAME} v{settings.APP_VERSION} [Env: {settings.APP_ENV}]")
    yield
    logger.info(f"Shutting down {settings.APP_NAME}")


def create_application() -> FastAPI:
    """Application factory configuring middleware, exception handlers, and routes."""
    app = FastAPI(
        title=settings.APP_NAME,
        version=settings.APP_VERSION,
        debug=settings.DEBUG,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # 1. Security & Tracing Middleware (Catches unhandled errors safely)
    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(PayloadSizeLimitMiddleware, max_payload_bytes=DEFAULT_MAX_PAYLOAD_SIZE)

    # 2. CORS Middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.ALLOWED_ORIGINS,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # 3. Global Exception Handlers
    app.add_exception_handler(AppException, app_exception_handler)
    app.add_exception_handler(RequestValidationError, validation_exception_handler)
    app.add_exception_handler(StarletteHTTPException, http_exception_handler)
    app.add_exception_handler(Exception, global_exception_handler)

    # 4. API v1 Routes
    app.include_router(api_v1_router, prefix=settings.API_V1_PREFIX)

    # Diagnostic endpoint for testing unhandled errors safely (disabled in production)
    if settings.DEBUG:
        @app.get("/api/v1/test/unhandled-error", include_in_schema=False)
        async def trigger_unhandled_error() -> None:
            raise RuntimeError("Deliberate unhandled test error to verify safe 500 handler")

    # 5. Local Dev UI Static Mount
    dev_ui_dir = Path("dev_ui")
    if dev_ui_dir.exists():
        app.mount("/dev-ui-assets", StaticFiles(directory="dev_ui"), name="dev_ui_assets")

        @app.get("/", include_in_schema=False)
        @app.get("/ui", include_in_schema=False)
        async def serve_dev_ui() -> FileResponse:
            index_path = dev_ui_dir / "index.html"
            return FileResponse(index_path)

    return app


app = create_application()
