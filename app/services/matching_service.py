"""Matching Application Service enforcing authorization, candidate isolation, and pipeline execution (Developer A)."""

import asyncio
from datetime import datetime, timezone
import time
from typing import Any, Dict, List, Optional, Tuple

from fastapi import Depends

from app.authorization.policies import assert_resource_owner
from app.core.exceptions import NotFoundError
from app.core.logging import logger
from app.pipelines.matching_pipeline import MatchingPipeline
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.repositories.match_repo import IMatchRepository, get_match_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.matching import (
    MatchResult,
    RecommendedMatchesResponse,
    RankedMatchesSummaryResponse,
    JobMatchItem,
)


class MatchingApplicationService:
    """Application-level service orchestrating matching pipelines, tenant isolation, and audit logging."""

    _match_cache: Dict[str, Tuple[float, RankedMatchesSummaryResponse]] = {}

    @classmethod
    def invalidate_cache(cls, user_id: Optional[str] = None) -> None:
        """Clear cached match responses globally or for a specific user."""
        if user_id:
            cls._match_cache = {k: v for k, v in cls._match_cache.items() if not k.startswith(f"{user_id}_")}
        else:
            cls._match_cache.clear()

    def __init__(
        self,
        pipeline: Optional[MatchingPipeline] = None,
        resume_repo: Optional[IResumeRepository] = None,
        match_repo: Optional[IMatchRepository] = None,
        job_repo: Optional[IJobRepository] = None,
    ) -> None:
        self.resume_repo = resume_repo or get_resume_repository()
        self.match_repo = match_repo or get_match_repository()
        self.job_repo = job_repo or get_job_repository()
        self.pipeline = pipeline or MatchingPipeline(resume_repo=self.resume_repo, job_repo=self.job_repo)

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
            candidate = await self.resume_repo.resolve_profile(
                identifier=candidate_id,
                user_id=principal.id if principal.role not in ("admin", "recruiter") else None,
            )
        if not candidate:
            raise NotFoundError(f"Candidate profile '{candidate_id}' not found.")
        candidate_id = candidate.id

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
            candidate = await self.resume_repo.resolve_profile(
                identifier=candidate_id,
                user_id=principal.id if principal.role not in ("admin", "recruiter") else None,
            )
        if not candidate:
            raise NotFoundError(f"Candidate profile '{candidate_id}' not found.")
        candidate_id = candidate.id

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

    async def _compute_and_save_matches(self, candidate_id: str, limit: int = 50) -> None:
        """Background worker computing matches asynchronously without blocking login/dashboard requests."""
        try:
            rec_res = await self.pipeline.recommend_jobs(candidate_id=candidate_id, min_score=0.0, limit=limit)
            for m in rec_res.matches:
                try:
                    await self.match_repo.save_match(m)
                except Exception as exc:
                    logger.warning("Failed to save background match record for job %s: %s", m.job_id, exc)
            self.invalidate_cache()
            logger.info("Background matching completed successfully for candidate_id=%s (%d matches)", candidate_id, len(rec_res.matches))
        except Exception as exc:
            logger.error("Background matching failed for candidate_id=%s: %s", candidate_id, exc)

    async def get_or_compute_ranked_matches(
        self,
        principal: AuthenticatedPrincipal,
        resume_id: Optional[str] = None,
        force_refresh: bool = False,
        limit: int = 50,
        async_matching: bool = False,
    ) -> RankedMatchesSummaryResponse:
        """
        Retrieve persisted ranked job matches for user's active resume or compute them on demand.
        Enforces user ownership, avoids unnecessary recalculation, and never fabricates matches.
        """
        cache_key = f"{principal.id}_{resume_id or 'active'}_{limit}"
        if not force_refresh and cache_key in self._match_cache:
            cached_time, cached_resp = self._match_cache[cache_key]
            if (time.time() - cached_time) < 60.0:
                return cached_resp.model_copy(update={"is_cached": True})

        # 1. Identify target resume and profile (optimized single-call path)
        if resume_id:
            resume = await self.resume_repo.get_resume(resume_id)
            if not resume:
                raise NotFoundError(f"Resume '{resume_id}' not found.")
            assert_resource_owner(
                resource_user_id=resume.user_id,
                principal=principal,
                allow_admin=True,
                allowed_roles=("recruiter", "admin"),
            )
            candidate = await self.resume_repo.resolve_profile(
                identifier=resume.profile_id,
                user_id=principal.id if principal.role not in ("admin", "recruiter") else None,
            )
        else:
            resume, candidate = await self.resume_repo.get_active_resume_and_profile(principal.id)
            if not resume:
                user_resumes = await self.resume_repo.get_user_resumes(principal.id)
                resume = user_resumes[0] if user_resumes else None
                if resume and not candidate:
                    candidate = await self.resume_repo.resolve_profile(
                        identifier=resume.profile_id,
                        user_id=principal.id if principal.role not in ("admin", "recruiter") else None,
                    )

        # 2. Check if user has uploaded any resume
        if not resume:
            return RankedMatchesSummaryResponse(
                status="no_resume",
                candidate_id=None,
                resume_id=None,
                ranked_matches=[],
                total_matches=0,
                roles_evaluated=0,
                is_cached=False,
                message="No resume uploaded yet. Please upload a resume to view job matches.",
            )

        # 3. Handle processing and failed states
        if resume.status == "processing":
            return RankedMatchesSummaryResponse(
                status="processing",
                candidate_id=resume.profile_id,
                resume_id=resume.id,
                ranked_matches=[],
                total_matches=0,
                roles_evaluated=0,
                is_cached=False,
                message="Your resume is currently being processed. Ranked job recommendations will be available shortly.",
            )

        if resume.status == "failed":
            return RankedMatchesSummaryResponse(
                status="failed",
                candidate_id=resume.profile_id,
                resume_id=resume.id,
                ranked_matches=[],
                total_matches=0,
                roles_evaluated=0,
                is_cached=False,
                message=resume.error_message or "Resume processing failed. Please retry extraction or upload an updated file.",
            )

        # 4. Resolve candidate profile if still missing
        if not candidate:
            candidate = await self.resume_repo.resolve_profile(
                identifier=resume.profile_id,
                user_id=principal.id if principal.role not in ("admin", "recruiter") else None,
            )
        if not candidate:
            return RankedMatchesSummaryResponse(
                status="no_resume",
                candidate_id=None,
                resume_id=resume.id,
                ranked_matches=[],
                total_matches=0,
                roles_evaluated=0,
                is_cached=False,
                message="Candidate profile record not found for resume.",
            )

        # 5. Check if persisted match records already exist (avoiding recalculation on login)
        if not force_refresh:
            persisted = await self.match_repo.get_ranked_matches_with_jobs(candidate.id, limit=limit)
            if persisted:
                total_active = await self.pipeline.job_repo.count_jobs(only_active=True)
                roles_eval = max(total_active, len(persisted))
                logger.info(
                    "Retrieved %d persisted ranked matches for candidate_id=%s user_id=%s (evaluated=%d)",
                    len(persisted),
                    candidate.id,
                    principal.id,
                    roles_eval,
                )
                resp = RankedMatchesSummaryResponse(
                    status="completed",
                    candidate_id=candidate.id,
                    resume_id=resume.id,
                    ranked_matches=persisted,
                    total_matches=len(persisted),
                    roles_evaluated=roles_eval,
                    is_cached=True,
                    message=f"Retrieved {len(persisted)} persisted ranked matches from PostgreSQL.",
                    calculated_at=datetime.now(timezone.utc),
                )
                self._match_cache[cache_key] = (time.time(), resp)
                return resp

        # 6. Check available jobs before calculation (never hallucinate jobs)
        total_active_jobs = await self.pipeline.job_repo.count_jobs(only_active=True)
        if total_active_jobs == 0:
            logger.info("No active jobs available for matching candidate_id=%s", candidate.id)
            return RankedMatchesSummaryResponse(
                status="empty_catalog",
                candidate_id=candidate.id,
                resume_id=resume.id,
                ranked_matches=[],
                total_matches=0,
                roles_evaluated=0,
                is_cached=False,
                message="No active job listings are currently available in the database catalog.",
            )

        # 7. If initial login and no matches exist, decouple and compute in background when async_matching is requested
        if not force_refresh and async_matching:
            logger.info(
                "No persisted matches found for candidate_id=%s on login; enqueueing background calculation across %d roles",
                candidate.id,
                total_active_jobs,
            )
            asyncio.create_task(self._compute_and_save_matches(candidate.id, limit=limit))
            return RankedMatchesSummaryResponse(
                status="matching",
                candidate_id=candidate.id,
                resume_id=resume.id,
                ranked_matches=[],
                total_matches=0,
                roles_evaluated=total_active_jobs,
                is_cached=False,
                message=f"Evaluating your profile against {total_active_jobs} active catalog roles in the background.",
                calculated_at=datetime.now(timezone.utc),
            )

        # 8. Force refresh requested: Calculate matches via deterministic weighted matching engine
        logger.info(
            "Calculating on-demand matches for candidate_id=%s across %d active catalog jobs (force_refresh=%s)",
            candidate.id,
            total_active_jobs,
            force_refresh,
        )
        rec_res = await self.pipeline.recommend_jobs(candidate_id=candidate.id, min_score=0.0, limit=limit)

        # Persist evaluated matches idempotently
        for m in rec_res.matches:
            try:
                await self.match_repo.save_match(m)
            except Exception as exc:
                logger.warning("Failed to save match record for job %s: %s", m.job_id, exc)

        # Invalidate memory cache so fresh results are served
        self.invalidate_cache(principal.id)

        # Fetch joined results with full job metadata
        fresh_matches = await self.match_repo.get_ranked_matches_with_jobs(candidate.id, limit=limit)
        resp = RankedMatchesSummaryResponse(
            status="completed",
            candidate_id=candidate.id,
            resume_id=resume.id,
            ranked_matches=fresh_matches,
            total_matches=len(fresh_matches),
            roles_evaluated=total_active_jobs,
            is_cached=False,
            message=f"Calculated and persisted {len(fresh_matches)} ranked job matches against {total_active_jobs} active catalog roles.",
            calculated_at=datetime.now(timezone.utc),
        )
        self._match_cache[cache_key] = (time.time(), resp)
        return resp


def get_matching_app_service(
    resume_repo: IResumeRepository = Depends(get_resume_repository),
    match_repo: IMatchRepository = Depends(get_match_repository),
    job_repo: IJobRepository = Depends(get_job_repository),
) -> MatchingApplicationService:
    """FastAPI dependency provider returning initialized MatchingApplicationService."""
    return MatchingApplicationService(
        resume_repo=resume_repo,
        match_repo=match_repo,
        job_repo=job_repo,
    )
