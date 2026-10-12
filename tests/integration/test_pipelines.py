"""Phase 3 Job & Resume Pipelines Test Suite (Developer A Lead).

Verifies Developer A Phase 3 pipeline orchestration responsibilities:
1. Duplicate Job Normalization: Multiple ingests of identical job deduplicate by SHA-256 content hash and source URL.
2. Malformed Listing Handling: Incomplete or invalid job data (missing title, description < 10 chars) handled safely.
3. Source Timeout and Rate Limit: External provider timeouts return 504 GATEWAY_TIMEOUT; rate limits return 429.
4. Unreadable CV Handling: Blank, empty, or corrupted binary files return clean 400 BAD_REQUEST.
5. CV Missing Optional Fields: Resumes with partial fields parse into valid CandidateProfile with safe defaults.
6. Prohibited & Executable File Defense: Executables (MZ/ELF) and scripts (PHP/bash) rejected with 415.
7. Oversized Document Defense: CV files exceeding 5 MB limit rejected with 413 PAYLOAD_TOO_LARGE.
8. Stale Listing Freshness Filter: Job postings older than 30 days marked inactive and filtered out from active queries.
9. Domain Allowlisting & SSRF Defense: Non-allowlisted domains and loopback/private IPs blocked before dispatch.
10. Candidate Profile Ownership Isolation: Candidate A cannot read Candidate B's profile (403 Forbidden).
11. Job Search & Pagination: Keyword, skill, and location filters with pagination limit and offset.
"""

from datetime import datetime, timedelta, timezone
import io
import pytest
from httpx import AsyncClient

from app.adapters.firecrawl_adapter import MockFirecrawlAdapter
from app.adapters.resume_parser import MockResumeParser
from app.authentication.service import get_auth_service
from app.pipelines.job_ingestion_pipeline import JobIngestionPipeline
from app.pipelines.resume_pipeline import ResumePipeline
from app.repositories.job_repo import get_job_repository, reset_job_repository
from app.repositories.resume_repo import get_resume_repository, reset_resume_repository
from app.repositories.user_repo import get_user_repository, reset_user_repository
from app.schemas.auth import UserRegisterRequest
from app.schemas.job import JobIngestRequest


@pytest.fixture(autouse=True)
async def reset_all_repositories() -> None:
    """Reset repository singletons before each test."""
    reset_job_repository()
    reset_resume_repository()
    reset_user_repository()


async def create_user_and_token(role: str = "candidate", email_suffix: str = "test") -> tuple:
    """Helper to provision authenticated test principal."""
    auth_svc = get_auth_service()
    repo = get_user_repository()
    register_role = "recruiter" if role == "admin" else role
    user = await auth_svc.register_user(
        UserRegisterRequest(
            email=f"{role}_{email_suffix}@example.com",
            password="SecurePassword123!",
            full_name=f"{role.capitalize()} User",
            role=register_role,
        )
    )
    if role == "admin":
        await repo.update_user(user.id, role="admin")
    token, _ = auth_svc.create_access_token(user_id=user.id, email=user.email, role=role)
    return user, {"Authorization": f"Bearer {token}"}


# ==============================================================================
# 1. DUPLICATE JOB NORMALIZATION
# ==============================================================================
@pytest.mark.asyncio
async def test_duplicate_job_normalization() -> None:
    """Test 1: Ingesting the same job multiple times deduplicates by content hash, updating last_seen_at."""
    repo = get_job_repository()
    adapter = MockFirecrawlAdapter(
        canned_jobs={
            "https://approved-board.example.com/jobs/dev-1": {
                "title": "Senior Python Developer",
                "company": "Tech Corp",
                "location": "Remote",
                "description": "Building scalable FastAPI and distributed backend systems with PostgreSQL.",
                "required_skills": ["Python", "FastAPI"],
                "preferred_skills": ["Docker"],
                "experience_level": "senior",
                "source_url": "https://approved-board.example.com/jobs/dev-1",
            }
        }
    )
    pipeline = JobIngestionPipeline(adapter=adapter, repo=repo)

    # First ingestion
    req1 = JobIngestRequest(source_urls=["https://approved-board.example.com/jobs/dev-1"])
    res1 = await pipeline.ingest_jobs(req1)
    assert res1.ingested_count == 1
    assert res1.skipped_duplicates == 0
    assert len(res1.jobs) == 1
    job_id = res1.jobs[0].id

    # Second ingestion of identical job
    res2 = await pipeline.ingest_jobs(req1)
    assert res2.ingested_count == 0
    assert res2.skipped_duplicates == 1

    # Verify repository only contains 1 job with updated timestamp
    all_jobs = await repo.list_all()
    assert len(all_jobs) == 1
    assert all_jobs[0].id == job_id
    assert all_jobs[0].last_seen_at is not None


