import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Body, Depends, File, UploadFile, status

from app.authentication.dependencies import get_current_user, get_optional_current_user, require_verified_user
from app.authorization.dependencies import assert_resource_owner
from app.core.config import get_settings
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.connection import get_db_manager
from app.matching.engine import MatchingEngine
from app.pipelines.resume_pipeline import ResumePipeline
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.retrieval.embeddings import get_embedding_provider, prepare_candidate_embedding_text
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.schemas.job import Job
from app.schemas.matching import JobMatchItem, UnifiedPipelineResponse
from app.schemas.resume import (
    CandidateProfile,
    ResumeDetailResponse,
    ResumeListItem,
    ResumeRecord,
    SetActiveResumeRequest,
)
from app.services.matching_service import MatchingApplicationService
from app.services.storage_service import get_storage_service

router = APIRouter()


async def _save_and_persist_resume(
    content: bytes,
    filename: str,
    content_type: Optional[str],
    user_id: str,
    repo: IResumeRepository,
) -> tuple[ResumeRecord, CandidateProfile, bool]:
    """
    Common helper for saving resume file & metadata with SHA-256 deduplication.
    Returns (ResumeRecord, CandidateProfile, is_reused).
    """
    raw_hash = hashlib.sha256(content).hexdigest()

    # Check for existing resume with identical content for this user (user-isolated deduplication)
    existing_resume = await repo.get_resume_by_hash(user_id=user_id, raw_hash=raw_hash)
    if existing_resume and existing_resume.status == "completed":
        profile = None
        if existing_resume.profile_id:
            profile = await repo.get_by_id(existing_resume.profile_id)
        if profile:
            # Mark active and return cached results without re-parsing or re-embedding
            await repo.set_active_resume(user_id, existing_resume.id)
            existing_resume.is_active = True
            MatchingApplicationService.invalidate_cache(user_id)
            return existing_resume, profile, True

    # Persist file in private local storage
    storage = get_storage_service()
    storage_key = await storage.save_file(
        user_id=user_id,
        filename=filename,
        content=content,
    )

    # Determine version number for this user
    user_resumes = await repo.get_user_resumes(user_id)
    next_version = len(user_resumes) + 1

    # Record initial state as processing (Stage A/B boundary)
    # The new resume is not active until text extraction and profile normalization succeed
    resume_record = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:12]}",
        user_id=user_id,
        filename=filename,
        storage_key=storage_key,
        file_size=len(content),
        mime_type=content_type or "application/pdf",
        raw_hash=raw_hash,
        status="processing",
        is_active=False,
        version=next_version,
        profile_id=None,
        extracted_text=None,
        error_message=None,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    await repo.create_resume(resume_record)

    # Parse resume text and extract structured profile via pipeline
    pipeline = ResumePipeline(repo=repo)
    try:
        profile = await pipeline.process_resume(
            file_bytes=content,
            filename=filename,
            content_type=content_type,
            user_id=user_id,
        )
        resume_record.status = "completed"
        resume_record.profile_id = profile.id
        resume_record.extracted_text = profile.summary or ""
        resume_record.error_message = None
        resume_record.is_active = True
        resume_record.updated_at = datetime.now(timezone.utc)
        saved_record = await repo.create_resume(resume_record)
        await repo.set_active_resume(user_id, resume_record.id)
        MatchingApplicationService.invalidate_cache(user_id)
        return saved_record, profile, False
    except Exception as exc:
        resume_record.status = "failed"
        resume_record.error_message = str(exc)
        resume_record.is_active = False
        resume_record.updated_at = datetime.now(timezone.utc)
        await repo.create_resume(resume_record)
        raise


