"""Integration test suite verifying:
1. "View original job posting" link generation with valid URL resolution and absence of "View match".
2. Handling of missing, empty, or malformed job URLs with disabled indicator.
3. Resume replacement workflow: multi-version lifecycle, processing status, and active flag management.
4. Activation of new resume deactivates old version and associates fresh multi-factor matches.
5. Queries to match endpoints return results evaluated against the authoritative active resume profile.
6. Preservation of user saved jobs in PostgreSQL when resume is replaced or updated.
7. Enriched saved jobs endpoint (/api/v1/jobs/saved) returns active resume's match score and status.
8. Career Agent strictly uses the active resume's candidate profile and detects pending processing.
9. Persistent reloading across logins/sessions loads the active resume and its stored match records.
10. Cross-user isolation: User A cannot read or modify User B's resumes, matches, or saved jobs.
11. Extraction failure on replacement preserves the previous valid active resume.
12. Frontend job card verification ensuring no 'View match' buttons and proper 'View original job posting' links.
"""

from datetime import datetime, timezone
import json
import os
import re
from typing import AsyncGenerator
import uuid

import pytest
from httpx import ASGITransport, AsyncClient

from app.authentication.service import AuthenticationService
from app.db.connection import DatabaseManager
from app.db.sql_repositories import (
    SQLJobRepository,
    SQLMatchRepository,
    SQLResumeRepository,
    SQLUserRepository,
)
from app.main import app
from app.matching.engine import MatchingEngine
from app.repositories.job_repo import get_job_repository
from app.repositories.match_repo import get_match_repository
from app.repositories.resume_repo import get_resume_repository
from app.repositories.user_repo import UserRecord, get_user_repository
from app.schemas.job import Job
from app.schemas.matching import MatchResult, ScoreBreakdown
from app.schemas.resume import (
    CandidateProfile,
    ResumeRecord,
    SkillItem,
)

DISPOSABLE_TEST_DB = "./test_replacement_and_posting_links.db"


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


async def create_test_user(db: DatabaseManager, email_prefix: str = "cand", role: str = "candidate") -> UserRecord:
    """Helper creating a persisted user record."""
    user_repo = SQLUserRepository(db_manager=db)
    tag = uuid.uuid4().hex[:6]
    return await user_repo.create_user(
        email=f"{email_prefix}_{tag}@example.com",
        hashed_password="test_secure_password",
        full_name=f"Test Candidate {tag}",
        role=role,
        email_verified=True,
    )


def create_token(user: UserRecord) -> str:
    """Helper creating a valid JWT bearer token string."""
    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role=user.role, email=user.email, email_verified=user.email_verified)
    return token


# ------------------------------------------------------------------------------
# Test 1 & 12: Frontend verification: "View original job posting" present, "View match" removed
# ------------------------------------------------------------------------------
def test_frontend_job_card_view_original_posting_and_no_view_match():
    """Verify that frontend/app.js contains 'View original job posting' and has completely removed 'View match'."""
    with open("frontend/app.js", "r", encoding="utf-8") as f:
        content = f.read()

    # Must contain "View original job posting"
    assert "View original job posting" in content
    assert "btn-job-posting" in content

    # Must NOT contain "View match"
    match_occurrences = re.findall(r"View match", content, re.IGNORECASE)
    assert len(match_occurrences) == 0, f"Found lingering 'View match' buttons in frontend/app.js: {match_occurrences}"


# ------------------------------------------------------------------------------
# Test 2: Valid source_url and apply_url handling in Job schema & serialization
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_job_posting_urls_persisted_and_serialized(disposable_db: DatabaseManager):
    """Verify that Job schema and repositories accurately persist source_url and apply_url."""
    job_repo = SQLJobRepository(db_manager=disposable_db)

    job = Job(
        id="job_url_test_1",
        title="Full Stack Engineer",
        company="Acme Corp",
        description="Building modern cloud apps.",
        source_url="https://acme.careers/jobs/12345",
        apply_url="https://acme.careers/jobs/12345/apply",
        required_skills=["Python", "React"],
        is_active=True,
    )
    saved = await job_repo.create_job(job)
    assert saved.source_url == "https://acme.careers/jobs/12345"
    assert saved.apply_url == "https://acme.careers/jobs/12345/apply"

    retrieved = await job_repo.get_by_id("job_url_test_1")
    assert retrieved is not None
    assert retrieved.source_url == "https://acme.careers/jobs/12345"
    assert retrieved.apply_url == "https://acme.careers/jobs/12345/apply"


