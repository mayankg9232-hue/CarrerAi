"""Sandbox orchestration service coordinating workspace lifecycle and test execution (Phase 7)."""

from datetime import datetime, timezone
import logging
from typing import Dict, List, Optional
import uuid

from app.core.config import Settings, get_settings
from app.sandbox.interface import ISandboxExecutor
from app.sandbox.limits import (
    SUPPORTED_LANGUAGE_SPECS,
    sanitize_limits,
    truncate_output,
    validate_code_payload,
    validate_language,
)
from app.sandbox.models import RawExecutionResult, SandboxWorkspace
from app.sandbox.providers.mock_executor import MockSandboxExecutor
from app.schemas.assessment import (
    AssessmentLimits,
    AssessmentResult,
    AssessmentSubmissionRequest,
    ExecutionStatus,
    SupportedLanguageSpec,
    TestCase,
    TestCaseResult,
)

logger = logging.getLogger("ai_job_matcher.sandbox.service")


class SandboxService:
    """Orchestrates candidate code assessment submissions against the configured sandbox executor."""

    def __init__(
        self,
        executor: Optional[ISandboxExecutor] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.executor = executor or self._resolve_executor()
        # In-memory persistence for local development and test retrieval
        self._results_store: Dict[str, AssessmentResult] = {}

    def _resolve_executor(self) -> ISandboxExecutor:
        """Resolve configured sandbox executor provider according to application settings."""
        provider = self.settings.SANDBOX_PROVIDER.lower().strip()
        if provider == "docker":
            from app.sandbox.providers.docker_executor import DockerSandboxExecutor
            docker_executor = DockerSandboxExecutor(settings=self.settings)
            if docker_executor.is_available():
                logger.info("Using DockerSandboxExecutor as active code execution provider.")
                return docker_executor
            else:
                logger.warning(
                    "Docker execution requested but Docker daemon/CLI is not available on host. "
                    "Falling back to MockSandboxExecutor."
                )
                return MockSandboxExecutor()

        if provider == "mock":
            return MockSandboxExecutor()

        logger.warning(f"Unknown sandbox provider '{provider}'; defaulting safely to MockSandboxExecutor")
        return MockSandboxExecutor()

    def get_supported_languages(self) -> List[SupportedLanguageSpec]:
        """Return metadata for all allowlisted assessment programming languages."""
        specs = []
        for lang, info in SUPPORTED_LANGUAGE_SPECS.items():
            specs.append(
                SupportedLanguageSpec(
                    language=lang,
                    display_name=str(info["display_name"]),
                    version=str(info["version"]),
                    default_timeout_seconds=float(info["default_timeout_seconds"]),
                    max_memory_mb=int(info["max_memory_mb"]),
                    is_active=bool(info["is_active"]),
                )
            )
        return specs

    def get_assessment_result(self, submission_id: str) -> Optional[AssessmentResult]:
        """Retrieve previously executed assessment result by submission ID."""
        return self._results_store.get(submission_id)

    async def execute_assessment(
        self,
        request: AssessmentSubmissionRequest,
    ) -> AssessmentResult:
        """Validate submission, orchestrate isolated workspace, execute tests, and aggregate results."""
        # 1. Validation of language and code bounds
        norm_language = validate_language(request.language)
        validate_code_payload(request.code)

        # 2. Sanitize and clamp execution limits
        effective_limits = sanitize_limits(request.custom_limits)

        submission_id = f"asm_{uuid.uuid4().hex[:12]}"
        logger.info(
            f"Initiating assessment submission {submission_id} for candidate {request.candidate_id} "
            f"[Language: {norm_language}, Skill: {request.skill}, Provider: {self.executor.get_provider_name()}]"
        )

        test_results: List[TestCaseResult] = []
        total_time_ms = 0.0
        max_mem_mb = 0.0
        overall_status = ExecutionStatus.COMPLETED
        combined_stdout_parts: List[str] = []
        combined_stderr_parts: List[str] = []
        is_any_truncated = False
        last_exit_code = 0

        # 3. If no test cases are specified, execute single execution pass
        active_test_cases = request.test_cases if request.test_cases else [
            TestCase(id=f"tc_{uuid.uuid4().hex[:6]}", input_data="", expected_output="")
        ]

        # 4. Execute test cases within isolated ephemeral workspace
        with SandboxWorkspace(submission_id=submission_id) as workspace:
            for tc in active_test_cases:
                raw: RawExecutionResult = await self.executor.execute(
                    submission_id=submission_id,
                    language=norm_language,
                    code=request.code,
                    input_data=tc.input_data,
                    limits=effective_limits,
                    workspace=workspace,
                )

                total_time_ms += raw.execution_time_ms
                max_mem_mb = max(max_mem_mb, raw.memory_used_mb)
                if raw.is_truncated:
                    is_any_truncated = True
                if raw.exit_code != 0:
                    last_exit_code = raw.exit_code

                # Accumulate logs
                if raw.stdout:
                    combined_stdout_parts.append(raw.stdout)
                if raw.stderr:
                    combined_stderr_parts.append(raw.stderr)

                # Determine test case pass/fail
                passed = False
                err_msg = raw.stderr or raw.error_message
                if raw.status == ExecutionStatus.COMPLETED and raw.exit_code == 0:
                    if tc.expected_output:
                        passed = (raw.stdout.strip() == tc.expected_output.strip())
                        if not passed and not err_msg:
                            err_msg = f"Expected '{tc.expected_output.strip()}', got '{raw.stdout.strip()}'"
                    else:
                        passed = True
                else:
                    overall_status = raw.status

                test_results.append(
                    TestCaseResult(
                        test_case_id=tc.id,
                        passed=passed,
                        actual_output="" if tc.is_hidden else raw.stdout.strip(),
                        expected_output="" if tc.is_hidden else tc.expected_output.strip(),
                        error_message=err_msg,
                        execution_time_ms=raw.execution_time_ms,
                    )
                )

                # Short-circuit on critical timeout or memory violation
                if raw.status in (ExecutionStatus.TIMEOUT, ExecutionStatus.MEMORY_EXCEEDED):
                    overall_status = raw.status
                    break

        # 5. Determine overall status if any test failed
        passed_count = sum(1 for r in test_results if r.passed)
        if overall_status == ExecutionStatus.COMPLETED and passed_count < len(test_results):
            overall_status = ExecutionStatus.ERROR

        combined_stdout = "\n".join(combined_stdout_parts)
        combined_stderr = "\n".join(combined_stderr_parts)

        # Final output bounds enforcement
        truncated_stdout, stdout_trunc = truncate_output(combined_stdout, effective_limits.max_output_bytes)
        truncated_stderr, stderr_trunc = truncate_output(combined_stderr, effective_limits.max_output_bytes)
        final_is_truncated = is_any_truncated or stdout_trunc or stderr_trunc

        result = AssessmentResult(
            submission_id=submission_id,
            candidate_id=request.candidate_id,
            skill=request.skill,
            language=norm_language,
            status=overall_status,
            exit_code=last_exit_code,
            stdout=truncated_stdout,
            stderr=truncated_stderr,
            passed_tests=passed_count,
            total_tests=len(test_results),
            test_results=test_results,
            execution_time_ms=round(total_time_ms, 2),
            memory_used_mb=round(max_mem_mb, 2),
            is_truncated=final_is_truncated,
            sandbox_provider=self.executor.get_provider_name(),
            created_at=datetime.now(timezone.utc),
        )

        self._results_store[submission_id] = result
        return result


_sandbox_service_instance: Optional[SandboxService] = None


def get_sandbox_service() -> SandboxService:
    """Singleton provider for SandboxService."""
    global _sandbox_service_instance
    if _sandbox_service_instance is None:
        _sandbox_service_instance = SandboxService()
    return _sandbox_service_instance


def reset_sandbox_service() -> None:
    """Reset sandbox service singleton for test isolation."""
    global _sandbox_service_instance
    _sandbox_service_instance = None

