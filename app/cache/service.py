"""Cache-aside service for job listings and candidate recommendations with graceful fallback."""

import hashlib
import json
import logging
from typing import Any, Dict, List, Optional

from app.cache.redis_client import AsyncRedisClient, RedisConnectionError
from app.core.config import Settings, get_settings
from app.schemas.job import Job

logger = logging.getLogger("ai_job_matcher.cache")


class CacheService:
    """Cache-aside service providing isolated key topologies, TTL management, and graceful degradation."""

    def __init__(
        self,
        client: Optional[AsyncRedisClient] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._client = client
        self.enabled = self.settings.REDIS_ENABLED and bool(self.settings.REDIS_URL)

    def _get_client(self) -> AsyncRedisClient:
        if self._client is None:
            self._client = AsyncRedisClient(
                redis_url=self.settings.REDIS_URL,
                timeout=self.settings.REDIS_CONNECT_TIMEOUT_SECONDS,
            )
        return self._client

    # ==========================================================================
    # KEY DERIVATION (STRICT NAMESPACING)
    # ==========================================================================
    @staticmethod
    def compute_query_hash(**kwargs: Any) -> str:
        """Deterministically hash query parameters to derive an immutable cache key."""
        sorted_repr = json.dumps(kwargs, sort_keys=True, default=str)
        return hashlib.sha256(sorted_repr.encode("utf-8")).hexdigest()[:16]

    def _job_query_key(self, query_hash: str) -> str:
        return f"cache:jobs:query:{query_hash}"

    def _job_detail_key(self, job_id: str) -> str:
        return f"cache:jobs:detail:{job_id}"

    def _recs_key(self, candidate_id: str, query_hash: str) -> str:
        return f"cache:recs:{candidate_id}:{query_hash}"

    # ==========================================================================
    # JOBS CACHE-ASIDE OPERATIONS
    # ==========================================================================
    async def get_cached_jobs_query(self, **params: Any) -> Optional[List[Job]]:
        """Retrieve cached job search results. Returns None on cache miss or Redis outage."""
        if not self.enabled:
            return None

        key = self._job_query_key(self.compute_query_hash(**params))
        try:
            client = self._get_client()
            val = await client.get(key)
            if val:
                raw_list = json.loads(val)
                logger.debug("Cache HIT for job query: %s", key)
                return [Job.model_validate(item) for item in raw_list]
            logger.debug("Cache MISS for job query: %s", key)
            return None
        except Exception as exc:
            logger.warning("Redis read failure for key '%s' - falling back to database: %s", key, exc)
            return None

    async def set_cached_jobs_query(self, jobs: List[Job], ttl: Optional[int] = None, **params: Any) -> bool:
        """Store job search results with TTL expiration. Fails safely on Redis outage."""
        if not self.enabled:
            return False

        key = self._job_query_key(self.compute_query_hash(**params))
        ttl_seconds = ttl or self.settings.REDIS_DEFAULT_TTL_SECONDS
        try:
            payload = json.dumps([j.model_dump(mode="json") for j in jobs])
            client = self._get_client()
            res = await client.set(key, payload, ex=ttl_seconds)
            logger.debug("Cached %d jobs at '%s' with TTL %ds", len(jobs), key, ttl_seconds)
            return res
        except Exception as exc:
            logger.warning("Redis write failure for key '%s': %s", key, exc)
            return False

    async def invalidate_jobs_cache(self) -> int:
        """Invalidate all job query caches upon new ingestion or updates."""
        if not self.enabled:
            return 0

        try:
            client = self._get_client()
            matching_keys = await client.keys("cache:jobs:*")
            if matching_keys:
                deleted_count = await client.delete(*matching_keys)
                logger.info("Invalidated %d job cache entries", deleted_count)
                return deleted_count
            return 0
        except Exception as exc:
            logger.warning("Redis cache invalidation error: %s", exc)
            return 0

    # ==========================================================================
    # CANDIDATE RECOMMENDATION CACHING (WITH USER ISOLATION)
    # ==========================================================================
    async def get_cached_recommendations(self, candidate_id: str, **params: Any) -> Optional[Dict[str, Any]]:
        """Retrieve cached candidate recommendations ensuring strict candidate isolation."""
        if not self.enabled:
            return None

        key = self._recs_key(candidate_id, self.compute_query_hash(**params))
        try:
            client = self._get_client()
            val = await client.get(key)
            if val:
                logger.debug("Cache HIT for recommendations: %s", key)
                return json.loads(val)
            return None
        except Exception as exc:
            logger.warning("Redis recommendation cache read error for key '%s': %s", key, exc)
            return None

    async def set_cached_recommendations(
        self,
        candidate_id: str,
        recommendations_data: Dict[str, Any],
        ttl: int = 180,
        **params: Any,
    ) -> bool:
        """Store candidate recommendations with short TTL (default 3m)."""
        if not self.enabled:
            return False

        key = self._recs_key(candidate_id, self.compute_query_hash(**params))
        try:
            payload = json.dumps(recommendations_data, default=str)
            client = self._get_client()
            return await client.set(key, payload, ex=ttl)
        except Exception as exc:
            logger.warning("Redis recommendation write error for key '%s': %s", key, exc)
            return False

    async def ping(self) -> bool:
        """Check cache liveness."""
        if not self.enabled:
            return False
        try:
            client = self._get_client()
            res = await client.ping()
            return res == "PONG"
        except Exception:
            return False

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.close()


_global_cache_service: Optional[CacheService] = None


def get_cache_service() -> CacheService:
    """Return singleton cache service."""
    global _global_cache_service
    if _global_cache_service is None:
        _global_cache_service = CacheService()
    return _global_cache_service