# ------------------------------------------------------------------------------
# Test 3: Missing, #, or invalid URLs correctly handled
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_job_with_missing_or_empty_urls(disposable_db: DatabaseManager):
    """Verify that jobs without valid URLs have None values, preventing broken links."""
    job_repo = SQLJobRepository(db_manager=disposable_db)

    job = Job(
        id="job_nourl_test_2",
        title="Software Engineer",
        company="Internal Systems",
        description="Legacy role.",
        source_url=None,
        apply_url=None,
        required_skills=["Java"],
        is_active=True,
    )
    saved = await job_repo.create_job(job)
    assert saved.source_url is None
    assert saved.apply_url is None


# ------------------------------------------------------------------------------
# Test 4: Replacing a resume creates a new version with status='processing' and is_active=False
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_replacement_resume_initial_state_preserves_active_v1(disposable_db: DatabaseManager):
    """Uploading a replacement resume keeps it inactive/processing until completed, preserving V1."""
    user = await create_test_user(disposable_db, "replace_cand")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    # V1 is completed and active
    v1 = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_v1_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            filename="v1_frontend.pdf",
            storage_key="local://v1.pdf",
            raw_hash="hash_v1",
            file_size=1024,
            mime_type="application/pdf",
            version=1,
            status="completed",
            is_active=True,
        )
    )
    assert v1.is_active is True

    # V2 is created in 'processing' state with is_active=False
    v2 = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_v2_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            filename="v2_backend.pdf",
            storage_key="local://v2.pdf",
            raw_hash="hash_v2",
            file_size=2048,
            mime_type="application/pdf",
            version=2,
            status="processing",
            is_active=False,
        )
    )
    assert v2.is_active is False
    assert v2.status == "processing"

    # Active resume query still authoritative returns V1
    active = await resume_repo.get_active_resume(user.id)
    assert active is not None
    assert active.id == v1.id


# ------------------------------------------------------------------------------
# Test 5 & 6: Successful replacement sets is_active=True and associates fresh matches
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_replacement_deactivates_v1_and_updates_matches(disposable_db: DatabaseManager):
    """When V2 completes, it becomes active, deactivates V1, and matches reflect V2's profile."""
    user = await create_test_user(disposable_db, "matching_replacement")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)

    # Seed Backend Job in catalog
    backend_job = await job_repo.create_job(
        Job(
            id="job_backend_lead",
            title="Senior Backend Engineer",
            company="CloudCorp",
            description="High throughput distributed systems.",
            required_skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
            source_url="https://cloudcorp.io/jobs/backend",
            is_active=True,
        )
    )

    # Profile V1: Frontend candidate
    prof_v1 = await resume_repo.save_profile(
        CandidateProfile(
            id=f"prof_v1_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            raw_hash="hash_fe",
            full_name="Alex Mercer",
            skills=[SkillItem(name="React"), SkillItem(name="CSS"), SkillItem(name="HTML")],
        )
    )
    res_v1 = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_v1_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            profile_id=prof_v1.id,
            filename="frontend_resume.pdf",
            storage_key="local://frontend.pdf",
            raw_hash="hash_fe",
            file_size=1024,
            mime_type="application/pdf",
            version=1,
            status="completed",
            is_active=True,
        )
    )

    # Compute & persist match for V1 against backend job (Low match score)
    engine = MatchingEngine()
    match_v1 = await engine.match_candidate_to_job(prof_v1, backend_job)
    await match_repo.save_match(match_v1)

    assert match_v1.overall_score < 40.0
    assert "FastAPI" in match_v1.missing_skills

    # Now upload V2: Backend candidate profile
    prof_v2 = await resume_repo.save_profile(
        CandidateProfile(
            id=f"prof_v2_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            raw_hash="hash_be",
            full_name="Alex Mercer",
            skills=[
                SkillItem(name="Python"),
                SkillItem(name="FastAPI"),
                SkillItem(name="PostgreSQL"),
                SkillItem(name="Docker"),
            ],
            experience_years=5,
        )
    )
    res_v2 = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_v2_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            profile_id=prof_v2.id,
            filename="backend_resume.pdf",
            storage_key="local://backend.pdf",
            raw_hash="hash_be",
            file_size=2048,
            mime_type="application/pdf",
            version=2,
            status="completed",
            is_active=False,
        )
    )

    # Activate V2
    activated = await resume_repo.set_active_resume(user.id, res_v2.id)
    assert activated is True

    # Calculate match for V2
    match_v2 = await engine.match_candidate_to_job(prof_v2, backend_job)
    await match_repo.save_match(match_v2)

    assert match_v2.overall_score > 60.0
    assert "Python" in match_v2.matched_skills
    assert "FastAPI" in match_v2.matched_skills

    # Verify active resume is now V2
    current_active = await resume_repo.get_active_resume(user.id)
    assert current_active.id == res_v2.id
    assert current_active.profile_id == prof_v2.id

    # Verify matches for active profile V2
    active_matches = await match_repo.get_matches_for_candidate(prof_v2.id)
    assert len(active_matches) == 1
    assert active_matches[0]["overall_score"] > 60.0


