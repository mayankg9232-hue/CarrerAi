"""Resume and candidate profile repository interface and in-memory implementation."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional

from app.schemas.resume import CandidateProfile


class IResumeRepository(ABC):
    """Abstract interface for candidate profile persistence."""

    @abstractmethod
    async def save_profile(self, profile: CandidateProfile) -> CandidateProfile:
        """Persist or update a candidate profile."""
        pass

    @abstractmethod
    async def get_by_id(self, profile_id: str) -> Optional[CandidateProfile]:
        """Fetch candidate profile by unique ID."""
        pass

    @abstractmethod
    async def get_by_user_id(self, user_id: str) -> List[CandidateProfile]:
        """Fetch candidate profiles owned by user ID (candidate isolation)."""
        pass

    @abstractmethod
    async def get_by_raw_hash(self, user_id: str, raw_hash: str) -> Optional[CandidateProfile]:
        """Fetch profile for user by raw document hash (deduplication/audit)."""
        pass

    @abstractmethod
    async def delete_profile(self, profile_id: str) -> bool:
        """Delete candidate profile by ID."""
        pass

    @abstractmethod
    async def list_all(self) -> List[CandidateProfile]:
        """List all candidate profiles."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all stored profiles (for tests)."""
        pass


class InMemoryResumeRepository(IResumeRepository):
    """In-memory dictionary-backed resume repository."""

    def __init__(self) -> None:
        self._profiles: Dict[str, CandidateProfile] = {}

    async def save_profile(self, profile: CandidateProfile) -> CandidateProfile:
        self._profiles[profile.id] = profile
        return profile

    async def get_by_id(self, profile_id: str) -> Optional[CandidateProfile]:
        return self._profiles.get(profile_id)

    async def get_by_user_id(self, user_id: str) -> List[CandidateProfile]:
        return [p for p in self._profiles.values() if p.user_id == user_id]

    async def get_by_raw_hash(self, user_id: str, raw_hash: str) -> Optional[CandidateProfile]:
        for p in self._profiles.values():
            if p.user_id == user_id and p.raw_hash == raw_hash:
                return p
        return None

    async def delete_profile(self, profile_id: str) -> bool:
        if profile_id in self._profiles:
            del self._profiles[profile_id]
            return True
        return False

    async def list_all(self) -> List[CandidateProfile]:
        return list(self._profiles.values())

    async def clear(self) -> None:
        self._profiles.clear()


_global_resume_repo: Optional[IResumeRepository] = None


def get_resume_repository() -> IResumeRepository:
    """Return singleton resume repository instance."""
    global _global_resume_repo
    if _global_resume_repo is None:
        _global_resume_repo = InMemoryResumeRepository()
    return _global_resume_repo


def reset_resume_repository() -> None:
    """Reset repository for unit test isolation."""
    global _global_resume_repo
    if _global_resume_repo is not None:
        _global_resume_repo._profiles.clear()
    else:
        _global_resume_repo = InMemoryResumeRepository()
