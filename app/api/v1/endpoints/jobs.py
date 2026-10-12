"""Job listing and ingestion endpoints."""

from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status

from app.authentication.dependencies import get_current_user
from app.authorization.dependencies import RequireRole
from app.core.exceptions import NotFoundError
from app.pipelines.job_ingestion_pipeline import JobIngestionPipeline
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.schemas.job import Job, JobIngestRequest, JobIngestResponse

router = APIRouter()


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
    repo: IJobRepository = Depends(get_job_repository),
) -> StandardResponse[JobIngestResponse]:
    """Ingest job listings from approved URLs."""
    pipeline = JobIngestionPipeline(repo=repo)
    result = await pipeline.ingest_jobs(payload)
    return StandardResponse(
        data=result,
        message=f"Ingestion completed: {result.ingested_count} added, {result.skipped_duplicates} duplicate(s) skipped.",
    )


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
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IJobRepository = Depends(get_job_repository),
) -> StandardResponse[List[Job]]:
    """Retrieve filtered list of job postings."""
    skills_list = [s.strip() for s in skills.split(",") if s.strip()] if skills else None
    jobs = await repo.query_jobs(
        q=q,
        skills=skills_list,
        location=location,
        only_active=only_active,
        limit=limit,
        offset=offset,
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
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
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
