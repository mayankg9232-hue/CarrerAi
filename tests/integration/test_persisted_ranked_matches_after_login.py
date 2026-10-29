"""Integration test suite verifying persistent ranked job results loading after login.

Phase Acceptance Criteria:
1. Existing persisted match records are fetched correctly after a new login/session.
2. The returned jobs are ordered by descending match score.
3. If match results are missing, the existing profile and database jobs are used to calculate and persist them.
4. A saved, completed resume is not unnecessarily uploaded, parsed or embedded again.
5. A user cannot retrieve another user's match records (IDOR protection).
6. Matching does not begin while the resume is still processing.
7. An empty job dataset returns the correct empty state without hallucinating jobs.
8. Force refresh recalculates and updates matches cleanly.
9. Multiple calculations do not create duplicate match records for the same candidate and job.
10. A previously saved result remains available after session reload.
"""

from datetime import datetime, timezone
import json
import os
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
from app.repositories.job_repo import get_job_repository
from app.repositories.match_repo import get_match_repository
from app.repositories.resume_repo import get_resume_repository
from app.repositories.user_repo import UserRecord, get_user_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.job import Job
from app.schemas.matching import MatchResult, ScoreBreakdown
from app.schemas.resume import (
    CandidateProfile,
    ResumeRecord,
    SkillItem,
)

DISPOSABLE_TEST_DB = "./test_persisted_matches.db"


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


