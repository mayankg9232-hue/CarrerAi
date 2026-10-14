"""Phase 7 Unit Test Suite: Assessment Sandbox Design & Controlled Interface (Developer A).

Mandatory Phase 7 Test Cases:
1. Supported language accepted.
2. Unsupported language rejected.
3. Invalid / missing / blank code submission rejected.
4. Resource limit validation and bounds clamping.
5. Timeout and runner error return safe bounded results.
6. Isolated workspace lifecycle and traversal protection.
7. Host execution boundary verification: exec(), eval(), subprocess, and shell are NEVER called.
"""

import os
from pathlib import Path
import pytest
from unittest.mock import patch, MagicMock
from pydantic import ValidationError

from app.sandbox.interface import ISandboxExecutor
from app.sandbox.limits import (
    DEFAULT_MAX_OUTPUT_BYTES,
    DEFAULT_MEMORY_MB,
    DEFAULT_TIMEOUT_SECONDS,
    HARD_MAX_OUTPUT_BYTES,
    MAX_CODE_BYTES,
    MAX_TIMEOUT_SECONDS,
    MIN_MEMORY_MB,
    MIN_TIMEOUT_SECONDS,
    SUPPORTED_LANGUAGE_SPECS,
    clamp_limits,
    sanitize_limits,
    truncate_output,
    validate_code_payload,
    validate_language,
)
from app.sandbox.models import RawExecutionResult, SandboxWorkspace
from app.sandbox.providers.mock_executor import MockSandboxExecutor
from app.schemas.assessment import (
    AssessmentLimits,
    AssessmentSubmissionRequest,
    ExecutionStatus,
    SupportedLanguage,
    TestCase,
)


# ==============================================================================
# TEST CASE 1: SUPPORTED LANGUAGES ACCEPTED
# ==============================================================================
def test_supported_languages_accepted() -> None:
    """Verify that all allowlisted languages are valid and recognized."""
    for lang in [SupportedLanguage.PYTHON, SupportedLanguage.JAVASCRIPT, SupportedLanguage.TYPESCRIPT]:
        validated = validate_language(lang.value)
        assert validated == lang.value

    # Case insensitivity & whitespace trimming
    assert validate_language("  PYTHON  ") == "python"
    assert validate_language("JavaScript") == "javascript"
    assert validate_language("typescript") == "typescript"


# ==============================================================================
# TEST CASE 2: UNSUPPORTED LANGUAGES REJECTED
# ==============================================================================
def test_unsupported_languages_rejected() -> None:
    """Verify that unapproved or dangerous languages are rejected with ValueError."""
    forbidden_languages = ["bash", "sh", "ruby", "perl", "php", "c++", "rust", "malicious_eval"]
    for lang in forbidden_languages:
        with pytest.raises(ValueError, match="Unsupported language"):
            validate_language(lang)

    # In Pydantic schema validation as well
    with pytest.raises(ValidationError):
        AssessmentSubmissionRequest(
            candidate_id="cand_123",
            skill="Scripting",
            language="bash",
            code="echo hello",
        )


# ==============================================================================
# TEST CASE 3: INVALID OR MISSING CODE SUBMISSION REJECTED
# ==============================================================================
def test_invalid_or_missing_submission_rejected() -> None:
    """Verify empty code, whitespace-only, and oversized submissions are rejected."""
    # 1. Empty string
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_code_payload("")

    # 2. Whitespace only
    with pytest.raises(ValueError, match="cannot be empty"):
        validate_code_payload("   \n\t  ")

    # 3. Oversized payload
    huge_code = "x = 1\n" * 20000  # > 64 KB
    assert len(huge_code.encode("utf-8")) > MAX_CODE_BYTES
    with pytest.raises(ValueError, match="exceeds maximum limit"):
        validate_code_payload(huge_code)

    # 4. Schema rejection
    with pytest.raises(ValidationError):
        AssessmentSubmissionRequest(
            candidate_id="cand_123",
            skill="Python",
            language="python",
            code="   ",
        )


# ==============================================================================
# TEST CASE 4: RESOURCE LIMIT VALIDATION AND BOUNDS CLAMPING
# ==============================================================================
def test_resource_limits_validation_and_clamping() -> None:
    """Verify limits are strictly clamped within min/max security thresholds."""
    # Default limits
    defaults = sanitize_limits(None)
    assert defaults.timeout_seconds == DEFAULT_TIMEOUT_SECONDS
    assert defaults.memory_mb == DEFAULT_MEMORY_MB
    assert defaults.max_output_bytes == DEFAULT_MAX_OUTPUT_BYTES

    # Clamping excessive limits via clamp_limits
    clamped = clamp_limits(timeout_seconds=60.0, memory_mb=1024, max_output_bytes=200 * 1024)
    assert clamped.timeout_seconds == MAX_TIMEOUT_SECONDS
    assert clamped.memory_mb == 256  # Hard max
    assert clamped.max_output_bytes == HARD_MAX_OUTPUT_BYTES

    # Clamping too low limits (< min)
    clamped_low = clamp_limits(timeout_seconds=0.01, memory_mb=1, max_output_bytes=10)
    assert clamped_low.timeout_seconds == MIN_TIMEOUT_SECONDS
    assert clamped_low.memory_mb == MIN_MEMORY_MB
    assert clamped_low.max_output_bytes == 256

    # Schema bounds validation rejects out-of-range bounds directly
    with pytest.raises(ValidationError):
        AssessmentLimits(timeout_seconds=60.0)

    with pytest.raises(ValidationError):
        AssessmentLimits(memory_mb=16)


