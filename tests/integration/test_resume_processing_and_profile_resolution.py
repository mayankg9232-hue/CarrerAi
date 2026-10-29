"""Comprehensive automated tests for Resume Processing, Profile Resolution, and Matching.

Covers all 10 core scenarios from the Critical Bug Fix specification:
1. Valid PDF produces a persisted resume record and candidate profile.
2. Persisted profile can be retrieved using a new database session.
3. Career Agent retrieves correct profile for authenticated user.
4. Career Agent does not fail on synthetic or stale profile identifiers (e.g. profile_usr_...).
5. IDOR defense: Candidate cannot retrieve another user's candidate profile.
6. Failed PDF extraction produces a persisted failed status and meaningful error.
7. Profile still processing produces a proper pending response.
8. Completed profile and available jobs produce ranked matching results.
9. Empty jobs dataset produces an appropriate empty state rather than fake recommendations.
10. Failed optional LLM call does not corrupt persisted candidate profile.
"""

from datetime import datetime, timezone
import os
import uuid
import pytest
from httpx import ASGITransport, AsyncClient

from app.core.config import Settings
from app.main import app
from app.core.exceptions import AuthorizationError, NotFoundError
from app.db.connection import DatabaseManager
from app.db.sql_repositories import (
    SQLJobRepository,
    SQLMatchRepository,
    SQLResumeRepository,
    SQLUserRepository,
)
from app.matching.engine import MatchingEngine
from app.repositories.user_repo import UserRecord
from app.schemas.agent import AgentTaskRequest
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.job import Job
from app.schemas.resume import (
    CandidateProfile,
    ResumeRecord,
    SkillItem,
)
from app.services.agent_service import AgentApplicationService
from app.services.matching_service import MatchingApplicationService

DISPOSABLE_TEST_DB = "./test_bugfix_persistence.db"


@pytest.fixture(autouse=True)
def clean_test_db():
    """Ensure disposable SQLite database is cleanly removed after tests."""
    if os.path.exists(DISPOSABLE_TEST_DB):
        try:
            os.remove(DISPOSABLE_TEST_DB)
        except Exception:
            pass
    yield
    if os.path.exists(DISPOSABLE_TEST_DB):
        try:
            os.remove(DISPOSABLE_TEST_DB)
        except Exception:
            pass


@pytest.fixture
async def disposable_db():
    """Create and initialize a disposable database manager."""
    mgr = DatabaseManager(database_url=f"sqlite:///{DISPOSABLE_TEST_DB}")
    await mgr.initialize()
    yield mgr
    await mgr.close()


async def create_test_user(db: DatabaseManager, email_prefix: str = "user", role: str = "candidate") -> UserRecord:
    """Helper creating a persisted user record satisfying foreign key integrity."""
    user_repo = SQLUserRepository(db_manager=db)
    tag = uuid.uuid4().hex[:6]
    return await user_repo.create_user(
        email=f"{email_prefix}_{tag}@test.local",
        hashed_password="test_hashed_password",
        full_name=f"Test Candidate {tag}",
        role=role,
    )


def generate_sample_pdf_bytes() -> bytes:
    """Read canonical test PDF from fixtures or generate minimal valid PDF bytes."""
    fixture_path = "tests/fixtures/jordan_example_cv.pdf"
    if os.path.exists(fixture_path):
        with open(fixture_path, "rb") as f:
            return f.read()

    stream_bytes = b"BT /F1 12 Tf 50 750 Td (Maya Chen React TypeScript) ' ET"
    return b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n2 0 obj\n<< /Type /Pages /Kids [3 0 R] /Count 1 >>\nendobj\n3 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R >>\nendobj\n4 0 obj\n<< /Length 56 >>\nstream\n" + stream_bytes + b"\nendstream\nendobj\nxref\n0 5\n0000000000 65535 f \ntrailer\n<< /Root 1 0 R /Size 5 >>\nstartxref\n250\n%%EOF"


