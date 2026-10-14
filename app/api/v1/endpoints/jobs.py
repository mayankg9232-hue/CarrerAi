"""Job listing and ingestion endpoints."""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status

from app.authentication.dependencies import get_current_user, get_optional_current_user
from app.authorization.dependencies import RequireRole
from app.core.exceptions import NotFoundError
from app.pipelines.job_ingestion_pipeline import JobIngestionPipeline
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.schemas.job import Job, JobIngestRequest, JobIngestResponse

router = APIRouter()


def get_job_pipeline(
    repo: IJobRepository = Depends(get_job_repository),
) -> JobIngestionPipeline:
    """Dependency provider for JobIngestionPipeline."""
    return JobIngestionPipeline(repo=repo)


@router.post(
    "/ingest",
    response_model=StandardResponse[JobIngestResponse],
    status_code=status.HTTP_200_OK,
    summary="Ingest job postings from approved sources via Firecrawl",
    description="Crawls job listings from allowlisted domains, deduplicates by content hash, and records provenance.",
)
async def ingest_jobs(
    payload: JobIngestRequest,
    current_user: AuthenticatedPrincipal = Depends(RequireRole("admin", "recruiter")),
    pipeline: JobIngestionPipeline = Depends(get_job_pipeline),
) -> StandardResponse[JobIngestResponse]:
    """Ingest job listings from approved URLs."""
    result = await pipeline.ingest_jobs(payload)
    return StandardResponse(
        data=result,
        message=f"Ingestion completed: {result.ingested_count} added, {result.skipped_duplicates} duplicate(s) skipped.",
    )



from app.cache.service import CacheService, get_cache_service


def get_jobs_cache_service() -> CacheService:
    """Dependency provider for CacheService."""
    return get_cache_service()


@router.get(
    "",
    response_model=StandardResponse[List[Job]],
    summary="Query and filter job listings",
    description="Search active job listings by keyword, skill taxonomy, and location with pagination.",
)
async def list_jobs(
    q: Optional[str] = Query(default=None, description="Keyword search in title, company, description"),
    skills: Optional[str] = Query(default=None, description="Comma-separated skill filter"),
    location: Optional[str] = Query(default=None, description="Location search filter"),
    only_active: bool = Query(default=True, description="Filter for active jobs only"),
    limit: int = Query(default=20, ge=1, le=100, description="Page size limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    current_user: Optional[AuthenticatedPrincipal] = Depends(get_optional_current_user),
    repo: IJobRepository = Depends(get_job_repository),
    cache: CacheService = Depends(get_jobs_cache_service),
) -> StandardResponse[List[Job]]:
    """Retrieve filtered list of job postings with Redis cache-aside."""
    cached = await cache.get_cached_jobs_query(
        q=q, skills=skills, location=location, only_active=only_active, limit=limit, offset=offset
    )

    if cached is not None:
        return StandardResponse(
            data=cached,
            message=f"Retrieved {len(cached)} job listing(s).",
        )

    skills_list = [s.strip() for s in skills.split(",") if s.strip()] if skills else None
    jobs = await repo.query_jobs(
        q=q,
        skills=skills_list,
        location=location,
        only_active=only_active,
        limit=limit,
        offset=offset,
    )
    await cache.set_cached_jobs_query(
        jobs, q=q, skills=skills, location=location, only_active=only_active, limit=limit, offset=offset
    )
    return StandardResponse(
        data=jobs,
        message=f"Retrieved {len(jobs)} job listing(s).",
    )


@router.get(
    "/{job_id}",
    response_model=StandardResponse[Job],
    summary="Get job listing by ID",
    description="Retrieve full job posting details by unique job identifier.",
)
async def get_job(
    job_id: str,
    current_user: Optional[AuthenticatedPrincipal] = Depends(get_optional_current_user),
    repo: IJobRepository = Depends(get_job_repository),
) -> StandardResponse[Job]:
    """Retrieve specific job listing by ID."""
    job = await repo.get_by_id(job_id)
    if not job:
        raise NotFoundError(f"Job listing '{job_id}' not found.")
    return StandardResponse(
        data=job,
        message="Job listing retrieved successfully.",
    )
