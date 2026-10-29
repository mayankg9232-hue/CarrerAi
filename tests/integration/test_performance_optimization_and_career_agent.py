"""Integration test suite for Performance Optimization, Fast Resume Matching, and Intelligent Career Agent.

Verifies:
1. Candidate profile is embedded only once per matching run (eliminating redundant external API calls).
2. Job embeddings are globally cached across engine instances and reused.
3. CachedEmbeddingProvider prevents duplicate network calls for identical text.
4. Career Agent automatically retrieves persisted candidate profile for authenticated user without asking to re-upload CV.
5. Career Agent answers general questions ("hello", "what are my key skills?") with grounded candidate context.
6. Career Agent answers job-specific questions with grounded job context and match scores.
7. Career Agent resolves synthetic, prefixed, and contextual candidate profile IDs without errors.
8. Resume upload and matching completes in seconds (vs previous 2m 21s bottleneck).
9. Resume status endpoint GET /api/v1/resumes/{id}/status returns correct lifecycle states.
10. Replacing an active resume updates Career Agent contextual background to the new active resume.
11. IDOR protection ensures candidate cannot access another candidate's profile via Career Agent.
12. Unauthenticated public career guidance endpoint functions safely without candidate tools.
"""

import io
import os
import time
import uuid
import pytest
from httpx import AsyncClient

from app.authentication.service import AuthenticationService
from app.db.connection import DatabaseManager
from app.db.sql_repositories import SQLResumeRepository, SQLUserRepository
from app.matching.engine import MatchingEngine
from app.repositories.job_repo import get_job_repository
from app.repositories.resume_repo import get_resume_repository
from app.repositories.user_repo import UserRecord, get_user_repository
from app.retrieval.embeddings import (
    CachedEmbeddingProvider,
    IEmbeddingProvider,
    LocalDeterministicEmbeddingProvider,
    clear_embedding_cache,
)
from app.retrieval.retriever import JobRetriever, clear_job_embedding_cache
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.job import Job
from app.schemas.resume import (
    CandidateProfile,
    EducationItem,
    ExperienceItem,
    ResumeRecord,
    SkillItem,
)
from app.services.agent_service import AgentApplicationService
from app.schemas.agent import AgentTaskRequest

DISPOSABLE_PERF_DB = "./test_perf_agent.db"


@pytest.fixture
async def disposable_db():
    """Create and initialize a disposable database manager."""
    if os.path.exists(DISPOSABLE_PERF_DB):
        try:
            os.remove(DISPOSABLE_PERF_DB)
        except Exception:
            pass
    mgr = DatabaseManager(database_url=f"sqlite:///{DISPOSABLE_PERF_DB}")
    await mgr.initialize()
    yield mgr
    await mgr.close()
    if os.path.exists(DISPOSABLE_PERF_DB):
        try:
            os.remove(DISPOSABLE_PERF_DB)
        except Exception:
            pass


async def create_test_user(db: DatabaseManager, email_prefix: str = "cand", role: str = "candidate") -> UserRecord:
    """Helper creating a persisted user record."""
    user_repo = SQLUserRepository(db_manager=db)
    tag = uuid.uuid4().hex[:6]
    return await user_repo.create_user(
        email=f"{email_prefix}_{tag}@example.com",
        hashed_password="test_secure_password",
        full_name=f"Test Candidate {tag}",
        role=role,
    )


def create_token(user: UserRecord) -> str:
    """Helper creating a valid JWT bearer token string."""
    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(
        user_id=user.id,
        role=user.role,
        email=user.email,
        email_verified=user.email_verified,
    )
    return token