# ==============================================================================
# 2. MALFORMED LISTING HANDLING
# ==============================================================================
@pytest.mark.asyncio
async def test_malformed_listing_handling() -> None:
    """Test 2: Malformed listing (missing title, empty company, or short description) handled safely."""
    repo = get_job_repository()
    adapter = MockFirecrawlAdapter(
        canned_jobs={
            "https://approved-board.example.com/jobs/bad-1": {
                "title": "",
                "company": "Missing Title Corp",
                "description": "Short",
            }
        }
    )
    pipeline = JobIngestionPipeline(adapter=adapter, repo=repo)

    # Should safely raise BadRequestError without unhandled crash
    req = JobIngestRequest(source_urls=["https://approved-board.example.com/jobs/bad-1"])
    with pytest.raises(Exception) as exc_info:
        await pipeline.ingest_jobs(req)
    assert "Malformed job listing" in str(exc_info.value)

    # Repository must remain clean
    all_jobs = await repo.list_all()
    assert len(all_jobs) == 0


# ==============================================================================
# 3. SOURCE TIMEOUT AND RATE LIMIT
# ==============================================================================
@pytest.mark.asyncio
async def test_source_timeout_and_rate_limit(async_client: AsyncClient) -> None:
    """Test 3: Upstream source timeouts return 504 GATEWAY_TIMEOUT, and rate limits return 429."""
    _, admin_headers = await create_user_and_token(role="admin", email_suffix="crawler")

    # Timeout simulation
    timeout_adapter = MockFirecrawlAdapter(simulate_timeout=True)
    pipeline_timeout = JobIngestionPipeline(adapter=timeout_adapter)

    with pytest.raises(Exception) as exc_timeout:
        await pipeline_timeout.ingest_jobs(
            JobIngestRequest(source_urls=["https://approved-board.example.com/jobs/1"])
        )
    assert "Timeout while collecting from source" in str(exc_timeout.value)

    # Rate limit (429) simulation
    rate_limit_adapter = MockFirecrawlAdapter(simulate_rate_limit=True)
    pipeline_ratelimit = JobIngestionPipeline(adapter=rate_limit_adapter)

    with pytest.raises(Exception) as exc_rl:
        await pipeline_ratelimit.ingest_jobs(
            JobIngestRequest(source_urls=["https://approved-board.example.com/jobs/1"])
        )
    assert "rate limited request" in str(exc_rl.value)


# ==============================================================================
# 4. UNREADABLE CV HANDLING
# ==============================================================================
@pytest.mark.asyncio
async def test_unreadable_cv_rejected(async_client: AsyncClient) -> None:
    """Test 4: Unreadable, blank, or corrupted document returns clean 400 Bad Request."""
    _, candidate_headers = await create_user_and_token(role="candidate", email_suffix="corrupt")

    # Corrupted pseudo-PDF (has header but corrupted stream / zero text)
    corrupted_pdf = b"%PDF-1.4\ncorrupted gibberish without text\n%%EOF"
    files = {"file": ("corrupt.pdf", io.BytesIO(corrupted_pdf), "application/pdf")}

    response = await async_client.post("/api/v1/resumes/upload", files=files, headers=candidate_headers)
    assert response.status_code == 400
    data = response.json()
    assert data["success"] is False
    assert data["error"]["code"] == "BAD_REQUEST"
    assert "Unreadable" in data["error"]["message"]


