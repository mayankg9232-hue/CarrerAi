"""Phase 8 Integration Test Suite: Docker Sandbox Service & API Pipeline (Developer A).

Tests cover:
1. End-to-end AssessmentService evaluation with DockerSandboxExecutor.
2. Container timeout handling across test cases.
3. Memory ceiling (OOM) handling across test cases.
4. Output limit truncation through the assessment service layer.
5. Automatic safe fallback from Docker to Mock executor when Docker is unavailable.
6. Full FastAPI HTTP API endpoint submission using Docker executor.
"""

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
import pytest
from httpx import AsyncClient

from app.authentication.service import get_auth_service
from app.core.config import Settings
from app.main import app
from app.repositories.resume_repo import get_resume_repository, reset_resume_repository
from app.repositories.user_repo import get_user_repository, reset_user_repository
from app.sandbox.providers.docker_executor import DockerSandboxExecutor
from app.sandbox.service import SandboxService
from app.schemas.assessment import (
    AssessmentSubmissionRequest,
    ExecutionStatus,
    TestCase,
)
from app.schemas.auth import UserRegisterRequest
from app.schemas.resume import CandidateProfile
from app.services.assessment_service import (
    AssessmentApplicationService,
    get_assessment_service,
    reset_assessment_service,
)


@pytest.fixture(autouse=True)
async def reset_state() -> None:
    """Ensure clean test repositories and dependency overrides."""
    reset_resume_repository()
    reset_user_repository()
    reset_assessment_service()
    app.dependency_overrides.clear()
    yield
    app.dependency_overrides.clear()


async def register_candidate(email: str = "docker_cand@example.com") -> tuple[str, str, str]:
    """Helper to provision test candidate and return (user_id, candidate_id, bearer_token)."""
    auth_svc = get_auth_service()
    user = await auth_svc.register_user(
        UserRegisterRequest(
            email=email,
            password="SecurePassword123!",
            full_name="Docker Candidate",
            role="candidate",
        )
    )
    token, _ = auth_svc.create_access_token(user_id=user.id, email=user.email, role="candidate")

    candidate_id = f"cand_{user.id}"
    resume_repo = get_resume_repository()
    profile = CandidateProfile(
        id=candidate_id,
        user_id=user.id,
        raw_hash="hash_docker_test",
        full_name="Docker Candidate",
        email=email,
        skills=[],
        experience=[],
        education=[],
        created_at=datetime.now(timezone.utc),
    )
    await resume_repo.save_profile(profile)
    return user.id, candidate_id, token


# ==============================================================================
# 1. E2E ASSESSMENT EVALUATION WITH DOCKER EXECUTOR
# ==============================================================================
@pytest.mark.asyncio
async def test_docker_service_e2e_evaluation() -> None:
    """Verify SandboxService with DockerSandboxExecutor evaluates test cases and aggregates results."""
    mock_proc = MagicMock()
    # Mock container returning 42
    mock_proc.communicate = AsyncMock(return_value=(b"42\n", b""))
    mock_proc.returncode = 0

    mock_runner = AsyncMock(return_value=mock_proc)
    executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    service = SandboxService(executor=executor)

    req = AssessmentSubmissionRequest(
        candidate_id="cand_test_01",
        skill="Algorithms",
        language="python",
        code="print(42)",
        test_cases=[
            TestCase(id="tc_1", input_data="input_1", expected_output="42"),
            TestCase(id="tc_2", input_data="input_2", expected_output="42"),
        ],
    )

    result = await service.execute_assessment(req)

    assert result.status == ExecutionStatus.COMPLETED
    assert result.passed_tests == 2
    assert result.total_tests == 2
    assert result.sandbox_provider == "docker"
    assert len(result.test_results) == 2
    assert all(tr.passed for tr in result.test_results)


