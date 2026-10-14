"""Phase 10 Frontend & UI Integration Test Suite.

Validates Phase 10 requirements from docs/AI_Job_Matcher_Phases_and_Structure.md:
1. Initial Page State & Asset Serving: GET / serves production frontend HTML with Figma layout and unique DOM IDs.
2. CV Submission & Profile Extraction: Validates candidate upload, magic bytes, and structured profile fields.
3. Match Result & Explanation Display: Validates multi-factor breakdown, skill overlap matrix, and legal caveat.
4. API Failure & Error Handling: Ensures robust JSON error responses for UI error boundaries and toast notifications.
5. No-Matches & Empty States: Verifies clean empty-collection responses for UI empty-state rendering.
6. Assessment Catalog & Sandbox Execution: Validates challenge retrieval and isolated sandbox submission flow.
"""

from datetime import datetime, timezone
from typing import List
import pytest
from httpx import ASGITransport, AsyncClient

from app.authentication.service import get_auth_service
from app.main import app
from app.repositories.job_repo import (
    InMemoryJobRepository,
    get_job_repository,
    reset_job_repository,
)
from app.repositories.resume_repo import (
    InMemoryResumeRepository,
    get_resume_repository,
    reset_resume_repository,
)
from app.repositories.user_repo import (
    InMemoryUserRepository,
    get_user_repository,
    reset_user_repository,
)
from app.sandbox.service import reset_sandbox_service
from app.schemas.auth import UserRegisterRequest
from app.schemas.job import Job
from app.schemas.resume import CandidateProfile


def make_valid_pdf_bytes(text_lines: List[str]) -> bytes:
    """Generate valid PDF-1.4 binary bytes for file upload validation."""
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


async def create_authenticated_candidate(email: str, full_name: str) -> tuple[str, str]:
    """Helper to register and generate a valid JWT bearer token."""
    auth_svc = get_auth_service()
    user = await auth_svc.register_user(
        UserRegisterRequest(
            email=email,
            password="SecurePassword123!",
            full_name=full_name,
            role="candidate",
        )
    )
    token, _ = auth_svc.create_access_token(user_id=user.id, email=user.email, role="candidate")
    return user.id, token


@pytest.fixture(autouse=True)
async def reset_state():
    """Ensure clean test repositories and dependency overrides between tests."""
    reset_job_repository()
    reset_resume_repository()
    reset_user_repository()
    reset_sandbox_service()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://testserver", follow_redirects=True)