# ==============================================================================
# 5. CV MISSING OPTIONAL FIELDS
# ==============================================================================
@pytest.mark.asyncio
async def test_cv_missing_optional_fields() -> None:
    """Test 5: CV missing optional fields (no graduation year, no summary, no experience dates) conforms to CandidateProfile."""
    repo = get_resume_repository()
    # Mock parser returning profile with minimal optional fields
    partial_parser = MockResumeParser(
        canned_response={
            "full_name": "Taylor Swift",
            "email": "taylor@example.com",
            "summary": None,  # Missing optional summary
            "skills": [
                {"name": "Python", "category": "language", "confidence": 0.9},
            ],
            "experience": [
                {
                    "title": "Junior Developer",
                    "company": "Startup Co",
                    "start_date": None,  # Missing optional dates
                    "end_date": None,
                    "description": None,
                }
            ],
            "education": [
                {
                    "institution": "Community College",
                    "degree": "Associate Degree",
                    "field_of_study": None,
                    "graduation_year": None,  # Missing optional graduation year
                }
            ],
        }
    )
    pipeline = ResumePipeline(parser=partial_parser, repo=repo)

    profile = await pipeline.process_resume(
        file_bytes=b"%PDF-1.5 test minimal",
        filename="minimal.pdf",
        content_type="application/pdf",
        user_id="usr_partial_123",
    )

    assert profile.user_id == "usr_partial_123"
    assert profile.full_name == "Taylor Swift"
    assert profile.summary is None
    assert len(profile.skills) == 1
    assert profile.skills[0].name == "Python"
    assert len(profile.experience) == 1
    assert profile.experience[0].start_date is None
    assert len(profile.education) == 1
    assert profile.education[0].graduation_year is None


# ==============================================================================
# 6. PROHIBITED & EXECUTABLE FILE DEFENSE
# ==============================================================================
@pytest.mark.asyncio
async def test_prohibited_and_executable_cv_blocked(async_client: AsyncClient) -> None:
    """Test 6: Executable binaries (MZ/ELF) and scripts uploaded as CV are rejected with 415."""
    _, candidate_headers = await create_user_and_token(role="candidate", email_suffix="malware")

    # Attempt Windows MZ executable
    mz_payload = b"MZ\x90\x00\x03\x00\x00\x00malicious binary content"
    files = {"file": ("malware.pdf", io.BytesIO(mz_payload), "application/pdf")}
    res_mz = await async_client.post("/api/v1/resumes/upload", files=files, headers=candidate_headers)
    assert res_mz.status_code == 415
    assert "Prohibited file type" in res_mz.json()["error"]["message"]

    # Attempt shell script disguised as txt
    sh_payload = b"#!/bin/bash\nrm -rf /\n"
    files_sh = {"file": ("script.txt", io.BytesIO(sh_payload), "text/plain")}
    res_sh = await async_client.post("/api/v1/resumes/upload", files=files_sh, headers=candidate_headers)
    assert res_sh.status_code == 415

    # Attempt unsupported extension .exe
    files_exe = {"file": ("payload.exe", io.BytesIO(b"somecontent"), "application/octet-stream")}
    res_exe = await async_client.post("/api/v1/resumes/upload", files=files_exe, headers=candidate_headers)
    assert res_exe.status_code == 415


# ==============================================================================
# 7. OVERSIZED DOCUMENT DEFENSE
# ==============================================================================
@pytest.mark.asyncio
async def test_oversized_cv_rejected(async_client: AsyncClient) -> None:
    """Test 7: Uploaded CV exceeding 5 MB limit is rejected with 413 Payload Too Large."""
    _, candidate_headers = await create_user_and_token(role="candidate", email_suffix="oversize")

    oversized_bytes = b"%PDF-1.4 " + (b"A" * (6 * 1024 * 1024))  # 6 MB
    files = {"file": ("large_cv.pdf", io.BytesIO(oversized_bytes), "application/pdf")}

    response = await async_client.post("/api/v1/resumes/upload", files=files, headers=candidate_headers)
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "PAYLOAD_TOO_LARGE"


# ==============================================================================
# 8. STALE LISTING FRESHNESS FILTER
# ==============================================================================
@pytest.mark.asyncio
async def test_stale_job_filtering() -> None:
    """Test 8: Jobs older than 30 days are flagged is_active=False and omitted from active queries."""
    repo = get_job_repository()
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=35)

    # Ingest fresh job
    fresh_adapter = MockFirecrawlAdapter(
        canned_jobs={
            "https://approved-board.example.com/jobs/fresh": {
                "title": "Fresh Job",
                "company": "Modern Systems",
                "description": "Active job posting with modern backend technologies.",
                "required_skills": ["Python"],
                "source_url": "https://approved-board.example.com/jobs/fresh",
            }
        }
    )
    pipeline = JobIngestionPipeline(adapter=fresh_adapter, repo=repo)
    res_fresh = await pipeline.ingest_jobs(JobIngestRequest(source_urls=["https://approved-board.example.com/jobs/fresh"]))
    fresh_id = res_fresh.jobs[0].id

    # Ingest and manually age a stale job
    stale_adapter = MockFirecrawlAdapter(
        canned_jobs={
            "https://approved-board.example.com/jobs/stale": {
                "title": "Stale Job",
                "company": "Legacy Corp",
                "description": "Old listing that has expired 35 days ago.",
                "required_skills": ["COBOL"],
                "source_url": "https://approved-board.example.com/jobs/stale",
            }
        }
    )
    pipeline_stale = JobIngestionPipeline(adapter=stale_adapter, repo=repo)
    res_stale = await pipeline_stale.ingest_jobs(JobIngestRequest(source_urls=["https://approved-board.example.com/jobs/stale"]))
    stale_id = res_stale.jobs[0].id

    # Simulate aging the stale job beyond 30 days
    await repo.update_job(stale_id, last_seen_at=old_time, collected_at=old_time)

    # Run freshness filter
    stale_marked = await repo.mark_stale_jobs(stale_threshold_days=30)
    assert stale_marked == 1

    # Active query must only return fresh job
    active_jobs = await repo.query_jobs(only_active=True)
    active_ids = [j.id for j in active_jobs]
    assert fresh_id in active_ids
    assert stale_id not in active_ids


