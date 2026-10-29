"""Job listing and ingestion endpoints."""

import json
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, status

from app.authentication.dependencies import get_current_user, get_optional_current_user, require_verified_user
from app.authorization.dependencies import RequireRole
from app.core.exceptions import NotFoundError
from app.pipelines.job_ingestion_pipeline import JobIngestionPipeline
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.repositories.match_repo import IMatchRepository, get_match_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.schemas.job import Job, JobIngestRequest, JobIngestResponse, SavedJobResponse

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


from app.schemas.job import Job, JobIngestRequest, JobIngestResponse, SavedJobResponse


@router.get(
    "/saved",
    response_model=StandardResponse[SavedJobResponse],
    summary="List saved jobs for current user",
    description="Retrieve paginated list of jobs saved to the authenticated user's shortlist with active resume match scores.",
)
async def list_saved_jobs(
    limit: int = Query(default=50, ge=1, le=100, description="Page limit"),
    offset: int = Query(default=0, ge=0, description="Page offset"),
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IJobRepository = Depends(get_job_repository),
    resume_repo: IResumeRepository = Depends(get_resume_repository),
    match_repo: IMatchRepository = Depends(get_match_repository),
) -> StandardResponse[SavedJobResponse]:
    """Retrieve all jobs saved by the authenticated user with active resume alignment."""
    saved_jobs = await repo.get_saved_jobs(user_id=current_user.id, limit=limit, offset=offset)
    total_count = await repo.count_saved_jobs(user_id=current_user.id)

    active_resume = await resume_repo.get_active_resume(current_user.id)
    active_profile = None
    if active_resume and active_resume.status == "completed" and active_resume.profile_id:
        active_profile = await resume_repo.get_by_id(active_resume.profile_id)

    enriched_jobs: List[Job] = []
    for job in saved_jobs:
        job_data = job.model_dump()
        if active_resume and active_resume.status == "processing":
            job_data["match_status"] = "pending"
            job_data["score"] = None
        elif active_profile:
            match_rec = await match_repo.get_match(candidate_id=active_profile.id, job_id=job.id)
            if match_rec:
                m_dict = dict(match_rec) if not hasattr(match_rec, "overall_score") else {
                    "overall_score": match_rec.overall_score,
                    "matched_skills": match_rec.matched_skills,
                    "missing_skills": match_rec.missing_skills,
                }
                score_val = m_dict.get("overall_score")
                matched_s = m_dict.get("matched_skills", [])
                missing_s = m_dict.get("missing_skills", [])
                if isinstance(matched_s, str):
                    try:
                        matched_s = json.loads(matched_s)
                    except Exception:
                        matched_s = []
                if isinstance(missing_s, str):
                    try:
                        missing_s = json.loads(missing_s)
                    except Exception:
                        missing_s = []
                job_data["score"] = score_val
                job_data["matched_skills"] = matched_s
                job_data["missing_skills"] = missing_s
                job_data["match_status"] = "completed"
            else:
                job_data["score"] = None
                job_data["match_status"] = "unavailable"
        else:
            job_data["score"] = None
            job_data["match_status"] = "no_resume"
        enriched_jobs.append(Job(**job_data))

    return StandardResponse(
        data=SavedJobResponse(count=total_count, jobs=enriched_jobs),
        message=f"Retrieved {len(enriched_jobs)} saved job(s).",
    )


@router.get(
    "/saved/count",
    response_model=StandardResponse[dict],
    summary="Count saved jobs for current user",
    description="Return total count of saved jobs for user dashboard.",
)
async def count_saved_jobs(
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IJobRepository = Depends(get_job_repository),
) -> StandardResponse[dict]:
    """Return count of saved jobs for the authenticated user."""
    count = await repo.count_saved_jobs(user_id=current_user.id)
    return StandardResponse(
        data={"count": count},
        message="Saved jobs count retrieved successfully.",
    )


@router.post(
    "/{job_id}/save",
    response_model=StandardResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="Save a job listing to user shortlist",
    description="Persist a job to the authenticated user's saved shortlist idempotently.",
)
async def save_job(
    job_id: str,
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IJobRepository = Depends(get_job_repository),
) -> StandardResponse[dict]:
    """Save a job listing to user's saved shortlist."""
    job = await repo.get_by_id(job_id)
    if not job:
        raise NotFoundError(f"Job listing '{job_id}' not found.")

    saved = await repo.save_job(user_id=current_user.id, job_id=job_id)
    return StandardResponse(
        data={"job_id": job_id, "saved": saved},
        message="Job saved to your shortlist.",
    )


@router.delete(
    "/{job_id}/save",
    response_model=StandardResponse[dict],
    summary="Unsave a job listing from user shortlist",
    description="Remove a job from user's saved shortlist without deleting the shared job listing.",
)
async def unsave_job(
    job_id: str,
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IJobRepository = Depends(get_job_repository),
) -> StandardResponse[dict]:
    """Remove a job from the user's saved shortlist."""
    unsaved = await repo.unsave_job(user_id=current_user.id, job_id=job_id)
    return StandardResponse(
        data={"job_id": job_id, "saved": False},
        message="Job removed from your shortlist.",
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

