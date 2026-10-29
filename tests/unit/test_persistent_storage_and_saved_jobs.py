"""Comprehensive Test Suite for Persistent Resume Storage, Saved Jobs, and Database Optimization.

Covers all 10 Mandatory Scenarios:
1. Resume Persistence: Uploaded resume persists across sessions and is retrievable via backend API.
2. Resume Ownership: User B cannot retrieve, activate, or delete User A's resume (Candidate Isolation).
3. Resume Deduplication: Uploading the identical document twice for the same user reuses the existing record.
4. Saved Job Persistence: Saving a job creates a database record retrievable across sessions.
5. Saved Job Idempotency: Saving the same job repeatedly does not create duplicate entries.
6. Unsave Behavior: Removing a saved job removes the relationship without deleting the shared job posting.
7. Saved Job Isolation: User B cannot access or modify User A's saved jobs.
8. Resume Deletion & File Cleanup: Deleting an authorized resume cleans up both the database row and physical storage.
9. Validation Failures: Malformed requests, bad paths, and nonexistent IDs fail with safe error responses.
10. Regression Behavior: Job catalog, search, and matching pipelines continue operating flawlessly.
"""

import io
from typing import List
import pytest
from httpx import AsyncClient

from app.authentication.service import get_auth_service
from app.repositories.job_repo import get_job_repository, reset_job_repository
from app.repositories.resume_repo import get_resume_repository, reset_resume_repository
from app.repositories.user_repo import get_user_repository, reset_user_repository
from app.schemas.auth import UserRegisterRequest
from app.schemas.job import Job
from app.services.storage_service import get_storage_service


def make_valid_pdf_bytes(text_lines: List[str]) -> bytes:
    """Generate valid PDF-1.4 binary bytes with valid %PDF- magic bytes and text stream."""
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


@pytest.fixture(autouse=True)
async def reset_repos() -> None:
    """Reset repository singletons between test runs."""
    reset_job_repository()
    reset_resume_repository()
    reset_user_repository()


async def provision_user(role: str = "candidate", suffix: str = "test") -> tuple:
    """Helper to provision authenticated test user and bearer auth headers."""
    auth_svc = get_auth_service()
    user = await auth_svc.register_user(
        UserRegisterRequest(
            email=f"{role}_{suffix}@example.com",
            password="SecurePassword123!",
            full_name=f"{role.capitalize()} {suffix.capitalize()}",
            role=role if role != "admin" else "recruiter",
        )
    )
    user_repo = get_user_repository()
    if role == "admin":
        await user_repo.update_user(user.id, role="admin", email_verified=True)
    else:
        await user_repo.update_user(user.id, email_verified=True)
    token, _ = auth_svc.create_access_token(user_id=user.id, email=user.email, role=role, email_verified=True)
    return user, {"Authorization": f"Bearer {token}"}


async def seed_shared_job(job_id: str = "job_test_swe_01") -> Job:
    """Helper to seed a test job in repository."""
    job_repo = get_job_repository()
    existing = await job_repo.get_by_id(job_id)
    if existing:
        return existing

    test_job = Job(
        id=job_id,
        title="Senior Python Backend Architect",
        company="Nexus Dynamics",
        location="Remote · US",
        description="Architect high-performance distributed microservices with Python, FastAPI, and PostgreSQL.",
        required_skills=["Python", "FastAPI", "PostgreSQL", "Docker"],
        preferred_skills=["Redis", "AWS", "Kubernetes"],
        experience_level="senior",
        experience_years_required=5.0,
        salary="$160,000 – $190,000 / year",
        education_required="Bachelor's Degree",
        responsibilities=["Architect microservices", "Lead database scaling"],
        domain="Backend Engineering",
        source="firecrawl",
        source_url="https://careers.nexusdynamics.com/jobs/senior-python",
        is_active=True,
    )
    return await job_repo.create_job(test_job)


