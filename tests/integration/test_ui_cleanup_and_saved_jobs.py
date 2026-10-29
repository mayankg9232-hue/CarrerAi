"""Integration tests for UI cleanup, persistent ranked job results, and saved jobs workflows."""

import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
import pytest
from httpx import ASGITransport, AsyncClient

from app.authentication.dependencies import get_current_user
from app.db.connection import DatabaseManager
from app.db.sql_repositories import (
    SQLJobRepository,
    SQLMatchRepository,
    SQLResumeRepository,
    SQLUserRepository,
)
from app.main import app
from app.repositories.job_repo import get_job_repository
from app.repositories.match_repo import get_match_repository
from app.repositories.resume_repo import get_resume_repository
from app.repositories.user_repo import UserRecord
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.job import Job
from app.schemas.matching import MatchResult, ScoreBreakdown
from app.schemas.resume import CandidateProfile, ResumeRecord, SkillItem

DISPOSABLE_UI_TEST_DB = "./test_ui_cleanup_and_saved_jobs.db"


@pytest.fixture(autouse=True)
def clean_test_db():
    """Ensure disposable test SQLite database is cleanly reset."""
    if os.path.exists(DISPOSABLE_UI_TEST_DB):
        try:
            os.remove(DISPOSABLE_UI_TEST_DB)
        except Exception:
            pass
    yield
    if os.path.exists(DISPOSABLE_UI_TEST_DB):
        try:
            os.remove(DISPOSABLE_UI_TEST_DB)
        except Exception:
            pass


@pytest.fixture
async def disposable_db():
    """Create and initialize a disposable database manager."""
    mgr = DatabaseManager(database_url=f"sqlite:///{DISPOSABLE_UI_TEST_DB}")
    await mgr.initialize()
    yield mgr
    await mgr.close()


async def create_persisted_user(db: DatabaseManager, email_prefix: str = "candidate", role: str = "candidate") -> UserRecord:
    """Helper creating a persisted user record satisfying foreign key integrity."""
    user_repo = SQLUserRepository(db_manager=db)
    tag = uuid.uuid4().hex[:6]
    return await user_repo.create_user(
        email=f"{email_prefix}_{tag}@test.local",
        hashed_password="test_hashed_password",
        full_name=f"Test Candidate {tag}",
        role=role,
    )


@pytest.fixture
def sample_job_1():
    return Job(
        id="job_ui_test_001",
        title="Senior AI Platform Engineer",
        company="NeuralDynamics Labs",
        location="Remote (US / CA)",
        description="Build cutting edge distributed AI agent infrastructure using Python and FastAPI.",
        required_skills=["Python", "FastAPI", "Docker", "PostgreSQL"],
        preferred_skills=["Redis", "Kubernetes", "Vector Databases"],
        experience_level="Senior",
        salary="$160,000 - $190,000 / year",
        is_active=True,
    )


@pytest.fixture
def sample_job_2():
    return Job(
        id="job_ui_test_002",
        title="Full Stack Engineer",
        company="Prism Cloud",
        location="San Francisco, CA",
        description="Design intuitive modern dashboards with TypeScript, React, and GraphQL APIs.",
        required_skills=["TypeScript", "React", "Node.js", "GraphQL"],
        preferred_skills=["Next.js", "Tailwind CSS"],
        experience_level="Mid-Senior",
        salary="$140,000 - $170,000 / year",
        is_active=True,
    )


