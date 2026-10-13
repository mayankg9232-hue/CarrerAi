"""Vector retrieval and semantic similarity ranking for jobs and candidate profiles."""

from typing import Dict, List, Optional, Tuple

from app.repositories.job_repo import IJobRepository, get_job_repository
from app.retrieval.embeddings import (
    IEmbeddingProvider,
    LocalDeterministicEmbeddingProvider,
    cosine_similarity,
    prepare_candidate_embedding_text,
    prepare_job_embedding_text,
)
from app.schemas.job import Job
from app.schemas.resume import CandidateProfile


class JobRetriever:
    """Semantic vector retriever matching candidate profiles against active job listings."""

    def __init__(
        self,
        job_repository: Optional[IJobRepository] = None,
        embedding_provider: Optional[IEmbeddingProvider] = None,
    ) -> None:
        self.job_repo = job_repository or get_job_repository()
        self.embedder = embedding_provider or LocalDeterministicEmbeddingProvider()
        self._job_embedding_cache: Dict[str, List[float]] = {}

    async def get_or_create_job_embedding(self, job: Job) -> List[float]:
        """Retrieve cached embedding vector for job or compute and cache if absent."""
        cache_key = f"{job.id}:{job.content_hash or 'nohash'}"
        if cache_key in self._job_embedding_cache:
            return self._job_embedding_cache[cache_key]

        text = prepare_job_embedding_text(job)
        vector = await self.embedder.embed_text(text)
        self._job_embedding_cache[cache_key] = vector
        return vector

    async def search_relevant_jobs(
        self,
        candidate: CandidateProfile,
        top_k: int = 10,
        min_similarity: float = 0.0,
    ) -> List[Tuple[Job, float]]:
        """Retrieve and rank active jobs according to semantic cosine similarity to candidate.

        Args:
            candidate: Structured candidate profile.
            top_k: Maximum number of ranked results to return.
            min_similarity: Minimum cosine similarity threshold (0.0 to 1.0).

        Returns:
            List of (Job, similarity_score) tuples ordered descending by similarity.
        """
        # Fetch active job listings
        active_jobs = await self.job_repo.query_jobs(only_active=True, limit=1000)
        if not active_jobs:
            return []

        # Embed candidate profile
        candidate_text = prepare_candidate_embedding_text(candidate)
        candidate_vec = await self.embedder.embed_text(candidate_text)

        results: List[Tuple[Job, float]] = []
        for job in active_jobs:
            job_vec = await self.get_or_create_job_embedding(job)
            sim = cosine_similarity(candidate_vec, job_vec)
            if sim >= min_similarity:
                results.append((job, sim))

        # Sort descending by similarity
        results.sort(key=lambda item: item[1], reverse=True)
        return results[:top_k]
