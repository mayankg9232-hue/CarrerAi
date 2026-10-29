"""Vector retrieval and semantic similarity ranking for jobs and candidate profiles."""

from typing import Dict, List, Optional, Tuple

from app.repositories.job_repo import IJobRepository, get_job_repository
from app.retrieval.embeddings import (
    IEmbeddingProvider,
    LocalDeterministicEmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
    prepare_candidate_embedding_text,
    prepare_job_embedding_text,
)
from app.schemas.job import Job
from app.schemas.resume import CandidateProfile

# Process-level persistent cache for job embeddings across engine instances
_GLOBAL_JOB_EMBEDDING_CACHE: Dict[str, List[float]] = {}


def clear_job_embedding_cache() -> None:
    """Clear process-level job embedding cache (for testing)."""
    _GLOBAL_JOB_EMBEDDING_CACHE.clear()


def get_job_embedding_cache_size() -> int:
    """Return count of cached job vectors."""
    return len(_GLOBAL_JOB_EMBEDDING_CACHE)


class JobRetriever:
    """Semantic vector retriever matching candidate profiles against active job listings."""

    def __init__(
        self,
        job_repository: Optional[IJobRepository] = None,
        embedding_provider: Optional[IEmbeddingProvider] = None,
    ) -> None:
        self.job_repo = job_repository or get_job_repository()
        self.embedder = embedding_provider or get_embedding_provider()

    @staticmethod
    def _make_cache_key(job: Job) -> str:
        return f"{job.id}:{job.content_hash or 'nohash'}"

    def has_cached_embedding(self, job: Job) -> bool:
        """Check if job embedding is already stored in memory."""
        return self._make_cache_key(job) in _GLOBAL_JOB_EMBEDDING_CACHE

    def cache_job_embedding(self, job: Job, vector: List[float]) -> None:
        """Store job embedding vector into persistent memory cache."""
        _GLOBAL_JOB_EMBEDDING_CACHE[self._make_cache_key(job)] = vector

    async def get_or_create_job_embedding(self, job: Job) -> List[float]:
        """Retrieve cached embedding vector for job or compute and cache if absent."""
        cache_key = self._make_cache_key(job)
        if cache_key in _GLOBAL_JOB_EMBEDDING_CACHE:
            return _GLOBAL_JOB_EMBEDDING_CACHE[cache_key]

        text = prepare_job_embedding_text(job)
        vector = await self.embedder.embed_text(text)
        _GLOBAL_JOB_EMBEDDING_CACHE[cache_key] = vector
        return vector

    async def precompute_job_embeddings(self, jobs: List[Job]) -> None:
        """Precompute embeddings in a single batch for all jobs not already in cache."""
        missing = [j for j in jobs if not self.has_cached_embedding(j)]
        if not missing:
            return

        texts = [prepare_job_embedding_text(j) for j in missing]
        try:
            vectors = await self.embedder.embed_batch(texts)
            for j, vec in zip(missing, vectors):
                self.cache_job_embedding(j, vec)
        except Exception:
            # Fall back to single or local deterministic if batch fails
            fallback = LocalDeterministicEmbeddingProvider()
            for j, t in zip(missing, texts):
                try:
                    vec = await self.embedder.embed_text(t)
                except Exception:
                    vec = await fallback.embed_text(t)
                self.cache_job_embedding(j, vec)

    async def search_relevant_jobs(
        self,
        candidate: CandidateProfile,
        top_k: int = 10,
        min_similarity: float = 0.0,
    ) -> List[Tuple[Job, float]]:
        """Retrieve and rank active jobs according to semantic cosine similarity to candidate."""
        # Fetch active job listings
        active_jobs = await self.job_repo.query_jobs(only_active=True, limit=1000)
        if not active_jobs:
            return []

        # Batch precompute any uncached jobs
        await self.precompute_job_embeddings(active_jobs)

        # Embed candidate profile once
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
