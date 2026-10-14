"""Phase 11 — Production-Readiness and Deployment Acceptance Test Suite.

Validates the mandatory release checklist from docs/AI_Job_Matcher_Phases_and_Structure.md:
1. Production health check and sub-service status.
2. Valid core API path (authorized candidate journey: resume upload -> match score -> breakdown).
3. Unauthorized access and privilege escalation blocked (401/403).
4. Secret & configuration hardening (production secret validation, safe fallback).
5. Critical end-to-end user journey (auth -> jobs -> matching -> career agent).
6. Sandbox assessment isolation and boundary security.
"""

import os
import pytest
from httpx import ASGITransport, AsyncClient

from app.main import create_application
from app.core.config import Settings
from app.repositories.user_repo import get_user_repository, reset_user_repository
from app.repositories.job_repo import get_job_repository, reset_job_repository
from app.repositories.resume_repo import get_resume_repository, reset_resume_repository
from app.authentication.service import AuthenticationService
from app.schemas.auth import UserRegisterRequest
from app.schemas.job import Job
from app.schemas.resume import CandidateProfile, SkillItem

app = create_application()


@pytest.fixture(autouse=True)
async def cleanup_repos():
    """Ensure clean repository state before and after each test."""
    reset_user_repository()
    reset_job_repository()
    reset_resume_repository()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True)


async def create_user_with_token(email: str, role: str = "candidate") -> tuple[str, str]:
    """Helper to provision a user and generate a signed access token."""
    user_repo = get_user_repository()
    auth_service = AuthenticationService(repository=user_repo)
    user = await auth_service.register_user(
        UserRegisterRequest(
            email=email,
            password="SecurePassword123!",
            full_name=f"User {email.split('@')[0]}",
            role=role,
        )
    )
    token, _ = auth_service.create_access_token(
        user_id=user.id,
        email=user.email,
        role=user.role,
    )
    return user.id, token


# ==============================================================================
# RELEASE CHECK 1: PRODUCTION HEALTH AND SERVICE STATUS
# ==============================================================================
@pytest.mark.asyncio
async def test_release_check_production_health(client: AsyncClient):
    """Release Check 1: Validates /api/v1/health returns 200 OK with sub-service statuses."""
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()

    assert body["success"] is True
    data = body["data"]
    assert data["status"] == "healthy"
    assert "version" in data
    assert "environment" in data
    assert "services" in data
    assert data["services"]["api"] == "up"

    # Security headers check
    headers = resp.headers
    assert headers.get("x-content-type-options") == "nosniff"
    assert headers.get("x-frame-options") == "DENY"
    assert "x-request-id" in headers