@pytest.mark.asyncio
async def test_navigation_sidebar_contains_only_approved_items():
    """Verify left sidebar in index.html renders only 4 items: AI Job Matching, Saved jobs, Career Agent, Profile & settings."""
    html_path = Path("frontend/index.html")
    assert html_path.exists(), "frontend/index.html must exist"
    content = html_path.read_text(encoding="utf-8")

    # Visible items must be present
    assert 'id="tabOverview"' in content
    assert 'AI Job Matching' in content
    assert 'id="tabSaved"' in content
    assert 'Saved jobs' in content
    assert 'id="tabAgent"' in content
    assert 'Career Agent' in content
    assert 'id="tabSettings"' in content
    assert 'Profile & settings' in content

    # Unwanted navigation items must be hidden from rendered UI
    app_tab_match = re.search(r'<li[^>]*display:\s*none[^>]*>[\s\S]*?id="tabApplications"[\s\S]*?</li>', content)
    assert app_tab_match is not None, "Applications tab must be hidden with display: none !important"

    insights_tab_match = re.search(r'<li[^>]*display:\s*none[^>]*>[\s\S]*?id="tabInsights"[\s\S]*?</li>', content)
    assert insights_tab_match is not None, "Career insights tab must be hidden with display: none !important"

    assessments_tab_match = re.search(r'<li[^>]*display:\s*none[^>]*>[\s\S]*?id="tabAssessments"[\s\S]*?</li>', content)
    assert assessments_tab_match is not None, "Coding Sandbox tab must be hidden with display: none !important"


@pytest.mark.asyncio
async def test_resume_upload_panel_and_candidate_profile_card_in_html():
    """Verify HTML structure supports hiding resume upload panel and showing candidate profile card."""
    content = Path("frontend/index.html").read_text(encoding="utf-8")

    # Main upload panel container
    assert 'id="resumeUploadCard"' in content
    assert 'id="dropzoneCv"' in content
    assert 'id="btnSelectCvFile"' in content

    # Candidate profile summary card
    assert 'id="candidateSummaryBanner"' in content
    assert 'id="summaryCandidateName"' in content
    assert 'id="summaryCandidateAvatar"' in content
    assert 'id="summaryCandidateHeadline"' in content
    assert 'id="summaryCandidateEmail"' in content
    assert 'id="summaryCandidateSkills"' in content
    assert 'id="summaryCandidateBadge"' in content
    assert 'id="btnReplaceResumeCompact"' in content
    assert 'id="btnViewFullProfile"' in content

    # Settings resume management card
    assert 'id="settingsResumeCard"' in content
    assert 'id="settingsResumeFilename"' in content
    assert 'id="btnSettingsUploadResume"' in content