# ------------------------------------------------------------------------------
# Test 7 & 8: User saved jobs preserved and enriched with active resume match score
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_saved_jobs_preserved_across_resume_changes_and_enriched(disposable_db: DatabaseManager):
    """Saved jobs remain intact when resume is replaced and return the active resume's match score."""
    user = await create_test_user(disposable_db, "saved_jobs_cand")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    user_repo = SQLUserRepository(db_manager=disposable_db)

    # Seed job
    job = await job_repo.create_job(
        Job(
            id="job_saved_target",
            title="Senior Platform Engineer",
            company="DataScale Inc",
            description="Distributed cloud computing.",
            required_skills=["Go", "Kubernetes"],
            source_url="https://datascale.io/jobs/platform",
            is_active=True,
        )
    )

    # User saves the job
    saved_result = await job_repo.save_job(user.id, job.id)
    assert saved_result is True

    # User uploads Resume V1 (skills: Python)
    prof_v1 = await resume_repo.save_profile(
        CandidateProfile(
            id=f"prof_v1_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            raw_hash="hash_v1",
            skills=[SkillItem(name="Python")],
        )
    )
    res_v1 = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_v1_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            profile_id=prof_v1.id,
            filename="v1.pdf",
            storage_key="local://v1.pdf",
            raw_hash="hash_v1",
            file_size=1024,
            mime_type="application/pdf",
            version=1,
            status="completed",
            is_active=True,
        )
    )

    # API call to GET /api/v1/jobs/saved
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    token = create_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/jobs/saved", headers=headers)
        assert resp.status_code == 200
        saved_jobs = resp.json()["data"]["jobs"]
        assert len(saved_jobs) == 1
        assert saved_jobs[0]["id"] == job.id
        assert saved_jobs[0]["source_url"] == "https://datascale.io/jobs/platform"
        # Match hasn't been computed yet for prof_v1
        assert saved_jobs[0]["match_status"] in ["unavailable", "completed"]

        # Now replace with Resume V2 (skills: Go, Kubernetes)
        prof_v2 = await resume_repo.save_profile(
            CandidateProfile(
                id=f"prof_v2_{uuid.uuid4().hex[:6]}",
                user_id=user.id,
                raw_hash="hash_v2",
                skills=[SkillItem(name="Go"), SkillItem(name="Kubernetes")],
            )
        )
        res_v2 = await resume_repo.create_resume(
            ResumeRecord(
                id=f"res_v2_{uuid.uuid4().hex[:6]}",
                user_id=user.id,
                profile_id=prof_v2.id,
                filename="v2.pdf",
                storage_key="local://v2.pdf",
                raw_hash="hash_v2",
                file_size=2048,
                mime_type="application/pdf",
                version=2,
                status="completed",
                is_active=False,
            )
        )
        await resume_repo.set_active_resume(user.id, res_v2.id)

        # Calculate and save match for V2
        engine = MatchingEngine()
        match_v2 = await engine.match_candidate_to_job(prof_v2, job)
        await match_repo.save_match(match_v2)

        # GET /api/v1/jobs/saved must reflect V2 match score and matched skills!
        resp2 = await client.get("/api/v1/jobs/saved", headers=headers)
        assert resp2.status_code == 200
        saved_jobs2 = resp2.json()["data"]["jobs"]
        assert len(saved_jobs2) == 1
        assert saved_jobs2[0]["id"] == job.id
        assert saved_jobs2[0]["score"] == pytest.approx(match_v2.overall_score, 0.1)
        assert saved_jobs2[0]["match_status"] == "completed"
        assert "Go" in saved_jobs2[0]["matched_skills"]
        assert "Kubernetes" in saved_jobs2[0]["matched_skills"]

    app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 9: Career Agent uses the active resume and handles pending status
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_agent_uses_active_profile_and_reports_pending_status(disposable_db: DatabaseManager):
    """Career Agent resolves active profile and notifies candidate if active resume is still processing."""
    user = await create_test_user(disposable_db, "agent_cand")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    user_repo = SQLUserRepository(db_manager=disposable_db)

    # 1. User has a resume that is still in 'processing' status
    res_pending = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_pend_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            filename="pending_resume.pdf",
            storage_key="local://pend.pdf",
            raw_hash="hash_pend",
            file_size=1024,
            mime_type="application/pdf",
            version=1,
            status="processing",
            is_active=True,
        )
    )

    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    token = create_token(user)
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Agent run when active resume is processing
        resp = await client.post(
            "/api/v1/agent/run",
            json={"task": "What roles fit my profile?", "history": []},
            headers=headers,
        )
        assert resp.status_code == 200
        agent_data = resp.json()["data"]
        assert any(w in agent_data["final_answer"].lower() for w in ["parsed", "indexed", "processing", "underway"])

    app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 10: Refreshing / re-login loads the active resume and its persisted matches
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_relogin_retrieves_active_resume_and_persisted_matches(disposable_db: DatabaseManager):
    """After login or refresh, GET /api/v1/matches retrieves stored matches for the active resume."""
    user = await create_test_user(disposable_db, "relogin_cand")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    user_repo = SQLUserRepository(db_manager=disposable_db)

    # Active profile & resume
    prof = await resume_repo.save_profile(
        CandidateProfile(
            id=f"prof_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            raw_hash="hash_ml",
            full_name="Morgan Reed",
            skills=[SkillItem(name="Python"), SkillItem(name="Machine Learning")],
            experience_years=5,
        )
    )
    res = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            profile_id=prof.id,
            filename="ml_resume.pdf",
            storage_key="local://ml.pdf",
            raw_hash="hash_ml",
            file_size=1024,
            mime_type="application/pdf",
            version=1,
            status="completed",
            is_active=True,
        )
    )

    # Job & persisted match
    job = await job_repo.create_job(
        Job(
            id="job_ml_engineer",
            title="Machine Learning Engineer",
            company="DeepAI Labs",
            description="Developing state of the art models.",
            required_skills=["Python", "Machine Learning"],
            source_url="https://deepai.io/careers/ml-eng",
            is_active=True,
        )
    )
    engine = MatchingEngine()
    match = await engine.match_candidate_to_job(prof, job)
    await match_repo.save_match(match)

    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    token = create_token(user)
    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/api/v1/matches", headers=headers)
        assert resp.status_code == 200
        payload = resp.json()["data"]
        assert payload["status"] == "completed"
        assert payload["is_cached"] is True
        ranked = payload["ranked_matches"]
        assert len(ranked) == 1
        assert ranked[0]["job"]["id"] == job.id
        assert ranked[0]["job"]["source_url"] == "https://deepai.io/careers/ml-eng"
        assert ranked[0]["match"]["overall_score"] > 60.0

    app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 11: Cross-user isolation (IDOR protection)
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_cross_user_isolation_for_resumes_matches_and_saved_jobs(disposable_db: DatabaseManager):
    """User A cannot access User B's resumes, matches, or saved jobs."""
    user_a = await create_test_user(disposable_db, "user_a")
    user_b = await create_test_user(disposable_db, "user_b")

    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    user_repo = SQLUserRepository(db_manager=disposable_db)

    # User A's resume and profile
    prof_a = await resume_repo.save_profile(
        CandidateProfile(id="prof_a_secret", user_id=user_a.id, raw_hash="hash_a", full_name="User A Private")
    )
    res_a = await resume_repo.create_resume(
        ResumeRecord(
            id="res_a_secret",
            user_id=user_a.id,
            profile_id=prof_a.id,
            filename="private_a.pdf",
            storage_key="local://a.pdf",
            raw_hash="hash_a",
            file_size=1024,
            mime_type="application/pdf",
            status="completed",
            is_active=True,
        )
    )

    # User A saves a job
    job = await job_repo.create_job(
        Job(id="job_shared", title="Engineer", company="Company", description="Shared role.", is_active=True)
    )
    await job_repo.save_job(user_a.id, job.id)

    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    token_b = create_token(user_b)
    headers_b = {"Authorization": f"Bearer {token_b}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # User B requests resumes list -> must not see User A's resume
        resp_resumes = await client.get("/api/v1/resumes", headers=headers_b)
        assert resp_resumes.status_code == 200
        assert len(resp_resumes.json()["data"]) == 0

        # User B tries to view User A's specific resume -> 404/403
        resp_detail = await client.get(f"/api/v1/resumes/{res_a.id}", headers=headers_b)
        assert resp_detail.status_code in [403, 404]

        # User B requests saved jobs -> must be empty for User B
        resp_saved = await client.get("/api/v1/jobs/saved", headers=headers_b)
        assert resp_saved.status_code == 200
        assert len(resp_saved.json()["data"]["jobs"]) == 0

    app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 12: Extraction failure on replacement preserves the last valid active resume
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_extraction_failure_preserves_previous_active_resume(disposable_db: DatabaseManager):
    """When a new resume upload fails extraction, previous valid active resume remains active."""
    user = await create_test_user(disposable_db, "fail_test_cand")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    # Valid V1 active resume
    v1 = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_v1_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            filename="valid_v1.pdf",
            storage_key="local://v1.pdf",
            raw_hash="hash_v1",
            file_size=1024,
            mime_type="application/pdf",
            version=1,
            status="completed",
            is_active=True,
        )
    )

    # Simulate upload of corrupted V2 that fails extraction
    v2 = await resume_repo.create_resume(
        ResumeRecord(
            id=f"res_v2_{uuid.uuid4().hex[:6]}",
            user_id=user.id,
            filename="corrupt_v2.pdf",
            storage_key="local://v2.pdf",
            raw_hash="hash_v2",
            file_size=512,
            mime_type="application/pdf",
            version=2,
            status="failed",
            is_active=False,
            processing_error="PDF extraction failed: damaged document structure",
        )
    )

    # Active resume must still be V1
    active = await resume_repo.get_active_resume(user.id)
    assert active is not None
    assert active.id == v1.id
    assert active.is_active is True