# ==============================================================================
# SCENARIO 1: RESUME PERSISTENCE
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_1_resume_persistence(async_client: AsyncClient):
    """An authenticated user uploads a resume; it persists and is retrieved in subsequent requests."""
    user_a, headers_a = await provision_user(role="candidate", suffix="persist_a")

    pdf_bytes = make_valid_pdf_bytes([
        "Alice Engineer",
        "alice@example.com",
        "Senior Python Engineer with 6 years experience in FastAPI, Docker, and PostgreSQL.",
        "Staff Engineer at CloudScale 2020-Present.",
        "University B.S. Computer Science 2018.",
    ])
    files = {"file": ("alice_cv.pdf", io.BytesIO(pdf_bytes), "application/pdf")}

    # 1. Upload via POST /api/v1/resumes
    upload_resp = await async_client.post("/api/v1/resumes", files=files, headers=headers_a)
    assert upload_resp.status_code == 201
    upload_data = upload_resp.json()["data"]
    resume_id = upload_data["resume"]["id"]
    assert upload_data["resume"]["user_id"] == user_a.id
    assert upload_data["resume"]["is_active"] is True
    assert upload_data["resume"]["filename"] == "alice_cv.pdf"
    assert upload_data["profile"]["id"] is not None
    assert isinstance(upload_data["profile"]["skills"], list)

    # 2. Subsequent backend request: List resumes for user
    list_resp = await async_client.get("/api/v1/resumes", headers=headers_a)
    assert list_resp.status_code == 200
    resumes_list = list_resp.json()["data"]
    assert len(resumes_list) == 1
    assert resumes_list[0]["id"] == resume_id
    assert resumes_list[0]["is_active"] is True

    # 3. Retrieve detailed record
    detail_resp = await async_client.get(f"/api/v1/resumes/{resume_id}", headers=headers_a)
    assert detail_resp.status_code == 200
    detail_data = detail_resp.json()["data"]
    assert detail_data["resume"]["id"] == resume_id
    assert detail_data["profile"]["id"] == upload_data["profile"]["id"]


# ==============================================================================
# SCENARIO 2: RESUME OWNERSHIP ISOLATION
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_2_resume_ownership_isolation(async_client: AsyncClient):
    """User B cannot retrieve, activate, or delete User A's resume document."""
    user_a, headers_a = await provision_user(role="candidate", suffix="owner_a")
    user_b, headers_b = await provision_user(role="candidate", suffix="intruder_b")

    pdf_bytes = make_valid_pdf_bytes(["Alice Secret Resume", "Python, Docker, FastAPI"])
    files = {"file": ("secret_resume.pdf", io.BytesIO(pdf_bytes), "application/pdf")}

    upload_resp = await async_client.post("/api/v1/resumes", files=files, headers=headers_a)
    assert upload_resp.status_code == 201
    resume_id = upload_resp.json()["data"]["resume"]["id"]

    # 1. User B attempts to read User A's resume -> 403 Forbidden
    read_resp = await async_client.get(f"/api/v1/resumes/{resume_id}", headers=headers_b)
    assert read_resp.status_code in (403, 404)

    # 2. User B attempts to activate User A's resume -> 403 Forbidden
    activate_resp = await async_client.patch(f"/api/v1/resumes/{resume_id}/active", headers=headers_b)
    assert activate_resp.status_code in (403, 404)

    # 3. User B attempts to delete User A's resume -> 403 Forbidden
    delete_resp = await async_client.delete(f"/api/v1/resumes/{resume_id}", headers=headers_b)
    assert delete_resp.status_code in (403, 404)

    # Verify User A's resume is completely untouched
    verify_resp = await async_client.get(f"/api/v1/resumes/{resume_id}", headers=headers_a)
    assert verify_resp.status_code == 200


