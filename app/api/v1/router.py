"""Aggregated router for API version 1."""

from fastapi import APIRouter
from app.api.v1.endpoints import auth, health, jobs, llm, matching, resumes

api_v1_router = APIRouter()

# Register health and diagnostics
api_v1_router.include_router(health.router)

# Register authentication and identity (Developer B)
api_v1_router.include_router(auth.router)

# Register resume pipeline and candidate profiles (Developer A)
api_v1_router.include_router(resumes.router, prefix="/resumes", tags=["Resumes"])

# Register job ingestion and search (Developer A)
api_v1_router.include_router(jobs.router, prefix="/jobs", tags=["Jobs"])

# Register LLM structured output and two-model service (Phase 4 Developer A)
api_v1_router.include_router(llm.router, prefix="/llm", tags=["LLM"])

# Register matching and recommendation engine (Phase 5 Developer A)
api_v1_router.include_router(matching.router, prefix="/matching", tags=["Matching"])