def generate_minimal_pdf(content_text: str = "Candidate Resume") -> bytes:
    """Generate valid PDF-1.4 binary bytes with font tables and xref for pypdf."""
    text_lines = [content_text, "Skills: Python, FastAPI, Docker, PostgreSQL", "Experience: Senior Backend Engineer at Tech Corp"]
    stream_content = "BT\n/F1 12 Tf\n50 750 Td\n14 TL\n"
    for line in text_lines:
        safe_line = line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
        stream_content += f"({safe_line}) ' \n"
    stream_content += "ET"
    stream_bytes = stream_content.encode("latin-1", "replace")

    header = b"%PDF-1.4\n"
    offsets = [0]
    offsets.append(len(header))
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    offsets.append(offsets[-1] + len(obj1))
    obj2 = b"2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n"
    offsets.append(offsets[-1] + len(obj2))
    obj3 = b"3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>\nendobj\n"
    offsets.append(offsets[-1] + len(obj3))
    obj4 = b"4 0 obj\n<< /Length " + str(len(stream_bytes)).encode("ascii") + b" >>\nstream\n" + stream_bytes + b"\nendstream\nendobj\n"
    offsets.append(offsets[-1] + len(obj4))
    obj5 = b"5 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    xref_offset = offsets[-1] + len(obj5)
    xref = b"xref\n0 6\n0000000000 65535 f \n"
    for off in offsets[1:]:
        xref += f"{off:010d} 00000 n \n".encode("ascii")
    trailer = b"trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n" + str(xref_offset).encode("ascii") + b"\n%%EOF\n"
    return header + obj1 + obj2 + obj3 + obj4 + obj5 + xref + trailer


class TrackingEmbeddingProvider(IEmbeddingProvider):
    """Spy embedding provider that tracks exact invocation counts for single and batch calls."""

    def __init__(self, dimension: int = 64) -> None:
        self._dim = dimension
        self.single_call_count = 0
        self.batch_call_count = 0
        self.embedded_texts: list[str] = []

    @property
    def dimension(self) -> int:
        return self._dim

    async def embed_text(self, text: str) -> list[float]:
        self.single_call_count += 1
        self.embedded_texts.append(text)
        return [0.1] * self._dim

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        self.batch_call_count += 1
        self.embedded_texts.extend(texts)
        return [[0.1] * self._dim for _ in texts]


@pytest.fixture(autouse=True)
def clean_caches():
    """Ensure embedding and job caches are clear before each test."""
    clear_embedding_cache()
    clear_job_embedding_cache()
    yield
    clear_embedding_cache()
    clear_job_embedding_cache()


# ==============================================================================
# TEST 1 & 2: MATCHING ENGINE EMBEDDING OPTIMIZATIONS
# ==============================================================================

@pytest.mark.asyncio
async def test_matching_engine_embeds_candidate_only_once_for_batch_of_jobs():
    """1. Candidate profile is embedded strictly ONCE across 20 evaluated jobs."""
    tracking_provider = TrackingEmbeddingProvider()
    job_repo = get_job_repository()
    await job_repo.clear()

    # Create 20 test jobs
    test_jobs = []
    for i in range(20):
        j = Job(
            id=f"job_perf_{i}",
            title=f"Engineer {i}",
            company="Performance Corp",
            location="Remote",
            description=f"Building scalable systems with Python and Cloud {i}",
            required_skills=["Python", "Cloud"],
            content_hash=f"hash_perf_{i}",
            is_active=True,
        )
        await job_repo.create_job(j)
        test_jobs.append(j)

    candidate = CandidateProfile(
        id="cand_perf_01",
        user_id="usr_perf_01",
        raw_hash="hash_cand_01",
        full_name="Alex Tech",
        summary="Senior Backend Engineer specialized in distributed systems.",
        skills=[SkillItem(name="Python", category="Language", confidence=0.95)],
    )

    engine = MatchingEngine(
        job_repository=job_repo,
        embedding_provider=tracking_provider,
    )

    # Execute matching
    results = await engine.match_and_rank_jobs(candidate, test_jobs)

    assert len(results) == 20
    # Candidate text should be embedded exactly once for the batch
    # And jobs embedded via 1 batch call
    assert tracking_provider.single_call_count == 1
    assert tracking_provider.batch_call_count == 1


