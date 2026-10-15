"""Developer B Skill Verification Service (Phase 7 & 8).

Evaluates assessment execution outcomes, certifies candidate skills into CandidateProfile,
and generates truthful, non-hallucinatory feedback and remediation guidance.
"""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, Field

from app.core.logging import logger
from app.matching.explain import MatchExplainer
from app.nlp.normalizer import canonicalize_skill_name
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.assessment import AssessmentResult, ExecutionStatus
from app.schemas.resume import CandidateProfile, SkillItem


class SkillVerificationFeedback(BaseModel):
    """Structured feedback and verification result for candidate code assessment."""

    verified: bool = Field(..., description="Whether the skill was successfully verified")
    skill: str = Field(..., description="Canonical target skill name")
    score: float = Field(..., ge=0.0, le=100.0, description="Score percentage achieved")
    passed_tests: int = Field(..., ge=0, description="Count of passed test cases")
    total_tests: int = Field(..., ge=0, description="Total count of test cases")
    feedback_summary: str = Field(..., description="Grounded explanation of execution result")
    remediation_advice: List[str] = Field(default_factory=list, description="Truthful advice for unverified skills")
    disclaimer: str = Field(
        default=MatchExplainer.MANDATORY_DISCLAIMER,
        description="Mandatory legal disclaimer regarding assessment metrics",
    )


class SkillVerificationService:
    """Service evaluating assessment results, certifying candidate skills, and managing feedback."""

    def __init__(
        self,
        resume_repo: Optional[IResumeRepository] = None,
        passing_threshold: float = 100.0,
    ) -> None:
        self._resume_repo = resume_repo
        self.passing_threshold = passing_threshold

    @property
    def resume_repo(self) -> IResumeRepository:
        return self._resume_repo or get_resume_repository()

    @resume_repo.setter
    def resume_repo(self, value: Optional[IResumeRepository]) -> None:
        self._resume_repo = value

    async def evaluate_and_verify(
        self,
        assessment_result: AssessmentResult,
    ) -> SkillVerificationFeedback:
        """Evaluate an assessment result, certify skill on candidate profile if passed, and return feedback."""
        canonical_skill = canonicalize_skill_name(assessment_result.skill)

        total_tests = assessment_result.total_tests
        passed_tests = assessment_result.passed_tests

        if total_tests > 0:
            score = round((passed_tests / total_tests) * 100.0, 1)
        else:
            score = 100.0 if assessment_result.status == ExecutionStatus.COMPLETED and assessment_result.exit_code == 0 else 0.0

        is_passed = (
            assessment_result.status == ExecutionStatus.COMPLETED
            and assessment_result.exit_code == 0
            and score >= self.passing_threshold
        )

        remediation_advice: List[str] = []

        if is_passed:
            feedback_summary = (
                f"Candidate successfully passed {passed_tests}/{total_tests} test cases for {canonical_skill} "
                f"({score}% score). The skill has been certified as verified in the candidate profile."
            )
            # Update candidate profile in repository
            await self._record_verified_skill(
                candidate_id=assessment_result.candidate_id,
                skill_name=canonical_skill,
                score=score,
                submission_id=assessment_result.submission_id,
            )
        else:
            feedback_summary = (
                f"Assessment did not satisfy verification criteria ({passed_tests}/{total_tests} tests passed, "
                f"status: {assessment_result.status.value}). Skill '{canonical_skill}' remains unverified."
            )
            if assessment_result.status == ExecutionStatus.TIMEOUT:
                remediation_advice.append(
                    "Execution exceeded timeout limit. Optimize algorithmic time complexity and inspect loop termination."
                )
            elif assessment_result.status == ExecutionStatus.MEMORY_EXCEEDED:
                remediation_advice.append(
                    "Execution exceeded memory limit. Reduce auxiliary space allocation and avoid unbounded caching."
                )
            elif assessment_result.stderr:
                remediation_advice.append(
                    f"Runtime/syntax error encountered: {assessment_result.stderr[:200].strip()}. Inspect error trace and boundary conditions."
                )
            else:
                remediation_advice.append(
                    "Ensure edge cases (empty inputs, boundaries, duplicates) conform to the problem specification."
                )

        return SkillVerificationFeedback(
            verified=is_passed,
            skill=canonical_skill,
            score=score,
            passed_tests=passed_tests,
            total_tests=total_tests,
            feedback_summary=feedback_summary,
            remediation_advice=remediation_advice,
        )

    async def _record_verified_skill(
        self,
        candidate_id: str,
        skill_name: str,
        score: float,
        submission_id: str,
    ) -> Optional[CandidateProfile]:
        """Certify skill into candidate profile in the persistent repository."""
        profile = await self.resume_repo.get_by_id(candidate_id)
        if not profile:
            logger.warning("Cannot verify skill: candidate profile '%s' not found.", candidate_id)
            return None

        canonical = canonicalize_skill_name(skill_name).lower()
        now = datetime.now(timezone.utc)

        # Check if skill exists on candidate
        found = False
        updated_skills: List[SkillItem] = []
        for s in profile.skills:
            if canonicalize_skill_name(s.name).lower() == canonical:
                # Update existing skill
                updated_skills.append(
                    SkillItem(
                        name=s.name,
                        category=s.category,
                        confidence=1.0,
                        years_experience=s.years_experience,
                        is_verified=True,
                        verified_at=now,
                        verification_score=score,
                        verification_submission_id=submission_id,
                    )
                )
                found = True
            else:
                updated_skills.append(s)

        if not found:
            # Add newly verified skill
            updated_skills.append(
                SkillItem(
                    name=skill_name,
                    category="technical",
                    confidence=1.0,
                    is_verified=True,
                    verified_at=now,
                    verification_score=score,
                    verification_submission_id=submission_id,
                )
            )

        profile.skills = updated_skills
        await self.resume_repo.save_profile(profile)
        logger.info(
            "Skill '%s' verified for candidate '%s' (score=%.1f, submission=%s).",
            skill_name,
            candidate_id,
            score,
            submission_id,
        )
        return profile


_verification_service_instance: Optional[SkillVerificationService] = None


def get_skill_verification_service() -> SkillVerificationService:
    """Singleton accessor for SkillVerificationService."""
    global _verification_service_instance
    if _verification_service_instance is None:
        _verification_service_instance = SkillVerificationService()
    return _verification_service_instance


def reset_skill_verification_service() -> None:
    """Reset singleton for test isolation."""
    global _verification_service_instance
    _verification_service_instance = None
