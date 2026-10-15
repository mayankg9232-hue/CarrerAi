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

    # 1. Initialize persistent database connection pool if SQL backend is active
    if settings.REPOSITORY_BACKEND == "sql":
        from app.db.connection import get_db_manager
        db_mgr = get_db_manager()
        await db_mgr.initialize()
        logger.info("Database pool initialized (backend: %s)", "PostgreSQL" if db_mgr.is_postgres else "SQLite")

    # Seed canonical sample jobs and demo accounts matching CareerMatch AI spec
    try:
        from app.repositories.job_repo import get_job_repository
        from app.schemas.job import Job
        job_repo = get_job_repository()
        existing_jobs = await job_repo.list_all()
        if not existing_jobs:
            sample_jobs = [
                Job(
                    id="job_atlas_01",
                    title="Frontend Engineer",
                    company="Atlas Labs",
                    location="San Francisco, CA · Hybrid (2 days / week)",
                    description="Atlas Labs is a product team building collaborative work software. This role focuses on fast, reliable interfaces that make complex workflows feel simple.",
                    required_skills=["React", "TypeScript", "CSS", "Jest"],
                    preferred_skills=["GraphQL", "Accessibility", "WCAG"],
                    is_active=True,
                ),
                Job(
                    id="job_forma_02",
                    title="Product Engineer",
                    company="Forma",
                    location="US · Remote",
                    description="Build user-facing workflows and design systems for enterprise team analytics.",
                    required_skills=["React", "TypeScript", "CSS"],
                    preferred_skills=["Node.js", "Docker"],
                    is_active=True,
                ),
                Job(
                    id="job_northstar_03",
                    title="UI Engineer",
                    company="Northstar",
                    location="San Francisco, CA · Hybrid",
                    description="Create reusable web components and interface libraries across multiple product lines.",
                    required_skills=["React", "TypeScript", "CSS"],
                    preferred_skills=["Accessibility", "Storybook"],
                    is_active=True,
                ),
                Job(
                    id="job_parcel_04",
                    title="Frontend Developer",
                    company="Parcel",
                    location="US · Remote",
                    description="Modern ecommerce logistics interface development with React and test automation.",
                    required_skills=["React", "TypeScript", "CSS"],
                    preferred_skills=["Cypress", "Testing"],
                    is_active=True,
                ),
            ]
            for j in sample_jobs:
                await job_repo.create_job(j)
            logger.info(f"Seeded {len(sample_jobs)} CareerMatch sample jobs for local host demonstration")

            from app.cache.service import get_cache_service
            cache_svc = get_cache_service()
            await cache_svc.invalidate_jobs_cache()

        from app.repositories.user_repo import get_user_repository
        from app.authentication.service import AuthenticationService
        from app.repositories.resume_repo import get_resume_repository
        from app.schemas.resume import CandidateProfile, SkillItem
        user_repo = get_user_repository()
        auth_service = AuthenticationService(repository=user_repo)
        resume_repo = get_resume_repository()

        # 1. Maya Chen (Canonical Profile from PDF)
        maya_user = await user_repo.get_by_email("maya.chen@example.com")
        if not maya_user:
            maya_user = await user_repo.create_user(
                email="maya.chen@example.com",
                hashed_password=auth_service.hash_password("Password123!"),
                full_name="Maya Chen",
                role="candidate",
            )
            maya_profile = CandidateProfile(
                id=f"profile_{maya_user.id}",
                user_id=maya_user.id,
                raw_hash="hash_maya_chen_resume",
                full_name="Maya Chen",
                email="maya.chen@example.com",
                summary="Frontend engineer with 4 years building accessible-minded, performant web products with React and TypeScript. Experienced in reusable UI, testing and design collaboration.",
                skills=[
                    SkillItem(name="React", category="Framework", confidence=0.98, is_verified=True),
                    SkillItem(name="TypeScript", category="Language", confidence=0.96, is_verified=True),
                    SkillItem(name="CSS", category="Styling", confidence=0.95),
                    SkillItem(name="Jest", category="Testing", confidence=0.90),
                    SkillItem(name="Figma", category="Design", confidence=0.88),
                    SkillItem(name="Git", category="DevOps", confidence=0.85),
                ],
                experience=[],
                education=[],
            )
            await resume_repo.save_profile(maya_profile)

        # 2. General Candidate Demo
        candidate_user = await user_repo.get_by_email("candidate@example.com")
        if not candidate_user:
            candidate_user = await user_repo.create_user(
                email="candidate@example.com",
                hashed_password=auth_service.hash_password("Password123!"),
                full_name="Maya Chen",
                role="candidate",
            )
            profile = CandidateProfile(
                id=f"profile_{candidate_user.id}",
                user_id=candidate_user.id,
                raw_hash="hash_demo_candidate",
                full_name="Maya Chen",
                email="candidate@example.com",
                summary="Frontend engineer with 4 years building accessible-minded, performant web products with React and TypeScript.",
                skills=[
                    SkillItem(name="React", category="Framework", confidence=0.98, is_verified=True),
                    SkillItem(name="TypeScript", category="Language", confidence=0.95, is_verified=True),
                    SkillItem(name="CSS", category="Styling", confidence=0.90),
                    SkillItem(name="Jest", category="Testing", confidence=0.88),
                    SkillItem(name="Python", category="Language", confidence=0.85),
                ],
                experience=[],
                education=[],
            )
            await resume_repo.save_profile(profile)

        # 3. Recruiter Demo
        recruiter_user = await user_repo.get_by_email("recruiter@example.com")
        if not recruiter_user:
            await user_repo.create_user(
                email="recruiter@example.com",
                hashed_password=auth_service.hash_password("Password123!"),
                full_name="Alex Recruiter",
                role="recruiter",
            )
    except Exception as exc:
        logger.warning(f"Demo data seeding encountered error: {exc}")

    yield
    logger.info(f"Shutting down {settings.APP_NAME}")
    if settings.REPOSITORY_BACKEND == "sql":
        from app.db.connection import get_db_manager
        await get_db_manager().close()
        logger.info("Database connection pool closed successfully.")


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

    # 5. Frontend & Dev UI Static Mounts
    frontend_dir = Path("frontend")
    dev_ui_dir = Path("dev_ui")

    if frontend_dir.exists():
        app.mount("/frontend-assets", StaticFiles(directory="frontend"), name="frontend_assets")

        @app.get("/", include_in_schema=False)
        @app.get("/app", include_in_schema=False)
        async def serve_frontend() -> FileResponse:
            index_path = frontend_dir / "index.html"
            return FileResponse(index_path)

    if dev_ui_dir.exists():
        app.mount("/dev-ui-assets", StaticFiles(directory="dev_ui"), name="dev_ui_assets")

        @app.get("/dev-ui", include_in_schema=False)
        @app.get("/ui", include_in_schema=False)
        async def serve_dev_ui() -> FileResponse:
            index_path = dev_ui_dir / "index.html"
            return FileResponse(index_path)

    return app


app = create_application()
