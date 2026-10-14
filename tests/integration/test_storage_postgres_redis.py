"""Pre-Phase 7 PostgreSQL and Redis Storage & Caching Integration Test Suite.

Validates the 8 core infrastructure-readiness criteria:
1. Successful database connection, schema initialization, and User persistence.
2. Schema constraints (unique email), ACID transaction commit and rollback behavior.
3. Duplicate job prevention via SHA-256 content hash unique constraints.
4. Redis cache-aside miss followed by cache hit.
5. TTL expiration and cache invalidation on write/mutation.
6. Redis unavailable/outage resilience (graceful degradation without HTTP 500).
7. Candidate profile data isolation across tenant boundaries and foreign keys.
8. Match record durability, score breakdown persistence, and caveat preservation.
"""

import asyncio
from datetime import datetime, timezone
import os
import pytest
from httpx import AsyncClient

from app.cache.redis_client import AsyncRedisClient
from app.cache.service import CacheService
from app.core.config import Settings
from app.db.connection import DatabaseManager, UniqueConstraintViolationError
from app.db.sql_repositories import (
    SQLJobRepository,
    SQLMatchRepository,
    SQLResumeRepository,
    SQLUserRepository,
)
from app.matching.explain import MatchExplainer
from app.schemas.job import Job
from app.schemas.matching import MatchResult, ScoreBreakdown
from app.schemas.resume import CandidateProfile, EducationItem, ExperienceItem, SkillItem

TEST_DB_FILE = "./test_storage_disposable.db"
UPSTASH_REDIS_URL = "rediss://default:gQAAAAAAA5EXAAIgcDIyZGE5ODhhYWY3ZDM0MmRmYWMzNzBhOTc4YTM4NGU4Yw@cheerful-cow-233751.upstash.io:6379"


@pytest.fixture(autouse=True)
def cleanup_test_db():
    """Ensure clean disposable test database for isolation."""
    if os.path.exists(TEST_DB_FILE):
        try:
            os.remove(TEST_DB_FILE)
        except Exception:
            pass
    yield
    if os.path.exists(TEST_DB_FILE):
        try:
            os.remove(TEST_DB_FILE)
        except Exception:
            pass


@pytest.fixture
def db_manager():
    """Create isolated DatabaseManager pointing to disposable test database."""
    manager = DatabaseManager(database_url=f"sqlite:///{TEST_DB_FILE}")
    return manager


@pytest.fixture
def cache_service():
    """Create CacheService connected to Upstash Redis over TLS."""
    settings = Settings(
        REDIS_URL=UPSTASH_REDIS_URL,
        REDIS_ENABLED=True,
        REDIS_CONNECT_TIMEOUT_SECONDS=3.0,
    )
    return CacheService(settings=settings)


# ==============================================================================
# TEST 1: DATABASE CONNECTION & USER PERSISTENCE
# ==============================================================================
@pytest.mark.asyncio
async def test_db_connection_and_user_persistence(db_manager: DatabaseManager):
    """Test 1: Verifies database connection, table initialization, and user persistence."""
    repo = SQLUserRepository(db_manager=db_manager)

    user = await repo.create_user(
        email="developer@example.com",
        hashed_password="hashed_bcrypt_secret",
        full_name="Mayank Developer",
        role="candidate",
    )

    assert user.id.startswith("usr_")
    assert user.email == "developer@example.com"
    assert user.role == "candidate"

    # Query by ID
    by_id = await repo.get_by_id(user.id)
    assert by_id is not None
    assert by_id.email == "developer@example.com"

    # Query by Email
    by_email = await repo.get_by_email("developer@example.com")
    assert by_email is not None
    assert by_email.id == user.id

    # Update User
    updated = await repo.update_user(user.id, full_name="Mayank Senior Developer")
    assert updated.full_name == "Mayank Senior Developer"


# ==============================================================================
# TEST 2: SCHEMA CONSTRAINTS & TRANSACTION ROLLBACK
# ==============================================================================
@pytest.mark.asyncio
async def test_db_schema_constraints_and_transactions(db_manager: DatabaseManager):
    """Test 2: Verifies unique email constraint and ACID transaction rollback on error."""
    repo = SQLUserRepository(db_manager=db_manager)

    # 1. Unique email constraint
    await repo.create_user(
        email="unique@example.com",
        hashed_password="pass1",
        full_name="User One",
    )
    with pytest.raises(ValueError) as exc_info:
        await repo.create_user(
            email="unique@example.com",
            hashed_password="pass2",
            full_name="User Two",
        )
    assert "already exists" in str(exc_info.value)

    # 2. Transaction Rollback Behavior
    try:
        async with db_manager.transaction() as tx:
            res = tx.execute(
                "INSERT INTO users (id, email, hashed_password, full_name, role, is_active, created_at) "
                "VALUES (?, ?, ?, ?, ?, 1, ?)",
                ("usr_tx_01", "tx_test@example.com", "hash", "TX User", "candidate", "2026-10-14T12:00:00Z"),
            )
            if asyncio.iscoroutine(res):
                await res
            # Deliberately raise error inside transaction
            raise RuntimeError("Simulated transaction failure")
    except RuntimeError:
        pass

    # Confirm record was rolled back and not committed
    rolled_back_user = await repo.get_by_id("usr_tx_01")
    assert rolled_back_user is None