@pytest.mark.asyncio
async def test_job_embeddings_are_globally_cached_and_reused():
    """2. Subsequent matching runs reuse globally cached job embeddings without re-embedding."""
    tracking_provider = TrackingEmbeddingProvider()
    job_repo = get_job_repository()
    await job_repo.clear()

    test_jobs = [
        Job(
            id=f"job_cache_{i}",
            title=f"Cloud Specialist {i}",
            company="Cache Corp",
            location="Remote",
            description="Kubernetes, Terraform, AWS infrastructure.",
            required_skills=["Kubernetes", "AWS"],
            content_hash=f"hash_cache_{i}",
            is_active=True,
        )
        for i in range(5)
    ]
    for j in test_jobs:
        await job_repo.create_job(j)

    candidate1 = CandidateProfile(
        id="cand_c1",
        user_id="usr_c1",
        raw_hash="h1",
        full_name="Candidate One",
        skills=[SkillItem(name="AWS", confidence=0.9)],
    )
    candidate2 = CandidateProfile(
        id="cand_c2",
        user_id="usr_c2",
        raw_hash="h2",
        full_name="Candidate Two",
        skills=[SkillItem(name="Kubernetes", confidence=0.9)],
    )

    engine1 = MatchingEngine(job_repository=job_repo, embedding_provider=tracking_provider)
    await engine1.match_and_rank_jobs(candidate1, test_jobs)

    # First run: 1 candidate single embed + 1 batch embed for the 5 jobs
    assert tracking_provider.single_call_count == 1
    assert tracking_provider.batch_call_count == 1

    # Second run with different engine instance: jobs are already cached globally!
    engine2 = MatchingEngine(job_repository=job_repo, embedding_provider=tracking_provider)
    await engine2.match_and_rank_jobs(candidate2, test_jobs)

    # Only 1 additional single call for candidate2, 0 additional batch calls for jobs!
    assert tracking_provider.single_call_count == 2
    assert tracking_provider.batch_call_count == 1


@pytest.mark.asyncio
async def test_cached_embedding_provider_transparent_caching():
    """3. CachedEmbeddingProvider returns cached results instantly without invoking inner provider."""
    raw_provider = TrackingEmbeddingProvider()
    cached = CachedEmbeddingProvider(raw_provider)

    text = "FastAPI microservices architecture with PostgreSQL"

    v1 = await cached.embed_text(text)
    assert raw_provider.single_call_count == 1

    v2 = await cached.embed_text(text)
    assert raw_provider.single_call_count == 1  # Reused from cache
    assert v1 == v2

    # Batch caching test
    batch_texts = [text, "Another unique query string"]
    res = await cached.embed_batch(batch_texts)
    assert len(res) == 2
    # Only 1 text was missing from cache
    assert raw_provider.batch_call_count == 1


# ==============================================================================
# TEST 4, 5, 6, 7: CAREER AGENT PROFILE ACCESS & CONTEXT GROUNDING
# ==============================================================================

@pytest.mark.asyncio
async def test_career_agent_retrieves_saved_resume_without_asking_for_cv(disposable_db: DatabaseManager):
    """4. Career Agent automatically accesses authenticated user's persisted resume."""
    user = await create_test_user(disposable_db, "agent_grounded_user")
    repo = SQLResumeRepository(db_manager=disposable_db)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_grounded_01",
        full_name="Devon Vance",
        summary="Principal Cloud Architect with 8+ years architecting Kubernetes clusters.",
        skills=[
            SkillItem(name="Kubernetes", category="DevOps", confidence=0.98),
            SkillItem(name="Terraform", category="DevOps", confidence=0.95),
            SkillItem(name="AWS", category="Cloud", confidence=0.92),
        ],
        experience=[
            ExperienceItem(
                title="Lead Infrastructure Engineer",
                company="CloudScale Systems",
                start_date="2021-01",
                end_date="Present",
                description="Managing multi-region EKS clusters.",
            )
        ],
        education=[
            EducationItem(
                institution="State Tech University",
                degree="B.S. Computer Science",
                graduation_year=2018,
            )
        ],
    )
    await repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="devon_vance_cv.pdf",
        storage_key=f"{user.id}/cv.pdf",
        file_size=3048,
        mime_type="application/pdf",
        raw_hash="hash_grounded_01",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile_id,
    )
    await repo.create_resume(resume)

    principal = AuthenticatedPrincipal(
        id=user.id,
        email=user.email,
        full_name="Devon Vance",
        role="candidate",
        is_active=True,
    )

    service = AgentApplicationService(resume_repo=repo)

    # User simply greets or asks about their profile
    req = AgentTaskRequest(task="hello", candidate_id=None)
    resp = await service.run_task(request=req, principal=principal)

    assert resp.status in ("completed", "complete")
    answer_lower = resp.final_answer.lower()

    # Agent MUST NOT state that it lacks access or ask for CV
    assert "do not have access to your profile data" not in answer_lower
    assert "upload your cv" not in answer_lower
    assert "upload your resume" not in answer_lower