# ==============================================================================
# TEST CASE 5: TIMEOUT AND RUNNER ERROR RETURN SAFE BOUNDED RESULTS
# ==============================================================================
@pytest.mark.asyncio
async def test_timeout_and_error_simulation_safe_results() -> None:
    """Verify mock executor handles timeouts and errors returning safe bounded objects."""
    executor = MockSandboxExecutor()
    limits = sanitize_limits(None)

    with SandboxWorkspace(submission_id="sub_test_timeout") as ws:
        # Timeout marker
        timeout_res = await executor.execute(
            submission_id="sub_test_timeout",
            language="python",
            code="def loop():\n    # timeout simulation\n    pass",
            input_data="",
            limits=limits,
            workspace=ws,
        )
        assert timeout_res.status == ExecutionStatus.TIMEOUT
        assert timeout_res.exit_code == 124
        assert "timed out" in timeout_res.stderr

        # Error marker
        err_res = await executor.execute(
            submission_id="sub_test_err",
            language="python",
            code="def error():\n    # syntax-error\n    pass",
            input_data="",
            limits=limits,
            workspace=ws,
        )
        assert err_res.status == ExecutionStatus.ERROR
        assert err_res.exit_code == 1
        assert "Simulated Execution Error" in err_res.stderr


# ==============================================================================
# TEST CASE 6: ISOLATED WORKSPACE LIFECYCLE & PATH TRAVERSAL DEFENSE
# ==============================================================================
def test_isolated_workspace_lifecycle_and_traversal_defense() -> None:
    """Verify ephemeral directory creation, permissions, file writes, and clean purge."""
    workspace_dir: Path

    with SandboxWorkspace(submission_id="test_ws_life") as ws:
        workspace_dir = ws.path
        assert workspace_dir is not None
        assert workspace_dir.exists()
        assert workspace_dir.is_dir()

        # Write code file
        src = ws.write_source_file("solution.py", "print('hello isolated world')")
        assert src.exists()
        assert src.read_text(encoding="utf-8") == "print('hello isolated world')"

        # Path traversal rejection
        with pytest.raises(ValueError, match="Path traversal detected"):
            ws.write_source_file("../../malicious.sh", "rm -rf /")

    # Outside the context manager: guaranteed deterministic cleanup
    assert not workspace_dir.exists()


# ==============================================================================
# TEST CASE 7: TRUNCATION ENFORCEMENT ON EXCESSIVE OUTPUT
# ==============================================================================
def test_output_truncation_enforcement() -> None:
    """Verify output exceeding max_output_bytes is deterministically truncated."""
    long_text = "A" * 5000
    truncated, was_truncated = truncate_output(long_text, max_bytes=1000)
    assert was_truncated is True
    assert len(truncated.encode("utf-8")) <= 1000 + len("\n[OUTPUT TRUNCATED: exceeded limit of 1000 bytes]")
    assert "[OUTPUT TRUNCATED" in truncated

    short_text = "Hello world"
    untruncated, was_truncated = truncate_output(short_text, max_bytes=1000)
    assert was_truncated is False
    assert untruncated == short_text


# ==============================================================================
# TEST CASE 8: STRICT SECURITY INVARIANT — HOST EXEC/EVAL/SUBPROCESS NEVER CALLED
# ==============================================================================
@pytest.mark.asyncio
async def test_candidate_code_never_executes_on_host() -> None:
    """CRITICAL SECURITY GUARANTEE:
    Verify that candidate-submitted code is NEVER passed to host exec(), eval(),
    subprocess.run(), subprocess.Popen(), or os.system() during sandbox execution.
    """
    executor = MockSandboxExecutor()
    limits = sanitize_limits(None)
    malicious_code = """
import os
import sys
# Attempt dangerous host operations
os.system('id')
open('/etc/passwd', 'r').read()
"""

    with patch("builtins.exec") as mock_exec, \
         patch("builtins.eval") as mock_eval, \
         patch("subprocess.run") as mock_sub_run, \
         patch("subprocess.Popen") as mock_sub_popen, \
         patch("os.system") as mock_os_system:

        with SandboxWorkspace(submission_id="sub_sec_check") as ws:
            res = await executor.execute(
                submission_id="sub_sec_check",
                language="python",
                code=malicious_code,
                input_data="",
                limits=limits,
                workspace=ws,
            )

        # Assert NONE of the host execution primitives were called
        mock_exec.assert_not_called()
        mock_eval.assert_not_called()
        mock_sub_run.assert_not_called()
        mock_sub_popen.assert_not_called()
        mock_os_system.assert_not_called()

        assert res.status == ExecutionStatus.COMPLETED
