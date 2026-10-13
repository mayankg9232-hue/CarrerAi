"""Unified matching engine combining scoring, gap analysis, vector retrieval, and grounded explanation."""

from typing import List, Optional

from app.matching.explain import MatchExplainer
from app.matching.gap_analysis import GapAnalyzer
from app.matching.scorer import DeterministicScorer
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.retrieval.embeddings import (
    IEmbeddingProvider,
    LocalDeterministicEmbeddingProvider,
    cosine_similarity,
    prepare_candidate_embedding_text,
    prepare_job_embedding_text,
)
from app.retrieval.retriever import JobRetriever
from app.schemas.job import Job
from app.schemas.matching import MatchResult, RecommendedMatchesResponse
from app.schemas.resume import CandidateProfile


class MatchingEngine:
    """Core matching and explainability engine for Developer B."""

    def __init__(
        self,
        scorer: Optional[DeterministicScorer] = None,
        gap_analyzer: Optional[GapAnalyzer] = None,
        explainer: Optional[MatchExplainer] = None,
        embedding_provider: Optional[IEmbeddingProvider] = None,
        job_repository: Optional[IJobRepository] = None,
    ) -> None:
        self.scorer = scorer or DeterministicScorer()
        self.gap_analyzer = gap_analyzer or GapAnalyzer()
        self.explainer = explainer or MatchExplainer()
        self.embedder = embedding_provider or LocalDeterministicEmbeddingProvider()
        self.job_repo = job_repository or get_job_repository()
        self.retriever = JobRetriever(job_repository=self.job_repo, embedding_provider=self.embedder)

    async def compute_semantic_similarity(self, candidate: CandidateProfile, job: Job) -> float:
        """Calculate cosine similarity between candidate profile and job listing embeddings."""
        candidate_text = prepare_candidate_embedding_text(candidate)
        candidate_vec = await self.embedder.embed_text(candidate_text)
        job_vec = await self.retriever.get_or_create_job_embedding(job)
        return cosine_similarity(candidate_vec, job_vec)

    async def match_candidate_to_job(
        self,
        candidate: CandidateProfile,
        job: Job,
        semantic_similarity: Optional[float] = None,
    ) -> MatchResult:
        """Produce explainable candidate-job match evaluation with score breakdown and gap analysis."""
        if semantic_similarity is None:
            semantic_similarity = await self.compute_semantic_similarity(candidate, job)

        overall_score, breakdown = self.scorer.compute_overall_score(
            candidate=candidate,
            job=job,
            semantic_similarity=semantic_similarity,
        )

        gaps = self.gap_analyzer.analyze(candidate=candidate, job=job)
        explanation = self.explainer.generate_explanation(
            candidate=candidate,
            job=job,
            overall_score=overall_score,
            breakdown=breakdown,
            gap_analysis=gaps,
        )

        return MatchResult(
            candidate_id=candidate.id,
            job_id=job.id,
            overall_score=overall_score,
            breakdown=breakdown,
            matched_skills=gaps.matched_skills,
            missing_skills=gaps.missing_skills,
            uncertain_skills=gaps.uncertain_skills,
            explanation=explanation,
            improvement_suggestions=gaps.improvement_suggestions,
            caveat=MatchExplainer.MANDATORY_DISCLAIMER,
        )

    async def recommend_jobs_for_candidate(
        self,
        candidate: CandidateProfile,
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> RecommendedMatchesResponse:
        """Evaluate candidate profile against active jobs in repository and return ranked matches."""
        active_jobs = await self.job_repo.query_jobs(only_active=True, limit=1000)

        matches: List[MatchResult] = []
        for job in active_jobs:
            result = await self.match_candidate_to_job(candidate, job)
            if result.overall_score >= min_score:
                matches.append(result)

        # Sort descending by overall match score
        matches.sort(key=lambda m: m.overall_score, reverse=True)

        return RecommendedMatchesResponse(
            candidate_id=candidate.id,
            matches=matches[:top_k],
            total_evaluated=len(active_jobs),
        )