# ------------------------------------------------------------------------------
# Test 1: Existing persisted match records are fetched correctly after login
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persisted_match_records_fetched_after_login(disposable_db: DatabaseManager):
    """1. User logs in; previously generated match records are retrieved immediately."""
    user = await create_test_user(disposable_db, "login_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    # Seed job in DB
    job = Job(
        id="job_persisted_01",
        title="Full Stack Engineer",
        company="Persisted Corp",
        location="Remote",
        description="FastAPI & React engineer role",
        required_skills=["Python", "React"],
        is_active=True,
    )
    await job_repo.create_job(job)

    # Seed candidate profile and resume
    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_persisted_1",
        full_name=user.full_name,
        skills=[SkillItem(name="Python"), SkillItem(name="React")],
    )
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="persisted_cv.pdf",
        storage_key=f"{user.id}/cv.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_persisted_1",
        status="completed",
        is_active=True,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    # Seed pre-existing match in DB
    pre_match = MatchResult(
        candidate_id=profile_id,
        job_id=job.id,
        overall_score=88.5,
        breakdown=ScoreBreakdown(skill_score=90.0, experience_score=85.0, education_score=90.0),
        matched_skills=["Python", "React"],
        missing_skills=[],
        explanation="Candidate demonstrates strong overlap in Python and React.",
        caveat="Pre-calculated verified match",
    )
    await match_repo.save_match(pre_match)

    # Perform API request as authenticated user
    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "completed"
            assert data["is_cached"] is True
            assert len(data["ranked_matches"]) == 1
            first_match = data["ranked_matches"][0]
            assert first_match["match"]["job_id"] == job.id
            assert first_match["match"]["overall_score"] == 88.5
            assert first_match["job"]["title"] == "Full Stack Engineer"
            assert first_match["job"]["company"] == "Persisted Corp"
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 2: The returned jobs are ordered by descending match score
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_returned_jobs_ordered_descending(disposable_db: DatabaseManager):
    """2. Ranked jobs returned from /api/v1/matches are ordered descending by score."""
    user = await create_test_user(disposable_db, "order_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    # Seed 3 jobs
    for i, title in enumerate(["Junior Dev", "Staff Dev", "Senior Dev"]):
        await job_repo.create_job(
            Job(
                id=f"job_order_{i}",
                title=title,
                company="Tech",
                location="Remote",
                description=f"{title} position details",
                is_active=True,
            )
        )

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_order_1",
        full_name=user.full_name,
    )
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="order_cv.pdf",
        storage_key=f"{user.id}/cv.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_order_1",
        status="completed",
        is_active=True,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    # Insert matches out of order: 65.0, 95.0, 80.0
    scores = [65.0, 95.0, 80.0]
    for i, s in enumerate(scores):
        await match_repo.save_match(
            MatchResult(
                candidate_id=profile_id,
                job_id=f"job_order_{i}",
                overall_score=s,
                breakdown=ScoreBreakdown(skill_score=s, experience_score=s, education_score=s),
                matched_skills=[],
                missing_skills=[],
                explanation=f"Rank order score {s}",
                caveat="Order test",
            )
        )

    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            matches = resp.json()["data"]["ranked_matches"]
            assert len(matches) == 3
            scores_returned = [m["match"]["overall_score"] for m in matches]
            assert scores_returned == [95.0, 80.0, 65.0]
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 3: Missing match results are calculated from profile and jobs, then persisted
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_missing_matches_calculated_and_persisted(disposable_db: DatabaseManager):
    """3. If match records do not exist, the system calculates and persists them automatically."""
    user = await create_test_user(disposable_db, "calc_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    # Seed 2 catalog jobs
    await job_repo.create_job(
        Job(
            id="job_calc_01",
            title="Python Architect",
            company="Atlas Tech",
            location="Remote",
            description="Lead backend architecture with Python and FastAPI",
            required_skills=["Python", "FastAPI"],
            is_active=True,
        )
    )
    await job_repo.create_job(
        Job(
            id="job_calc_02",
            title="Frontend Specialist",
            company="Forma",
            location="Remote",
            description="Build interactive UI with React and TypeScript",
            required_skills=["TypeScript", "React"],
            is_active=True,
        )
    )

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_calc_1",
        full_name=user.full_name,
        skills=[SkillItem(name="Python", confidence=0.95), SkillItem(name="FastAPI", confidence=0.9)],
    )
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="calc_cv.pdf",
        storage_key=f"{user.id}/cv.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_calc_1",
        status="completed",
        is_active=True,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    # Verify no matches exist yet
    initial_matches = await match_repo.get_matches_for_candidate(profile_id)
    assert len(initial_matches) == 0

    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # 1. First call calculates and saves
            resp1 = await client.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            assert resp1.status_code == 200
            data1 = resp1.json()["data"]
            assert data1["status"] == "completed"
            assert data1["is_cached"] is False
            assert len(data1["ranked_matches"]) == 2

            # Confirm matches were persisted to SQLite/PostgreSQL table
            persisted_in_db = await match_repo.get_matches_for_candidate(profile_id)
            assert len(persisted_in_db) == 2

            # 2. Second call loads directly from database cache
            resp2 = await client.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            assert resp2.status_code == 200
            data2 = resp2.json()["data"]
            assert data2["is_cached"] is True
            assert len(data2["ranked_matches"]) == 2
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 4: Saved resume is not re-parsed or re-uploaded
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_saved_resume_not_reprocessed(disposable_db: DatabaseManager):
    """4. Stored resume metadata and checksum are untouched when matches are fetched."""
    user = await create_test_user(disposable_db, "untouched_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="checksum_fixed_abc123",
        full_name=user.full_name,
    )
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="stable_cv.pdf",
        storage_key=f"{user.id}/cv.pdf",
        file_size=2048,
        mime_type="application/pdf",
        raw_hash="checksum_fixed_abc123",
        status="completed",
        is_active=True,
        profile_id=profile_id,
        extracted_text="Original extracted text string",
    )
    await resume_repo.create_resume(resume)

    job = Job(id="job_stable", title="DevOps", company="Cloud", location="Remote", description="DevOps role", is_active=True)
    await job_repo.create_job(job)

    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200

            # Verify resume record was not overwritten or corrupted
            saved_resume = await resume_repo.get_resume(resume.id)
            assert saved_resume.raw_hash == "checksum_fixed_abc123"
            assert saved_resume.extracted_text == "Original extracted text string"
            assert saved_resume.filename == "stable_cv.pdf"
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 5: IDOR protection: User A cannot retrieve User B's match records
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idor_cross_user_match_access_denied(disposable_db: DatabaseManager):
    """5. Candidate User A cannot retrieve match records belonging to Candidate User B."""
    user_a = await create_test_user(disposable_db, "user_alice")
    user_b = await create_test_user(disposable_db, "user_bob")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    # Bob's profile and resume
    bob_prof = CandidateProfile(id=f"prof_{uuid.uuid4().hex[:8]}", user_id=user_b.id, raw_hash="hash_bob")
    await resume_repo.save_profile(bob_prof)
    bob_resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user_b.id,
        filename="bob.pdf",
        storage_key=f"{user_b.id}/bob.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_bob",
        status="completed",
        is_active=True,
        profile_id=bob_prof.id,
    )
    await resume_repo.create_resume(bob_resume)

    auth_svc = AuthenticationService()
    alice_token, _ = auth_svc.create_access_token(user_id=user_a.id, role="candidate", email=user_a.email)

    transport = ASGITransport(app=app)
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            # Alice attempts to access Bob's resume matches
            resp = await client.get(
                f"/api/v1/matches?resume_id={bob_resume.id}",
                headers={"Authorization": f"Bearer {alice_token}"},
            )
            assert resp.status_code == 403
            assert resp.json()["error"]["code"] == "FORBIDDEN"
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 6: Matching does not begin while resume is still processing
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_matching_pending_when_resume_is_processing(disposable_db: DatabaseManager):
    """6. When resume is still in processing state, endpoint returns pending status."""
    user = await create_test_user(disposable_db, "pending_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="pending_resume.pdf",
        storage_key=f"{user.id}/pending.pdf",
        file_size=4096,
        mime_type="application/pdf",
        raw_hash="hash_pending_99",
        status="processing",
        is_active=True,
        profile_id=None,
    )
    await resume_repo.create_resume(resume)

    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "processing"
            assert len(data["ranked_matches"]) == 0
            assert "process" in data["message"].lower()
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 7: Empty job dataset returns correct empty state without hallucinated jobs
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_jobs_dataset_returns_empty_catalog(disposable_db: DatabaseManager):
    """7. When catalog has 0 jobs, returns empty_catalog status without fake scores."""
    user = await create_test_user(disposable_db, "empty_jobs_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_empty_catalog",
        full_name=user.full_name,
        skills=[SkillItem(name="Python")],
    )
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="cv.pdf",
        storage_key=f"{user.id}/cv.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_empty_catalog",
        status="completed",
        is_active=True,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    # Ensure 0 jobs in catalog
    await job_repo.clear()

    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    user_repo = SQLUserRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "empty_catalog"
            assert len(data["ranked_matches"]) == 0
            assert "no active job listings" in data["message"].lower()
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 8: Force refresh recalculates and updates matches
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_force_refresh_endpoint(disposable_db: DatabaseManager):
    """8. POST /api/v1/matches/refresh forces fresh calculation."""
    user = await create_test_user(disposable_db, "refresh_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    job = Job(id="job_refresh_01", title="Backend Lead", company="Scale", location="Remote", description="Backend lead position", is_active=True)
    await job_repo.create_job(job)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(id=profile_id, user_id=user.id, raw_hash="hash_refresh_1", full_name=user.full_name)
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="refresh.pdf",
        storage_key=f"{user.id}/refresh.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_refresh_1",
        status="completed",
        is_active=True,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    # Pre-seed an outdated match
    await match_repo.save_match(
        MatchResult(
            candidate_id=profile_id,
            job_id=job.id,
            overall_score=45.0,
            breakdown=ScoreBreakdown(skill_score=45.0, experience_score=45.0, education_score=45.0),
            matched_skills=[],
            missing_skills=[],
            explanation="Initial outdated match score.",
            caveat="Outdated",
        )
    )

    auth_svc = AuthenticationService()
    token, _ = auth_svc.create_access_token(user_id=user.id, role="candidate", email=user.email)

    transport = ASGITransport(app=app)
    user_repo = SQLUserRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo
    app.dependency_overrides[get_user_repository] = lambda: user_repo

    try:
        async with AsyncClient(transport=transport, base_url="http://testserver") as client:
            resp = await client.post("/api/v1/matches/refresh", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "completed"
            assert data["is_cached"] is False
            assert len(data["ranked_matches"]) == 1
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 9: Multiple calls do not create duplicate match records
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_duplicate_match_records_prevented(disposable_db: DatabaseManager):
    """9. Repeated calls to save_match update in place without creating duplicate rows."""
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    user = await create_test_user(disposable_db, "dedup_user")
    profile = CandidateProfile(id=f"prof_{uuid.uuid4().hex[:8]}", user_id=user.id, raw_hash="dedup_hash")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    await resume_repo.save_profile(profile)

    job_repo = SQLJobRepository(db_manager=disposable_db)
    job = Job(id="job_dedup_01", title="Engineer", company="Alpha", location="Remote", description="Software Engineer", is_active=True)
    await job_repo.create_job(job)

    match_res = MatchResult(
        candidate_id=profile.id,
        job_id=job.id,
        overall_score=75.0,
        breakdown=ScoreBreakdown(skill_score=75.0, experience_score=75.0, education_score=75.0),
        matched_skills=["Python"],
        missing_skills=[],
        explanation="Candidate matches engineer role.",
        caveat="First run",
    )
    # Save first time
    await match_repo.save_match(match_res)

    # Save second time with updated score
    match_res.overall_score = 85.0
    match_res.caveat = "Second run"
    match_res.explanation = "Candidate match score updated."
    await match_repo.save_match(match_res)

    # Verify only 1 record exists in database
    records = await match_repo.get_matches_for_candidate(profile.id)
    assert len(records) == 1
    assert records[0]["overall_score"] == 85.0
    assert records[0]["caveat"] == "Second run"


# ------------------------------------------------------------------------------
# Test 10: Previously saved result remains available after session reload
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_saved_matches_survive_session_reload(disposable_db: DatabaseManager):
    """10. Match records remain intact and retrievable across new database connections."""
    user = await create_test_user(disposable_db, "reload_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    job = Job(id="job_reload_01", title="Principal Architect", company="Beta", location="Remote", description="Principal Architect role", is_active=True)
    await job_repo.create_job(job)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    profile = CandidateProfile(id=profile_id, user_id=user.id, raw_hash="hash_reload", full_name=user.full_name)
    await resume_repo.save_profile(profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="reload.pdf",
        storage_key=f"{user.id}/reload.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_reload",
        status="completed",
        is_active=True,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    await match_repo.save_match(
        MatchResult(
            candidate_id=profile_id,
            job_id=job.id,
            overall_score=91.0,
            breakdown=ScoreBreakdown(skill_score=90.0, experience_score=92.0, education_score=91.0),
            matched_skills=["System Design"],
            missing_skills=[],
            explanation="Strong match on system design qualifications.",
            caveat="Persisted across sessions",
        )
    )

    # Close initial db manager and instantiate a brand-new connection pool manager
    await disposable_db.close()
    new_db = DatabaseManager(database_url=f"sqlite:///{DISPOSABLE_TEST_DB}")
    await new_db.initialize()

    try:
        new_match_repo = SQLMatchRepository(db_manager=new_db)
        persisted_items = await new_match_repo.get_ranked_matches_with_jobs(profile_id)
        assert len(persisted_items) == 1
        assert persisted_items[0].match.overall_score == 91.0
        assert persisted_items[0].job.title == "Principal Architect"
        assert persisted_items[0].job.company == "Beta"
    finally:
        await new_db.close()
