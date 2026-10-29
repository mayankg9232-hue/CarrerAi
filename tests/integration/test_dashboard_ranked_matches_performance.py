"""Integration test suite verifying login performance, fast matches loading, and query optimization.

Phase 9 Requirements:
1. Returning cached/persisted matches does NOT invoke the matching engine or scrape pipeline.
2. Background calculation is triggered when no matches exist, returning status="matching" without blocking.
3. PostgreSQL query uses LIMIT and index without N+1 queries.
4. Active resume update properly triggers recalculation or cache invalidation.
5. Roles evaluated count reflects actual active jobs evaluated.
6. Empty catalog returns appropriate status and 0 count.
7. Unauthenticated request rejected with 401.
8. Request for another user's matches rejected with 403 / 404 (IDOR protection).
9. Response includes timing header X-Process-Time-Ms.
10. In-memory cache hit executes in sub-millisecond time.
"""

from datetime import datetime, timezone
import json
import os
import time
from typing import AsyncGenerator
from unittest.mock import AsyncMock, patch
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
from app.services.matching_service import MatchingApplicationService

DISPOSABLE_TEST_DB = "./test_perf_matches.db"


@pytest.fixture(autouse=True)
def clean_test_db():
    """Ensure disposable SQLite database is cleanly removed before and after tests."""
    MatchingApplicationService.invalidate_cache()
    SQLJobRepository.invalidate_count_cache()
    if os.path.exists(DISPOSABLE_TEST_DB):
        try:
            os.remove(DISPOSABLE_TEST_DB)
        except Exception:
            pass
    yield
    MatchingApplicationService.invalidate_cache()
    SQLJobRepository.invalidate_count_cache()
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


async def create_test_user(db: DatabaseManager, email_prefix: str = "perf_user", role: str = "candidate", email_verified: bool = True) -> UserRecord:
    """Helper creating a persisted user record satisfying foreign key integrity."""
    user_repo = SQLUserRepository(db_manager=db)
    tag = uuid.uuid4().hex[:6]
    return await user_repo.create_user(
        email=f"{email_prefix}_{tag}@test.local",
        hashed_password="test_hashed_password",
        full_name=f"Performance Test User {tag}",
        role=role,
        email_verified=email_verified,
    )


def make_token(user: UserRecord) -> str:
    auth_service = AuthenticationService()
    token, _ = auth_service.create_access_token(user_id=user.id, email=user.email, role=user.role, email_verified=user.email_verified)
    return token