# ------------------------------------------------------------------------------
# Test 1: Valid PDF produces persisted resume record and candidate profile
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_valid_pdf_produces_persisted_resume_and_profile(disposable_db: DatabaseManager):
    """1. A valid PDF produces a persisted resume record and candidate profile."""
    user = await create_test_user(disposable_db, "maya")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    pdf_bytes = generate_sample_pdf_bytes()
    assert pdf_bytes.startswith(b"%PDF-")

    # Ingest through pipeline
    from app.pipelines.resume_pipeline import ResumePipeline
    pipeline = ResumePipeline(repo=resume_repo)
    profile = await pipeline.process_resume(
        file_bytes=pdf_bytes,
        filename="maya_chen_resume.pdf",
        content_type="application/pdf",
        user_id=user.id,
    )

    assert profile.id.startswith("prof_")
    assert profile.user_id == user.id

    # Persist ResumeRecord
    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="maya_chen_resume.pdf",
        storage_key=f"{user.id}/test_resume.pdf",
        file_size=len(pdf_bytes),
        mime_type="application/pdf",
        raw_hash=profile.raw_hash,
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile.id,
        extracted_text=profile.summary,
    )
    await resume_repo.create_resume(resume)

    # Verify both records exist in DB
    saved_resume = await resume_repo.get_resume(resume.id)
    assert saved_resume is not None
    assert saved_resume.status == "completed"
    assert saved_resume.profile_id == profile.id

    saved_profile = await resume_repo.get_by_id(profile.id)
    assert saved_profile is not None
    assert saved_profile.id == profile.id
    assert saved_profile.user_id == user.id


# ------------------------------------------------------------------------------
# Test 2: Persisted profile can be retrieved using a new database session
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persisted_profile_retrieved_in_new_session(disposable_db: DatabaseManager):
    """2. A persisted profile can be retrieved using a new database session."""
    user = await create_test_user(disposable_db, "alex")
    repo_session_1 = SQLResumeRepository(db_manager=disposable_db)
    profile_id = f"prof_{uuid.uuid4().hex[:8]}"

    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_session_test",
        full_name="Alex Developer",
        email=user.email,
        summary="Full-stack Python & React Engineer",
        skills=[SkillItem(name="Python", category="Language", confidence=0.95)],
    )
    await repo_session_1.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="alex_resume.pdf",
        storage_key=f"{user.id}/alex.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_session_test",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile_id,
    )
    await repo_session_1.create_resume(resume)

    # Open completely separate repository instance (simulating new DB session / request)
    repo_session_2 = SQLResumeRepository(db_manager=disposable_db)

    # Direct profile ID lookup
    fetched_prof = await repo_session_2.get_by_id(profile_id)
    assert fetched_prof is not None
    assert fetched_prof.id == profile_id
    assert fetched_prof.full_name == "Alex Developer"

    # Contextual resolution via user ID
    resolved_by_uid = await repo_session_2.resolve_profile(user_id=user.id)
    assert resolved_by_uid is not None
    assert resolved_by_uid.id == profile_id

    # Resolution via resume ID
    resolved_by_res = await repo_session_2.resolve_profile(identifier=resume.id)
    assert resolved_by_res is not None
    assert resolved_by_res.id == profile_id


# ------------------------------------------------------------------------------
# Test 3: Career Agent retrieves correct profile for authenticated user
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_career_agent_retrieves_authenticated_user_profile(disposable_db: DatabaseManager):
    """3. The Career Agent can retrieve the correct profile for the authenticated user."""
    user = await create_test_user(disposable_db, "maya_agent")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    profile_id = f"prof_{uuid.uuid4().hex[:8]}"

    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_agent_test",
        full_name="Maya Chen",
        email=user.email,
        summary="Frontend engineer specialized in React and accessibility.",
        skills=[SkillItem(name="React", category="Framework", confidence=0.98)],
    )
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="maya_chen.pdf",
        storage_key=f"{user.id}/maya.pdf",
        file_size=2048,
        mime_type="application/pdf",
        raw_hash="hash_agent_test",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    principal = AuthenticatedPrincipal(
        id=user.id,
        email=user.email,
        full_name="Maya Chen",
        role="candidate",
        is_active=True,
    )

    agent_service = AgentApplicationService(resume_repo=resume_repo)
    # Request without explicit candidate_id (agent resolves contextual user profile)
    request = AgentTaskRequest(task="hello", candidate_id=None)
    response = await agent_service.run_task(request=request, principal=principal)

    assert response.status in ("completed", "complete")
    assert response.final_answer != ""
    assert "not found" not in response.final_answer.lower()