# ==============================================================================
# TEST 1: INITIAL PAGE STATE & STATIC ASSET DELIVERY
# ==============================================================================
@pytest.mark.asyncio
async def test_initial_page_state_and_assets(client: AsyncClient):
    """Test 1: GET / serves the production Figma-aligned frontend with required interactive DOM IDs."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "text/html" in resp.headers.get("content-type", "")

    html_text = resp.text
    # Verify title and meta description
    assert "<title>AI Job Matcher" in html_text
    assert "AI Job Matcher connects candidates to tech jobs" in html_text

    # Verify Figma template interactive components & IDs exist
    required_ids = [
        "brandLogo",
        "tabJobs",
        "tabProfile",
        "tabMatching",
        "tabAssessments",
        "tabAgent",
        "jobSearchForm",
        "inputJobQuery",
        "dropzoneCv",
        "selectMatchingJob",
        "btnCalculateMatch",
        "matchingResultsDashboard",
        "scoreGaugeElem",
        "assessmentCatalog",
        "sandboxWorkspace",
        "codeEditorInput",
        "btnSubmitCode",
        "modalAuth",
    ]
    for dom_id in required_ids:
        assert f'id="{dom_id}"' in html_text, f"Expected DOM element id='{dom_id}' in index.html"

    # Verify static CSS and JS are accessible
    css_resp = await client.get("/frontend-assets/styles.css")
    assert css_resp.status_code == 200
    assert "text/css" in css_resp.headers.get("content-type", "")

    js_resp = await client.get("/frontend-assets/app.js")
    assert js_resp.status_code == 200


# ==============================================================================
# TEST 2: CV SUBMISSION & STRUCTURED PROFILE DATA PIPELINE
# ==============================================================================
@pytest.mark.asyncio
async def test_cv_submission_and_profile_extraction_flow(client: AsyncClient):
    """Test 2: Verifies candidate CV submission, magic bytes check, and structured profile fields."""
    user_id, token = await create_authenticated_candidate("alex.candidate@example.com", "Alex Candidate")
    headers = {"Authorization": f"Bearer {token}"}

    cv_lines = [
        "Alex Candidate",
        "Email: alex.candidate@example.com",
        "Summary: Senior Software Engineer with expertise in Python, FastAPI, and Docker.",
        "Skills: Python, FastAPI, Docker, SQL",
        "Experience: Senior Backend Engineer at FinTech Corp from 2020 to 2026",
        "Education: Bachelor of Science in Computer Engineering",
    ]
    pdf_bytes = make_valid_pdf_bytes(cv_lines)

    resp = await client.post(
        "/api/v1/resumes/upload",
        headers=headers,
        files={"file": ("resume.pdf", pdf_bytes, "application/pdf")},
    )
    assert resp.status_code == 201
    data = resp.json()["data"]

    # Verify structured fields for UI rendering
    assert data["user_id"] == user_id
    assert "Alex Candidate" in data["full_name"]
    assert len(data["skills"]) >= 2
    skill_names = [s["name"] for s in data["skills"]]
    assert "Python" in skill_names or any("python" in s.lower() for s in skill_names)


# ==============================================================================
# TEST 3: MATCH RESULT & FACTOR EXPLANATION DISPLAY
# ==============================================================================
@pytest.mark.asyncio
async def test_match_result_and_explanation_display(client: AsyncClient):
    """Test 3: Validates match score computation, factor breakdown, and legal caveats."""
    user_id, token = await create_authenticated_candidate("match.candidate@example.com", "Match User")
    headers = {"Authorization": f"Bearer {token}"}

    # Upload candidate CV
    pdf_bytes = make_valid_pdf_bytes([
        "Match User",
        "Email: match.candidate@example.com",
        "Summary: Backend Python developer.",
        "Skills: Python, Docker",
    ])
    up_resp = await client.post(
        "/api/v1/resumes/upload",
        headers=headers,
        files={"file": ("cv.pdf", pdf_bytes, "application/pdf")},
    )
    assert up_resp.status_code == 201
    candidate_id = up_resp.json()["data"]["id"]

    # Create target job
    job_repo = get_job_repository()
    job = Job(
        id="job_frontend_match_01",
        title="Python Engineer",
        company="ScaleTech Labs",
        location="Remote",
        description="Python development position.",
        required_skills=["Python"],
        preferred_skills=["Docker"],
        is_active=True,
    )
    await job_repo.create_job(job)

    # Compute match
    match_resp = await client.post(
        f"/api/v1/matching/{candidate_id}/jobs/{job.id}",
        headers=headers,
    )
    assert match_resp.status_code == 200
    match_data = match_resp.json()["data"]

    # Verify score fields required by frontend gauge and breakdown bars
    assert "overall_score" in match_data
    assert match_data["overall_score"] >= 50.0
    assert "breakdown" in match_data
    assert "skill_score" in match_data["breakdown"]
    assert "experience_score" in match_data["breakdown"]
    assert "education_score" in match_data["breakdown"]

    # Verify skill matrix categories
    assert "Python" in match_data["matched_skills"]
    assert "caveat" in match_data
    assert "automated estimate" in match_data["caveat"].lower()


# ==============================================================================
# TEST 4: API FAILURE AND SAFE ERROR BOUNDARY RESPONSES
# ==============================================================================
@pytest.mark.asyncio
async def test_api_failure_and_error_handling(client: AsyncClient):
    """Test 4: Verifies structured error responses on invalid requests without 500 crashes."""
    user_id, token = await create_authenticated_candidate("err.candidate@example.com", "Err User")
    headers = {"Authorization": f"Bearer {token}"}

    # 1. Invalid file upload (unsupported non-PDF/DOCX executable)
    bad_upload = await client.post(
        "/api/v1/resumes/upload",
        headers=headers,
        files={"file": ("malicious.exe", b"MZ\x90\x00executable binary", "application/x-msdownload")},
    )
    assert bad_upload.status_code in (400, 415, 422)
    err_body = bad_upload.json()
    assert "detail" in err_body or "message" in err_body or "error" in err_body

    # 2. Match against non-existent job ID
    bad_match = await client.post(
        f"/api/v1/matching/cand_nonexistent_123/jobs/job_missing_999",
        headers=headers,
    )
    assert bad_match.status_code == 404

    # 3. Unauthenticated access to protected route
    unauth = await client.post(
        "/api/v1/assessments/submit",
        json={"candidate_id": "c1", "skill": "Python", "language": "python", "code": "print(1)"},
    )
    assert unauth.status_code == 401


# ==============================================================================
# TEST 5: NO-MATCHES AND EMPTY STATE HANDLING
# ==============================================================================
@pytest.mark.asyncio
async def test_no_matches_and_empty_state_handling(client: AsyncClient):
    """Test 5: Verifies that empty job queries cleanly return an empty list with 200 OK."""
    user_id, token = await create_authenticated_candidate("search.candidate@example.com", "Search User")
    headers = {"Authorization": f"Bearer {token}"}

    # Query with query parameter that matches zero jobs
    resp = await client.get("/api/v1/jobs/?q=xyz_impossible_keyword_nonexistent_99999", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert isinstance(data, list)
    assert len(data) == 0, "Empty search should return an empty list for frontend empty state rendering"


# ==============================================================================
# TEST 6: ASSESSMENT CATALOG AND SANDBOX EXECUTION FLOW
# ==============================================================================
@pytest.mark.asyncio
async def test_assessment_catalog_and_sandbox_submission_flow(client: AsyncClient):
    """Test 6: Verifies challenge catalog retrieval and sandbox submission execution."""
    user_id, token = await create_authenticated_candidate("coder.candidate@example.com", "Coder Candidate")
    headers = {"Authorization": f"Bearer {token}"}

    # Create candidate profile
    resume_repo = get_resume_repository()
    profile = await resume_repo.save_profile(
        CandidateProfile(
            id="cand_sandbox_ui_01",
            user_id=user_id,
            raw_hash="hash_sandbox_ui",
            full_name="Coder Candidate",
            email="coder.candidate@example.com",
            skills=[],
        )
    )

    # 1. Query challenges catalog
    chal_resp = await client.get("/api/v1/assessments/challenges", headers=headers)
    assert chal_resp.status_code == 200
    challenges = chal_resp.json()["data"]
    assert len(challenges) >= 1
    py_challenge = next(c for c in challenges if c["skill"].lower() == "python")
    assert py_challenge["title"] is not None

    # 2. Submit solution to isolated sandbox
    sub_resp = await client.post(
        "/api/v1/assessments/submit",
        headers=headers,
        json={
            "candidate_id": profile.id,
            "skill": "Python",
            "language": "python",
            "code": "# output: 0 1\nprint('0 1')\n",
            "test_cases": [
                {"input_data": "2,7;9\n", "expected_output": "0 1"},
            ],
        },
    )
    assert sub_resp.status_code == 200
    res_data = sub_resp.json()["data"]
    assert res_data["status"] == "completed"
    assert res_data["passed_tests"] == 1
    assert "submission_id" in res_data
