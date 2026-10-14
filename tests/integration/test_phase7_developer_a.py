"""Phase 7 Integration Test Suite: Assessment Sandbox HTTP Endpoints & Authorization (Developer A).

Tests cover:
1. Supported language introspection via GET /api/v1/assessments/languages.
2. End-to-end code assessment submission and test case evaluation.
3. IDOR protection: candidate A cannot submit assessments for candidate B.
4. Recruiter/admin authorized evaluation of candidate assessments.
5. Simulated timeout and resource exhaustion returns safe status codes.
6. Output limits and automatic truncation under high output volume.
7. Assessment result retrieval and cross-user isolation.
"""

from datetime import datetime, timezone
from typing import Dict
import pytest
from httpx import AsyncClient

from app.authentication.service import get_auth_service
from app.main import app
from app.repositories.resume_repo import get_resume_repository, reset_resume_repository
from app.repositories.user_repo import get_user_repository, reset_user_repository
from app.schemas.auth import UserRegisterRequest
from app.schemas.resume import CandidateProfile
from app.services.assessment_service import reset_assessment_service


@pytest.fixture(autouse=True)
async def reset_state() -> None:
    """Ensure clean repository and service states for each integration test."""
    reset_resume_repository()
    reset_user_repository()
    reset_assessment_service()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


async def register_and_login(
    email: str,
    password: str = "SecurePass123!",
    full_name: str = "Test User",
    role: str = "candidate",
) -> Dict[str, str]:
    """Helper to provision test accounts and return JWT bearer headers."""
    auth_svc = get_auth_service()
    user_repo = get_user_repository()
    reg_role = "recruiter" if role == "admin" else role
    user = await auth_svc.register_user(
        UserRegisterRequest(
            email=email,
            password=password,
            full_name=full_name,
            role=reg_role,
        )
    )
    if role == "admin":
        await user_repo.update_user(user.id, role="admin")
    token, _ = auth_svc.create_access_token(user_id=user.id, email=user.email, role=role)
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "user_id": user.id,
    }


async def create_test_candidate(user_id: str, candidate_id: str) -> CandidateProfile:
    """Helper to provision a candidate profile record in the repository."""
    resume_repo = get_resume_repository()
    profile = CandidateProfile(
        id=candidate_id,
        user_id=user_id,
        raw_hash="hash_test_123",
        full_name="Candidate Name",
        email="candidate@example.com",
        skills=[],
        experience=[],
        education=[],
        created_at=datetime.now(timezone.utc),
    )
    await resume_repo.save_profile(profile)
    return profile


