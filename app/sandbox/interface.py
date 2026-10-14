"""Abstract sandbox executor interface (Phase 7)."""

from abc import ABC, abstractmethod
from typing import Optional

from app.sandbox.limits import AssessmentLimits
from app.sandbox.models import RawExecutionResult, SandboxWorkspace


class ISandboxExecutor(ABC):
    """Abstract interface defining the contract for sandboxed code execution backends."""

    @abstractmethod
    def get_provider_name(self) -> str:
        """Return the name of this sandbox executor provider."""
        raise NotImplementedError

    @abstractmethod
    def is_available(self) -> bool:
        """Check whether this execution backend is available and configured on the host."""
        raise NotImplementedError

    @abstractmethod
    async def execute(
        self,
        submission_id: str,
        language: str,
        code: str,
        input_data: str,
        limits: AssessmentLimits,
        workspace: SandboxWorkspace,
    ) -> RawExecutionResult:
        """Execute candidate code inside the isolated environment.

        CRITICAL SECURITY INVARIANT:
        This method must NEVER execute untrusted candidate code inside the host Python process
        via exec(), eval(), or host shell execution.
        """
        raise NotImplementedError
