"""SQL-backed repository implementations for Users, Jobs, Resumes, and Matches."""

from datetime import datetime, timedelta, timezone
import json
import logging
from typing import Any, Dict, List, Optional
import uuid

from app.db.connection import DatabaseManager, UniqueConstraintViolationError, get_db_manager
from app.repositories.job_repo import IJobRepository
from app.repositories.resume_repo import IResumeRepository
from app.repositories.user_repo import IUserRepository, UserRecord
from app.schemas.job import Job
from app.schemas.matching import MatchResult, ScoreBreakdown
from app.schemas.resume import CandidateProfile, EducationItem, ExperienceItem, SkillItem

logger = logging.getLogger("ai_job_matcher.repositories")


class SQLUserRepository(IUserRepository):
    """Persistent SQL-backed user repository enforcing unique email constraints."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or get_db_manager()

    async def create_user(
        self,
        email: str,
        hashed_password: str,
        full_name: str,
        role: str = "candidate",
    ) -> UserRecord:
        norm_email = email.lower().strip()
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        query = """
        INSERT INTO users (id, email, hashed_password, full_name, role, is_active, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?)
        """
        try:
            await self.db.execute(query, (user_id, norm_email, hashed_password, full_name, role, now.isoformat()))
        except UniqueConstraintViolationError:
            raise ValueError(f"User with email {norm_email} already exists")

        return UserRecord(
            id=user_id,
            email=norm_email,
            hashed_password=hashed_password,
            full_name=full_name,
            role=role,
            is_active=True,
            created_at=now,
        )

    async def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        row = await self.db.fetchone("SELECT * FROM users WHERE id = ?", (user_id,))
        return self._to_user_record(row) if row else None

    async def get_by_email(self, email: str) -> Optional[UserRecord]:
        row = await self.db.fetchone("SELECT * FROM users WHERE email = ?", (email.lower().strip(),))
        return self._to_user_record(row) if row else None

    async def list_users(self) -> List[UserRecord]:
        rows = await self.db.fetchall("SELECT * FROM users ORDER BY created_at DESC")
        return [self._to_user_record(r) for r in rows if r]

    async def update_user(self, user_id: str, **updates) -> Optional[UserRecord]:
        existing = await self.get_by_id(user_id)
        if not existing:
            return None

        fields = []
        params = []
        for k, v in updates.items():
            if hasattr(existing, k) and k != "id":
                fields.append(f"{k} = ?")
                params.append(1 if v is True else (0 if v is False else v))

        if not fields:
            return existing

        params.append(user_id)
        query = f"UPDATE users SET {', '.join(fields)} WHERE id = ?"
        await self.db.execute(query, tuple(params))
        return await self.get_by_id(user_id)

    async def clear(self) -> None:
        await self.db.execute("DELETE FROM users")

    def _to_user_record(self, row: Dict[str, Any]) -> UserRecord:
        created_at = datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"]
        return UserRecord(
            id=row["id"],
            email=row["email"],
            hashed_password=row["hashed_password"],
            full_name=row["full_name"],
            role=row["role"],
            is_active=bool(row["is_active"]),
            created_at=created_at,
        )


class SQLJobRepository(IJobRepository):
    """Persistent SQL-backed job repository enforcing SHA-256 deduplication and query filtering."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or get_db_manager()

    async def create_job(self, job: Job) -> Job:
        now_iso = datetime.now(timezone.utc).isoformat()
        collected_at = job.collected_at.isoformat() if job.collected_at else now_iso
        last_seen_at = job.last_seen_at.isoformat() if job.last_seen_at else now_iso

        query = """
        INSERT INTO jobs (
            id, title, company, location, description, required_skills, preferred_skills,
            experience_level, source, source_url, content_hash, collected_at, last_seen_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            job.id,
            job.title,
            job.company,
            job.location,
            job.description,
            json.dumps(job.required_skills),
            json.dumps(job.preferred_skills),
            job.experience_level,
            job.source,
            job.source_url,
            job.content_hash,
            collected_at,
            last_seen_at,
            1 if job.is_active else 0,
        )
        try:
            await self.db.execute(query, params)
        except UniqueConstraintViolationError:
            # Idempotent deduplication update
            if job.content_hash:
                existing = await self.get_by_content_hash(job.content_hash)
                if existing:
                    await self.update_job(existing.id, last_seen_at=datetime.now(timezone.utc))
                    return await self.get_by_id(existing.id) or job
            raise
        return job

    async def get_by_id(self, job_id: str) -> Optional[Job]:
        row = await self.db.fetchone("SELECT * FROM jobs WHERE id = ?", (job_id,))
        return self._to_job(row) if row else None

    async def get_by_content_hash(self, content_hash: str) -> Optional[Job]:
        row = await self.db.fetchone("SELECT * FROM jobs WHERE content_hash = ?", (content_hash,))
        return self._to_job(row) if row else None

    async def get_by_source_url(self, source_url: str) -> Optional[Job]:
        row = await self.db.fetchone("SELECT * FROM jobs WHERE source_url = ?", (source_url,))
        return self._to_job(row) if row else None

    async def update_job(self, job_id: str, **updates) -> Optional[Job]:
        existing = await self.get_by_id(job_id)
        if not existing:
            return None

        fields = []
        params = []
        for k, v in updates.items():
            if k in ("required_skills", "preferred_skills") and isinstance(v, list):
                fields.append(f"{k} = ?")
                params.append(json.dumps(v))
            elif isinstance(v, datetime):
                fields.append(f"{k} = ?")
                params.append(v.isoformat())
            elif isinstance(v, bool):
                fields.append(f"{k} = ?")
                params.append(1 if v else 0)
            elif hasattr(existing, k) and k != "id":
                fields.append(f"{k} = ?")
                params.append(v)

        if not fields:
            return existing

        params.append(job_id)
        query = f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?"
        await self.db.execute(query, tuple(params))
        return await self.get_by_id(job_id)

    async def query_jobs(
        self,
        q: Optional[str] = None,
        skills: Optional[List[str]] = None,
        location: Optional[str] = None,
        only_active: bool = True,
        limit: int = 20,
        offset: int = 0,
    ) -> List[Job]:
        where_clauses = []
        params = []

        if only_active:
            where_clauses.append("is_active = 1")

        if location:
            where_clauses.append("LOWER(location) LIKE ?")
            params.append(f"%{location.lower()}%")

        if q:
            where_clauses.append("(LOWER(title) LIKE ? OR LOWER(description) LIKE ?)")
            params.extend([f"%{q.lower()}%", f"%{q.lower()}%"])

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"SELECT * FROM jobs {where_sql} ORDER BY collected_at DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        rows = await self.db.fetchall(query, tuple(params))
        jobs = [self._to_job(r) for r in rows if r]

        # In-memory skill filtering if skills specified
        if skills:
            skills_lower = [s.lower() for s in skills]
            jobs = [
                j for j in jobs
                if any(req.lower() in skills_lower for req in j.required_skills)
                or any(pref.lower() in skills_lower for pref in j.preferred_skills)
            ]

        return jobs

    async def count_jobs(
        self,
        q: Optional[str] = None,
        skills: Optional[List[str]] = None,
        location: Optional[str] = None,
        only_active: bool = True,
    ) -> int:
        jobs = await self.query_jobs(q=q, skills=skills, location=location, only_active=only_active, limit=10000, offset=0)
        return len(jobs)

    async def mark_stale_jobs(self, stale_threshold_days: int = 30) -> int:
        threshold = (datetime.now(timezone.utc) - timedelta(days=stale_threshold_days)).isoformat()
        query = "UPDATE jobs SET is_active = 0 WHERE is_active = 1 AND last_seen_at < ?"
        return await self.db.execute(query, (threshold,))

    async def list_all(self) -> List[Job]:
        rows = await self.db.fetchall("SELECT * FROM jobs ORDER BY collected_at DESC")
        return [self._to_job(r) for r in rows if r]

    async def clear(self) -> None:
        await self.db.execute("DELETE FROM jobs")

    def _to_job(self, row: Dict[str, Any]) -> Job:
        collected = datetime.fromisoformat(row["collected_at"]) if isinstance(row["collected_at"], str) else row["collected_at"]
        last_seen = datetime.fromisoformat(row["last_seen_at"]) if row.get("last_seen_at") and isinstance(row["last_seen_at"], str) else None
        return Job(
            id=row["id"],
            title=row["title"],
            company=row["company"],
            location=row["location"],
            description=row["description"],
            required_skills=json.loads(row["required_skills"]) if isinstance(row["required_skills"], str) else row["required_skills"],
            preferred_skills=json.loads(row["preferred_skills"]) if isinstance(row["preferred_skills"], str) else row["preferred_skills"],
            experience_level=row.get("experience_level"),
            salary=row.get("salary"),
            experience_years_required=row.get("experience_years_required"),
            education_required=row.get("education_required"),
            responsibilities=json.loads(row["responsibilities"]) if row.get("responsibilities") and isinstance(row["responsibilities"], str) else (row.get("responsibilities") or []),
            domain=row.get("domain"),
            source=row.get("source", "firecrawl"),
            source_url=row.get("source_url"),
            content_hash=row.get("content_hash"),
            collected_at=collected,
            last_seen_at=last_seen,
            is_active=bool(row["is_active"]),
        )


class SQLResumeRepository(IResumeRepository):
    """Persistent SQL-backed candidate profile repository enforcing user data isolation and CV deduplication."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or get_db_manager()

    async def save_profile(self, profile: CandidateProfile) -> CandidateProfile:
        now_iso = profile.created_at.isoformat() if profile.created_at else datetime.now(timezone.utc).isoformat()
        query = """
        INSERT INTO candidate_profiles (
            id, user_id, raw_hash, full_name, email, phone, summary,
            skills, experience, education, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            full_name = excluded.full_name,
            email = excluded.email,
            phone = excluded.phone,
            summary = excluded.summary,
            skills = excluded.skills,
            experience = excluded.experience,
            education = excluded.education
        """
        params = (
            profile.id,
            profile.user_id,
            profile.raw_hash,
            profile.full_name,
            profile.email,
            getattr(profile, "phone", None),
            profile.summary,
            json.dumps([s.model_dump(mode="json") for s in profile.skills]),
            json.dumps([e.model_dump(mode="json") for e in profile.experience]),
            json.dumps([ed.model_dump(mode="json") for ed in profile.education]),
            now_iso,
        )
        try:
            await self.db.execute(query, params)
        except UniqueConstraintViolationError:
            # User uploaded identical document hash
            existing = await self.get_by_raw_hash(profile.user_id, profile.raw_hash)
            if existing:
                return existing
            raise
        return profile

    async def get_by_id(self, profile_id: str) -> Optional[CandidateProfile]:
        row = await self.db.fetchone("SELECT * FROM candidate_profiles WHERE id = ?", (profile_id,))
        return self._to_candidate_profile(row) if row else None

    async def get_by_user_id(self, user_id: str) -> List[CandidateProfile]:
        rows = await self.db.fetchall("SELECT * FROM candidate_profiles WHERE user_id = ? ORDER BY created_at DESC", (user_id,))
        return [self._to_candidate_profile(r) for r in rows if r]

    async def get_by_raw_hash(self, user_id: str, raw_hash: str) -> Optional[CandidateProfile]:
        row = await self.db.fetchone(
            "SELECT * FROM candidate_profiles WHERE user_id = ? AND raw_hash = ?",
            (user_id, raw_hash),
        )
        return self._to_candidate_profile(row) if row else None

    async def delete_profile(self, profile_id: str) -> bool:
        rows_affected = await self.db.execute("DELETE FROM candidate_profiles WHERE id = ?", (profile_id,))
        return rows_affected > 0

    async def list_all(self) -> List[CandidateProfile]:
        rows = await self.db.fetchall("SELECT * FROM candidate_profiles ORDER BY created_at DESC")
        return [self._to_candidate_profile(r) for r in rows if r]

    async def clear(self) -> None:
        await self.db.execute("DELETE FROM candidate_profiles")

    def _to_candidate_profile(self, row: Dict[str, Any]) -> CandidateProfile:
        created = datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"]
        raw_skills = json.loads(row["skills"]) if isinstance(row["skills"], str) else row["skills"]
        raw_exp = json.loads(row["experience"]) if isinstance(row["experience"], str) else row["experience"]
        raw_edu = json.loads(row["education"]) if isinstance(row["education"], str) else row["education"]

        return CandidateProfile(
            id=row["id"],
            user_id=row["user_id"],
            raw_hash=row["raw_hash"],
            full_name=row["full_name"],
            email=row["email"],
            summary=row["summary"],
            skills=[SkillItem.model_validate(s) for s in raw_skills],
            experience=[ExperienceItem.model_validate(e) for e in raw_exp],
            education=[EducationItem.model_validate(ed) for ed in raw_edu],
            created_at=created,
        )


class SQLMatchRepository:
    """Persistent SQL-backed repository for match records, score breakdowns, and audit trails."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or get_db_manager()

    async def save_match(self, match: MatchResult) -> MatchResult:
        now_iso = datetime.now(timezone.utc).isoformat()
        match_id = f"match_{uuid.uuid4().hex[:12]}"
        query = """
        INSERT INTO match_records (
            id, candidate_id, job_id, overall_score, score_breakdown,
            matched_skills, missing_skills, caveat, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            match_id,
            match.candidate_id,
            match.job_id,
            match.overall_score,
            json.dumps(match.breakdown.model_dump(mode="json")),
            json.dumps(match.matched_skills),
            json.dumps(match.missing_skills),
            match.caveat,
            now_iso,
        )
        await self.db.execute(query, params)
        return match

    async def get_matches_for_candidate(self, candidate_id: str) -> List[Dict[str, Any]]:
        rows = await self.db.fetchall(
            "SELECT * FROM match_records WHERE candidate_id = ? ORDER BY overall_score DESC",
            (candidate_id,),
        )
        return rows