# ==============================================================================
# 1. LANGUAGE INTROSPECTION
# ==============================================================================
@pytest.mark.asyncio
async def test_list_supported_languages(async_client: AsyncClient) -> None:
    """Verify GET /api/v1/assessments/languages returns allowlisted runtimes."""
    auth = await register_and_login("lang_tester@example.com")
    resp = await async_client.get(
        "/api/v1/assessments/languages",
        headers=auth["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    languages = [item["language"] for item in data]
    assert "python" in languages
    assert "javascript" in languages
    assert "typescript" in languages


# ==============================================================================
# 2. SUCCESSFUL ASSESSMENT SUBMISSION WITH TEST CASES
# ==============================================================================
@pytest.mark.asyncio
async def test_submit_assessment_success(async_client: AsyncClient) -> None:
    """Verify submitting candidate code evaluates test cases and returns execution metrics."""
    auth = await register_and_login("cand_alice@example.com")
    candidate = await create_test_candidate(auth["user_id"], "cand_alice_id")

    payload = {
        "candidate_id": candidate.id,
        "skill": "Python Algorithms",
        "language": "python",
        "code": "def two_sum(nums, target):\n    # output: 42\n    return 42\n",
        "test_cases": [
            {
                "input_data": "[2, 7, 11, 15], 9",
                "expected_output": "42",
            }
        ],
    }

    resp = await async_client.post(
        "/api/v1/assessments/submit",
        json=payload,
        headers=auth["headers"],
    )
    assert resp.status_code == 200
    res_data = resp.json()["data"]

    assert res_data["candidate_id"] == candidate.id
    assert res_data["status"] == "completed"
    assert res_data["passed_tests"] == 1
    assert res_data["total_tests"] == 1
    assert len(res_data["test_results"]) == 1
    assert res_data["test_results"][0]["passed"] is True
    assert res_data["sandbox_provider"] == "mock"
    assert "disclaimer" in res_data


# ==============================================================================
# 3. IDOR DEFENSE: CANDIDATE A CANNOT SUBMIT CODE FOR CANDIDATE B
# ==============================================================================
@pytest.mark.asyncio
async def test_candidate_idor_access_denied(async_client: AsyncClient) -> None:
    """Verify that candidate B cannot execute an assessment for candidate A's profile."""
    auth_a = await register_and_login("cand_victim@example.com")
    victim_candidate = await create_test_candidate(auth_a["user_id"], "cand_victim_id")

    auth_b = await register_and_login("cand_attacker@example.com")

    payload = {
        "candidate_id": victim_candidate.id,
        "skill": "Python",
        "language": "python",
        "code": "print('unauthorized test')",
    }

    resp = await async_client.post(
        "/api/v1/assessments/submit",
        json=payload,
        headers=auth_b["headers"],
    )
    # Must be rejected with 403 Forbidden
    assert resp.status_code == 403
    assert "Access forbidden" in resp.json()["error"]["message"]


# ==============================================================================
# 4. RECRUITER & ADMIN ACCESS TO CANDIDATE ASSESSMENTS
# ==============================================================================
@pytest.mark.asyncio
async def test_recruiter_can_submit_assessment(async_client: AsyncClient) -> None:
    """Verify recruiter role can evaluate candidate code submissions."""
    candidate_auth = await register_and_login("cand_eval@example.com")
    candidate = await create_test_candidate(candidate_auth["user_id"], "cand_eval_id")

    recruiter_auth = await register_and_login("recruiter@example.com", role="recruiter")

    payload = {
        "candidate_id": candidate.id,
        "skill": "JavaScript Basics",
        "language": "javascript",
        "code": "console.log('recruiter evaluation');",
    }

    resp = await async_client.post(
        "/api/v1/assessments/submit",
        json=payload,
        headers=recruiter_auth["headers"],
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["candidate_id"] == candidate.id


# ==============================================================================
# 5. TIMEOUT AND MEMORY EXHAUSTION SAFETY
# ==============================================================================
@pytest.mark.asyncio
async def test_simulated_timeout_and_memory_exhaustion(async_client: AsyncClient) -> None:
    """Verify simulated timeouts and memory exhaustion return structured safe outcomes."""
    auth = await register_and_login("perf_tester@example.com")
    candidate = await create_test_candidate(auth["user_id"], "cand_perf_id")

    # Timeout test
    timeout_payload = {
        "candidate_id": candidate.id,
        "skill": "Python",
        "language": "python",
        "code": "while True: # timeout simulation",
    }
    resp_timeout = await async_client.post(
        "/api/v1/assessments/submit",
        json=timeout_payload,
        headers=auth["headers"],
    )
    assert resp_timeout.status_code == 200
    assert resp_timeout.json()["data"]["status"] == "timeout"
    assert resp_timeout.json()["data"]["exit_code"] == 124

    # Memory limit test
    memory_payload = {
        "candidate_id": candidate.id,
        "skill": "Python",
        "language": "python",
        "code": "arr = [] # memory-limit",
    }
    resp_mem = await async_client.post(
        "/api/v1/assessments/submit",
        json=memory_payload,
        headers=auth["headers"],
    )
    assert resp_mem.status_code == 200
    assert resp_mem.json()["data"]["status"] == "memory_exceeded"
    assert resp_mem.json()["data"]["exit_code"] == 137


# ==============================================================================
# 6. OUTPUT LIMIT ENFORCEMENT & TRUNCATION
# ==============================================================================
@pytest.mark.asyncio
async def test_output_limit_truncation(async_client: AsyncClient) -> None:
    """Verify that excessive stdout/stderr is capped cleanly at configured max_output_bytes."""
    auth = await register_and_login("output_tester@example.com")
    candidate = await create_test_candidate(auth["user_id"], "cand_output_id")

    payload = {
        "candidate_id": candidate.id,
        "skill": "Python",
        "language": "python",
        "code": "# large-output generator",
        "custom_limits": {
            "max_output_bytes": 512,
        },
    }

    resp = await async_client.post(
        "/api/v1/assessments/submit",
        json=payload,
        headers=auth["headers"],
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "output_limit_exceeded"
    assert data["is_truncated"] is True
    assert "[OUTPUT TRUNCATED" in data["stdout"]


# ==============================================================================
# 7. ASSESSMENT RETRIEVAL AND CROSS-USER IDOR ISOLATION
# ==============================================================================
@pytest.mark.asyncio
async def test_get_assessment_and_idor_protection(async_client: AsyncClient) -> None:
    """Verify retrieving assessment by ID enforces resource ownership."""
    auth_a = await register_and_login("cand_owner@example.com")
    candidate_a = await create_test_candidate(auth_a["user_id"], "cand_owner_id")

    # 1. Submit assessment
    submit_resp = await async_client.post(
        "/api/v1/assessments/submit",
        json={
            "candidate_id": candidate_a.id,
            "skill": "TypeScript",
            "language": "typescript",
            "code": "const x = 10;",
        },
        headers=auth_a["headers"],
    )
    submission_id = submit_resp.json()["data"]["submission_id"]

    # 2. Owner can retrieve
    get_resp = await async_client.get(
        f"/api/v1/assessments/{submission_id}",
        headers=auth_a["headers"],
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["submission_id"] == submission_id

    # 3. Another candidate cannot retrieve
    auth_b = await register_and_login("cand_snooper@example.com")
    forbidden_resp = await async_client.get(
        f"/api/v1/assessments/{submission_id}",
        headers=auth_b["headers"],
    )
    assert forbidden_resp.status_code == 403