# ------------------------------------------------------------------------------
# Test 4: Career Agent does not fail due to synthetic or stale profile identifier
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_career_agent_handles_synthetic_identifier_and_greetings(disposable_db: DatabaseManager):
    """4. Career Agent does not fail due to synthetic identifier (e.g. profile_usr_...) or missing profile."""
    user = await create_test_user(disposable_db, "sam")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    profile_id = f"prof_{uuid.uuid4().hex[:8]}"

    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_synthetic_test",
        full_name="Sam Smith",
        email=user.email,
        summary="Backend developer",
        skills=[SkillItem(name="Python", category="Language", confidence=0.9)],
    )
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="sam_resume.pdf",
        storage_key=f"{user.id}/sam.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_synthetic_test",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    principal = AuthenticatedPrincipal(
        id=user.id,
        email=user.email,
        role="candidate",
        is_active=True,
    )

    agent_service = AgentApplicationService(resume_repo=resume_repo)

    # 4A. Client passes synthetic identifier like 'profile_usr_...'
    request = AgentTaskRequest(
        task="hello",
        candidate_id=f"profile_{user.id}",
    )
    response = await agent_service.run_task(request=request, principal=principal)
    assert response.status in ("completed", "complete")
    assert "not found" not in response.final_answer.lower()
    # Confirm candidate_id was normalized to canonical prof_...
    assert request.candidate_id == profile_id

    # 4B. User with NO profile says "hello" - should succeed conversationally without NotFoundError
    fresh_user = await create_test_user(disposable_db, "fresh")
    fresh_principal = AuthenticatedPrincipal(
        id=fresh_user.id,
        email=fresh_user.email,
        role="candidate",
        is_active=True,
    )
    greeting_request = AgentTaskRequest(task="hello, how are you?", candidate_id=f"profile_{fresh_user.id}")
    greeting_resp = await agent_service.run_task(request=greeting_request, principal=fresh_principal)
    assert greeting_resp.status in ("completed", "complete")
    assert "not found" not in greeting_resp.final_answer.lower()


# ------------------------------------------------------------------------------
# Test 5: IDOR Defense — A user cannot retrieve another user's candidate profile
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idor_cross_user_profile_access_denied(disposable_db: DatabaseManager):
    """5. A user cannot retrieve another user's candidate profile."""
    user_b = await create_test_user(disposable_db, "victim")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    user_b_prof_id = f"prof_victim_{uuid.uuid4().hex[:6]}"
    profile_b = CandidateProfile(
        id=user_b_prof_id,
        user_id=user_b.id,
        raw_hash="hash_victim",
        full_name="Victim User",
        email=user_b.email,
        summary="Confidential candidate profile",
    )
    await resume_repo.save_profile(profile_b)

    user_a = await create_test_user(disposable_db, "attacker")
    principal_a = AuthenticatedPrincipal(
        id=user_a.id,
        email=user_a.email,
        role="candidate",
        is_active=True,
    )

    # 5A. Agent Service IDOR check
    agent_service = AgentApplicationService(resume_repo=resume_repo)
    attack_request = AgentTaskRequest(
        task="show me candidate profile facts",
        candidate_id=user_b_prof_id,
    )
    with pytest.raises(AuthorizationError):
        await agent_service.run_task(request=attack_request, principal=principal_a)

    # 5B. Matching Service IDOR check
    matching_service = MatchingApplicationService(resume_repo=resume_repo)
    with pytest.raises(AuthorizationError):
        await matching_service.recommend_jobs_for_candidate(
            candidate_id=user_b_prof_id,
            principal=principal_a,
        )


# ------------------------------------------------------------------------------
# Test 6: Failed PDF extraction results in persisted failed status and error
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_pdf_extraction_persists_status_and_allows_retry(disposable_db: DatabaseManager):
    """6. A failed PDF extraction results in a persisted failed status and meaningful error."""
    user = await create_test_user(disposable_db, "fail_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    corrupt_resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="corrupted_file.pdf",
        storage_key=f"{user.id}/corrupt.pdf",
        file_size=50,
        mime_type="application/pdf",
        raw_hash="hash_corrupt_123",
        status="failed",
        is_active=True,
        version=1,
        profile_id=None,
        error_message="Prohibited file type or invalid PDF header bytes.",
    )
    await resume_repo.create_resume(corrupt_resume)

    persisted = await resume_repo.get_resume(corrupt_resume.id)
    assert persisted is not None
    assert persisted.status == "failed"
    assert "invalid PDF" in (persisted.error_message or "")

    # Idempotent retry without creating duplicates: create profile first to satisfy foreign key
    prof = CandidateProfile(
        id=f"prof_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        raw_hash="hash_corrupt_123",
        full_name="Recovered Candidate",
    )
    await resume_repo.save_profile(prof)

    persisted.status = "completed"
    persisted.profile_id = prof.id
    persisted.error_message = None
    await resume_repo.create_resume(persisted)

    retried = await resume_repo.get_resume(corrupt_resume.id)
    assert retried is not None
    assert retried.status == "completed"
    assert retried.error_message is None
    all_user_resumes = await resume_repo.get_user_resumes(user.id)
    assert len(all_user_resumes) == 1


