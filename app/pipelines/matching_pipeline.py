"""Matching pipeline orchestrating candidate profile retrieval, job listing lookups, and evaluation."""

from typing import Dict, Optional

from app.core.exceptions import NotFoundError
from app.matching.engine import MatchingEngine
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.retrieval.rag_service import RAGContextAssembler
from app.schemas.job import Job
from app.schemas.matching import MatchResult, RecommendedMatchesResponse
from app.schemas.resume import CandidateProfile


class MatchingPipeline:
    """Orchestrates candidate profile and job retrieval for matching and recommendations."""

    def __init__(
        self,
        engine: Optional[MatchingEngine] = None,
        resume_repo: Optional[IResumeRepository] = None,
        job_repo: Optional[IJobRepository] = None,
    ) -> None:
        self.resume_repo = resume_repo or get_resume_repository()
        self.job_repo = job_repo or get_job_repository()
        self.engine = engine or MatchingEngine(job_repository=self.job_repo)

    async def get_candidate_profile(self, candidate_id: str) -> CandidateProfile:
        """Fetch candidate profile or raise NotFoundError."""
        profile = await self.resume_repo.get_by_id(candidate_id)
        if not profile:
            profile = await self.resume_repo.resolve_profile(identifier=candidate_id)
        if not profile:
            raise NotFoundError(f"Candidate profile '{candidate_id}' not found.")
        return profile

    async def get_job(self, job_id: str) -> Job:
        """Fetch active job listing or raise NotFoundError."""
        job = await self.job_repo.get_by_id(job_id)
        if not job:
            raise NotFoundError(f"Job listing '{job_id}' not found.")
        if not job.is_active:
            raise NotFoundError(f"Job listing '{job_id}' is inactive or has expired.")
        return job

    async def match_candidate_job(self, candidate_id: str, job_id: str) -> MatchResult:
        """Retrieve candidate and job, then compute explainable match result."""
        candidate = await self.get_candidate_profile(candidate_id)
        job = await self.get_job(job_id)
        return await self.engine.match_candidate_to_job(candidate=candidate, job=job)

    async def recommend_jobs(
        self,
        candidate_id: str,
        min_score: float = 60.0,
        limit: int = 10,
    ) -> RecommendedMatchesResponse:
        """Retrieve candidate profile and return ranked recommended jobs meeting score threshold."""
        candidate = await self.get_candidate_profile(candidate_id)
        return await self.engine.recommend_jobs_for_candidate(
            candidate=candidate,
            top_k=limit,
            min_score=min_score,
        )

    async def assemble_rag_context(self, candidate_id: str, job_id: str) -> Dict[str, str]:
        """Assemble prompt-injection shielded RAG context for candidate and job."""
        candidate = await self.get_candidate_profile(candidate_id)
        job = await self.get_job(job_id)
        return RAGContextAssembler.assemble_grounded_context(candidate=candidate, job=job)
