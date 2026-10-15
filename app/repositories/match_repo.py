"""Match repository interface and implementations conforming to project architecture."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from app.core.config import get_settings
from app.schemas.matching import MatchResult


class IMatchRepository(ABC):
    """Abstract interface for match evaluation persistence."""

    @abstractmethod
    async def save_match(self, match: MatchResult) -> MatchResult:
        """Persist a computed match result."""
        pass

    @abstractmethod
    async def get_matches_for_candidate(self, candidate_id: str) -> List[Dict[str, Any]]:
        """Retrieve match records for a candidate profile."""
        pass

    @abstractmethod
    async def get_match(self, candidate_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve latest match between a specific candidate and job."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all stored match records (for tests)."""
        pass


class InMemoryMatchRepository(IMatchRepository):
    """In-memory dictionary-backed match repository for development and tests."""

    def __init__(self) -> None:
        self._matches: List[Dict[str, Any]] = []

    async def save_match(self, match: MatchResult) -> MatchResult:
        match_id = f"match_{uuid.uuid4().hex[:12]}"
        record = {
            "id": match_id,
            "candidate_id": match.candidate_id,
            "job_id": match.job_id,
            "overall_score": match.overall_score,
            "score_breakdown": match.breakdown.model_dump(mode="json"),
            "matched_skills": match.matched_skills,
            "missing_skills": match.missing_skills,
            "caveat": match.caveat,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        self._matches.append(record)
        return match

    async def get_matches_for_candidate(self, candidate_id: str) -> List[Dict[str, Any]]:
        results = [m for m in self._matches if m["candidate_id"] == candidate_id]
        results.sort(key=lambda m: m["overall_score"], reverse=True)
        return results

    async def get_match(self, candidate_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        for m in reversed(self._matches):
            if m["candidate_id"] == candidate_id and m["job_id"] == job_id:
                return m
        return None

    async def clear(self) -> None:
        self._matches.clear()


_global_match_repo: Optional[IMatchRepository] = None


def get_match_repository() -> IMatchRepository:
    """Return singleton match repository instance configured for current environment."""
    global _global_match_repo
    if _global_match_repo is None:
        settings = get_settings()
        if settings.REPOSITORY_BACKEND == "sql":
            from app.db.sql_repositories import SQLMatchRepository
            _global_match_repo = SQLMatchRepository()
        else:
            _global_match_repo = InMemoryMatchRepository()
    return _global_match_repo


def reset_match_repository(repo: Optional[IMatchRepository] = None) -> None:
    """Reset repository for unit test isolation."""
    global _global_match_repo
    _global_match_repo = repo or InMemoryMatchRepository()
