"""Assessment Application Service enforcing candidate ownership, IDOR isolation, and sandbox dispatch (Developer A lead)."""

from typing import List, Optional

from app.authorization.policies import assert_resource_owner
from app.core.exceptions import NotFoundError
from app.core.logging import logger
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.sandbox.service import SandboxService
from app.schemas.assessment import (
    AssessmentResult,
    AssessmentSubmissionRequest,
    SupportedLanguageSpec,
)
from app.schemas.auth import AuthenticatedPrincipal
from app.services.skill_verification_service import (
    SkillVerificationService,
    get_skill_verification_service,
)


class AssessmentApplicationService:
    """Application-level service orchestrating candidate code assessments, access control, and sandbox execution."""

    def __init__(
        self,
        sandbox_service: Optional[SandboxService] = None,
        resume_repo: Optional[IResumeRepository] = None,
        skill_verifier: Optional[SkillVerificationService] = None,
    ) -> None:
        self.sandbox = sandbox_service or SandboxService()
        self.resume_repo = resume_repo or get_resume_repository()
        self.skill_verifier = skill_verifier or get_skill_verification_service()

    def get_supported_languages(self) -> List[SupportedLanguageSpec]:
        """Return allowlisted programming languages and execution limits."""
        return self.sandbox.get_supported_languages()

    async def submit_assessment(
        self,
        request: AssessmentSubmissionRequest,
        principal: AuthenticatedPrincipal,
    ) -> AssessmentResult:
        """Authorize candidate ownership (IDOR defense) and execute assessment in sandbox."""
        # 1. Verify candidate profile exists
        candidate = await self.resume_repo.get_by_id(request.candidate_id)
        if not candidate:
            raise NotFoundError(f"Candidate profile '{request.candidate_id}' not found.")

        # 2. Enforce strict IDOR isolation: only profile owner, recruiter, or admin may submit assessments
        assert_resource_owner(
            resource_user_id=candidate.user_id,
            principal=principal,
            allow_admin=True,
            allowed_roles=("recruiter", "admin"),
        )

        # 3. Dispatch to sandbox execution service
        result = await self.sandbox.execute_assessment(request)

        # 4. Developer B Skill Verification and profile certification
        try:
            await self.skill_verifier.evaluate_and_verify(result)
        except Exception as exc:
            logger.warning("Skill verification hook failed for submission %s: %s", result.submission_id, exc)

        # 5. Redacted audit event
        logger.info(
            "Assessment Executed: submission_id=%s candidate_id=%s skill=%s language=%s "
            "status=%s passed=%d/%d duration_ms=%.1f caller_id=%s role=%s",
            result.submission_id,
            result.candidate_id,
            result.skill,
            result.language,
            result.status,
            result.passed_tests,
            result.total_tests,
            result.execution_time_ms,
            principal.id,
            principal.role,
        )

        return result

    async def get_assessment(
        self,
        submission_id: str,
        principal: AuthenticatedPrincipal,
    ) -> AssessmentResult:
        """Retrieve assessment result verifying that caller owns the candidate profile."""
        result = self.sandbox.get_assessment_result(submission_id)
        if not result:
            raise NotFoundError(f"Assessment submission '{submission_id}' not found.")

        # IDOR verification
        candidate = await self.resume_repo.get_by_id(result.candidate_id)
        if candidate:
            assert_resource_owner(
                resource_user_id=candidate.user_id,
                principal=principal,
                allow_admin=True,
                allowed_roles=("recruiter", "admin"),
            )

        return result


_assessment_service_instance: Optional[AssessmentApplicationService] = None


def get_assessment_service() -> AssessmentApplicationService:
    """Singleton provider for AssessmentApplicationService."""
    global _assessment_service_instance
    if _assessment_service_instance is None:
        _assessment_service_instance = AssessmentApplicationService()
    return _assessment_service_instance


def reset_assessment_service() -> None:
    """Reset assessment service singleton for test isolation."""
    global _assessment_service_instance
    _assessment_service_instance = None
