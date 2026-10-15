"""Job repository interface and in-memory implementation."""

from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional
import uuid

from app.schemas.job import Job


class IJobRepository(ABC):
    """Abstract interface for job listing persistence."""

    @abstractmethod
    async def create_job(self, job: Job) -> Job:
        """Persist a new job listing."""
        pass

    @abstractmethod
    async def get_by_id(self, job_id: str) -> Optional[Job]:
        """Retrieve a job by unique ID."""
        pass

    @abstractmethod
    async def get_by_content_hash(self, content_hash: str) -> Optional[Job]:
        """Retrieve a job by content hash for deduplication."""
        pass

    @abstractmethod
    async def get_by_source_url(self, source_url: str) -> Optional[Job]:
        """Retrieve a job by source URL."""
        pass

    @abstractmethod
    async def get_by_platform_and_external_id(self, source_platform: str, external_job_id: str) -> Optional[Job]:
        """Retrieve a job by source platform and external ATS ID for deduplication."""
        pass

    @abstractmethod
    async def update_job(self, job_id: str, **updates) -> Optional[Job]:
        """Update fields of an existing job listing."""
        pass

    @abstractmethod
    async def query_jobs(
        self,
        q: Optional[str] = None,
        skills: Optional[List[str]] = None,
        location: Optional[str] = None,
        only_active: bool = True,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Job]:
        """Query and filter job listings with pagination."""
        pass

    @abstractmethod
    async def count_jobs(
        self,
        q: Optional[str] = None,
        skills: Optional[List[str]] = None,
        location: Optional[str] = None,
        only_active: bool = True,
    ) -> int:
        """Count matching jobs for pagination metadata."""
        pass

    @abstractmethod
    async def mark_stale_jobs(self, stale_threshold_days: int = 30) -> int:
        """Mark listings older than stale threshold as inactive."""
        pass

    @abstractmethod
    async def list_all(self) -> List[Job]:
        """List all stored jobs."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all stored jobs (for tests)."""
        pass


class InMemoryJobRepository(IJobRepository):
    """In-memory dictionary-backed job repository for development and tests."""

    def __init__(self) -> None:
        self._jobs: Dict[str, Job] = {}
        self._hash_index: Dict[str, str] = {}  # content_hash -> job_id
        self._url_index: Dict[str, str] = {}   # source_url -> job_id
        self._platform_index: Dict[str, str] = {}  # "platform:ext_id" -> job_id

    async def create_job(self, job: Job) -> Job:
        self._jobs[job.id] = job
        if job.content_hash:
            self._hash_index[job.content_hash] = job.id
        if job.source_url:
            self._url_index[job.source_url] = job.id
        if job.source_platform and job.external_job_id:
            self._platform_index[f"{job.source_platform}:{job.external_job_id}"] = job.id
        return job

    async def get_by_id(self, job_id: str) -> Optional[Job]:
        return self._jobs.get(job_id)

    async def get_by_content_hash(self, content_hash: str) -> Optional[Job]:
        job_id = self._hash_index.get(content_hash)
        if job_id:
            return self._jobs.get(job_id)
        return None

    async def get_by_source_url(self, source_url: str) -> Optional[Job]:
        job_id = self._url_index.get(source_url)
        if job_id:
            return self._jobs.get(job_id)
        return None

    async def get_by_platform_and_external_id(self, source_platform: str, external_job_id: str) -> Optional[Job]:
        job_id = self._platform_index.get(f"{source_platform}:{external_job_id}")
        if job_id:
            return self._jobs.get(job_id)
        return None

    async def update_job(self, job_id: str, **updates) -> Optional[Job]:
        job = self._jobs.get(job_id)
        if not job:
            return None

        job_data = job.model_dump()
        for k, v in updates.items():
            if k in job_data:
                job_data[k] = v

        updated_job = Job(**job_data)
        self._jobs[job_id] = updated_job
        if updated_job.content_hash:
            self._hash_index[updated_job.content_hash] = job_id
        if updated_job.source_url:
            self._url_index[updated_job.source_url] = job_id
        return updated_job

    async def query_jobs(
        self,
        q: Optional[str] = None,
        skills: Optional[List[str]] = None,
        location: Optional[str] = None,
        only_active: bool = True,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Job]:
        results = await self._filter_jobs(q, skills, location, only_active)
        return results[offset : offset + limit]

    async def count_jobs(
        self,
        q: Optional[str] = None,
        skills: Optional[List[str]] = None,
        location: Optional[str] = None,
        only_active: bool = True,
    ) -> int:
        results = await self._filter_jobs(q, skills, location, only_active)
        return len(results)

    async def _filter_jobs(
        self,
        q: Optional[str],
        skills: Optional[List[str]],
        location: Optional[str],
        only_active: bool,
    ) -> List[Job]:
        matched: List[Job] = []
        for job in self._jobs.values():
            if only_active and not job.is_active:
                continue

            if q:
                query_lower = q.lower()
                title_match = query_lower in job.title.lower()
                desc_match = query_lower in job.description.lower()
                company_match = query_lower in job.company.lower()
                if not (title_match or desc_match or company_match):
                    continue

            if location:
                loc_lower = location.lower()
                if loc_lower not in job.location.lower():
                    continue

            if skills:
                # Job must match at least one of the queried skills (case-insensitive)
                job_skills_lower = [s.lower() for s in (job.required_skills + job.preferred_skills)]
                queried_skills_lower = [s.lower() for s in skills]
                if not any(qs in job_skills_lower for qs in queried_skills_lower):
                    continue

            matched.append(job)

        # Sort by collected_at descending
        matched.sort(key=lambda j: j.collected_at, reverse=True)
        return matched

    async def mark_stale_jobs(self, stale_threshold_days: int = 30) -> int:
        now = datetime.now(timezone.utc)
        threshold_dt = now - timedelta(days=stale_threshold_days)
        marked_count = 0

        for job_id, job in list(self._jobs.items()):
            reference_time = job.last_seen_at or job.collected_at
            if job.is_active and reference_time < threshold_dt:
                await self.update_job(job_id, is_active=False)
                marked_count += 1

        return marked_count

    async def list_all(self) -> List[Job]:
        return list(self._jobs.values())

    async def clear(self) -> None:
        self._jobs.clear()
        self._hash_index.clear()
        self._url_index.clear()


_global_job_repo: Optional[IJobRepository] = None


def get_job_repository() -> IJobRepository:
    """Return singleton job repository instance based on configured backend."""
    global _global_job_repo
    if _global_job_repo is None:
        from app.core.config import get_settings
        settings = get_settings()
        if settings.REPOSITORY_BACKEND == "sql":
            from app.db.sql_repositories import SQLJobRepository
            _global_job_repo = SQLJobRepository()
        else:
            _global_job_repo = InMemoryJobRepository()
    return _global_job_repo


def reset_job_repository(repo: Optional[IJobRepository] = None) -> None:
    """Reset repository for unit test isolation."""
    global _global_job_repo
    if repo is not None:
        _global_job_repo = repo
    elif _global_job_repo is not None and isinstance(_global_job_repo, InMemoryJobRepository):
        _global_job_repo._jobs.clear()
        _global_job_repo._hash_index.clear()
        _global_job_repo._url_index.clear()
    else:
        _global_job_repo = InMemoryJobRepository()
