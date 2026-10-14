"""Docker-based isolated container sandbox executor (Phase 8 Developer A).

Enforces unprivileged containers, zero network access, capability dropping, strict memory/CPU/PID limits,
and guaranteed container cleanup on success, timeout, or failure.
"""

import asyncio
import logging
from pathlib import Path
import shutil
import time
from typing import Any, Callable, List, Optional
import uuid

from app.core.config import Settings, get_settings
from app.sandbox.interface import ISandboxExecutor
from app.sandbox.limits import AssessmentLimits, truncate_output
from app.sandbox.models import RawExecutionResult, SandboxWorkspace
from app.schemas.assessment import ExecutionStatus, SupportedLanguage

logger = logging.getLogger("ai_job_matcher.sandbox.docker")


class DockerSandboxExecutor(ISandboxExecutor):
    """Hardened Docker container execution provider for candidate assessments."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        cli_path: Optional[str] = None,
        subprocess_runner: Optional[Callable[..., Any]] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.cli_path = cli_path or shutil.which("docker")
        self._subprocess_runner = subprocess_runner
        self._available: Optional[bool] = None

    def get_provider_name(self) -> str:
        return "docker"

    def is_available(self) -> bool:
        """Verify whether Docker CLI exists and docker daemon is reachable."""
        if self.cli_path is None:
            return False
        if self._available is not None:
            return self._available

        try:
            # Synchronous probe with short timeout
            import subprocess
            res = subprocess.run(
                [self.cli_path, "info"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=2.0,
            )
            self._available = (res.returncode == 0)
        except Exception:
            self._available = False

        return self._available

    def build_docker_run_command(
        self,
        container_name: str,
        workspace_path: Path,
        limits: AssessmentLimits,
        image: str,
        entry_script: str,
    ) -> List[str]:
        """Construct the hardened 'docker run' argument list with mandatory isolation flags."""
        return [
            self.cli_path or "docker",
            "run",
            "--name",
            container_name,
            "--rm",
            "--network",
            "none",                                   # 1. No network access
            "--cap-drop",
            "ALL",                                    # 2. Drop all Linux capabilities
            "--security-opt",
            "no-new-privileges:true",                 # 3. Prevent privilege escalation
            "--read-only",                            # 4. Read-only container root
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=32m",         # 5. Non-executable ephemeral /tmp
            f"--memory={limits.memory_mb}m",          # 6. Hard memory limit
            f"--memory-swap={limits.memory_mb}m",     # 7. Disable swap expansion
            f"--cpus={self.settings.SANDBOX_DOCKER_CPUS}",       # 8. CPU quota
            f"--pids-limit={self.settings.SANDBOX_DOCKER_PIDS_LIMIT}", # 9. Defend against fork bombs
            "--user",
            "10001:10001",                            # 10. Run strictly as unprivileged user
            "-v",
            f"{workspace_path}:/workspace:rw",        # 11. Ephemeral candidate workspace ONLY
            "-w",
            "/workspace",
            image,
            "python",
            entry_script,
        ]

    async def _ensure_container_cleanup(self, container_name: str) -> None:
        """Force remove container if it remains after interruption or timeout."""
        try:
            if self._subprocess_runner:
                await self._subprocess_runner(
                    self.cli_path or "docker", "rm", "-f", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    self.cli_path or "docker", "rm", "-f", container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await proc.wait()
        except Exception as exc:
            logger.debug(f"Cleanup probe for container {container_name}: {exc}")

    async def execute(
        self,
        submission_id: str,
        language: str,
        code: str,
        input_data: str,
        limits: AssessmentLimits,
        workspace: SandboxWorkspace,
    ) -> RawExecutionResult:
        """Execute submission inside hardened Docker container under strict resource constraints."""
        if language != SupportedLanguage.PYTHON.value:
            return RawExecutionResult(
                status=ExecutionStatus.REJECTED,
                exit_code=1,
                stderr=f"Docker sandbox executor currently supports Python; requested '{language}'",
            )

        # 1. Write submission file into isolated workspace
        entry_file = "solution.py"
        workspace.write_source_file(entry_file, code)
        if input_data:
            workspace.write_source_file("input.txt", input_data)

        image = self.settings.SANDBOX_DOCKER_PYTHON_IMAGE
        container_name = f"sandbox_{submission_id}_{uuid.uuid4().hex[:6]}"
        cmd = self.build_docker_run_command(
            container_name=container_name,
            workspace_path=workspace.path,
            limits=limits,
            image=image,
            entry_script=entry_file,
        )

        logger.info(
            f"Launching Docker sandbox container {container_name} "
            f"[Memory: {limits.memory_mb}MB, Timeout: {limits.timeout_seconds}s]"
        )

        start_time = time.perf_counter()
        proc: Optional[asyncio.subprocess.Process] = None

        try:
            stdin_data = input_data.encode("utf-8") if input_data else None

            if self._subprocess_runner:
                proc = await self._subprocess_runner(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if stdin_data else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdin=asyncio.subprocess.PIPE if stdin_data else None,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

            # Wait for execution bounded by wall-clock timeout
            stdout_bytes, stderr_bytes = await asyncio.wait_for(
                proc.communicate(input=stdin_data),
                timeout=limits.timeout_seconds,
            )

            execution_time_ms = (time.perf_counter() - start_time) * 1000.0
            raw_stdout = stdout_bytes.decode("utf-8", errors="replace")
            raw_stderr = stderr_bytes.decode("utf-8", errors="replace")
            exit_code = proc.returncode or 0

            # 2. Status analysis based on exit codes
            if exit_code == 0:
                status = ExecutionStatus.COMPLETED
            elif exit_code == 137:
                # OOM killer triggered by --memory limit
                status = ExecutionStatus.MEMORY_EXCEEDED
                raw_stderr = f"Process killed: Memory limit of {limits.memory_mb}MB exceeded\n" + raw_stderr
            elif exit_code == 124:
                status = ExecutionStatus.TIMEOUT
                raw_stderr = f"Process timed out after {limits.timeout_seconds:.1f}s\n" + raw_stderr
            else:
                status = ExecutionStatus.ERROR

            # 3. Output truncation enforcement
            safe_stdout, stdout_trunc = truncate_output(raw_stdout, limits.max_output_bytes)
            safe_stderr, stderr_trunc = truncate_output(raw_stderr, limits.max_output_bytes)

            return RawExecutionResult(
                status=status,
                exit_code=exit_code,
                stdout=safe_stdout,
                stderr=safe_stderr,
                execution_time_ms=round(execution_time_ms, 2),
                memory_used_mb=float(limits.memory_mb if status == ExecutionStatus.MEMORY_EXCEEDED else 25.0),
                is_truncated=stdout_trunc or stderr_trunc,
            )

        except asyncio.TimeoutError:
            # Kill process and remove container forcefully
            execution_time_ms = (time.perf_counter() - start_time) * 1000.0
            if proc:
                try:
                    proc.kill()
                except ProcessLookupError:
                    pass
            await self._ensure_container_cleanup(container_name)

            return RawExecutionResult(
                status=ExecutionStatus.TIMEOUT,
                exit_code=124,
                stdout="",
                stderr=f"Execution timed out: exceeded limit of {limits.timeout_seconds:.1f}s",
                execution_time_ms=round(execution_time_ms, 2),
                memory_used_mb=32.0,
                is_truncated=False,
            )

        except Exception as exc:
            execution_time_ms = (time.perf_counter() - start_time) * 1000.0
            logger.error(f"Docker sandbox execution exception: {exc}")
            return RawExecutionResult(
                status=ExecutionStatus.ERROR,
                exit_code=1,
                stdout="",
                stderr=f"Container execution error: {exc}",
                execution_time_ms=round(execution_time_ms, 2),
                memory_used_mb=0.0,
                is_truncated=False,
            )

        finally:
            await self._ensure_container_cleanup(container_name)