# ==============================================================================
# 9. DOMAIN ALLOWLISTING & SSRF DEFENSE
# ==============================================================================
@pytest.mark.asyncio
async def test_unapproved_domain_and_ssrf_blocked() -> None:
    """Test 9: Scraping from non-allowlisted domains or private/loopback IPs is blocked."""
    repo = get_job_repository()
    pipeline = JobIngestionPipeline(repo=repo)

    # SSRF Loopback target
    with pytest.raises(Exception) as exc_loopback:
        await pipeline.ingest_jobs(JobIngestRequest(source_urls=["http://127.0.0.1:8000/internal"]))
    assert "private/loopback" in str(exc_loopback.value) or "unapproved" in str(exc_loopback.value)

    # AWS Metadata target
    with pytest.raises(Exception) as exc_aws:
        await pipeline.ingest_jobs(JobIngestRequest(source_urls=["http://169.254.169.254/latest/meta-data"]))
    assert "private" in str(exc_aws.value) or "unapproved" in str(exc_aws.value)

    # Unapproved external domain
    with pytest.raises(Exception) as exc_unapproved:
        await pipeline.ingest_jobs(JobIngestRequest(source_urls=["https://unapproved-random-site.com/jobs"]))
    assert "not in the approved job source allowlist" in str(exc_unapproved.value) or "unapproved" in str(exc_unapproved.value)


# ==============================================================================
# 10. CANDIDATE PROFILE OWNERSHIP ISOLATION
# ==============================================================================
@pytest.mark.asyncio
async def test_resume_upload_and_ownership_isolation(async_client: AsyncClient) -> None:
    """Test 10: Candidate A cannot access Candidate B's resume (403 Forbidden); Recruiter and Admin can."""
    candidate_a, headers_a = await create_user_and_token(role="candidate", email_suffix="alice")
    candidate_b, headers_b = await create_user_and_token(role="candidate", email_suffix="bob")
    _, headers_recruiter = await create_user_and_token(role="recruiter", email_suffix="recruiter_view")

    cv_content = (
        "Alice Engineer\n"
        "alice@example.com\n"
        "Experienced backend software engineer with Python and Docker skills.\n"
        "Software Engineer at Tech Inc from 2022 to Present.\n"
        "University of Tech B.S. CS 2021.\n"
    )
    files = {"file": ("alice_cv.txt", io.BytesIO(cv_content.encode("utf-8")), "text/plain")}
    res_upload = await async_client.post("/api/v1/resumes/upload", files=files, headers=headers_a)
    assert res_upload.status_code == 201
    profile_id = res_upload.json()["data"]["id"]

    # Candidate A accesses their own profile -> 200 OK
    res_self = await async_client.get(f"/api/v1/resumes/{profile_id}", headers=headers_a)
    assert res_self.status_code == 200
    assert res_self.json()["data"]["id"] == profile_id

    # Candidate B attempts to access Candidate A's profile -> 403 Forbidden
    res_cross = await async_client.get(f"/api/v1/resumes/{profile_id}", headers=headers_b)
    assert res_cross.status_code == 403
    assert res_cross.json()["error"]["code"] == "FORBIDDEN"

    # Recruiter accesses Candidate A's profile -> 200 OK
    res_rec = await async_client.get(f"/api/v1/resumes/{profile_id}", headers=headers_recruiter)
    assert res_rec.status_code == 200
    assert res_rec.json()["data"]["id"] == profile_id