# ==============================================================================
# 2. DOCKER TIMEOUT ENFORCEMENT IN SERVICE LAYER
# ==============================================================================
@pytest.mark.asyncio
async def test_docker_service_timeout_handling() -> None:
    """Verify timeout in Docker execution reflects correctly in the aggregated assessment result."""
    import asyncio
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_proc.kill = MagicMock()

    mock_runner = AsyncMock(return_value=mock_proc)
    executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    service = SandboxService(executor=executor)

    req = AssessmentSubmissionRequest(
        candidate_id="cand_test_timeout",
        skill="Python",
        language="python",
        code="while True: pass",
        test_cases=[
            TestCase(id="tc_timeout", input_data="", expected_output="done"),
        ],
    )

    result = await service.execute_assessment(req)

    assert result.status == ExecutionStatus.TIMEOUT
    assert result.passed_tests == 0
    assert result.exit_code == 124
    assert "timed out" in result.stderr


# ==============================================================================
# 3. DOCKER MEMORY LIMIT (OOM) IN SERVICE LAYER
# ==============================================================================
@pytest.mark.asyncio
async def test_docker_service_memory_limit_handling() -> None:
    """Verify OOM kill in Docker execution reflects as MEMORY_EXCEEDED."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Killed\n"))
    mock_proc.returncode = 137

    mock_runner = AsyncMock(return_value=mock_proc)
    executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    service = SandboxService(executor=executor)

    req = AssessmentSubmissionRequest(
        candidate_id="cand_test_mem",
        skill="Python",
        language="python",
        code="arr = [1] * 100_000_000",
        test_cases=[
            TestCase(id="tc_mem", input_data="", expected_output="ok"),
        ],
    )

    result = await service.execute_assessment(req)

    assert result.status == ExecutionStatus.MEMORY_EXCEEDED
    assert result.passed_tests == 0
    assert result.exit_code == 137
    assert "Memory limit" in result.stderr


# ==============================================================================
# 4. SAFE AUTOMATIC FALLBACK WHEN DOCKER DAEMON IS ABSENT
# ==============================================================================
@pytest.mark.asyncio
async def test_service_automatic_fallback_when_docker_absent() -> None:
    """Verify SandboxService automatically and safely falls back to mock when Docker is absent."""
    custom_settings = Settings(SANDBOX_PROVIDER="docker")
    # Do not supply executor so _resolve_executor() runs with real host state
    service = SandboxService(settings=custom_settings)

    # Because docker is absent on host, it must safely resolve to MockSandboxExecutor
    assert service.executor.get_provider_name() == "mock"

    # Execution must succeed via mock fallback without crashing
    req = AssessmentSubmissionRequest(
        candidate_id="cand_fallback",
        skill="Python",
        language="python",
        code="print('fallback success')",
    )
    result = await service.execute_assessment(req)
    assert result.status == ExecutionStatus.COMPLETED
    assert result.sandbox_provider == "mock"


# ==============================================================================
# 5. FULL FASTAPI HTTP API SUBMISSION USING DOCKER EXECUTOR
# ==============================================================================
@pytest.mark.asyncio
async def test_http_api_submission_with_docker_executor(async_client: AsyncClient) -> None:
    """Verify HTTP API endpoint /api/v1/assessments/submit functions with Docker executor."""
    _, candidate_id, token = await register_candidate("api_docker@example.com")

    # Prepare mock Docker executor
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"100\n", b""))
    mock_proc.returncode = 0

    mock_runner = AsyncMock(return_value=mock_proc)
    docker_executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    app_service = AssessmentApplicationService(
        sandbox_service=SandboxService(executor=docker_executor)
    )

    app.dependency_overrides[get_assessment_service] = lambda: app_service

    payload = {
        "candidate_id": candidate_id,
        "skill": "Python Algorithms",
        "language": "python",
        "code": "print(100)",
        "test_cases": [
            {
                "input_data": "",
                "expected_output": "100",
            }
        ],
    }

    resp = await async_client.post(
        "/api/v1/assessments/submit",
        json=payload,
        headers={"Authorization": f"Bearer {token}"},
    )

    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["candidate_id"] == candidate_id
    assert data["status"] == "completed"
    assert data["passed_tests"] == 1
    assert data["sandbox_provider"] == "docker"