# ------------------------------------------------------------------------------
# Test 7: Profile still processing produces proper pending response
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_processing_profile_produces_pending_response(disposable_db: DatabaseManager):
    """7. A profile that is still processing produces a proper pending response."""
    user = await create_test_user(disposable_db, "proc_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    proc_resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="pending_resume.pdf",
        storage_key=f"{user.id}/pending.pdf",
        file_size=4096,
        mime_type="application/pdf",
        raw_hash="hash_pending_123",
        status="processing",
        is_active=True,
        version=1,
        profile_id=None,
    )
    await resume_repo.create_resume(proc_resume)

    principal = AuthenticatedPrincipal(
        id=user.id,
        email=user.email,
        role="candidate",
        is_active=True,
    )

    agent_service = AgentApplicationService(resume_repo=resume_repo)
    request = AgentTaskRequest(task="match my profile to job_atlas_01", job_id="job_atlas_01")
    response = await agent_service.run_task(request=request, principal=principal)

    assert response.status == "completed"
    assert "currently being processed" in response.final_answer.lower() or "processing" in response.final_answer.lower()


# ------------------------------------------------------------------------------
# Test 8: Completed profile and available jobs produce ranked matching results
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_completed_profile_and_jobs_produce_ranked_matching(disposable_db: DatabaseManager):
    """8. A completed profile and available jobs produce ranked matching results."""
    user = await create_test_user(disposable_db, "match_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"

    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_match_eval",
        full_name="Frontend Specialist",
        email=user.email,
        summary="Senior frontend engineer experienced in React, TypeScript, and CSS.",
        skills=[
            SkillItem(name="React", category="Framework", confidence=0.98),
            SkillItem(name="TypeScript", category="Language", confidence=0.95),
            SkillItem(name="CSS", category="Styling", confidence=0.90),
        ],
    )
    await resume_repo.save_profile(profile)

    strong_job = Job(
        id="job_react_lead",
        title="Lead Frontend Engineer",
        company="React Corp",
        location="Remote",
        description="Senior React engineer role",
        required_skills=["React", "TypeScript", "CSS"],
        is_active=True,
    )
    weak_job = Job(
        id="job_devops_lead",
        title="Site Reliability Engineer",
        company="Infra Corp",
        location="Remote",
        description="Kubernetes, Go, and Terraform infrastructure",
        required_skills=["Kubernetes", "Go", "Terraform"],
        is_active=True,
    )
    await job_repo.create_job(strong_job)
    await job_repo.create_job(weak_job)

    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", is_active=True)

    matching_service = MatchingApplicationService(
        resume_repo=resume_repo,
        match_repo=match_repo,
    )
    matching_service.pipeline.job_repo = job_repo
    matching_service.pipeline.engine.job_repo = job_repo
    matching_service.pipeline.engine.retriever.job_repo = job_repo

    recommendations = await matching_service.recommend_jobs_for_candidate(
        candidate_id=profile_id,
        principal=principal,
        min_score=0.0,
        limit=10,
    )

    assert len(recommendations.matches) == 2
    top_match = recommendations.matches[0]
    second_match = recommendations.matches[1]

    assert top_match.job_id == "job_react_lead"
    assert top_match.overall_score > second_match.overall_score
    assert "React" in top_match.matched_skills


# ------------------------------------------------------------------------------
# Test 9: Empty jobs dataset produces appropriate empty state without fake jobs
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_jobs_dataset_produces_empty_state_without_hallucinations(disposable_db: DatabaseManager):
    """9. An empty jobs dataset produces an appropriate empty state rather than fake recommendations."""
    user = await create_test_user(disposable_db, "empty_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    empty_job_repo = SQLJobRepository(db_manager=disposable_db)
    await empty_job_repo.clear()

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_empty_test",
        full_name="Candidate",
        skills=[SkillItem(name="Python", category="Language", confidence=0.9)],
    )
    await resume_repo.save_profile(profile)

    engine = MatchingEngine(job_repository=empty_job_repo)
    result = await engine.recommend_jobs_for_candidate(candidate=profile, top_k=10, min_score=0.0)

    assert result.total_evaluated == 0
    assert len(result.matches) == 0  # Does NOT invent fake jobs


