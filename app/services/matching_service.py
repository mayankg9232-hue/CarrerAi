"""Matching Application Service enforcing authorization, candidate isolation, and pipeline execution (Developer A)."""

from typing import Any, Dict, List, Optional

from app.authorization.policies import assert_resource_owner
from app.core.exceptions import NotFoundError
from app.core.logging import logger
from app.pipelines.matching_pipeline import MatchingPipeline
from app.repositories.match_repo import IMatchRepository, get_match_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.matching import MatchResult, RecommendedMatchesResponse


class MatchingApplicationService:
    """Application-level service orchestrating matching pipelines, tenant isolation, and audit logging."""

    def __init__(
        self,
        pipeline: Optional[MatchingPipeline] = None,
        resume_repo: Optional[IResumeRepository] = None,
        match_repo: Optional[IMatchRepository] = None,
    ) -> None:
        self.pipeline = pipeline or MatchingPipeline()
        self.resume_repo = resume_repo or get_resume_repository()
        self.match_repo = match_repo or get_match_repository()

    async def match_candidate_to_job(
        self,
        candidate_id: str,
        job_id: str,
        principal: AuthenticatedPrincipal,
    ) -> MatchResult:
        """Calculate explainable match score between candidate and job with server-side IDOR defense."""
        # 1. Fetch candidate profile and verify existence
        candidate = await self.resume_repo.get_by_id(candidate_id)
        if not candidate:
            raise NotFoundError(f"Candidate profile '{candidate_id}' not found.")

        # 2. Enforce candidate data isolation (IDOR protection)
        assert_resource_owner(
            resource_user_id=candidate.user_id,
            principal=principal,
            allow_admin=True,
            allowed_roles=("recruiter", "admin"),
        )

        # 3. Delegate to pipeline for job retrieval, scoring, and explanation
        result = await self.pipeline.match_candidate_job(candidate_id=candidate_id, job_id=job_id)

        # 4. Persist match record to database
        if self.match_repo:
            try:
                await self.match_repo.save_match(result)
            except Exception as exc:
                logger.warning("Failed to persist match record: %s", exc)

        # 5. Redacted audit logging (no PII or secrets logged)
        logger.info(
            "Match calculated: candidate_id=%s job_id=%s overall_score=%.1f caller_id=%s role=%s",
            candidate_id,
            job_id,
            result.overall_score,
            principal.id,
            principal.role,
        )

        return result

    async def recommend_jobs_for_candidate(
        self,
        candidate_id: str,
        principal: AuthenticatedPrincipal,
        min_score: float = 60.0,
        limit: int = 10,
    ) -> RecommendedMatchesResponse:
        """Retrieve ranked job recommendations for candidate with server-side IDOR defense."""
        # 1. Fetch candidate profile and verify existence
        candidate = await self.resume_repo.get_by_id(candidate_id)
        if not candidate:
            raise NotFoundError(f"Candidate profile '{candidate_id}' not found.")

        # 2. Enforce candidate data isolation (IDOR protection)
        assert_resource_owner(
            resource_user_id=candidate.user_id,
            principal=principal,
            allow_admin=True,
            allowed_roles=("recruiter", "admin"),
        )

        # 3. Delegate to pipeline for matching across active jobs
        result = await self.pipeline.recommend_jobs(
            candidate_id=candidate_id,
            min_score=min_score,
            limit=limit,
        )

        # 4. Persist recommendation match evaluations to database
        if self.match_repo:
            for m in result.matches:
                try:
                    await self.match_repo.save_match(m)
                except Exception as exc:
                    logger.warning("Failed to persist match record: %s", exc)

        # 5. Redacted audit logging
        logger.info(
            "Recommendations generated: candidate_id=%s match_count=%d caller_id=%s",
            candidate_id,
            len(result.matches),
            principal.id,
        )

        return result

    async def get_candidate_matches(
        self,
        candidate_id: str,
        principal: AuthenticatedPrincipal,
    ) -> List[Dict[str, Any]]:
        """Retrieve persisted match history for candidate ensuring IDOR isolation."""
        candidate = await self.resume_repo.get_by_id(candidate_id)
        if not candidate:
            raise NotFoundError(f"Candidate profile '{candidate_id}' not found.")

        assert_resource_owner(
            resource_user_id=candidate.user_id,
            principal=principal,
            allow_admin=True,
            allowed_roles=("recruiter", "admin"),
        )
        return await self.match_repo.get_matches_for_candidate(candidate_id)

    async def get_grounded_rag_context(
        self,
        candidate_id: str,
        job_id: str,
        principal: AuthenticatedPrincipal,
    ) -> Dict[str, str]:
        """Assemble injection-shielded RAG context for candidate and job."""
        candidate = await self.resume_repo.get_by_id(candidate_id)
        if not candidate:
            raise NotFoundError(f"Candidate profile '{candidate_id}' not found.")

        assert_resource_owner(
            resource_user_id=candidate.user_id,
            principal=principal,
            allow_admin=True,
            allowed_roles=("recruiter", "admin"),
        )

        return await self.pipeline.assemble_rag_context(candidate_id=candidate_id, job_id=job_id)


def get_matching_app_service() -> MatchingApplicationService:
    """FastAPI dependency provider returning initialized MatchingApplicationService."""
    return MatchingApplicationService()