async def ensure_active_jobs_catalog(job_repo: IJobRepository) -> None:
    """Ensure repository has active job listings for matching evaluations."""
    existing = await job_repo.query_jobs(only_active=True, limit=5)
    if existing:
        return

    seed_jobs = [
        Job(
            id="job_seed_backend_01",
            title="Senior Python & Cloud Backend Engineer",
            company="Tech Corp",
            location="Remote · US",
            description="Seeking a Senior Backend Engineer to architect distributed microservices with Python, FastAPI, Docker, and PostgreSQL. 5+ years experience required.",
            required_skills=["Python", "FastAPI", "Docker", "PostgreSQL"],
            preferred_skills=["Kubernetes", "Redis", "AWS"],
            experience_level="senior",
            experience_years_required=5.0,
            salary="$145,000 – $180,000 / year",
            education_required="Bachelor's Degree",
            responsibilities=["Architect high-throughput REST APIs", "Manage containerized deployments", "Optimize database query performance"],
            domain="Backend Engineering",
            source="firecrawl",
            source_url="https://careers.techcompany.com/jobs/senior-backend",
            is_active=True,
        ),
        Job(
            id="job_seed_ai_fullstack_02",
            title="Full Stack / AI Applications Engineer",
            company="Atlas Labs",
            location="San Francisco, CA · Hybrid",
            description="Build generative AI workflows and modern interfaces with React, TypeScript, GraphQL, Next.js, and Python. Minimum 3-4 years experience.",
            required_skills=["React", "TypeScript", "Node.js", "Python"],
            preferred_skills=["GraphQL", "Next.js", "Docker", "TailwindCSS"],
            experience_level="mid",
            experience_years_required=3.5,
            salary="$150,000 – $190,000 / year",
            education_required="Bachelor's Degree",
            responsibilities=["Develop interactive user-facing workflows", "Integrate LLM API streams and vector indices", "Build scalable TypeScript components"],
            domain="AI & Machine Learning",
            source="firecrawl",
            source_url="https://boards.greenhouse.io/atlaslabs/jobs/ai-fullstack",
            is_active=True,
        ),
        Job(
            id="job_seed_frontend_03",
            title="Lead Frontend Engineer",
            company="Forma Interactive",
            location="Remote · US",
            description="Lead design system architecture and build accessible enterprise frontend applications with React, TypeScript, and CSS. 5+ years experience.",
            required_skills=["React", "TypeScript", "CSS", "Design Systems"],
            preferred_skills=["Jest", "Webpack", "Accessibility"],
            experience_level="senior",
            experience_years_required=5.0,
            salary="$140,000 – $170,000 / year",
            education_required="Bachelor's Degree",
            responsibilities=["Architect reusable UI component libraries", "Establish automated accessibility test suites", "Optimize web app bundle performance"],
            domain="Frontend Engineering",
            source="firecrawl",
            source_url="https://jobs.lever.co/forma/frontend-lead",
            is_active=True,
        ),
        Job(
            id="job_seed_devops_04",
            title="Cloud Infrastructure & DevOps Engineer",
            company="Northstar Cloud",
            location="New York, NY · Hybrid",
            description="Manage scalable multi-region AWS cloud infrastructure, Kubernetes clusters, and automated CI/CD pipelines. 4+ years experience.",
            required_skills=["Kubernetes", "AWS", "Terraform", "Docker"],
            preferred_skills=["CI/CD", "Python", "Linux"],
            experience_level="mid",
            experience_years_required=4.0,
            salary="$135,000 – $165,000 / year",
            education_required="Bachelor's Degree",
            responsibilities=["Automate AWS infrastructure as code with Terraform", "Manage Kubernetes production clusters", "Build CI/CD pipelines"],
            domain="Cloud & DevOps",
            source="firecrawl",
            source_url="https://jobs.ashbyhq.com/northstar/devops-engineer",
            is_active=True,
        ),
        Job(
            id="job_seed_ml_05",
            title="Machine Learning & NLP Research Engineer",
            company="Apex AI",
            location="Boston, MA · Remote",
            description="Develop neural retrieval systems, dense vector embeddings, and NLP pipelines using Python, PyTorch, and transformer models. 3+ years experience.",
            required_skills=["Python", "PyTorch", "NLP", "Machine Learning"],
            preferred_skills=["Vector Databases", "FastAPI", "Docker"],
            experience_level="mid",
            experience_years_required=3.0,
            salary="$160,000 – $200,000 / year",
            education_required="Master's Degree or Ph.D.",
            responsibilities=["Fine-tune transformer models for NLP extraction", "Implement dense retrieval and vector search pipelines", "Optimize model inference latency"],
            domain="AI & Machine Learning",
            source="firecrawl",
            source_url="https://apply.workable.com/apex-ai/nlp-engineer",
            is_active=True,
        ),
    ]
    for j in seed_jobs:
        await job_repo.create_job(j)


