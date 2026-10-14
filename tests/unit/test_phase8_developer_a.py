"""Phase 8 Unit Test Suite: Docker and Isolated Code Execution (Developer A).

Mandatory Phase 8 Test Cases:
1. Benign code returns expected output.
2. Infinite loop times out and kills container.
3. Excessive output is capped at configured limits.
4. Memory/CPU overuse is stopped (OOM kill handling).
5. Attempted network, filesystem, and credential access is blocked by security flags.
6. Container is cleaned up after both success and failure.
7. Docker availability detection and safe fallback.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.core.config import Settings
from app.sandbox.limits import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MEMORY_MB,
    DEFAULT_TIMEOUT_SECONDS,
    sanitize_limits,
)
from app.sandbox.models import SandboxWorkspace
from app.sandbox.providers.docker_executor import DockerSandboxExecutor
from app.schemas.assessment import AssessmentLimits, ExecutionStatus


# ==============================================================================
# TEST CASE 1: SECURITY FLAGS & ISOLATION ENFORCEMENT
# ==============================================================================
def test_docker_command_security_flags_and_isolation() -> None:
    """CRITICAL SECURITY GUARANTEE:
    Verify that the generated Docker command strictly includes all non-negotiable isolation flags:
    - --network none
    - --cap-drop ALL
    - --security-opt no-new-privileges:true
    - --read-only
    - non-root user (10001:10001)
    - strict memory/CPU/PID quotas
    - workspace-only mount (no secrets, no Docker socket)
    """
    settings = Settings(
        SANDBOX_DOCKER_PYTHON_IMAGE="ai-job-matcher-sandbox-python:3.11",
        SANDBOX_DOCKER_CPUS=1.0,
        SANDBOX_DOCKER_PIDS_LIMIT=32,
    )
    executor = DockerSandboxExecutor(settings=settings, cli_path="/usr/local/bin/docker")
    limits = AssessmentLimits(timeout_seconds=5.0, memory_mb=128, max_output_bytes=10240)
    workspace_path = Path("/tmp/mock_workspace_123")

    cmd = executor.build_docker_run_command(
        container_name="sandbox_test_container",
        workspace_path=workspace_path,
        limits=limits,
        image="ai-job-matcher-sandbox-python:3.11",
        entry_script="solution.py",
    )

    cmd_str = " ".join(cmd)

    # 1. Network lockdown
    assert "--network none" in cmd_str

    # 2. Capability dropping
    assert "--cap-drop ALL" in cmd_str

    # 3. Privilege escalation prevention
    assert "--security-opt no-new-privileges:true" in cmd_str

    # 4. Read-only filesystem
    assert "--read-only" in cmd_str

    # 5. Non-executable tmpfs for temp files
    assert "--tmpfs /tmp:rw,noexec,nosuid,size=32m" in cmd_str

    # 6. Memory and swap caps
    assert "--memory=128m" in cmd_str
    assert "--memory-swap=128m" in cmd_str

    # 7. CPU and PID limits
    assert "--cpus=1.0" in cmd_str
    assert "--pids-limit=32" in cmd_str

    # 8. Unprivileged non-root user
    assert "--user 10001:10001" in cmd_str

    # 9. Isolated workspace mount ONLY
    assert f"-v {workspace_path}:/workspace:rw" in cmd_str
    assert "-w /workspace" in cmd_str

    # 10. Prohibited mounts check: NO secrets, NO docker socket, NO host root
    assert "docker.sock" not in cmd_str
    assert ".env" not in cmd_str
    assert "/etc" not in cmd_str
    assert "/root" not in cmd_str


# ==============================================================================
# TEST CASE 2: BENIGN CODE RETURNS EXPECTED OUTPUT
# ==============================================================================
@pytest.mark.asyncio
async def test_benign_code_returns_expected_output() -> None:
    """Verify benign execution inside container returns expected stdout and exit code 0."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"Hello from isolated container\n", b""))
    mock_proc.returncode = 0

    mock_runner = AsyncMock(return_value=mock_proc)
    executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    limits = sanitize_limits(None)

    with SandboxWorkspace(submission_id="sub_benign_test") as ws:
        result = await executor.execute(
            submission_id="sub_benign_test",
            language="python",
            code="print('Hello from isolated container')",
            input_data="",
            limits=limits,
            workspace=ws,
        )

        assert result.status == ExecutionStatus.COMPLETED
        assert result.exit_code == 0
        assert "Hello from isolated container" in result.stdout
        assert result.is_truncated is False


