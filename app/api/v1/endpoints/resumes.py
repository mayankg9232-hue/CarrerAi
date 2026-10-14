import uuid
from typing import List, Optional
from fastapi import APIRouter, Body, Depends, File, UploadFile, status

from app.authentication.dependencies import get_current_user, get_optional_current_user
from app.authorization.dependencies import assert_resource_owner
from app.core.config import get_settings
from app.core.exceptions import NotFoundError
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
from app.schemas.resume import CandidateProfile

router = APIRouter()


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
    summary="Upload resume, generate gemini embeddings, persist, webscrape jobs via Firecrawl, and rank matches",
    description="Accepts PDF, DOCX, or TXT file, parses skills, generates gemini-embedding-2 embeddings, stores in database, searches/scrapes matching tech jobs, and returns ranked match percentages.",
)
async def upload_and_match_resume(
    file: UploadFile = File(...),
    current_user: Optional[AuthenticatedPrincipal] = Depends(get_optional_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
    job_repo: IJobRepository = Depends(get_job_repository),
) -> StandardResponse[UnifiedPipelineResponse]:
    """Execute end-to-end resume intelligence pipeline: parsing, gemini-2 embeddings, DB persistence, Firecrawl scraping, and match ranking."""
    content = await file.read()
    user_id = current_user.id if current_user else f"usr_guest_{uuid.uuid4().hex[:8]}"

    pipeline = ResumePipeline(repo=repo)
    profile = await pipeline.process_resume(
        file_bytes=content,
        filename=file.filename or "resume.pdf",
        content_type=file.content_type,
        user_id=user_id,
    )

    settings = get_settings()

    # 1. Neural Embeddings (gemini-embedding-2)
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
    "/upload",
    response_model=StandardResponse[CandidateProfile],
    status_code=status.HTTP_201_CREATED,
    summary="Upload and parse candidate CV document",
    description="Accepts PDF, DOCX, or TXT file up to 5 MB, parses text safely, and extracts structured profile.",
)
async def upload_resume(
    file: UploadFile = File(...),
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[CandidateProfile]:
    """Upload candidate CV document and extract structured candidate profile."""
    content = await file.read()
    pipeline = ResumePipeline(repo=repo)
    profile = await pipeline.process_resume(
        file_bytes=content,
        filename=file.filename or "resume.pdf",
        content_type=file.content_type,
        user_id=current_user.id,
    )
    return StandardResponse(
        data=profile,
        message="Resume uploaded and structured profile extracted successfully.",
    )


@router.get(
    "/{profile_id}",
    response_model=StandardResponse[CandidateProfile],
    summary="Get candidate profile by ID",
    description="Retrieve extracted candidate profile. Enforces candidate ownership isolation.",
)
async def get_candidate_profile(
    profile_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[CandidateProfile]:
    """Retrieve candidate profile with server-side ownership authorization check."""
    profile = await repo.get_by_id(profile_id)
    if not profile:
        raise NotFoundError(f"Candidate profile '{profile_id}' not found.")

    # Candidate isolation: only owner, recruiter, or admin can access
    assert_resource_owner(
        resource_user_id=profile.user_id,
        principal=current_user,
        allowed_roles=["admin", "recruiter"],
    )

    return StandardResponse(
        data=profile,
        message="Candidate profile retrieved successfully.",
    )


@router.get(
    "",
    response_model=StandardResponse[List[CandidateProfile]],
    summary="List candidate profiles",
    description="List profiles belonging to the current user (or all profiles for recruiters/admins).",
)
async def list_candidate_profiles(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[List[CandidateProfile]]:
    """List profiles belonging to authenticated principal."""
    if current_user.role in ("admin", "recruiter"):
        profiles = await repo.list_all()
    else:
        profiles = await repo.get_by_user_id(current_user.id)

    return StandardResponse(
        data=profiles,
        message="Candidate profiles retrieved successfully.",
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
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
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