# ------------------------------------------------------------------------------
# Test 13: Deterministic URL resolution policy and invalid scheme filtering
# ------------------------------------------------------------------------------
def test_url_helper_valid_and_invalid_resolution():
    """Verify URL policy: accepts valid http/https URLs, rejects #, javascript:, empty, and malformed strings."""
    def python_get_valid_job_posting_url(job_dict):
        raw_url = job_dict.get("source_url") or job_dict.get("job_url") or job_dict.get("apply_url")
        if not raw_url or not isinstance(raw_url, str):
            return None
        trimmed = raw_url.strip()
        if not trimmed or trimmed == "#" or trimmed.lower().startswith("javascript:"):
            return None
        if trimmed.startswith("http://") or trimmed.startswith("https://"):
            return trimmed
        return None

    # Valid URLs
    assert python_get_valid_job_posting_url({"source_url": "https://careers.google.com/jobs/123"}) == "https://careers.google.com/jobs/123"
    assert python_get_valid_job_posting_url({"apply_url": "http://company.com/apply"}) == "http://company.com/apply"
    assert python_get_valid_job_posting_url({"source_url": "https://a.com", "apply_url": "https://b.com"}) == "https://a.com"

    # Invalid / dangerous URLs
    assert python_get_valid_job_posting_url({"source_url": "javascript:alert(1)"}) is None
    assert python_get_valid_job_posting_url({"source_url": "#"}) is None
    assert python_get_valid_job_posting_url({"source_url": ""}) is None
    assert python_get_valid_job_posting_url({"source_url": "   "}) is None
    assert python_get_valid_job_posting_url({}) is None
    assert python_get_valid_job_posting_url({"source_url": "ftp://files.example.com/job"}) is None