# ==============================================================================
# TEST 3: DUPLICATE JOB PREVENTION VIA UNIQUE CONTENT HASH
# ==============================================================================
@pytest.mark.asyncio
async def test_duplicate_job_prevention_and_deduplication(db_manager: DatabaseManager):
    """Test 3: Enforces unique content_hash constraint preventing duplicate jobs."""
    repo = SQLJobRepository(db_manager=db_manager)

    job1 = Job(
        id="job_dup_01",
        title="Python Backend Engineer",
        company="FastCloud Labs",
        location="Remote",
        description="Developing high-throughput microservices in FastAPI.",
        required_skills=["Python", "FastAPI"],
        content_hash="sha256_job_identical_hash_12345",
        source_url="https://approved-board.example.com/jobs/1",
        is_active=True,
    )

    saved1 = await repo.create_job(job1)
    assert saved1.id == "job_dup_01"

    # Second insertion with identical content_hash updates last_seen_at instead of duplicating
    job2 = Job(
        id="job_dup_02",
        title="Python Backend Engineer",
        company="FastCloud Labs",
        location="Remote",
        description="Developing high-throughput microservices in FastAPI.",
        required_skills=["Python", "FastAPI"],
        content_hash="sha256_job_identical_hash_12345",
        source_url="https://approved-board.example.com/jobs/1",
        is_active=True,
    )

    saved2 = await repo.create_job(job2)
    assert saved2.id == "job_dup_01"  # Returns existing job

    # Confirm database only contains 1 job
    all_jobs = await repo.list_all()
    assert len(all_jobs) == 1
    assert all_jobs[0].id == "job_dup_01"


# ==============================================================================
# TEST 4: REDIS CACHE MISS FOLLOWED BY CACHE HIT
# ==============================================================================
@pytest.mark.asyncio
async def test_redis_cache_miss_followed_by_cache_hit(cache_service: CacheService):
    """Test 4: Verifies cache-aside pattern: miss on first request, hit on subsequent request."""
    # Ensure cache is healthy
    is_live = await cache_service.ping()
    if not is_live:
        pytest.skip("Upstash Redis connection not available in this environment.")

    query_params = {"q": "fastapi", "location": "remote", "limit": 10}

    # 1. First read -> Cache MISS
    miss_result = await cache_service.get_cached_jobs_query(**query_params)
    assert miss_result is None

    # 2. Populate cache with mock jobs
    mock_jobs = [
        Job(
            id="job_cached_01",
            title="FastAPI Specialist",
            company="Cloud APIs Inc",
            location="Remote",
            description="High-speed API engineering.",
            required_skills=["Python", "FastAPI"],
            content_hash="hash_c1",
            is_active=True,
        )
    ]
    set_success = await cache_service.set_cached_jobs_query(mock_jobs, ttl=60, **query_params)
    assert set_success is True

    # 3. Second read -> Cache HIT
    hit_result = await cache_service.get_cached_jobs_query(**query_params)
    assert hit_result is not None
    assert len(hit_result) == 1
    assert hit_result[0].id == "job_cached_01"
    assert hit_result[0].title == "FastAPI Specialist"

    # Clean up test key
    await cache_service.invalidate_jobs_cache()


# ==============================================================================
# TEST 5: TTL EXPIRATION AND CACHE INVALIDATION
# ==============================================================================
@pytest.mark.asyncio
async def test_redis_ttl_expiration_and_invalidation(cache_service: CacheService):
    """Test 5: Verifies that cached records expire after TTL and are invalidated on write."""
    is_live = await cache_service.ping()
    if not is_live:
        pytest.skip("Upstash Redis connection not available in this environment.")

    params = {"q": "ephemeral_ttl"}
    jobs = [
        Job(
            id="job_ttl_01",
            title="Temporary Role",
            company="Ephemeral Corp",
            location="Remote",
            description="Testing TTL expiration.",
            required_skills=["Python"],
            content_hash="hash_ttl_1",
            is_active=True,
        )
    ]

    # Set with 1-second TTL
    await cache_service.set_cached_jobs_query(jobs, ttl=1, **params)
    cached_immediate = await cache_service.get_cached_jobs_query(**params)
    assert cached_immediate is not None

    # Wait 1.3 seconds for TTL to elapse
    await asyncio.sleep(1.3)
    cached_after_ttl = await cache_service.get_cached_jobs_query(**params)
    assert cached_after_ttl is None

    # Test explicit invalidation
    await cache_service.set_cached_jobs_query(jobs, ttl=300, **params)
    assert await cache_service.get_cached_jobs_query(**params) is not None

    invalidated_count = await cache_service.invalidate_jobs_cache()
    assert invalidated_count >= 1
    assert await cache_service.get_cached_jobs_query(**params) is None


