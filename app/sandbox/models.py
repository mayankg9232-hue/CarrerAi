"""Sandbox domain models, execution payloads, and isolated workspace lifecycle (Phase 7)."""

import os
from pathlib import Path
import shutil
import tempfile
from typing import Optional
from pydantic import BaseModel, Field

from app.schemas.assessment import AssessmentLimits, ExecutionStatus


class RawExecutionResult(BaseModel):
    """Raw low-level result returned by a sandbox executor implementation."""
    status: ExecutionStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    execution_time_ms: float = 0.0
    memory_used_mb: float = 0.0
    is_truncated: bool = False
    error_message: Optional[str] = None


class SandboxWorkspace:
    """Isolated ephemeral directory workspace with strict lifecycle and deterministic cleanup."""

    def __init__(self, submission_id: str, base_temp_dir: Optional[str] = None) -> None:
        self.submission_id = submission_id
        self.base_temp_dir = base_temp_dir
        self.path: Optional[Path] = None

    def __enter__(self) -> "SandboxWorkspace":
        prefix = f"sandbox_{self.submission_id}_"
        temp_path = tempfile.mkdtemp(prefix=prefix, dir=self.base_temp_dir)
        self.path = Path(temp_path).resolve()
        # Enforce restricted directory permissions (rwx------ / 0o700)
        try:
            os.chmod(self.path, 0o700)
        except OSError:
            pass
        return self

    def __exit__(self, exc_type: object, exc_val: object, exc_tb: object) -> None:
        self.cleanup()

    def cleanup(self) -> None:
        """Deterministically purge the temporary directory workspace and contents."""
        if self.path and self.path.exists():
            try:
                shutil.rmtree(self.path, ignore_errors=True)
            except Exception:
                pass
            self.path = None

    def write_source_file(self, filename: str, content: str) -> Path:
        """Write source file into isolated workspace with restricted file permissions."""
        if not self.path or not self.path.exists():
            raise RuntimeError("Sandbox workspace has not been initialized or has already been cleaned up")
        
        target_path = (self.path / filename).resolve()
        # Prevent path traversal outside the workspace
        if not str(target_path).startswith(str(self.path)):
            raise ValueError(f"Path traversal detected: {filename}")

        target_path.write_text(content, encoding="utf-8")
        try:
            os.chmod(target_path, 0o600)
        except OSError:
            pass
        return target_path