@pytest.mark.asyncio
async def test_saved_jobs_persistence_api_workflow(disposable_db: DatabaseManager, sample_job_1):
    """Verify saving a job persists in database, retrieves via /jobs/saved, and updates count."""
    user = await create_persisted_user(disposable_db, "user_save")
    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", full_name=user.full_name)

    job_repo = SQLJobRepository(db_manager=disposable_db)
    await job_repo.create_job(sample_job_1)

    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_job_repository] = lambda: job_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Save job
        save_resp = await client.post(f"/api/v1/jobs/{sample_job_1.id}/save")
        assert save_resp.status_code == 200
        save_json = save_resp.json()
        assert save_json["success"] is True
        assert save_json["data"]["saved"] is True

        # 2. Query saved jobs list
        list_resp = await client.get("/api/v1/jobs/saved")
        assert list_resp.status_code == 200
        list_json = list_resp.json()
        assert list_json["success"] is True
        assert "jobs" in list_json["data"]
        assert list_json["data"]["count"] == 1
        assert list_json["data"]["jobs"][0]["id"] == sample_job_1.id
        assert list_json["data"]["jobs"][0]["title"] == sample_job_1.title

        # 3. Query saved jobs count
        count_resp = await client.get("/api/v1/jobs/saved/count")
        assert count_resp.status_code == 200
        count_json = count_resp.json()
        assert count_json["success"] is True
        assert count_json["data"]["count"] == 1

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_saved_jobs_idempotent_save(disposable_db: DatabaseManager, sample_job_1):
    """Verify saving the same job multiple times is idempotent."""
    user = await create_persisted_user(disposable_db, "user_idempotent")
    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", full_name=user.full_name)

    job_repo = SQLJobRepository(db_manager=disposable_db)
    await job_repo.create_job(sample_job_1)

    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_job_repository] = lambda: job_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp1 = await client.post(f"/api/v1/jobs/{sample_job_1.id}/save")
        assert resp1.status_code == 200

        resp2 = await client.post(f"/api/v1/jobs/{sample_job_1.id}/save")
        assert resp2.status_code == 200

        count_resp = await client.get("/api/v1/jobs/saved/count")
        assert count_resp.json()["data"]["count"] == 1

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_unsave_job_removes_relationship_without_deleting_shared_job(disposable_db: DatabaseManager, sample_job_1):
    """Verify unsaving removes the user shortlist link but preserves the shared job record in jobs table."""
    user = await create_persisted_user(disposable_db, "user_unsave")
    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", full_name=user.full_name)

    job_repo = SQLJobRepository(db_manager=disposable_db)
    await job_repo.create_job(sample_job_1)

    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_job_repository] = lambda: job_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Save first
        await client.post(f"/api/v1/jobs/{sample_job_1.id}/save")

        # Unsave
        unsave_resp = await client.delete(f"/api/v1/jobs/{sample_job_1.id}/save")
        assert unsave_resp.status_code == 200
        assert unsave_resp.json()["data"]["saved"] is False

        # Verify saved jobs is now empty
        saved_resp = await client.get("/api/v1/jobs/saved")
        assert saved_resp.json()["data"]["count"] == 0
        assert len(saved_resp.json()["data"]["jobs"]) == 0

        # Verify shared job STILL exists in database
        job_resp = await client.get(f"/api/v1/jobs/{sample_job_1.id}")
        assert job_resp.status_code == 200
        assert job_resp.json()["data"]["id"] == sample_job_1.id

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_saved_jobs_user_isolation(disposable_db: DatabaseManager, sample_job_1, sample_job_2):
    """Verify User A's saved jobs are strictly isolated from User B."""
    user_a = await create_persisted_user(disposable_db, "user_a")
    user_b = await create_persisted_user(disposable_db, "user_b")
    principal_a = AuthenticatedPrincipal(id=user_a.id, email=user_a.email, role="candidate", full_name=user_a.full_name)
    principal_b = AuthenticatedPrincipal(id=user_b.id, email=user_b.email, role="candidate", full_name=user_b.full_name)

    job_repo = SQLJobRepository(db_manager=disposable_db)
    await job_repo.create_job(sample_job_1)
    await job_repo.create_job(sample_job_2)

    app.dependency_overrides[get_job_repository] = lambda: job_repo

    # User A saves Job 1
    app.dependency_overrides[get_current_user] = lambda: principal_a
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client_a:
        await client_a.post(f"/api/v1/jobs/{sample_job_1.id}/save")

    # User B saves Job 2
    app.dependency_overrides[get_current_user] = lambda: principal_b
    async with AsyncClient(transport=transport, base_url="http://test") as client_b:
        await client_b.post(f"/api/v1/jobs/{sample_job_2.id}/save")

    # Verify User A only sees Job 1
    app.dependency_overrides[get_current_user] = lambda: principal_a
    async with AsyncClient(transport=transport, base_url="http://test") as client_a:
        resp_a = await client_a.get("/api/v1/jobs/saved")
        jobs_a = [j["id"] for j in resp_a.json()["data"]["jobs"]]
        assert sample_job_1.id in jobs_a
        assert sample_job_2.id not in jobs_a

    # Verify User B only sees Job 2
    app.dependency_overrides[get_current_user] = lambda: principal_b
    async with AsyncClient(transport=transport, base_url="http://test") as client_b:
        resp_b = await client_b.get("/api/v1/jobs/saved")
        jobs_b = [j["id"] for j in resp_b.json()["data"]["jobs"]]
        assert sample_job_2.id in jobs_b
        assert sample_job_1.id not in jobs_b

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_persisted_matches_cache_first_loading(disposable_db: DatabaseManager, sample_job_1):
    """Verify matches endpoint loads previously persisted results with is_cached=True upon page refresh."""
    user = await create_persisted_user(disposable_db, "user_match")
    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", full_name=user.full_name)

    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    # 1. Create candidate profile
    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        full_name=user.full_name,
        email=user.email,
        headline="Senior AI Platform Engineer",
        summary="Experienced engineer with Python and Docker background.",
        skills=[SkillItem(name="Python", category="language", verified=True)],
        experience_years=5.0,
        raw_hash="prof_hash_12345",
    )
    await resume_repo.save_profile(profile)

    # 2. Create completed active resume
    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="Maya_Chen_Resume.pdf",
        status="completed",
        version=1,
        is_active=True,
        profile_id=profile_id,
        storage_key="test_storage_key",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_12345",
    )
    await resume_repo.create_resume(resume)

    # 3. Create job
    await job_repo.create_job(sample_job_1)

    # 4. Create persistent match record
    match = MatchResult(
        candidate_id=profile_id,
        job_id=sample_job_1.id,
        overall_score=89.5,
        breakdown=ScoreBreakdown(skill_score=92.0, experience_score=88.0, education_score=90.0),
        matched_skills=["Python", "FastAPI", "Docker", "PostgreSQL"],
        missing_skills=[],
        explanation="Strong technical skill overlap in Python, Docker, and PostgreSQL with high role alignment.",
    )
    await match_repo.save_match(match)

    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/api/v1/matches")
        assert resp.status_code == 200
        data = resp.json()["data"]

        # Cache-first confirmation
        assert data["is_cached"] is True
        assert data["total_matches"] >= 1
        assert data["ranked_matches"][0]["job"]["id"] == sample_job_1.id
        assert data["ranked_matches"][0]["match"]["overall_score"] == 89.5

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_saved_jobs_404_when_job_does_not_exist(disposable_db: DatabaseManager):
    """Verify attempting to save a non-existent job returns HTTP 404."""
    user = await create_persisted_user(disposable_db, "user_404")
    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", full_name=user.full_name)
    job_repo = SQLJobRepository(db_manager=disposable_db)

    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_job_repository] = lambda: job_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/api/v1/jobs/non_existent_job_id_999/save")
        assert resp.status_code == 404

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_saved_jobs_pagination(disposable_db: DatabaseManager):
    """Verify saved jobs endpoint respects limit and offset pagination."""
    user = await create_persisted_user(disposable_db, "user_page")
    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", full_name=user.full_name)
    job_repo = SQLJobRepository(db_manager=disposable_db)

    # Create 3 jobs and save all 3
    for i in range(3):
        job = Job(
            id=f"job_page_{i}",
            title=f"Engineer {i}",
            company="Acme Corp",
            location="Remote",
            description=f"Job description {i}",
            is_active=True,
        )
        await job_repo.create_job(job)
        await job_repo.save_job(user.id, job.id)

    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_job_repository] = lambda: job_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # Page 1 (limit 2)
        resp_p1 = await client.get("/api/v1/jobs/saved?limit=2&offset=0")
        assert resp_p1.status_code == 200
        data_p1 = resp_p1.json()["data"]
        assert data_p1["count"] == 3
        assert len(data_p1["jobs"]) == 2

        # Page 2 (offset 2)
        resp_p2 = await client.get("/api/v1/jobs/saved?limit=2&offset=2")
        assert resp_p2.status_code == 200
        data_p2 = resp_p2.json()["data"]
        assert data_p2["count"] == 3
        assert len(data_p2["jobs"]) == 1

    app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_saved_jobs_count_zero_for_new_user(disposable_db: DatabaseManager):
    """Verify new user without saved jobs returns count=0 and empty list."""
    user = await create_persisted_user(disposable_db, "user_zero")
    principal = AuthenticatedPrincipal(id=user.id, email=user.email, role="candidate", full_name=user.full_name)
    job_repo = SQLJobRepository(db_manager=disposable_db)

    app.dependency_overrides[get_current_user] = lambda: principal
    app.dependency_overrides[get_job_repository] = lambda: job_repo

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        count_resp = await client.get("/api/v1/jobs/saved/count")
        assert count_resp.status_code == 200
        assert count_resp.json()["data"]["count"] == 0

        list_resp = await client.get("/api/v1/jobs/saved")
        assert list_resp.status_code == 200
        assert list_resp.json()["data"]["count"] == 0
        assert len(list_resp.json()["data"]["jobs"]) == 0

    app.dependency_overrides.clear()