# ==============================================================================
# TEST 6: REDIS UNAVAILABLE GRACEFUL DEGRADATION
# ==============================================================================
@pytest.mark.asyncio
async def test_redis_outage_graceful_degradation():
    """Test 6: Verifies that application continues without error when Redis is down/unreachable."""
    failing_settings = Settings(
        REDIS_URL="rediss://default:bad_token@127.0.0.1:9999",  # Unreachable port
        REDIS_ENABLED=True,
        REDIS_CONNECT_TIMEOUT_SECONDS=0.5,
    )
    degraded_cache = CacheService(settings=failing_settings)

    # Cache read should return None (miss) rather than raising exception
    read_result = await degraded_cache.get_cached_jobs_query(q="test")
    assert read_result is None

    # Cache write should return False safely without crashing
    dummy_job = Job(
        id="job_resilience",
        title="Resilient Dev",
        company="Robust Systems",
        description="Handles network partitions.",
        content_hash="resilient_hash",
    )
    write_result = await degraded_cache.set_cached_jobs_query([dummy_job], q="test")
    assert write_result is False

    # Invalidation returns 0 without crashing
    inval_count = await degraded_cache.invalidate_jobs_cache()
    assert inval_count == 0


# ==============================================================================
# TEST 7: CANDIDATE PROFILE ISOLATION & DEDUPLICATION
# ==============================================================================
@pytest.mark.asyncio
async def test_candidate_profile_isolation_and_deduplication(db_manager: DatabaseManager):
    """Test 7: Verifies candidate profile ownership isolation and CV deduplication."""
    user_repo = SQLUserRepository(db_manager=db_manager)
    resume_repo = SQLResumeRepository(db_manager=db_manager)

    user_a = await user_repo.create_user("alice@example.com", "pass_a", "Alice Candidate")
    user_b = await user_repo.create_user("bob@example.com", "pass_b", "Bob Candidate")

    profile_a = CandidateProfile(
        id="prof_alice_01",
        user_id=user_a.id,
        raw_hash="sha256_alice_resume_cv",
        full_name="Alice Candidate",
        email="alice@example.com",
        summary="Experienced Python Engineer.",
        skills=[SkillItem(name="Python", category="language", confidence=0.98)],
    )
    await resume_repo.save_profile(profile_a)

    profile_b = CandidateProfile(
        id="prof_bob_01",
        user_id=user_b.id,
        raw_hash="sha256_bob_resume_cv",
        full_name="Bob Candidate",
        email="bob@example.com",
        summary="Backend developer with Docker experience.",
        skills=[SkillItem(name="Docker", category="tool", confidence=0.95)],
    )
    await resume_repo.save_profile(profile_b)

    # Candidate isolation: User A only sees User A's profile
    alice_profiles = await resume_repo.get_by_user_id(user_a.id)
    assert len(alice_profiles) == 1
    assert alice_profiles[0].id == "prof_alice_01"
    assert alice_profiles[0].user_id == user_a.id

    bob_profiles = await resume_repo.get_by_user_id(user_b.id)
    assert len(bob_profiles) == 1
    assert bob_profiles[0].id == "prof_bob_01"

    # Deduplication test: re-uploading identical CV hash for Alice returns existing profile
    dup_profile = CandidateProfile(
        id="prof_alice_02",  # Different ID, same user + hash
        user_id=user_a.id,
        raw_hash="sha256_alice_resume_cv",
        full_name="Alice Candidate",
    )
    saved_dup = await resume_repo.save_profile(dup_profile)
    assert saved_dup.id == "prof_alice_01"  # Preserves initial profile ID