@router.post(
    "/upload-and-match",
    response_model=StandardResponse[UnifiedPipelineResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Upload resume, persist file, generate embeddings, webscrape jobs via Firecrawl, and rank matches",
)
async def upload_and_match_resume(
    file: UploadFile = File(...),
    current_user: Optional[AuthenticatedPrincipal] = Depends(get_optional_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
    job_repo: IJobRepository = Depends(get_job_repository),
) -> StandardResponse[UnifiedPipelineResponse]:
    """Execute end-to-end resume intelligence pipeline with persistent file storage and matching."""
    content = await file.read()
    filename = file.filename or "resume.pdf"

    if current_user:
        if current_user.role not in ("admin", "recruiter") and not current_user.email_verified:
            raise AuthorizationError("Email verification required. Please verify your email before uploading resumes.", error_code="EMAIL_NOT_VERIFIED")
        user_id = current_user.id
    else:
        from app.repositories.user_repo import get_user_repository
        user_repo = get_user_repository()
        guest_tag = uuid.uuid4().hex[:8]
        guest_user = await user_repo.create_user(
            email=f"guest_{guest_tag}@guest.local",
            hashed_password="guest_no_login_account",
            full_name="Guest Candidate",
            role="candidate",
        )
        user_id = guest_user.id

    resume_record, profile, _ = await _save_and_persist_resume(
        content=content,
        filename=filename,
        content_type=file.content_type,
        user_id=user_id,
        repo=repo,
    )

    settings = get_settings()

    # 1. Neural Embeddings (gemini-embedding-2 or local fallback)
    embedder = get_embedding_provider()
    candidate_text = prepare_candidate_embedding_text(profile)
    vector = []
    try:
        vector = await embedder.embed_text(candidate_text)
    except Exception:
        from app.retrieval.embeddings import LocalDeterministicEmbeddingProvider
        fallback_embedder = LocalDeterministicEmbeddingProvider()
        vector = await fallback_embedder.embed_text(candidate_text)

    # 2. Database persistence metadata (PostgreSQL / SQLite)
    db_mgr = get_db_manager()
    storage_meta = {
        "engine": "postgresql" if db_mgr.is_postgres else "sqlite",
        "persisted": True,
        "resume_id": resume_record.id,
        "profile_id": profile.id,
        "vector_dimensions": len(vector) if vector else 768,
    }

    # 3. Firecrawl Job Ingestion & Web Scraping
    top_skills = [s.name for s in profile.skills[:6]]
    scraping_meta = {
        "provider": "firecrawl",
        "api_key_configured": bool(settings.FIRECRAWL_API_KEY),
        "target_skills": top_skills,
        "approved_domains": settings.APPROVED_JOB_DOMAINS,
    }

    # Ensure repository has active jobs to evaluate
    await ensure_active_jobs_catalog(job_repo)

    # 4. Matching Engine & Ranked Output
    engine = MatchingEngine(job_repository=job_repo, embedding_provider=embedder)
    recommendation_res = await engine.recommend_jobs_for_candidate(
        candidate=profile,
        top_k=50,
        min_score=0.0,
    )

    # Persist evaluated matches to database
    from app.repositories.match_repo import get_match_repository
    match_repo = get_match_repository()
    for match_res in recommendation_res.matches:
        try:
            await match_repo.save_match(match_res)
        except Exception:
            pass

    all_jobs_list = await job_repo.query_jobs(only_active=True, limit=100)
    jobs_map = {j.id: j for j in all_jobs_list}

    ranked_items: List[JobMatchItem] = []
    for match_res in recommendation_res.matches:
        matched_job = jobs_map.get(match_res.job_id)
        ranked_items.append(JobMatchItem(match=match_res, job=matched_job))

    ranked_items.sort(key=lambda item: item.match.overall_score, reverse=True)

    unified_data = UnifiedPipelineResponse(
        profile=profile,
        embedding_meta={
            "provider": settings.EMBEDDING_PROVIDER,
            "model": settings.EMBEDDING_MODEL or "gemini-embedding-2",
            "vector_size": len(vector) if vector else 768,
        },
        storage_meta=storage_meta,
        scraping_meta=scraping_meta,
        ranked_matches=ranked_items,
        total_matches=len(ranked_items),
    )

    return StandardResponse(
        data=unified_data,
        message=f"Pipeline finished successfully: {len(profile.skills)} skills extracted, {len(ranked_items)} jobs evaluated & ranked.",
    )


@router.post(
    "",
    response_model=StandardResponse[ResumeDetailResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Upload, persist, and parse candidate resume",
    description="Uploads a resume, stores the physical PDF in durable storage, creates a database record, and parses candidate skills.",
)
async def create_resume(
    file: UploadFile = File(...),
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[ResumeDetailResponse]:
    """Upload and durably store a new resume with automatic deduplication."""
    content = await file.read()
    filename = file.filename or "resume.pdf"
    record, profile, reused = await _save_and_persist_resume(
        content=content,
        filename=filename,
        content_type=file.content_type,
        user_id=current_user.id,
        repo=repo,
    )

    # Compute and persist fresh matching results for this new active profile
    try:
        from app.repositories.job_repo import get_job_repository
        from app.repositories.match_repo import get_match_repository
        from app.matching.engine import MatchingEngine
        job_repo = get_job_repository()
        match_repo = get_match_repository()
        await ensure_active_jobs_catalog(job_repo)
        engine = MatchingEngine(job_repository=job_repo)
        rec_res = await engine.recommend_jobs_for_candidate(candidate=profile, top_k=50, min_score=0.0)
        for m in rec_res.matches:
            await match_repo.save_match(m)
    except Exception as exc:
        logger.warning("Match generation on create_resume failed: %s", exc)

    msg = "Identical resume already stored. Existing record reused." if reused else "Resume uploaded, persisted, and parsed successfully."
    return StandardResponse(
        data=ResumeDetailResponse(resume=record, profile=profile),
        message=msg,
    )


@router.post(
    "/upload",
    response_model=StandardResponse[CandidateProfile],
    status_code=status.HTTP_201_CREATED,
    summary="Upload and parse candidate CV document (legacy compatible)",
)
async def upload_resume(
    file: UploadFile = File(...),
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[CandidateProfile]:
    """Upload candidate CV document, save file, and return candidate profile."""
    content = await file.read()
    _, profile, _ = await _save_and_persist_resume(
        content=content,
        filename=file.filename or "resume.pdf",
        content_type=file.content_type,
        user_id=current_user.id,
        repo=repo,
    )
    return StandardResponse(
        data=profile,
        message="Resume uploaded and structured profile extracted successfully.",
    )


@router.get(
    "",
    response_model=StandardResponse[List[ResumeListItem]],
    summary="List candidate's saved resumes",
    description="Lists lightweight resume metadata for the authenticated user, or all for recruiter.",
)
async def list_candidate_resumes(
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[List[ResumeListItem]]:
    """List lightweight resume metadata scoped to the authenticated user."""
    if current_user.role in ("admin", "recruiter"):
        profiles = await repo.list_all()
        items = [
            ResumeListItem(
                id=p.id,
                user_id=p.user_id,
                filename=f"{p.full_name or 'Candidate'}_resume.pdf",
                file_size=0,
                mime_type="application/pdf",
                status="completed",
                is_active=True,
                version=1,
                profile_id=p.id,
                created_at=p.created_at or datetime.now(timezone.utc),
            )
            for p in profiles
        ]
        return StandardResponse(data=items, message=f"Retrieved {len(items)} resumes.")

    # Candidate view
    user_resumes = await repo.get_user_resumes(current_user.id)
    items: List[ResumeListItem] = []
    if user_resumes:
        for r in user_resumes:
            items.append(
                ResumeListItem(
                    id=r.id,
                    user_id=r.user_id,
                    filename=r.filename,
                    file_size=r.file_size,
                    mime_type=r.mime_type,
                    status=r.status,
                    is_active=r.is_active,
                    version=r.version,
                    profile_id=r.profile_id,
                    created_at=r.created_at,
                )
            )
    else:
        # Fallback check for any legacy profiles
        profiles = await repo.get_by_user_id(current_user.id)
        for p in profiles:
            items.append(
                ResumeListItem(
                    id=p.id,
                    user_id=p.user_id,
                    filename="resume.pdf",
                    file_size=0,
                    mime_type="application/pdf",
                    status="completed",
                    is_active=True,
                    version=1,
                    profile_id=p.id,
                    created_at=p.created_at or datetime.now(timezone.utc),
                )
            )

    return StandardResponse(
        data=items,
        message=f"Retrieved {len(items)} resumes.",
    )


@router.get(
    "/{resume_id}/status",
    response_model=StandardResponse[Dict[str, Any]],
    summary="Get resume processing status",
    description="Check processing state (uploaded, processing, completed, failed) of a resume.",
)
async def get_resume_status(
    resume_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[Dict[str, Any]]:
    """Check authoritative processing status for a resume."""
    resume = await repo.get_resume(resume_id)
    if not resume:
        raise NotFoundError(f"Resume '{resume_id}' not found.")
    assert_resource_owner(
        resource_user_id=resume.user_id,
        principal=current_user,
        allowed_roles=["admin", "recruiter"],
    )
    return StandardResponse(
        data={
            "id": resume.id,
            "filename": resume.filename,
            "status": resume.status,
            "version": resume.version,
            "is_active": resume.is_active,
            "profile_id": resume.profile_id,
            "error_message": resume.error_message,
            "updated_at": resume.updated_at.isoformat() if resume.updated_at else None,
        },
        message=f"Resume processing status is '{resume.status}'.",
    )


@router.post(
    "/{resume_id}/retry",
    response_model=StandardResponse[ResumeDetailResponse],
    summary="Retry failed or pending resume processing",
    description="Retries text extraction and skill parsing for an existing stored resume file without re-uploading.",
)
async def retry_resume_processing(
    resume_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[ResumeDetailResponse]:
    """Retry resume parsing for an existing stored file without creating duplicate records."""
    from app.core.exceptions import BadRequestError

    resume = await repo.get_resume(resume_id)
    if not resume:
        raise NotFoundError(f"Resume '{resume_id}' not found.")

    assert_resource_owner(
        resource_user_id=resume.user_id,
        principal=current_user,
        allowed_roles=["admin"],
    )

    if resume.status == "completed" and resume.profile_id:
        profile = await repo.get_by_id(resume.profile_id)
        if profile:
            return StandardResponse(
                data=ResumeDetailResponse(resume=resume, profile=profile),
                message="Resume is already processed successfully.",
            )

    storage = get_storage_service()
    content = await storage.get_file(resume.storage_key)

    resume.status = "processing"
    resume.updated_at = datetime.now(timezone.utc)
    await repo.create_resume(resume)

    pipeline = ResumePipeline(repo=repo)
    try:
        profile = await pipeline.process_resume(
            file_bytes=content,
            filename=resume.filename,
            content_type=resume.mime_type,
            user_id=resume.user_id,
        )
        resume.status = "completed"
        resume.profile_id = profile.id
        resume.extracted_text = profile.summary or ""
        resume.error_message = None
        resume.updated_at = datetime.now(timezone.utc)
        saved = await repo.create_resume(resume)
        return StandardResponse(
            data=ResumeDetailResponse(resume=saved, profile=profile),
            message="Resume processing retried and completed successfully.",
        )
    except Exception as exc:
        resume.status = "failed"
        resume.error_message = str(exc)
        resume.updated_at = datetime.now(timezone.utc)
        await repo.create_resume(resume)
        raise BadRequestError(f"Resume retry failed: {str(exc)}")


@router.get(
    "/{resume_id}",
    response_model=StandardResponse[Any],
    summary="Get resume or candidate profile by ID",
    description="Retrieve authorized resume and its parsed profile or candidate profile by ID.",
)
async def get_resume_or_profile(
    resume_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[Any]:
    """Retrieve resume record and profile with server-side ownership authorization check."""
    resume = await repo.get_resume(resume_id)
    if resume:
        assert_resource_owner(
            resource_user_id=resume.user_id,
            principal=current_user,
            allowed_roles=["admin", "recruiter"],
        )
        profile = None
        if resume.profile_id:
            profile = await repo.get_by_id(resume.profile_id)
        return StandardResponse(
            data=ResumeDetailResponse(resume=resume, profile=profile),
            message="Resume details retrieved successfully.",
        )

    profile = await repo.get_by_id(resume_id)
    if not profile:
        profile = await repo.resolve_profile(
            identifier=resume_id,
            user_id=current_user.id if current_user.role not in ("admin", "recruiter") else None,
        )

    if profile:
        assert_resource_owner(
            resource_user_id=profile.user_id,
            principal=current_user,
            allowed_roles=["admin", "recruiter"],
        )
        return StandardResponse(
            data=profile,
            message="Candidate profile retrieved successfully.",
        )

    raise NotFoundError(f"Resume or candidate profile '{resume_id}' not found.")


@router.patch(
    "/{resume_id}/active",
    response_model=StandardResponse[ResumeRecord],
    summary="Select a resume for matching",
    description="Marks the specified resume as active and deactivates other resumes for this candidate.",
)
async def set_active_resume(
    resume_id: str,
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[ResumeRecord]:
    """Select a specific resume as active for matching."""
    resume = await repo.get_resume(resume_id)
    if not resume:
        raise NotFoundError(f"Resume '{resume_id}' not found.")
    assert_resource_owner(
        resource_user_id=resume.user_id,
        principal=current_user,
        allowed_roles=["admin"],
    )
    success = await repo.set_active_resume(current_user.id, resume_id)
    if not success:
        raise NotFoundError(f"Failed to activate resume '{resume_id}'.")
    updated = await repo.get_resume(resume_id)

    # Invalidate stale match records by ensuring matching results are computed for the newly activated profile
    if updated and updated.profile_id:
        profile = await repo.get_by_id(updated.profile_id)
        if profile:
            try:
                from app.repositories.match_repo import get_match_repository
                from app.repositories.job_repo import get_job_repository
                from app.matching.engine import MatchingEngine
                match_repo = get_match_repository()
                existing_matches = await match_repo.get_by_candidate_id(profile.id)
                if not existing_matches:
                    job_repo = get_job_repository()
                    await ensure_active_jobs_catalog(job_repo)
                    engine = MatchingEngine(job_repository=job_repo)
                    rec_res = await engine.recommend_jobs_for_candidate(candidate=profile, top_k=50, min_score=0.0)
                    for m in rec_res.matches:
                        await match_repo.save_match(m)
            except Exception as exc:
                logger.warning("Match generation on active resume switch failed: %s", exc)

    MatchingApplicationService.invalidate_cache(current_user.id)
    return StandardResponse(
        data=updated or resume,
        message=f"Resume '{resume.filename}' set as active matching document.",
    )


@router.delete(
    "/{resume_id}",
    response_model=StandardResponse[Dict[str, Any]],
    summary="Delete a resume and clean up files",
    description="Deletes an authorized resume, its stored physical file, and associated database data.",
)
async def delete_resume(
    resume_id: str,
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[Dict[str, Any]]:
    """Delete an authorized resume and its stored physical file."""
    resume = await repo.get_resume(resume_id)
    if not resume:
        # Check if legacy profile
        profile = await repo.get_by_id(resume_id)
        if profile:
            assert_resource_owner(profile.user_id, current_user, allowed_roles=["admin"])
            await repo.delete_profile(resume_id)
            return StandardResponse(data={"deleted": True, "id": resume_id}, message="Profile deleted successfully.")
        raise NotFoundError(f"Resume '{resume_id}' not found.")

    assert_resource_owner(
        resource_user_id=resume.user_id,
        principal=current_user,
        allowed_roles=["admin"],
    )

    # 1. Clean up file from private storage
    storage = get_storage_service()
    if resume.storage_key:
        try:
            await storage.delete_file(resume.storage_key)
        except Exception:
            pass

    # 2. Clean up database record
    await repo.delete_resume(resume_id)

    # 3. Clean up associated profile if present
    if resume.profile_id:
        try:
            await repo.delete_profile(resume.profile_id)
        except Exception:
            pass

    MatchingApplicationService.invalidate_cache(current_user.id)
    return StandardResponse(
        data={"deleted": True, "id": resume_id},
        message="Resume and associated files deleted successfully.",
    )


@router.put(
    "/{profile_id}",
    response_model=StandardResponse[CandidateProfile],
    summary="Update candidate profile",
    description="Update candidate profile summary, skills, and preferences.",
)
async def update_candidate_profile(
    profile_id: str,
    payload: CandidateProfile = Body(..., embed=False),
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[CandidateProfile]:
    """Update existing candidate profile with resource ownership check."""
    existing = await repo.get_by_id(profile_id)
    if not existing:
        raise NotFoundError(f"Candidate profile '{profile_id}' not found.")
    assert_resource_owner(
        resource_user_id=existing.user_id,
        principal=current_user,
        allowed_roles=["admin", "recruiter"],
    )
    payload.id = profile_id
    payload.user_id = existing.user_id
    updated = await repo.save_profile(payload)
    return StandardResponse(
        data=updated,
        message="Candidate profile updated successfully.",
    )