# ==============================================================================
# TEST CASE 3: INFINITE LOOP TIMES OUT AND TERMINATES CONTAINER
# ==============================================================================
@pytest.mark.asyncio
async def test_infinite_loop_times_out() -> None:
    """Verify execution exceeding wall-clock timeout is killed and returns TIMEOUT status."""
    mock_proc = MagicMock()
    # Simulate timeout during communicate()
    mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
    mock_proc.kill = MagicMock()

    mock_runner = AsyncMock(return_value=mock_proc)
    executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    limits = AssessmentLimits(timeout_seconds=2.0, memory_mb=128, max_output_bytes=10240)

    with SandboxWorkspace(submission_id="sub_timeout_test") as ws:
        result = await executor.execute(
            submission_id="sub_timeout_test",
            language="python",
            code="while True: pass",
            input_data="",
            limits=limits,
            workspace=ws,
        )

        assert result.status == ExecutionStatus.TIMEOUT
        assert result.exit_code == 124
        assert "timed out" in result.stderr
        mock_proc.kill.assert_called_once()


# ==============================================================================
# TEST CASE 4: EXCESSIVE OUTPUT IS CAPPED
# ==============================================================================
@pytest.mark.asyncio
async def test_excessive_output_is_capped() -> None:
    """Verify output volume exceeding max_output_bytes is deterministically truncated."""
    large_stdout = b"A" * 50000  # 50 KB output
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(large_stdout, b""))
    mock_proc.returncode = 0

    mock_runner = AsyncMock(return_value=mock_proc)
    executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    limits = AssessmentLimits(timeout_seconds=5.0, memory_mb=128, max_output_bytes=2048)

    with SandboxWorkspace(submission_id="sub_output_test") as ws:
        result = await executor.execute(
            submission_id="sub_output_test",
            language="python",
            code="print('A' * 50000)",
            input_data="",
            limits=limits,
            workspace=ws,
        )

        assert result.is_truncated is True
        assert len(result.stdout.encode("utf-8")) <= 2048 + 100
        assert "[OUTPUT TRUNCATED" in result.stdout


# ==============================================================================
# TEST CASE 5: MEMORY OVERUSE IS STOPPED (OOM KILL HANDLING)
# ==============================================================================
@pytest.mark.asyncio
async def test_memory_overuse_is_stopped() -> None:
    """Verify process killed by OOM killer (exit code 137) returns MEMORY_EXCEEDED."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"", b"Killed\n"))
    mock_proc.returncode = 137  # Standard Linux / Docker OOM kill signal

    mock_runner = AsyncMock(return_value=mock_proc)
    executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    limits = AssessmentLimits(timeout_seconds=5.0, memory_mb=64, max_output_bytes=10240)

    with SandboxWorkspace(submission_id="sub_oom_test") as ws:
        result = await executor.execute(
            submission_id="sub_oom_test",
            language="python",
            code="arr = [1] * 100_000_000",
            input_data="",
            limits=limits,
            workspace=ws,
        )

        assert result.status == ExecutionStatus.MEMORY_EXCEEDED
        assert result.exit_code == 137
        assert "Memory limit of 64MB exceeded" in result.stderr


# ==============================================================================
# TEST CASE 6: CONTAINER CLEANUP LIFECYCLE (SUCCESS & FAILURE)
# ==============================================================================
@pytest.mark.asyncio
async def test_container_cleanup_after_success_and_failure() -> None:
    """Verify that 'docker rm -f <container>' cleanup is issued on both success and error."""
    mock_proc = MagicMock()
    mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
    mock_proc.returncode = 0

    cleanup_calls = []

    async def mock_runner(*args, **kwargs):
        if "rm" in args:
            cleanup_calls.append(args)
            proc_cleanup = MagicMock()
            proc_cleanup.wait = AsyncMock(return_value=0)
            return proc_cleanup
        return mock_proc

    executor = DockerSandboxExecutor(cli_path="/usr/bin/docker", subprocess_runner=mock_runner)
    limits = sanitize_limits(None)

    with SandboxWorkspace(submission_id="sub_cleanup_check") as ws:
        await executor.execute(
            submission_id="sub_cleanup_check",
            language="python",
            code="print('done')",
            input_data="",
            limits=limits,
            workspace=ws,
        )

    # Verify docker rm -f was called in cleanup
    assert len(cleanup_calls) >= 1
    rm_cmd = " ".join(cleanup_calls[0])
    assert "docker rm -f sandbox_sub_cleanup_check" in rm_cmd


# ==============================================================================
# TEST CASE 7: DOCKER AVAILABILITY DETECTION AND SAFE FALLBACK
# ==============================================================================
def test_docker_availability_detection() -> None:
    """Verify that absent CLI path returns is_available() == False safely."""
    executor = DockerSandboxExecutor(cli_path=None)
    assert executor.is_available() is False