# ==============================================================================
# TEST 8: MATCH RECORD PERSISTENCE & LEGAL CAVEAT PRESERVATION
# ==============================================================================
@pytest.mark.asyncio
async def test_match_records_persistence_and_caveat_integrity(db_manager: DatabaseManager):
    """Test 8: Verifies match calculations, score breakdowns, and mandatory caveats are persisted."""
    user_repo = SQLUserRepository(db_manager=db_manager)
    job_repo = SQLJobRepository(db_manager=db_manager)
    resume_repo = SQLResumeRepository(db_manager=db_manager)
    match_repo = SQLMatchRepository(db_manager=db_manager)

    user = await user_repo.create_user("c@example.com", "p", "Match Test User")
    prof = await resume_repo.save_profile(
        CandidateProfile(
            id="prof_m_01",
            user_id=user.id,
            raw_hash="hash_m",
            full_name="Test Candidate",
            skills=[SkillItem(name="Python", category="language")],
        )
    )
    job = await job_repo.create_job(
        Job(
            id="job_m_01",
            title="Python Dev",
            company="Tech Corp",
            description="Python API developer.",
            content_hash="hash_job_m",
        )
    )

    match_result = MatchResult(
        candidate_id=prof.id,
        job_id=job.id,
        overall_score=85.5,
        breakdown=ScoreBreakdown(
            skill_score=90.0,
            experience_score=80.0,
            education_score=85.0,
        ),
        matched_skills=["Python"],
        missing_skills=["FastAPI"],
        explanation="Candidate demonstrates strong Python proficiency aligning with requirements.",
        caveat=MatchExplainer.MANDATORY_DISCLAIMER,
    )

    # Persist match result
    saved = await match_repo.save_match(match_result)
    assert saved.overall_score == 85.5

    # Retrieve matches for candidate
    records = await match_repo.get_matches_for_candidate(prof.id)
    assert len(records) == 1
    assert records[0]["overall_score"] == 85.5
    assert records[0]["caveat"] == MatchExplainer.MANDATORY_DISCLAIMER


# ==============================================================================
# TEST 9: LIVE NEON POSTGRESQL PERSISTENCE AND REPOSITORY INTEGRATION
# ==============================================================================
@pytest.mark.asyncio
async def test_live_neon_postgresql_persistence_and_isolation():
    """Test 9: Verifies end-to-end repository persistence on live Neon Serverless PostgreSQL."""
    settings = Settings()
    if not settings.DATABASE_URL or not ("postgres://" in settings.DATABASE_URL or "postgresql://" in settings.DATABASE_URL):
        pytest.skip("PostgreSQL DATABASE_URL not configured.")

    pg_mgr = DatabaseManager(database_url=settings.DATABASE_URL)
    await pg_mgr.initialize()

    user_repo = SQLUserRepository(db_manager=pg_mgr)
    job_repo = SQLJobRepository(db_manager=pg_mgr)
    resume_repo = SQLResumeRepository(db_manager=pg_mgr)
    match_repo = SQLMatchRepository(db_manager=pg_mgr)

    test_uid = f"usr_neon_{datetime.now(timezone.utc).strftime('%H%M%S')}"
    test_email = f"neon_test_{test_uid}@example.com"
    test_job_id = f"job_neon_{test_uid}"

    try:
        # 1. Create User
        user = await user_repo.create_user(test_email, "neon_pass", "Neon Integration Candidate")
        assert user.email == test_email

        # 2. Save Profile
        profile = CandidateProfile(
            id=f"prof_{test_uid}",
            user_id=user.id,
            raw_hash=f"hash_{test_uid}",
            full_name="Neon Candidate",
            email=test_email,
            skills=[SkillItem(name="PostgreSQL", category="database")],
        )
        saved_prof = await resume_repo.save_profile(profile)
        assert saved_prof.id == profile.id

        # 3. Create Job
        job = Job(
            id=test_job_id,
            title="Database Reliability Engineer",
            company="Neon Systems",
            location="Remote",
            description="Production PostgreSQL reliability engineering.",
            required_skills=["PostgreSQL", "Python"],
            content_hash=f"content_hash_{test_uid}",
            is_active=True,
        )
        saved_job = await job_repo.create_job(job)
        assert saved_job.id == test_job_id

        # 4. Save and Verify Match
        match = MatchResult(
            candidate_id=profile.id,
            job_id=job.id,
            overall_score=92.0,
            breakdown=ScoreBreakdown(skill_score=95.0, experience_score=90.0, education_score=90.0),
            matched_skills=["PostgreSQL"],
            missing_skills=[],
            explanation="Excellent database alignment.",
            caveat=MatchExplainer.MANDATORY_DISCLAIMER,
        )
        saved_match = await match_repo.save_match(match)
        assert saved_match.overall_score == 92.0

        matches = await match_repo.get_matches_for_candidate(profile.id)
        assert len(matches) >= 1
        assert matches[0]["job_id"] == test_job_id

    finally:
        # Strict cleanup to leave zero residual data
        await pg_mgr.execute("DELETE FROM match_records WHERE job_id = $1", (test_job_id,))
        await pg_mgr.execute("DELETE FROM jobs WHERE id = $1", (test_job_id,))
        if "user" in locals():
            await pg_mgr.execute("DELETE FROM candidate_profiles WHERE user_id = $1", (user.id,))
            await pg_mgr.execute("DELETE FROM users WHERE email = $1", (test_email,))
        await pg_mgr.close()

