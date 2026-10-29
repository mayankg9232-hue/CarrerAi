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
    get_embedding_provider,
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
        self.embedder = embedding_provider or get_embedding_provider()
        self.job_repo = job_repository or get_job_repository()
        self.retriever = JobRetriever(job_repository=self.job_repo, embedding_provider=self.embedder)

    async def compute_semantic_similarity(self, candidate: CandidateProfile, job: Job) -> float:
        """Calculate cosine similarity between candidate profile and job listing embeddings."""
        candidate_text = prepare_candidate_embedding_text(candidate)
        try:
            candidate_vec = await self.embedder.embed_text(candidate_text)
            job_vec = await self.retriever.get_or_create_job_embedding(job)
            return cosine_similarity(candidate_vec, job_vec)
        except Exception:
            fallback = LocalDeterministicEmbeddingProvider()
            candidate_vec = await fallback.embed_text(candidate_text)
            job_text = prepare_job_embedding_text(job)
            job_vec = await fallback.embed_text(job_text)
            return cosine_similarity(candidate_vec, job_vec)

    async def match_candidate_to_job(
        self,
        candidate: CandidateProfile,
        job: Job,
        semantic_similarity: Optional[float] = None,
    ) -> MatchResult:
        """Produce explainable candidate-job match evaluation with 5-factor breakdown and gap analysis."""
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

        # Experience comparison summary
        exp_reason = breakdown.relevant_experience.reason if breakdown.relevant_experience else ""
        exp_comparison = exp_reason or "Work experience evaluated against role target seniority."

        # Derive strong matching points
        strong_points: List[str] = []
        if breakdown.skill_score >= 70.0 and gaps.matched_skills:
            strong_points.append(f"Demonstrated core skills: {', '.join(gaps.matched_skills[:5])}")
        if breakdown.experience_score >= 80.0:
            strong_points.append("Experience duration meets or exceeds target seniority requirement")
        if breakdown.education_score >= 80.0 and breakdown.education and breakdown.education.available:
            strong_points.append("Academic credentials satisfy educational qualifications")
        if breakdown.role_alignment_score >= 70.0:
            strong_points.append(f"High role alignment with '{job.title}' duties")
        if breakdown.projects_score >= 70.0:
            strong_points.append("Hands-on technical domain experience evidenced in work history")

        # Derive missing or insufficiently demonstrated qualifications
        missing_quals: List[str] = []
        if gaps.missing_skills:
            missing_quals.append(f"Unmet technical requirements: {', '.join(gaps.missing_skills[:4])}")
        if breakdown.experience_score < 70.0:
            missing_quals.append("Candidate has fewer years of documented experience than position prefers")
        if breakdown.education and breakdown.education.available and breakdown.education_score < 75.0:
            missing_quals.append("Position prefers higher academic qualifications or specific degree focus")

        return MatchResult(
            candidate_id=candidate.id,
            job_id=job.id,
            overall_score=overall_score,
            breakdown=breakdown,
            matched_skills=gaps.matched_skills,
            missing_skills=gaps.missing_skills,
            uncertain_skills=gaps.uncertain_skills,
            experience_comparison=exp_comparison,
            strong_matching_points=strong_points,
            missing_qualifications=missing_quals,
            explanation=explanation,
            improvement_suggestions=gaps.improvement_suggestions,
            caveat=MatchExplainer.MANDATORY_DISCLAIMER,
            is_provisional=breakdown.is_provisional,
            reduced_confidence=breakdown.reduced_confidence,
        )

    async def match_and_rank_jobs(
        self,
        candidate: CandidateProfile,
        jobs: List[Job],
        min_score: float = 0.0,
    ) -> List[MatchResult]:
        """Evaluate candidate profile against a provided list of jobs, deduplicate, and rank them.
        
        Sorting policy:
        1. Descending by overall weighted match percentage.
        2. Tie-breaking consistently by technical skill score then experience score.
        3. Assign sequential ranks starting at Rank 1 for top match.
        """
        # Deduplicate jobs by content_hash or source_url or normalized title+company
        seen_keys = set()
        unique_jobs: List[Job] = []
        for j in jobs:
            key = j.content_hash or (str(j.source_url) if j.source_url else None) or f"{j.title}|{j.company}".lower()
            if key not in seen_keys:
                seen_keys.add(key)
                unique_jobs.append(j)

        if not unique_jobs:
            return []

        # 1. Batch precompute any missing job embeddings in a single API call
        await self.retriever.precompute_job_embeddings(unique_jobs)

        # 2. Embed candidate profile ONCE for the entire batch
        candidate_text = prepare_candidate_embedding_text(candidate)
        try:
            candidate_vec = await self.embedder.embed_text(candidate_text)
        except Exception:
            fallback = LocalDeterministicEmbeddingProvider()
            candidate_vec = await fallback.embed_text(candidate_text)

        # 3. Evaluate each job using instant in-memory cosine similarity (0 network calls)
        matches: List[MatchResult] = []
        for job in unique_jobs:
            try:
                job_vec = await self.retriever.get_or_create_job_embedding(job)
                sim = cosine_similarity(candidate_vec, job_vec)
            except Exception:
                sim = 0.0

            result = await self.match_candidate_to_job(candidate, job, semantic_similarity=sim)
            if result.overall_score >= min_score:
                result.job = job
                matches.append(result)

        # Sort descending by overall match score, breaking ties deterministically
        matches.sort(
            key=lambda m: (m.overall_score, m.breakdown.skill_score, m.breakdown.experience_score),
            reverse=True,
        )

        # Assign 1-indexed rank starting at 1 for the highest scoring match
        for rank_idx, m in enumerate(matches, start=1):
            m.rank = rank_idx

        return matches

    async def recommend_jobs_for_candidate(
        self,
        candidate: CandidateProfile,
        top_k: int = 10,
        min_score: float = 0.0,
    ) -> RecommendedMatchesResponse:
        """Evaluate candidate profile against active jobs in repository and return ranked matches."""
        active_jobs = await self.job_repo.query_jobs(only_active=True, limit=1000)
        ranked_matches = await self.match_and_rank_jobs(candidate, active_jobs, min_score=min_score)

        return RecommendedMatchesResponse(
            candidate_id=candidate.id,
            matches=ranked_matches[:top_k],
            total_evaluated=len(active_jobs),
        )