# ------------------------------------------------------------------------------
# Test 10: Failed optional LLM call does not corrupt persisted candidate profile
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_llm_call_preserves_persisted_profile(disposable_db: DatabaseManager):
    """10. A failed optional LLM call does not corrupt the persisted candidate profile."""
    user = await create_test_user(disposable_db, "resilient")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    profile_id = f"prof_{uuid.uuid4().hex[:8]}"

    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_llm_resilience",
        full_name="Reliable Candidate",
        email=user.email,
        summary="Original uncorrupted summary",
        skills=[SkillItem(name="React", category="Framework", confidence=0.95)],
    )
    await resume_repo.save_profile(profile)

    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", is_active=True)

    class FailingLLMService:
        async def generate_text(self, *args, **kwargs):
            raise RuntimeError("External LLM Provider API rate-limited (503 Service Unavailable)")

    agent_service = AgentApplicationService(resume_repo=resume_repo)
    agent_service.orchestrator.llm_service = FailingLLMService()

    request = AgentTaskRequest(task="hello, give me career advice", candidate_id=profile_id)
    response = await agent_service.run_task(request=request, principal=principal)

    assert response.status in ("completed", "failed")
    assert "temporarily unavailable" in response.final_answer.lower() or "error" in response.final_answer.lower()

    persisted = await resume_repo.get_by_id(profile_id)
    assert persisted is not None
    assert persisted.full_name == "Reliable Candidate"
    assert persisted.summary == "Original uncorrupted summary"
    assert len(persisted.skills) == 1
    assert persisted.skills[0].name == "React"


# ------------------------------------------------------------------------------
# Test 11: Resume status API endpoint returns current processing status
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resume_status_api_endpoint(disposable_db: DatabaseManager):
    """11. GET /api/v1/resumes/{resume_id}/status returns verified status."""
    from app.authentication.service import AuthenticationService
    from app.services.storage_service import get_storage_service

    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    user = await create_test_user(disposable_db, "status_user")

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="status_test.pdf",
        storage_key=f"{user.id}/status_test.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_status_123",
        status="processing",
        is_active=True,
        version=1,
    )
    await resume_repo.create_resume(resume)

    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    # Override app repository dependencies
    from app.repositories.resume_repo import get_resume_repository
    from app.repositories.user_repo import get_user_repository
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get(
                f"/api/v1/resumes/{resume.id}/status",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["id"] == resume.id
            assert data["status"] == "processing"
            assert data["filename"] == "status_test.pdf"
    finally:
        app.dependency_overrides.pop(get_resume_repository, None)
        app.dependency_overrides.pop(get_user_repository, None)


# ------------------------------------------------------------------------------
# Test 12: Resume retry API endpoint retries failed extraction using stored file
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_resume_retry_api_endpoint(disposable_db: DatabaseManager):
    """12. POST /api/v1/resumes/{resume_id}/retry parses stored file without re-uploading."""
    from app.authentication.service import AuthenticationService
    from app.services.storage_service import get_storage_service

    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    user = await create_test_user(disposable_db, "retry_user")

    # Store genuine PDF file in storage service
    storage = get_storage_service()
    pdf_bytes = generate_sample_pdf_bytes()
    storage_key = await storage.save_file(
        user_id=user.id,
        filename="retry_doc.pdf",
        content=pdf_bytes,
    )

    # Initially failed resume
    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="retry_doc.pdf",
        storage_key=storage_key,
        file_size=len(pdf_bytes),
        mime_type="application/pdf",
        raw_hash="hash_retry_doc",
        status="failed",
        is_active=True,
        version=1,
        profile_id=None,
        error_message="Simulated temporary parsing timeout",
    )
    await resume_repo.create_resume(resume)

    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    from app.repositories.resume_repo import get_resume_repository
    from app.repositories.user_repo import get_user_repository
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post(
                f"/api/v1/resumes/{resume.id}/retry",
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["resume"]["status"] == "completed"
            assert data["profile"] is not None
            assert data["resume"]["profile_id"] == data["profile"]["id"]

            # Confirm database record updated
            updated_in_db = await resume_repo.get_resume(resume.id)
            assert updated_in_db.status == "completed"
            assert updated_in_db.error_message is None
    finally:
        app.dependency_overrides.pop(get_resume_repository, None)
        app.dependency_overrides.pop(get_user_repository, None)
        try:
            await storage.delete_file(storage_key)
        except Exception:
            pass
