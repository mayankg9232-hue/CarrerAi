"""Mock sandbox executor for local testing and deterministic validation (Phase 7).

CRITICAL SECURITY GUARANTEE:
This mock executor NEVER passes candidate code to host exec(), eval(), or host subprocess/shell execution.
All execution outcomes are safely simulated or derived via static inspection.
"""

import logging
from typing import Dict, Optional

from app.sandbox.interface import ISandboxExecutor
from app.sandbox.limits import AssessmentLimits, truncate_output
from app.sandbox.models import RawExecutionResult, SandboxWorkspace
from app.schemas.assessment import ExecutionStatus

logger = logging.getLogger("ai_job_matcher.sandbox.mock")


class MockSandboxExecutor(ISandboxExecutor):
    """Safe, non-executing mock sandbox runner for local testing and development."""

    def __init__(
        self,
        default_stdout: Optional[str] = None,
        force_timeout: bool = False,
        force_error: bool = False,
    ) -> None:
        self.default_stdout = default_stdout
        self.force_timeout = force_timeout
        self.force_error = force_error

    def get_provider_name(self) -> str:
        return "mock"

    def is_available(self) -> bool:
        return True

    async def execute(
        self,
        submission_id: str,
        language: str,
        code: str,
        input_data: str,
        limits: AssessmentLimits,
        workspace: SandboxWorkspace,
    ) -> RawExecutionResult:
        """Simulate sandboxed execution without ever invoking the host runtime."""
        # 1. Exercise workspace filesystem isolation & permission assignment
        ext = "py" if language == "python" else ("js" if language == "javascript" else "ts")
        workspace.write_source_file(f"submission.{ext}", code)
        if input_data:
            workspace.write_source_file("input.txt", input_data)

        # 2. Check forced simulator directives or code markers
        code_lower = code.lower()

        # Timeout simulation
        if self.force_timeout or "simulate_timeout" in code_lower or "# timeout" in code_lower:
            return RawExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                exit_code=124,
                stdout="",
                stderr=f"Execution timed out: exceeded limit of {limits.timeout_seconds:.1f}s",
                execution_time_ms=limits.timeout_seconds * 1000.0,
                memory_used_mb=32.0,
                is_truncated=False,
            )

        # Memory exceeded simulation
        if "simulate_memory" in code_lower or "# memory-limit" in code_lower:
            return RawExecutionResult(
                status=ExecutionStatus.MEMORY_EXCEEDED,
                exit_code=137,
                stdout="",
                stderr=f"Process terminated: memory limit of {limits.memory_mb}MB exceeded",
                execution_time_ms=45.0,
                memory_used_mb=float(limits.memory_mb + 16),
                is_truncated=False,
            )

        # Runtime/syntax error simulation
        if self.force_error or "simulate_error" in code_lower or "# syntax-error" in code_lower:
            return RawExecutionResult(
                status=ExecutionStatus.ERROR,
                exit_code=1,
                stdout="",
                stderr="Simulated Execution Error: non-zero exit code returned",
                execution_time_ms=12.0,
                memory_used_mb=28.0,
                is_truncated=False,
            )

        # Excessive output simulation
        if "simulate_large_output" in code_lower or "# large-output" in code_lower:
            huge_output = "Line of debug output\n" * 4000
            truncated_stdout, is_truncated = truncate_output(huge_output, limits.max_output_bytes)
            return RawExecutionResult(
                status=ExecutionStatus.OUTPUT_LIMIT_EXCEEDED if is_truncated else ExecutionStatus.COMPLETED,
                exit_code=0,
                stdout=truncated_stdout,
                stderr="",
                execution_time_ms=25.0,
                memory_used_mb=35.0,
                is_truncated=is_truncated,
            )

        # 3. Normal simulated completion
        # If code contains directive '# expected: <val>', use it as simulated stdout
        simulated_stdout = self.default_stdout
        if simulated_stdout is None:
            if "# output:" in code:
                simulated_stdout = code.split("# output:")[1].split("\n")[0].strip()
            elif input_data:
                # Normal deterministic echo/processing simulation
                simulated_stdout = input_data.strip()
            else:
                simulated_stdout = "Execution completed successfully."

        # Truncate according to limit
        safe_stdout, is_truncated = truncate_output(simulated_stdout, limits.max_output_bytes)

        return RawExecutionResult(
            status=ExecutionStatus.COMPLETED,
            exit_code=0,
            stdout=safe_stdout,
            stderr="",
            execution_time_ms=18.5,
            memory_used_mb=31.2,
            is_truncated=is_truncated,
        )