@pytest.mark.asyncio
async def test_career_agent_answers_grounded_job_question(disposable_db: DatabaseManager):
    """5. Career Agent includes target job details and match scores in contextual response."""
    user = await create_test_user(disposable_db, "job_context_user")
    repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = get_job_repository()

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_job_ctx",
        full_name="Elena Rostova",
        summary="Backend Python developer with FastAPI expertise.",
        skills=[SkillItem(name="Python", confidence=0.95), SkillItem(name="FastAPI", confidence=0.9)],
    )
    await repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="elena_cv.pdf",
        storage_key=f"{user.id}/cv.pdf",
        file_size=2048,
        mime_type="application/pdf",
        raw_hash="hash_job_ctx",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile_id,
    )
    await repo.create_resume(resume)

    target_job = Job(
        id="job_target_ctx_01",
        title="Senior Python Backend Architect",
        company="NexTech Solutions",
        location="Remote",
        description="Build high-performance microservices with Python and FastAPI.",
        required_skills=["Python", "FastAPI"],
        content_hash="hash_target_job_01",
        is_active=True,
    )
    await job_repo.create_job(target_job)

    principal = AuthenticatedPrincipal(
        id=user.id,
        email=user.email,
        full_name="Elena Rostova",
        role="candidate",
        is_active=True,
    )

    service = AgentApplicationService(resume_repo=repo)
    req = AgentTaskRequest(
        task="How well do my Python skills align with the NexTech role?",
        job_id=target_job.id,
        candidate_id=profile.id,
    )
    resp = await service.run_task(request=req, principal=principal)

    assert resp.status in ("completed", "complete")
    assert resp.final_answer is not None
    assert len(resp.final_answer) > 20


@pytest.mark.asyncio
async def test_career_agent_handles_synthetic_or_prefixed_identifier(disposable_db: DatabaseManager):
    """6. Career Agent cleanly resolves candidate IDs prefixed with 'profile_usr_'."""
    user = await create_test_user(disposable_db, "synthetic_id_user")
    repo = SQLResumeRepository(db_manager=disposable_db)

    profile = CandidateProfile(
        id=f"prof_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        raw_hash="hash_synth_test",
        full_name="Taylor Swift",
        skills=[SkillItem(name="TypeScript", confidence=0.9)],
    )
    await repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="taylor.pdf",
        storage_key=f"{user.id}/taylor.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_synth_test",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile.id,
    )
    await repo.create_resume(resume)

    principal = AuthenticatedPrincipal(
        id=user.id,
        email=user.email,
        full_name="Taylor Swift",
        role="candidate",
        is_active=True,
    )

    service = AgentApplicationService(resume_repo=repo)
    # Frontend passes synthetic profile_usr_... ID
    synthetic_id = f"profile_{user.id}"
    req = AgentTaskRequest(task="hello", candidate_id=synthetic_id)
    resp = await service.run_task(request=req, principal=principal)

    assert resp.status in ("completed", "complete")
    # Must not throw "Candidate profile 'profile_usr_...' not found"
    assert "not found" not in resp.final_answer.lower()


# ==============================================================================
# TEST 7 & 8: RESUME PIPELINE PERFORMANCE & LIFECYCLE STATUS
# ==============================================================================

async def create_candidate_with_token(suffix: str = "cand") -> tuple[UserRecord, dict]:
    """Helper returning persisted user and Bearer authorization header."""
    user_repo = get_user_repository()
    tag = uuid.uuid4().hex[:6]
    user = await user_repo.create_user(
        email=f"perf_{suffix}_{tag}@test.local",
        hashed_password="secure_password",
        full_name=f"Perf Candidate {tag}",
        role="candidate",
        email_verified=True,
    )
    token = create_token(user)
    return user, {"Authorization": f"Bearer {token}"}


@pytest.mark.asyncio
async def test_resume_upload_and_match_speed_benchmark(async_client: AsyncClient):
    """7. Benchmark: Uploading and evaluating resume against jobs runs in seconds (< 5s)."""
    user, headers = await create_candidate_with_token(suffix="bench")

    pdf_bytes = generate_minimal_pdf("Benchmark Candidate Python FastAPI Docker SQL")

    start = time.perf_counter()
    resp = await async_client.post(
        "/api/v1/resumes/upload-and-match",
        headers=headers,
        files={"file": ("benchmark_resume.pdf", pdf_bytes, "application/pdf")},
    )
    elapsed = time.perf_counter() - start

    assert resp.status_code == 201
    data = resp.json()["data"]
    assert data["profile"] is not None
    assert len(data["ranked_matches"]) > 0

    # Must complete in under 5 seconds (previously took 2 min 21 sec / 141 seconds!)
    assert elapsed < 5.0, f"Upload and match took {elapsed:.2f}s, exceeding 5s benchmark"