# ==============================================================================
# 11. JOB SEARCH, FILTERING & PAGINATION
# ==============================================================================
@pytest.mark.asyncio
async def test_job_search_filters_and_pagination(async_client: AsyncClient) -> None:
    """Test 11: Job search supports keyword query, skill filtering, location, and pagination limit/offset."""
    _, candidate_headers = await create_user_and_token(role="candidate", email_suffix="searcher")
    repo = get_job_repository()

    # Pre-populate jobs in repository
    adapter = MockFirecrawlAdapter(
        canned_jobs={
            "https://approved-board.example.com/jobs/p1": {
                "title": "Python Backend Engineer",
                "company": "Acme Corp",
                "location": "Remote",
                "description": "Seeking Python and FastAPI backend specialist.",
                "required_skills": ["Python", "FastAPI"],
                "source_url": "https://approved-board.example.com/jobs/p1",
            },
            "https://approved-board.example.com/jobs/p2": {
                "title": "Frontend React Developer",
                "company": "UI Studios",
                "location": "New York, NY",
                "description": "React and TypeScript web interface developer.",
                "required_skills": ["React", "TypeScript"],
                "source_url": "https://approved-board.example.com/jobs/p2",
            },
        }
    )
    pipeline = JobIngestionPipeline(adapter=adapter, repo=repo)
    await pipeline.ingest_jobs(
        JobIngestRequest(
            source_urls=[
                "https://approved-board.example.com/jobs/p1",
                "https://approved-board.example.com/jobs/p2",
            ],
            max_pages=2,
        )
    )

    # Keyword search for Python
    res_kw = await async_client.get("/api/v1/jobs?q=Python", headers=candidate_headers)
    assert res_kw.status_code == 200
    jobs_kw = res_kw.json()["data"]
    assert len(jobs_kw) == 1
    assert jobs_kw[0]["title"] == "Python Backend Engineer"

    # Skill filter for React
    res_skill = await async_client.get("/api/v1/jobs?skills=React", headers=candidate_headers)
    assert res_skill.status_code == 200
    jobs_skill = res_skill.json()["data"]
    assert len(jobs_skill) == 1
    assert jobs_skill[0]["company"] == "UI Studios"

    # Pagination: limit=1, offset=1
    res_page = await async_client.get("/api/v1/jobs?limit=1&offset=1", headers=candidate_headers)
    assert res_page.status_code == 200
    assert len(res_page.json()["data"]) == 1

    # Get single job by ID
    job_id = jobs_kw[0]["id"]
    res_single = await async_client.get(f"/api/v1/jobs/{job_id}", headers=candidate_headers)
    assert res_single.status_code == 200
    assert res_single.json()["data"]["id"] == job_id

    # Non-existent job returns 404
    res_404 = await async_client.get("/api/v1/jobs/nonexistent_id", headers=candidate_headers)
    assert res_404.status_code == 404
    assert res_404.json()["error"]["code"] == "NOT_FOUND"


# ==============================================================================
# 12. LIST RESUMES ENDPOINT
# ==============================================================================
@pytest.mark.asyncio
async def test_list_resumes_endpoint(async_client: AsyncClient) -> None:
    """Test 12: GET /api/v1/resumes lists candidates own resumes or all resumes for recruiters."""
    candidate_user, candidate_headers = await create_user_and_token(role="candidate", email_suffix="list_cand")
    _, recruiter_headers = await create_user_and_token(role="recruiter", email_suffix="list_rec")

    # Upload resume for candidate
    cv_text = (
        "Charlie Developer\n"
        "charlie@example.com\n"
        "Senior Cloud Engineer specialized in AWS, Docker, Kubernetes, and Python.\n"
        "DevOps Engineer at CloudWorks 2021-Present.\n"
        "Tech Institute B.S. CS 2020.\n"
    )
    files = {"file": ("charlie_cv.txt", io.BytesIO(cv_text.encode("utf-8")), "text/plain")}
    res_upload = await async_client.post("/api/v1/resumes/upload", files=files, headers=candidate_headers)
    assert res_upload.status_code == 201

    # Candidate lists their profiles
    res_cand_list = await async_client.get("/api/v1/resumes", headers=candidate_headers)
    assert res_cand_list.status_code == 200
    profiles = res_cand_list.json()["data"]
    assert len(profiles) == 1
    assert profiles[0]["user_id"] == candidate_user.id

    # Recruiter lists profiles
    res_rec_list = await async_client.get("/api/v1/resumes", headers=recruiter_headers)
    assert res_rec_list.status_code == 200
    assert len(res_rec_list.json()["data"]) >= 1