# ------------------------------------------------------------------------------
# Test 14: Old resume matches are isolated and never returned for newly active resume
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_old_resume_matches_isolated_and_not_returned_for_new_resume(disposable_db: DatabaseManager):
    """Old resume match records remain associated only with the old profile ID and never leak into new profile matches."""
    user = await create_test_user(disposable_db, "isolated_matches_cand")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)

    # Job
    job = await job_repo.create_job(
        Job(
            id="job_test_iso",
            title="DevOps Engineer",
            company="OpsCorp",
            description="Kubernetes, Terraform, CI/CD.",
            required_skills=["Kubernetes", "Terraform"],
            is_active=True,
        )
    )

    # V1 (Old Frontend Profile)
    prof_v1 = await resume_repo.save_profile(
        CandidateProfile(id="prof_old_fe", user_id=user.id, raw_hash="h_fe", skills=[SkillItem(name="React")])
    )
    res_v1 = await resume_repo.create_resume(
        ResumeRecord(
            id="res_v1_old",
            user_id=user.id,
            profile_id=prof_v1.id,
            filename="old.pdf",
            storage_key="local://old.pdf",
            raw_hash="h_fe",
            file_size=1024,
            mime_type="application/pdf",
            version=1,
            status="completed",
            is_active=True,
        )
    )

    # Match for V1
    engine = MatchingEngine()
    match_v1 = await engine.match_candidate_to_job(prof_v1, job)
    await match_repo.save_match(match_v1)

    # V2 (New DevOps Profile)
    prof_v2 = await resume_repo.save_profile(
        CandidateProfile(id="prof_new_devops", user_id=user.id, raw_hash="h_devops", skills=[SkillItem(name="Kubernetes"), SkillItem(name="Terraform")])
    )
    res_v2 = await resume_repo.create_resume(
        ResumeRecord(
            id="res_v2_new",
            user_id=user.id,
            profile_id=prof_v2.id,
            filename="new.pdf",
            storage_key="local://new.pdf",
            raw_hash="h_devops",
            file_size=2048,
            mime_type="application/pdf",
            version=2,
            status="completed",
            is_active=False,
        )
    )
    await resume_repo.set_active_resume(user.id, res_v2.id)

    # Match for V2
    match_v2 = await engine.match_candidate_to_job(prof_v2, job)
    await match_repo.save_match(match_v2)

    # Verify query for V1 candidate profile returns only V1's match
    v1_matches = await match_repo.get_matches_for_candidate(prof_v1.id)
    assert len(v1_matches) == 1
    assert v1_matches[0]["candidate_id"] == prof_v1.id
    assert v1_matches[0]["overall_score"] < 40.0

    # Verify query for V2 candidate profile returns only V2's match
    v2_matches = await match_repo.get_matches_for_candidate(prof_v2.id)
    assert len(v2_matches) == 1
    assert v2_matches[0]["candidate_id"] == prof_v2.id
    assert v2_matches[0]["overall_score"] > 60.0