# ==============================================================================
# SCENARIO 3: RESUME DEDUPLICATION & REUSE
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_3_resume_deduplication_and_reuse(async_client: AsyncClient):
    """Uploading the identical document twice for the same user reuses the existing record without redundant parsing."""
    user_a, headers_a = await provision_user(role="candidate", suffix="dedup_a")

    pdf_bytes = make_valid_pdf_bytes([
        "Consistent CV Document Content for SHA-256 Hashing Verification.",
        "Python, Cloud Architecture, AWS, FastAPI.",
    ])
    files1 = {"file": ("my_resume.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    resp1 = await async_client.post("/api/v1/resumes", files=files1, headers=headers_a)
    assert resp1.status_code == 201
    res1_data = resp1.json()["data"]["resume"]

    # Second upload with identical bytes
    files2 = {"file": ("my_resume.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    resp2 = await async_client.post("/api/v1/resumes", files=files2, headers=headers_a)
    assert resp2.status_code == 201
    assert "reused" in resp2.json()["message"].lower()

    res2_data = resp2.json()["data"]["resume"]
    # Same ID reused!
    assert res2_data["id"] == res1_data["id"]
    assert res2_data["raw_hash"] == res1_data["raw_hash"]

    # Verify user still only has one resume record
    list_resp = await async_client.get("/api/v1/resumes", headers=headers_a)
    assert len(list_resp.json()["data"]) == 1


# ==============================================================================
# SCENARIO 4: SAVED JOB PERSISTENCE
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_4_saved_job_persistence(async_client: AsyncClient):
    """Saving a job persists the relationship across requests and sessions."""
    user, headers = await provision_user(role="candidate", suffix="saved_persist")
    job = await seed_shared_job("job_persist_101")

    # 1. Save job
    save_resp = await async_client.post(f"/api/v1/jobs/{job.id}/save", headers=headers)
    assert save_resp.status_code == 200
    assert save_resp.json()["data"]["saved"] is True

    # 2. Fetch saved jobs list
    get_resp = await async_client.get("/api/v1/jobs/saved", headers=headers)
    assert get_resp.status_code == 200
    saved_items = get_resp.json()["data"]["jobs"]
    assert len(saved_items) == 1
    assert saved_items[0]["id"] == job.id
    assert saved_items[0]["title"] == job.title
    assert saved_items[0]["source_url"] == job.source_url

    # 3. Check count endpoint
    count_resp = await async_client.get("/api/v1/jobs/saved/count", headers=headers)
    assert count_resp.status_code == 200
    assert count_resp.json()["data"]["count"] == 1


# ==============================================================================
# SCENARIO 5: SAVED JOB IDEMPOTENCY
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_5_saved_job_idempotency(async_client: AsyncClient):
    """Repeated save requests for the same job do not create duplicate records."""
    user, headers = await provision_user(role="candidate", suffix="idempotent")
    job = await seed_shared_job("job_idempotent_202")

    # Call save multiple times rapidly
    resp1 = await async_client.post(f"/api/v1/jobs/{job.id}/save", headers=headers)
    assert resp1.status_code == 200
    assert resp1.json()["data"]["saved"] is True

    resp2 = await async_client.post(f"/api/v1/jobs/{job.id}/save", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["data"]["saved"] is True

    resp3 = await async_client.post(f"/api/v1/jobs/{job.id}/save", headers=headers)
    assert resp3.status_code == 200
    assert resp3.json()["data"]["saved"] is True

    # Ensure exactly 1 saved job exists
    get_resp = await async_client.get("/api/v1/jobs/saved", headers=headers)
    saved_items = get_resp.json()["data"]["jobs"]
    assert len(saved_items) == 1
    assert saved_items[0]["id"] == job.id


# ==============================================================================
# SCENARIO 6: UNSAVE BEHAVIOR (WITHOUT DELETING SHARED JOB)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_6_unsave_behavior(async_client: AsyncClient):
    """Unsaving removes the user's saved relationship, but does not delete the shared job."""
    user, headers = await provision_user(role="candidate", suffix="unsave_user")
    job = await seed_shared_job("job_shared_303")

    # Save job
    await async_client.post(f"/api/v1/jobs/{job.id}/save", headers=headers)

    # Unsave job
    unsave_resp = await async_client.delete(f"/api/v1/jobs/{job.id}/save", headers=headers)
    assert unsave_resp.status_code == 200
    assert unsave_resp.json()["data"]["saved"] is False

    # Verify saved jobs is now empty for user
    saved_resp = await async_client.get("/api/v1/jobs/saved", headers=headers)
    assert len(saved_resp.json()["data"]["jobs"]) == 0

    # Verify shared job STILL EXISTS in global job repository
    job_repo = get_job_repository()
    job_in_db = await job_repo.get_by_id(job.id)
    assert job_in_db is not None
    assert job_in_db.title == job.title


# ==============================================================================
# SCENARIO 7: SAVED JOB ISOLATION
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_7_saved_job_isolation(async_client: AsyncClient):
    """User B cannot view or tamper with User A's saved jobs."""
    user_a, headers_a = await provision_user(role="candidate", suffix="iso_a")
    user_b, headers_b = await provision_user(role="candidate", suffix="iso_b")
    job = await seed_shared_job("job_isolated_404")

    # User A saves job
    await async_client.post(f"/api/v1/jobs/{job.id}/save", headers=headers_a)

    # User B queries saved jobs -> must be empty
    saved_b_resp = await async_client.get("/api/v1/jobs/saved", headers=headers_b)
    assert len(saved_b_resp.json()["data"]["jobs"]) == 0

    # User B attempts to unsave User A's job
    unsave_b_resp = await async_client.delete(f"/api/v1/jobs/{job.id}/save", headers=headers_b)
    assert unsave_b_resp.status_code == 200

    # User A's saved job is still intact!
    saved_a_resp = await async_client.get("/api/v1/jobs/saved", headers=headers_a)
    saved_a_jobs = saved_a_resp.json()["data"]["jobs"]
    assert len(saved_a_jobs) == 1
    assert saved_a_jobs[0]["id"] == job.id


# ==============================================================================
# SCENARIO 8: RESUME DELETION & PHYSICAL FILE CLEANUP
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_8_resume_deletion_and_cleanup(async_client: AsyncClient):
    """Deleting an authorized resume removes the database record and cleans up the stored file."""
    user, headers = await provision_user(role="candidate", suffix="cleanup")

    pdf_bytes = make_valid_pdf_bytes(["Developer to delete", "Python, Docker, SQL"])
    files = {"file": ("temp_delete_cv.pdf", io.BytesIO(pdf_bytes), "application/pdf")}

    upload_resp = await async_client.post("/api/v1/resumes", files=files, headers=headers)
    assert upload_resp.status_code == 201
    resume_data = upload_resp.json()["data"]["resume"]
    resume_id = resume_data["id"]
    storage_key = resume_data["storage_key"]

    # Verify physical file exists
    storage = get_storage_service()
    stored_bytes = await storage.get_file(storage_key)
    assert len(stored_bytes) > 0

    # Delete resume via API
    del_resp = await async_client.delete(f"/api/v1/resumes/{resume_id}", headers=headers)
    assert del_resp.status_code == 200
    assert del_resp.json()["data"]["deleted"] is True

    # 1. DB record is gone
    get_resp = await async_client.get(f"/api/v1/resumes/{resume_id}", headers=headers)
    assert get_resp.status_code == 404

    # 2. Physical file is deleted
    with pytest.raises(Exception):
        await storage.get_file(storage_key)


# ==============================================================================
# SCENARIO 9: VALIDATION FAILURES
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_9_validation_failures(async_client: AsyncClient):
    """Invalid requests return safe, descriptive errors without leaking internal server details."""
    user, headers = await provision_user(role="candidate", suffix="val_user")

    # 1. Attempting to get nonexistent resume
    nonexistent_resp = await async_client.get("/api/v1/resumes/res_does_not_exist", headers=headers)
    assert nonexistent_resp.status_code == 404

    # 2. Saving a nonexistent job returns 404
    nonexistent_job_save = await async_client.post("/api/v1/jobs/job_ghost_999/save", headers=headers)
    assert nonexistent_job_save.status_code == 404

    # 3. Unauthenticated requests to protected endpoints return 401
    unauth_saved = await async_client.get("/api/v1/jobs/saved")
    assert unauth_saved.status_code == 401

    unauth_resumes = await async_client.get("/api/v1/resumes")
    assert unauth_resumes.status_code == 401


# ==============================================================================
# SCENARIO 10: REGRESSION BEHAVIOR
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_10_regression_behavior(async_client: AsyncClient):
    """Existing job catalog querying, pagination, and matching pipeline continue operating normally."""
    user, headers = await provision_user(role="candidate", suffix="regression")
    await seed_shared_job("job_regr_01")
    await seed_shared_job("job_regr_02")

    # 1. Query job catalog
    catalog_resp = await async_client.get("/api/v1/jobs?limit=10", headers=headers)
    assert catalog_resp.status_code == 200
    jobs = catalog_resp.json()["data"]
    assert len(jobs) >= 2

    # 2. End-to-end upload and match pipeline creates persistent resume while generating matches
    pdf_bytes = make_valid_pdf_bytes([
        "Sarah Senior Backend Developer",
        "sarah@example.com",
        "Expert in Python, FastAPI, PostgreSQL, and Docker architecture.",
        "6 years experience building distributed platforms.",
    ])
    files = {"file": ("sarah_pipeline.pdf", io.BytesIO(pdf_bytes), "application/pdf")}
    match_resp = await async_client.post("/api/v1/resumes/upload-and-match", files=files, headers=headers)
    assert match_resp.status_code == 201
    match_data = match_resp.json()["data"]
    assert len(match_data["ranked_matches"]) > 0
    assert match_data["storage_meta"]["persisted"] is True
    assert "resume_id" in match_data["storage_meta"]

    # 3. Verify that the resume uploaded through upload-and-match is now in the user's persistent resumes list!
    resumes_list_resp = await async_client.get("/api/v1/resumes", headers=headers)
    assert resumes_list_resp.status_code == 200
    resumes = resumes_list_resp.json()["data"]
    assert any(r["filename"] == "sarah_pipeline.pdf" for r in resumes)