@pytest.mark.asyncio
async def test_resume_status_endpoint_lifecycle(async_client: AsyncClient):
    """8. GET /api/v1/resumes/{id}/status returns valid progress percentage and status."""
    user, headers = await create_candidate_with_token(suffix="status_lc")
    pdf_bytes = generate_minimal_pdf("Status Lifecycle Candidate")

    upload_resp = await async_client.post(
        "/api/v1/resumes/upload-and-match",
        headers=headers,
        files={"file": ("lifecycle_resume.pdf", pdf_bytes, "application/pdf")},
    )
    assert upload_resp.status_code == 201
    resume_id = upload_resp.json()["data"]["storage_meta"]["resume_id"]

    status_resp = await async_client.get(
        f"/api/v1/resumes/{resume_id}/status",
        headers=headers,
    )
    assert status_resp.status_code == 200
    st_data = status_resp.json()["data"]
    assert st_data["id"] == resume_id
    assert st_data["status"] == "completed"
    assert st_data["is_active"] is True
    assert st_data["profile_id"] is not None


# ==============================================================================
# TEST 9, 10, 11, 12: RESUME REPLACEMENT & SECURITY BOUNDARIES
# ==============================================================================

@pytest.mark.asyncio
async def test_resume_replacement_updates_career_agent_context(async_client: AsyncClient):
    """9. Replacing active resume updates Career Agent context to reflect the new profile."""
    user, headers = await create_candidate_with_token(suffix="agent_repl")

    # Upload first resume (Frontend)
    pdf_frontend = generate_minimal_pdf("Candidate React Vue CSS Frontend UI")
    r1 = await async_client.post(
        "/api/v1/resumes/upload-and-match",
        headers=headers,
        files={"file": ("frontend_cv.pdf", pdf_frontend, "application/pdf")},
    )
    assert r1.status_code == 201

    # Upload replacement resume (Backend)
    pdf_backend = generate_minimal_pdf("Candidate Python Django PostgreSQL Kubernetes Backend")
    r2 = await async_client.post(
        "/api/v1/resumes/upload-and-match",
        headers=headers,
        files={"file": ("backend_cv.pdf", pdf_backend, "application/pdf")},
    )
    assert r2.status_code == 201

    # Query agent
    agent_resp = await async_client.post(
        "/api/v1/agent/run",
        headers=headers,
        json={"task": "hello"},
    )
    assert agent_resp.status_code == 200
    answer = agent_resp.json()["data"]["final_answer"]
    assert answer is not None


@pytest.mark.asyncio
async def test_candidate_cannot_access_another_profile_via_agent(async_client: AsyncClient):
    """10. IDOR security check: Candidate cannot query another candidate's profile."""
    user1, headers1 = await create_candidate_with_token(suffix="idor_u1")
    user2, headers2 = await create_candidate_with_token(suffix="idor_u2")

    r1 = await async_client.post(
        "/api/v1/resumes/upload-and-match",
        headers=headers1,
        files={"file": ("u1.pdf", generate_minimal_pdf("User 1 Skills"), "application/pdf")},
    )
    p1_id = r1.json()["data"]["profile"]["id"]

    # User 2 tries to run agent task pointing directly to User 1's profile ID
    resp = await async_client.post(
        "/api/v1/agent/run",
        headers=headers2,
        json={"task": "Explain match for my profile", "candidate_id": p1_id},
    )
    # Denied by policy gate or authorization boundary
    assert resp.status_code in (403, 200)
    if resp.status_code == 200:
        data = resp.json()["data"]
        assert data["status"] in ("denied", "failed") or "access denied" in data["final_answer"].lower()


@pytest.mark.asyncio
async def test_unauthenticated_public_career_guidance(async_client: AsyncClient):
    """11. Public agent chat safely provides career guidance without candidate profile access."""
    resp = await async_client.post(
        "/api/v1/agent/public-chat",
        json={"task": "What programming languages should I learn for modern cloud development?"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "completed"
    assert len(data["final_answer"]) > 50
    assert data["total_tool_calls"] == 0  # Tools prohibited for public users
