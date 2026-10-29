"""Resume and candidate profile repository interface and in-memory implementation."""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Tuple

from app.schemas.resume import CandidateProfile, ResumeRecord


class IResumeRepository(ABC):
    """Abstract interface for candidate profile and persistent resume document storage."""

    # 1. Candidate Profile Operations
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

    # 2. Persistent Resume Document Operations
    @abstractmethod
    async def create_resume(self, resume: ResumeRecord) -> ResumeRecord:
        """Persist a new resume document record."""
        pass

    @abstractmethod
    async def get_resume(self, resume_id: str) -> Optional[ResumeRecord]:
        """Fetch resume document by ID."""
        pass

    @abstractmethod
    async def get_user_resumes(self, user_id: str) -> List[ResumeRecord]:
        """Fetch all resume records owned by a user."""
        pass

    @abstractmethod
    async def get_active_resume(self, user_id: str) -> Optional[ResumeRecord]:
        """Fetch the currently active resume for a user."""
        pass

    @abstractmethod
    async def set_active_resume(self, user_id: str, resume_id: str) -> bool:
        """Mark a specific resume as active and deactivate other versions for this user."""
        pass

    @abstractmethod
    async def delete_resume(self, resume_id: str) -> bool:
        """Delete a resume document record."""
        pass

    @abstractmethod
    async def get_resume_by_hash(self, user_id: str, raw_hash: str) -> Optional[ResumeRecord]:
        """Fetch existing resume record by checksum for deduplication."""
        pass

    @abstractmethod
    async def resolve_profile(
        self,
        identifier: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[CandidateProfile]:
        """Resolve authoritative candidate profile by profile_id, resume_id, user_id, or client reference."""
        pass

    async def get_active_resume_and_profile(
        self,
        user_id: str,
    ) -> Tuple[Optional[ResumeRecord], Optional[CandidateProfile]]:
        """Fetch active resume and candidate profile in an optimized single call."""
        resume = await self.get_active_resume(user_id)
        if not resume:
            user_resumes = await self.get_user_resumes(user_id)
            resume = user_resumes[0] if user_resumes else None
        if not resume:
            prof = await self.resolve_profile(user_id=user_id)
            return None, prof
        prof = await self.resolve_profile(identifier=resume.profile_id, user_id=user_id)
        return resume, prof

    @abstractmethod
    async def clear(self) -> None:
        """Clear all stored profiles and resumes (for tests)."""
        pass


class InMemoryResumeRepository(IResumeRepository):
    """In-memory dictionary-backed resume repository."""

    def __init__(self) -> None:
        self._profiles: Dict[str, CandidateProfile] = {}
        self._resumes: Dict[str, ResumeRecord] = {}

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

    async def create_resume(self, resume: ResumeRecord) -> ResumeRecord:
        # If this resume is set active, deactivate others for user
        if resume.is_active:
            for r in self._resumes.values():
                if r.user_id == resume.user_id and r.id != resume.id:
                    r.is_active = False
        self._resumes[resume.id] = resume
        return resume

    async def get_resume(self, resume_id: str) -> Optional[ResumeRecord]:
        return self._resumes.get(resume_id)

    async def get_user_resumes(self, user_id: str) -> List[ResumeRecord]:
        return [r for r in self._resumes.values() if r.user_id == user_id]

    async def get_active_resume(self, user_id: str) -> Optional[ResumeRecord]:
        for r in self._resumes.values():
            if r.user_id == user_id and r.is_active:
                return r
        # Fallback to most recent
        user_res = await self.get_user_resumes(user_id)
        return user_res[0] if user_res else None

    async def set_active_resume(self, user_id: str, resume_id: str) -> bool:
        target = self._resumes.get(resume_id)
        if not target or target.user_id != user_id:
            return False
        for r in self._resumes.values():
            if r.user_id == user_id:
                r.is_active = (r.id == resume_id)
        return True

    async def delete_resume(self, resume_id: str) -> bool:
        if resume_id in self._resumes:
            del self._resumes[resume_id]
            return True
        return False

    async def get_resume_by_hash(self, user_id: str, raw_hash: str) -> Optional[ResumeRecord]:
        for r in self._resumes.values():
            if r.user_id == user_id and r.raw_hash == raw_hash:
                return r
        return None

    async def resolve_profile(
        self,
        identifier: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[CandidateProfile]:
        if identifier:
            # 1. Direct profile ID match
            prof = self._profiles.get(identifier)
            if prof:
                if user_id and prof.user_id != user_id:
                    return None
                return prof

            # 2. Match via resume ID (e.g. res_...)
            resume = self._resumes.get(identifier)
            if resume:
                if user_id and resume.user_id != user_id:
                    return None
                if resume.profile_id and resume.profile_id in self._profiles:
                    return self._profiles[resume.profile_id]

            # 3. Handle synthetic or client-prefixed user identifiers (e.g. profile_usr_... or usr_...)
            if identifier.startswith("profile_") or identifier.startswith("usr_"):
                candidate_uid = identifier.replace("profile_", "")
                target_uid = candidate_uid if candidate_uid.startswith("usr_") else user_id
                if target_uid:
                    active_res = await self.get_active_resume(target_uid)
                    if active_res and active_res.profile_id and active_res.profile_id in self._profiles:
                        return self._profiles[active_res.profile_id]
                    user_profs = await self.get_by_user_id(target_uid)
                    if user_profs:
                        return user_profs[0]

            return None

        # 4. Contextual resolution by authenticated user ID (when identifier is omitted)
        if user_id:
            active_res = await self.get_active_resume(user_id)
            if active_res and active_res.profile_id and active_res.profile_id in self._profiles:
                return self._profiles[active_res.profile_id]
            user_profs = await self.get_by_user_id(user_id)
            if user_profs:
                return user_profs[0]

        return None

    async def clear(self) -> None:
        self._profiles.clear()
        self._resumes.clear()



_global_resume_repo: Optional[IResumeRepository] = None


def get_resume_repository() -> IResumeRepository:
    """Return singleton resume repository instance based on configured backend."""
    global _global_resume_repo
    if _global_resume_repo is None:
        from app.core.config import get_settings
        settings = get_settings()
        if settings.REPOSITORY_BACKEND == "sql":
            from app.db.sql_repositories import SQLResumeRepository
            _global_resume_repo = SQLResumeRepository()
        else:
            _global_resume_repo = InMemoryResumeRepository()
    return _global_resume_repo


def reset_resume_repository(repo: Optional[IResumeRepository] = None) -> None:
    """Reset repository for unit test isolation."""
    global _global_resume_repo
    if repo is not None:
        _global_resume_repo = repo
    elif _global_resume_repo is not None and isinstance(_global_resume_repo, InMemoryResumeRepository):
        _global_resume_repo._profiles.clear()
    else:
        _global_resume_repo = InMemoryResumeRepository()