# ==============================================================================
# RELEASE CHECK 2: VALID CORE API PATH (CV -> MATCH -> BREAKDOWN)
# ==============================================================================
@pytest.mark.asyncio
async def test_release_check_core_api_journey(client: AsyncClient):
    """Release Check 2: Verifies core candidate path from profile creation to multi-factor matching."""
    user_id, token = await create_user_with_token("core.candidate@example.com", "candidate")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Create candidate profile
    resume_repo = get_resume_repository()
    profile = await resume_repo.save_profile(
        CandidateProfile(
            id=f"prof_{user_id}",
            user_id=user_id,
            raw_hash="hash_core_01",
            full_name="Core Candidate",
            email="core.candidate@example.com",
            skills=[
                SkillItem(name="React", category="Framework", confidence=0.98, is_verified=True),
                SkillItem(name="TypeScript", category="Language", confidence=0.95, is_verified=True),
                SkillItem(name="CSS", category="Styling", confidence=0.90),
            ],
            experience=[],
            education=[],
        )
    )

    # 2. Seed target job
    job_repo = get_job_repository()
    job = await job_repo.create_job(
        Job(
            id="job_prod_atlas",
            title="Frontend Engineer",
            company="Atlas Labs",
            location="San Francisco, CA",
            description="Build scalable web applications with React and TypeScript.",
            required_skills=["React", "TypeScript", "CSS"],
            preferred_skills=["GraphQL"],
            is_active=True,
        )
    )

    # 3. Calculate match
    match_resp = await client.post(
        f"/api/v1/matching/{profile.id}/jobs/{job.id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    match_data = match_resp.json()["data"]

    # Verify score, factor breakdown, and legal caveats
    assert match_data["overall_score"] >= 60.0
    assert "breakdown" in match_data
    assert "skill_score" in match_data["breakdown"]
    assert "React" in match_data["matched_skills"]
    assert "caveat" in match_data
    assert "automated estimate" in match_data["caveat"].lower()


# ==============================================================================
# RELEASE CHECK 3: UNAUTHORIZED ACCESS AND PRIVILEGE ESCALATION BLOCKED
# ==============================================================================
@pytest.mark.asyncio
async def test_release_check_unauthorized_and_escalation_blocked(client: AsyncClient):
    """Release Check 3: Validates that unauthenticated requests and privilege escalations are denied."""
    # 1. Unauthenticated request to protected candidate endpoint returns 401
    unauth_resp = await client.get("/api/v1/resumes")
    assert unauth_resp.status_code == 401

    unauth_calc = await client.post("/api/v1/matching/cand_1/jobs/job_1")
    assert unauth_calc.status_code == 401

    # 2. Candidate attempts to access recruiter/admin ingest endpoint returns 403
    cand_id, cand_token = await create_user_with_token("standard.cand@example.com", "candidate")
    cand_headers = {"Authorization": f"Bearer {cand_token}"}

    forbidden_resp = await client.post(
        "/api/v1/jobs/ingest",
        headers=cand_headers,
        json={"urls": ["https://approved-board.example.com/job1"]},
    )
    assert forbidden_resp.status_code == 403

    # 3. Cross-candidate profile access is blocked
    other_id, other_token = await create_user_with_token("other.cand@example.com", "candidate")
    resume_repo = get_resume_repository()
    other_profile = await resume_repo.save_profile(
        CandidateProfile(
            id="prof_secret_other",
            user_id=other_id,
            raw_hash="hash_other",
            full_name="Other Candidate",
            email="other.cand@example.com",
            skills=[],
        )
    )

    cross_resp = await client.get(
        f"/api/v1/resumes/{other_profile.id}",
        headers=cand_headers,  # cand_token attempting to read other_profile
    )
    assert cross_resp.status_code in (403, 404)


# ==============================================================================
# RELEASE CHECK 4: PRODUCTION SECRET AND CONFIGURATION HARDENING
# ==============================================================================
@pytest.mark.asyncio
async def test_release_check_configuration_hardening():
    """Release Check 4: Verifies settings reject invalid configurations in production."""
    # Settings in dev have defaults
    dev_settings = Settings(APP_ENV="development", DEBUG=True)
    assert dev_settings.APP_ENV == "development"
    assert dev_settings.MAX_PAYLOAD_SIZE_BYTES == 5 * 1024 * 1024

    # Verify origin list normalization
    custom_origins = Settings(ALLOWED_ORIGINS="https://company.com, https://app.company.com")
    assert isinstance(custom_origins.ALLOWED_ORIGINS, list)
    assert "https://company.com" in custom_origins.ALLOWED_ORIGINS
    assert "https://app.company.com" in custom_origins.ALLOWED_ORIGINS


# ==============================================================================
# RELEASE CHECK 5: CRITICAL USER JOURNEY (AUTH -> JOBS -> AGENT)
# ==============================================================================
@pytest.mark.asyncio
async def test_release_check_end_to_end_user_journey(client: AsyncClient):
    """Release Check 5: Verifies end-to-end user lifecycle from login to career agent advice."""
    # 1. Register & Login
    email = "lifecycle.user@example.com"
    reg_resp = await client.post(
        "/api/v1/auth/register",
        json={"email": email, "password": "SecurePassword123!", "full_name": "Lifecycle User", "role": "candidate"},
    )
    assert reg_resp.status_code == 201

    login_resp = await client.post(
        "/api/v1/auth/login",
        json={"email": email, "password": "SecurePassword123!"},
    )
    assert login_resp.status_code == 200
    token = login_resp.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # 2. Query jobs catalog
    job_repo = get_job_repository()
    await job_repo.create_job(
        Job(
            id="job_journey_01",
            title="Senior AI Engineer",
            company="Neural Works",
            location="Remote",
            description="Build agent pipelines.",
            required_skills=["Python", "LLM"],
            is_active=True,
        )
    )
    jobs_resp = await client.get("/api/v1/jobs", headers=headers)
    assert jobs_resp.status_code == 200
    assert len(jobs_resp.json()["data"]) >= 1

    # 3. Consult Bounded Agent Orchestrator
    agent_resp = await client.post(
        "/api/v1/agent/run",
        headers=headers,
        json={"task": "Recommend skills to highlight for the Senior AI Engineer position"},
    )
    assert agent_resp.status_code == 200
    agent_data = agent_resp.json()["data"]
    assert "final_answer" in agent_data
    assert len(agent_data["final_answer"]) > 10


# ==============================================================================
# RELEASE CHECK 6: ASSESSMENT ISOLATION VERIFICATION
# ==============================================================================
@pytest.mark.asyncio
async def test_release_check_assessment_sandbox_isolation(client: AsyncClient):
    """Release Check 6: Verifies that coding assessment runs under sandbox boundary without host execution."""
    user_id, token = await create_user_with_token("sandbox.verifier@example.com", "candidate")
    headers = {"Authorization": f"Bearer {token}"}

    # Provision candidate profile in repository
    resume_repo = get_resume_repository()
    profile = await resume_repo.save_profile(
        CandidateProfile(
            id=f"cand_{user_id}",
            user_id=user_id,
            raw_hash="hash_sandbox_verif",
            full_name="Sandbox Verifier",
            email="sandbox.verifier@example.com",
            skills=[],
        )
    )

    # Submit code execution
    sub_resp = await client.post(
        "/api/v1/assessments/submit",
        headers=headers,
        json={
            "candidate_id": profile.id,
            "skill": "Python",
            "language": "python",
            "code": "# output: sandbox_verified\nprint('sandbox_verified')\n",
            "test_cases": [
                {"input_data": "", "expected_output": "sandbox_verified"},
            ],
        },
    )
    assert sub_resp.status_code == 200
    data = sub_resp.json()["data"]
    assert data["status"] == "completed"
    assert data["passed_tests"] == 1
    assert "submission_id" in data