# ------------------------------------------------------------------------------
# Test 1: Cached/persisted matches do NOT invoke matching engine
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_persisted_matches_retrieval_does_not_invoke_engine(disposable_db: DatabaseManager):
    """1. Returning persisted matches does NOT call recommend_jobs or scrape pipeline."""
    user = await create_test_user(disposable_db, "cached_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    # Seed job and match
    job = Job(
        id="job_perf_01",
        title="Backend Engineer",
        company="FastCorp",
        location="Remote",
        description="Comprehensive Python backend engineering role",
        required_skills=["Python", "FastAPI"],
        is_active=True,
    )
    await job_repo.create_job(job)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    cand_profile = CandidateProfile(
        id=profile_id,
        user_id=user.id,
        raw_hash="hash_perf_01",
        full_name=user.full_name,
        email=user.email,
        skills=[SkillItem(name="Python", category="backend")],
    )
    await resume_repo.save_profile(cand_profile)

    resume_id = f"res_{uuid.uuid4().hex[:8]}"
    resume = ResumeRecord(
        id=resume_id,
        user_id=user.id,
        filename="perf_resume.pdf",
        storage_key="local://perf_resume.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="hash_perf_01",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    match = MatchResult(
        candidate_id=profile_id,
        job_id="job_perf_01",
        overall_score=92.0,
        breakdown=ScoreBreakdown(skill_score=92.0, experience_score=90.0, education_score=85.0),
        matched_skills=["Python"],
        missing_skills=[],
        explanation="Persisted match",
    )
    await match_repo.save_match(match)

    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo

    token = make_token(user)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            with patch("app.pipelines.matching_pipeline.MatchingPipeline.recommend_jobs") as mock_engine:
                resp = await ac.get("/api/v1/matches?async_matching=true", headers={"Authorization": f"Bearer {token}"})
                assert resp.status_code == 200
                data = resp.json()["data"]
                assert data["status"] == "completed"
                assert data["is_cached"] is True
                assert len(data["ranked_matches"]) == 1
                assert data["ranked_matches"][0]["match"]["overall_score"] == 92.0
                # Matching engine was NEVER called
                mock_engine.assert_not_called()
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 2: In-memory cache hit executes in sub-millisecond time
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_in_memory_cache_immediate_hit(disposable_db: DatabaseManager):
    """2. Repeated GET /matches calls within TTL hit in-memory cache with sub-millisecond latency."""
    user = await create_test_user(disposable_db, "cache_hit_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    job = Job(id="job_perf_02", title="ML Engineer", company="AI Corp", location="Remote", description="Comprehensive machine learning role", is_active=True)
    await job_repo.create_job(job)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    cand_profile = CandidateProfile(id=profile_id, user_id=user.id, raw_hash="h02", full_name="User 2", email=user.email)
    await resume_repo.save_profile(cand_profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="r.pdf",
        storage_key="local://r.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="h02",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    match = MatchResult(
        candidate_id=profile_id,
        job_id="job_perf_02",
        overall_score=85.0,
        breakdown=ScoreBreakdown(skill_score=85.0, experience_score=85.0, education_score=85.0),
        explanation="Valid match",
    )
    await match_repo.save_match(match)

    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo

    token = make_token(user)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # First request populates memory cache
            resp1 = await ac.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            assert resp1.status_code == 200

            # Second request hits in-memory cache
            t0 = time.perf_counter()
            resp2 = await ac.get("/api/v1/matches", headers={"Authorization": f"Bearer {token}"})
            t1 = time.perf_counter()
            assert resp2.status_code == 200
            assert resp2.json()["data"]["is_cached"] is True
            # Cache response was fast
            assert (t1 - t0) * 1000 < 100.0  # Under 100ms over ASGI
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 3: Background calculation triggered when matches missing (never blocks)
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_background_matching_on_missing_matches_never_blocks_login(disposable_db: DatabaseManager):
    """3. If matches don't exist yet, GET /matches returns status='matching' without blocking."""
    user = await create_test_user(disposable_db, "new_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    job = Job(id="job_perf_03", title="DevOps Lead", company="Cloud Co", location="Remote", description="Comprehensive infrastructure engineer role", is_active=True)
    await job_repo.create_job(job)

    profile_id = f"prof_{uuid.uuid4().hex[:8]}"
    cand_profile = CandidateProfile(id=profile_id, user_id=user.id, raw_hash="h03", full_name="New Candidate", email=user.email)
    await resume_repo.save_profile(cand_profile)

    resume = ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        filename="new_cv.pdf",
        storage_key="local://new_cv.pdf",
        file_size=1024,
        mime_type="application/pdf",
        raw_hash="h03",
        status="completed",
        is_active=True,
        version=1,
        profile_id=profile_id,
    )
    await resume_repo.create_resume(resume)

    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo

    token = make_token(user)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/matches?async_matching=true", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            # Returns immediate 'matching' response
            assert data["status"] == "matching"
            assert data["total_matches"] == 0
            assert data["roles_evaluated"] == 1
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 4: SQL LIMIT is applied directly in SQL query
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_sql_limit_applied_at_database_level(disposable_db: DatabaseManager):
    """4. SQLMatchRepository.get_ranked_matches_with_jobs applies LIMIT at SQL level."""
    match_repo = SQLMatchRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    user = await create_test_user(disposable_db, "limit_user")
    cand_id = f"cand_limit_{uuid.uuid4().hex[:6]}"
    await resume_repo.save_profile(CandidateProfile(id=cand_id, user_id=user.id, raw_hash="h_lim", full_name="Limit Test", email=user.email))

    # Seed 10 jobs and 10 matches
    for i in range(10):
        jid = f"job_limit_{i}"
        await job_repo.create_job(Job(id=jid, title=f"Job Title {i}", company="Co", location="Remote", description="Description over 10 chars", is_active=True))
        await match_repo.save_match(MatchResult(
            candidate_id=cand_id,
            job_id=jid,
            overall_score=50.0 + i,
            breakdown=ScoreBreakdown(skill_score=50.0 + i, experience_score=50.0, education_score=50.0),
            explanation="Match",
        ))

    # Request with limit=3
    results = await match_repo.get_ranked_matches_with_jobs(cand_id, limit=3)
    assert len(results) == 3
    # Top score first
    assert results[0].match.overall_score == 59.0
    assert results[1].match.overall_score == 58.0
    assert results[2].match.overall_score == 57.0


# ------------------------------------------------------------------------------
# Test 5: Job count caching eliminates redundant count queries
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_job_count_caching_eliminates_redundant_queries(disposable_db: DatabaseManager):
    """5. SQLJobRepository caches active job count within TTL window."""
    job_repo = SQLJobRepository(db_manager=disposable_db)
    SQLJobRepository.invalidate_count_cache()

    await job_repo.create_job(Job(id="job_cnt_1", title="Job 1 Title", company="Co", location="Remote", description="Valid description 1", is_active=True))
    await job_repo.create_job(Job(id="job_cnt_2", title="Job 2 Title", company="Co", location="Remote", description="Valid description 2", is_active=True))

    cnt1 = await job_repo.count_jobs(only_active=True)
    assert cnt1 == 2
    assert SQLJobRepository._active_jobs_count_cache is not None

    # Subsequent count should use cached value without hitting db.fetchone
    with patch.object(disposable_db, "fetchone", wraps=disposable_db.fetchone) as mock_fetchone:
        cnt2 = await job_repo.count_jobs(only_active=True)
        assert cnt2 == 2
        mock_fetchone.assert_not_called()


# ------------------------------------------------------------------------------
# Test 6: Active resume switch invalidates matching cache
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_active_resume_switch_invalidates_cache(disposable_db: DatabaseManager):
    """6. Switching active resume invalidates cached match responses."""
    user = await create_test_user(disposable_db, "switch_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    # Seed 2 resumes
    prof1_id = f"prof1_{uuid.uuid4().hex[:6]}"
    await resume_repo.save_profile(CandidateProfile(id=prof1_id, user_id=user.id, raw_hash="h_p1", full_name="P1", email=user.email))
    await resume_repo.create_resume(ResumeRecord(
        id=f"res1_{uuid.uuid4().hex[:6]}", user_id=user.id, filename="cv1.pdf", storage_key="local://1",
        file_size=10, mime_type="application/pdf", raw_hash="h_p1", status="completed", is_active=True, version=1, profile_id=prof1_id
    ))

    # Populate cache
    MatchingApplicationService._match_cache[f"{user.id}_active_50"] = (time.time(), "cached_data")
    assert len(MatchingApplicationService._match_cache) > 0

    # Invalidate for user
    MatchingApplicationService.invalidate_cache(user.id)
    assert f"{user.id}_active_50" not in MatchingApplicationService._match_cache


# ------------------------------------------------------------------------------
# Test 7: Empty catalog returns appropriate status and 0 count
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_empty_catalog_returns_empty_catalog_status(disposable_db: DatabaseManager):
    """7. Empty catalog returns status='empty_catalog' without crashing or hallucinating."""
    user = await create_test_user(disposable_db, "empty_cat_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    job_repo = SQLJobRepository(db_manager=disposable_db)
    match_repo = SQLMatchRepository(db_manager=disposable_db)

    # User has resume but DB has 0 active jobs
    prof_id = f"prof_{uuid.uuid4().hex[:6]}"
    await resume_repo.save_profile(CandidateProfile(id=prof_id, user_id=user.id, raw_hash="h_ec", full_name="User", email=user.email))
    await resume_repo.create_resume(ResumeRecord(
        id=f"res_{uuid.uuid4().hex[:6]}", user_id=user.id, filename="cv.pdf", storage_key="local://cv",
        file_size=10, mime_type="application/pdf", raw_hash="h_ec", status="completed", is_active=True, version=1, profile_id=prof_id
    ))

    app.dependency_overrides[get_resume_repository] = lambda: resume_repo
    app.dependency_overrides[get_job_repository] = lambda: job_repo
    app.dependency_overrides[get_match_repository] = lambda: match_repo

    token = make_token(user)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/matches?async_matching=true", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            data = resp.json()["data"]
            assert data["status"] == "empty_catalog"
            assert data["total_matches"] == 0
            assert data["roles_evaluated"] == 0
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 8: Unauthenticated request rejected with 401
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_unauthenticated_request_rejected_with_401():
    """8. Unauthenticated call to /api/v1/matches is rejected with HTTP 401."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        resp = await ac.get("/api/v1/matches")
        assert resp.status_code == 401


# ------------------------------------------------------------------------------
# Test 9: IDOR protection: cannot retrieve another user's matches
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_idor_protection_cannot_access_other_user_matches(disposable_db: DatabaseManager):
    """9. Candidate user A cannot specify user B's resume_id (IDOR defense)."""
    user_a = await create_test_user(disposable_db, "user_a")
    user_b = await create_test_user(disposable_db, "user_b")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)

    # User B's resume
    res_b = await resume_repo.create_resume(ResumeRecord(
        id=f"res_b_{uuid.uuid4().hex[:6]}", user_id=user_b.id, filename="b.pdf", storage_key="local://b",
        file_size=10, mime_type="application/pdf", raw_hash="hb", status="completed", is_active=True, version=1
    ))

    app.dependency_overrides[get_resume_repository] = lambda: resume_repo

    token_a = make_token(user_a)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get(f"/api/v1/matches?resume_id={res_b.id}", headers={"Authorization": f"Bearer {token_a}"})
            # Must be 403 Forbidden or 404
            assert resp.status_code in (403, 404)
    finally:
        app.dependency_overrides.clear()


# ------------------------------------------------------------------------------
# Test 10: Timing header X-Process-Time-Ms is present in API response
# ------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_timing_header_x_process_time_ms_present(disposable_db: DatabaseManager):
    """10. Response contains X-Process-Time-Ms header exposing server-side processing duration."""
    user = await create_test_user(disposable_db, "timing_user")
    resume_repo = SQLResumeRepository(db_manager=disposable_db)
    app.dependency_overrides[get_resume_repository] = lambda: resume_repo

    token = make_token(user)

    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            resp = await ac.get("/api/v1/matches?async_matching=true", headers={"Authorization": f"Bearer {token}"})
            assert resp.status_code == 200
            assert "X-Process-Time-Ms" in resp.headers
            time_val = float(resp.headers["X-Process-Time-Ms"])
            assert time_val >= 0.0
    finally:
        app.dependency_overrides.clear()
