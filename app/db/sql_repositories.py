"""SQL-backed repository implementations for Users, Jobs, Resumes, and Matches."""

from datetime import datetime, timedelta, timezone
import hashlib
import json
import logging
import time
from typing import Any, Dict, List, Optional, Tuple
import uuid

from app.db.connection import DatabaseManager, UniqueConstraintViolationError, get_db_manager
from app.repositories.job_repo import IJobRepository
from app.repositories.match_repo import IMatchRepository
from app.repositories.resume_repo import IResumeRepository
from app.repositories.user_repo import IUserRepository, UserRecord
from app.schemas.job import Job
from app.schemas.matching import MatchResult, ScoreBreakdown
from app.schemas.resume import CandidateProfile, EducationItem, ExperienceItem, ResumeRecord, SkillItem

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
        email_verified: bool = False,
    ) -> UserRecord:
        norm_email = email.lower().strip()
        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc)
        query = """
        INSERT INTO users (id, email, hashed_password, full_name, role, is_active, email_verified, created_at)
        VALUES (?, ?, ?, ?, ?, 1, ?, ?)
        """
        try:
            await self.db.execute(query, (user_id, norm_email, hashed_password, full_name, role, 1 if email_verified else 0, now.isoformat()))
        except UniqueConstraintViolationError:
            raise ValueError(f"User with email {norm_email} already exists")

        return UserRecord(
            id=user_id,
            email=norm_email,
            hashed_password=hashed_password,
            full_name=full_name,
            role=role,
            is_active=True,
            email_verified=email_verified,
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
            email_verified=bool(row.get("email_verified", 0)),
            created_at=created_at,
        )


class SQLJobRepository(IJobRepository):
    """Persistent SQL-backed job repository enforcing SHA-256 deduplication and query filtering."""

    _active_jobs_count_cache: Optional[Tuple[float, int]] = None

    @classmethod
    def invalidate_count_cache(cls) -> None:
        cls._active_jobs_count_cache = None

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or get_db_manager()

    async def create_job(self, job: Job) -> Job:
        now_iso = datetime.now(timezone.utc).isoformat()
        collected_at = job.collected_at.isoformat() if job.collected_at else now_iso
        last_seen_at = job.last_seen_at.isoformat() if job.last_seen_at else now_iso
        content_hash = job.content_hash or hashlib.sha256(
            f"{job.title.strip().lower()}|{job.company.strip().lower()}|{job.description.strip().lower()}".encode("utf-8")
        ).hexdigest()
        job.content_hash = content_hash

        posted_at = job.posted_at.isoformat() if job.posted_at else None
        updated_at = job.updated_at.isoformat() if job.updated_at else None
        query = """
        INSERT INTO jobs (
            id, title, company, location, description, required_skills, preferred_skills,
            experience_level, source, source_platform, external_job_id, department,
            employment_type, workplace_type, source_url, apply_url, content_hash, collected_at,
            posted_at, updated_at, salary, experience_years_required, education_required,
            responsibilities, domain, last_seen_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            job.source_platform,
            job.external_job_id,
            job.department,
            job.employment_type,
            job.workplace_type,
            job.source_url,
            job.apply_url,
            content_hash,
            collected_at,
            posted_at,
            updated_at,
            job.salary,
            job.experience_years_required,
            job.education_required,
            json.dumps(job.responsibilities),
            job.domain,
            last_seen_at,
            1 if job.is_active else 0,
        )
        try:
            await self.db.execute(query, params)
        except UniqueConstraintViolationError:
            # Idempotent deduplication update
            if job.source_platform and job.external_job_id:
                existing = await self.get_by_platform_and_external_id(job.source_platform, job.external_job_id)
                if existing:
                    await self.update_job(existing.id, last_seen_at=datetime.now(timezone.utc), is_active=True)
                    return await self.get_by_id(existing.id) or job
            if job.content_hash:
                existing = await self.get_by_content_hash(job.content_hash)
                if existing:
                    await self.update_job(existing.id, last_seen_at=datetime.now(timezone.utc), is_active=True)
                    return await self.get_by_id(existing.id) or job
            raise
        SQLJobRepository.invalidate_count_cache()
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

    async def get_by_platform_and_external_id(self, source_platform: str, external_job_id: str) -> Optional[Job]:
        row = await self.db.fetchone(
            "SELECT * FROM jobs WHERE source_platform = ? AND external_job_id = ?",
            (source_platform, external_job_id),
        )
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
        SQLJobRepository.invalidate_count_cache()
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
        if skills:
            jobs = await self.query_jobs(q=q, skills=skills, location=location, only_active=only_active, limit=10000, offset=0)
            return len(jobs)

        # Fast path: check in-memory TTL cache for total active catalog jobs count
        if not q and not skills and not location and only_active:
            now = time.time()
            if self._active_jobs_count_cache and (now - self._active_jobs_count_cache[0]) < 60.0:
                return self._active_jobs_count_cache[1]
            row = await self.db.fetchone("SELECT COUNT(*) as cnt FROM jobs WHERE is_active = 1")
            cnt = int(row["cnt"]) if row and "cnt" in row else 0
            SQLJobRepository._active_jobs_count_cache = (now, cnt)
            return cnt

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
        row = await self.db.fetchone(f"SELECT COUNT(*) as cnt FROM jobs {where_sql}", tuple(params))
        return int(row["cnt"]) if row and "cnt" in row else 0

    async def mark_stale_jobs(self, stale_threshold_days: int = 30) -> int:
        threshold = (datetime.now(timezone.utc) - timedelta(days=stale_threshold_days)).isoformat()
        query = "UPDATE jobs SET is_active = 0 WHERE is_active = 1 AND last_seen_at < ?"
        rows = await self.db.execute(query, (threshold,))
        SQLJobRepository.invalidate_count_cache()
        return rows

    async def list_all(self) -> List[Job]:
        rows = await self.db.fetchall("SELECT * FROM jobs ORDER BY collected_at DESC")
        return [self._to_job(r) for r in rows if r]

    async def clear(self) -> None:
        await self.db.execute("DELETE FROM user_saved_jobs")
        await self.db.execute("DELETE FROM jobs")
        SQLJobRepository.invalidate_count_cache()

    # Saved Jobs Operations
    async def save_job(self, user_id: str, job_id: str, notes: Optional[str] = None) -> bool:
        job = await self.get_by_id(job_id)
        if not job:
            return False

        save_id = f"sv_{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        query = """
        INSERT INTO user_saved_jobs (id, user_id, job_id, saved_at, notes)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(user_id, job_id) DO UPDATE SET saved_at = excluded.saved_at
        """
        try:
            await self.db.execute(query, (save_id, user_id, job_id, now, notes))
            return True
        except Exception as e:
            logger.error("Error saving job %s for user %s: %s", job_id, user_id, e)
            return False

    async def unsave_job(self, user_id: str, job_id: str) -> bool:
        query = "DELETE FROM user_saved_jobs WHERE user_id = ? AND job_id = ?"
        rows = await self.db.execute(query, (user_id, job_id))
        return rows > 0

    async def get_saved_jobs(self, user_id: str, limit: int = 50, offset: int = 0) -> List[Job]:
        query = """
        SELECT j.* FROM jobs j
        INNER JOIN user_saved_jobs s ON j.id = s.job_id
        WHERE s.user_id = ?
        ORDER BY s.saved_at DESC
        LIMIT ? OFFSET ?
        """
        rows = await self.db.fetchall(query, (user_id, limit, offset))
        return [self._to_job(r) for r in rows if r]

    async def get_saved_job_ids(self, user_id: str) -> List[str]:
        query = "SELECT job_id FROM user_saved_jobs WHERE user_id = ? ORDER BY saved_at DESC"
        rows = await self.db.fetchall(query, (user_id,))
        return [r["job_id"] for r in rows if r]

    async def count_saved_jobs(self, user_id: str) -> int:
        query = "SELECT COUNT(*) as cnt FROM user_saved_jobs WHERE user_id = ?"
        row = await self.db.fetchone(query, (user_id,))
        return int(row["cnt"]) if row and "cnt" in row else 0

    async def is_job_saved(self, user_id: str, job_id: str) -> bool:
        query = "SELECT 1 FROM user_saved_jobs WHERE user_id = ? AND job_id = ?"
        row = await self.db.fetchone(query, (user_id, job_id))
        return bool(row)

    def _to_job(self, row: Dict[str, Any]) -> Job:
        collected = datetime.fromisoformat(row["collected_at"]) if isinstance(row["collected_at"], str) else row["collected_at"]
        last_seen = datetime.fromisoformat(row["last_seen_at"]) if row.get("last_seen_at") and isinstance(row["last_seen_at"], str) else None
        posted = datetime.fromisoformat(row["posted_at"]) if row.get("posted_at") and isinstance(row["posted_at"], str) else None
        updated = datetime.fromisoformat(row["updated_at"]) if row.get("updated_at") and isinstance(row["updated_at"], str) else None
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
            source_platform=row.get("source_platform"),
            external_job_id=row.get("external_job_id"),
            department=row.get("department"),
            employment_type=row.get("employment_type"),
            workplace_type=row.get("workplace_type"),
            source_url=row.get("source_url"),
            apply_url=row.get("apply_url"),
            content_hash=row.get("content_hash"),
            collected_at=collected,
            posted_at=posted,
            updated_at=updated,
            last_seen_at=last_seen,
            is_active=bool(row["is_active"]),
        )


class SQLResumeRepository(IResumeRepository):
    """Persistent SQL-backed candidate profile repository enforcing user data isolation and CV deduplication."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or get_db_manager()

    async def save_profile(self, profile: CandidateProfile) -> CandidateProfile:
        now_iso = profile.created_at.isoformat() if profile.created_at else datetime.now(timezone.utc).isoformat()
        raw_hash = profile.raw_hash or hashlib.sha256(
            f"{profile.user_id}|{profile.full_name or ''}|{profile.summary or ''}".encode("utf-8")
        ).hexdigest()
        profile.raw_hash = raw_hash

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
            raw_hash,
            profile.full_name,
            profile.email,
            getattr(profile, "phone", getattr(profile, "headline", None)),
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
            existing = await self.get_by_raw_hash(profile.user_id, raw_hash)
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

    async def resolve_profile(
        self,
        identifier: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Optional[CandidateProfile]:
        """Resolve authoritative candidate profile by profile_id, resume_id, user_id, or client reference."""
        if identifier:
            # 1. Direct profile ID match (e.g. prof_...)
            prof = await self.get_by_id(identifier)
            if prof:
                if user_id and prof.user_id != user_id:
                    return None
                return prof

            # 2. Match via resume ID (e.g. res_...)
            resume = await self.get_resume(identifier)
            if resume:
                if user_id and resume.user_id != user_id:
                    return None
                if resume.profile_id:
                    p = await self.get_by_id(resume.profile_id)
                    if p:
                        return p

            # 3. Handle synthetic or client-prefixed user identifiers (e.g. profile_usr_... or usr_...)
            if identifier.startswith("profile_") or identifier.startswith("usr_"):
                candidate_uid = identifier.replace("profile_", "")
                target_uid = candidate_uid if candidate_uid.startswith("usr_") else user_id
                if target_uid:
                    active_res = await self.get_active_resume(target_uid)
                    if active_res and active_res.profile_id:
                        p = await self.get_by_id(active_res.profile_id)
                        if p:
                            return p
                    user_profs = await self.get_by_user_id(target_uid)
                    if user_profs:
                        return user_profs[0]

            return None

        # 4. Contextual resolution by authenticated user ID
        if user_id:
            active_res = await self.get_active_resume(user_id)
            if active_res and active_res.profile_id:
                p = await self.get_by_id(active_res.profile_id)
                if p:
                    return p
            user_profs = await self.get_by_user_id(user_id)
            if user_profs:
                return user_profs[0]

        return None

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
            headline=row.get("phone") or row.get("headline"),
            email=row["email"],
            summary=row["summary"],
            skills=[SkillItem.model_validate(s) for s in raw_skills],
            experience=[ExperienceItem.model_validate(e) for e in raw_exp],
            education=[EducationItem.model_validate(ed) for ed in raw_edu],
            created_at=created,
        )

    # Resume Document Methods
    async def create_resume(self, resume: ResumeRecord) -> ResumeRecord:
        now_iso = resume.created_at.isoformat() if resume.created_at else datetime.now(timezone.utc).isoformat()
        updated_iso = resume.updated_at.isoformat() if resume.updated_at else now_iso

        # If active, deactivate other resumes for this user
        if resume.is_active:
            await self.db.execute(
                "UPDATE resumes SET is_active = 0, updated_at = ? WHERE user_id = ?",
                (updated_iso, resume.user_id),
            )

        query = """
        INSERT INTO resumes (
            id, user_id, filename, storage_key, file_size, mime_type, raw_hash,
            status, is_active, version, profile_id, extracted_text, error_message,
            created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            filename = excluded.filename,
            storage_key = excluded.storage_key,
            file_size = excluded.file_size,
            status = excluded.status,
            is_active = excluded.is_active,
            version = excluded.version,
            profile_id = excluded.profile_id,
            extracted_text = excluded.extracted_text,
            error_message = excluded.error_message,
            updated_at = excluded.updated_at
        """
        params = (
            resume.id,
            resume.user_id,
            resume.filename,
            resume.storage_key,
            resume.file_size,
            resume.mime_type,
            resume.raw_hash,
            resume.status,
            1 if resume.is_active else 0,
            resume.version,
            resume.profile_id,
            resume.extracted_text,
            resume.error_message,
            now_iso,
            updated_iso,
        )
        await self.db.execute(query, params)
        return resume

    async def get_resume(self, resume_id: str) -> Optional[ResumeRecord]:
        row = await self.db.fetchone("SELECT * FROM resumes WHERE id = ?", (resume_id,))
        return self._to_resume_record(row) if row else None

    async def get_user_resumes(self, user_id: str) -> List[ResumeRecord]:
        rows = await self.db.fetchall(
            "SELECT * FROM resumes WHERE user_id = ? ORDER BY version DESC, created_at DESC",
            (user_id,),
        )
        return [self._to_resume_record(r) for r in rows if r]

    async def get_active_resume(self, user_id: str) -> Optional[ResumeRecord]:
        row = await self.db.fetchone(
            "SELECT * FROM resumes WHERE user_id = ? AND is_active = 1 ORDER BY updated_at DESC LIMIT 1",
            (user_id,),
        )
        if not row:
            resumes = await self.get_user_resumes(user_id)
            return resumes[0] if resumes else None
        return self._to_resume_record(row)

    async def get_active_resume_and_profile(
        self, user_id: str
    ) -> Tuple[Optional[ResumeRecord], Optional[CandidateProfile]]:
        """
        Fast single-roundtrip retrieval of the user's active resume joined with candidate profile.
        Eliminates sequential DB queries during dashboard initialization.
        """
        query = """
        SELECT 
            r.id AS r_id, r.user_id AS r_user_id, r.filename AS r_filename, r.storage_key AS r_storage_key,
            r.file_size AS r_file_size, r.mime_type AS r_mime_type, r.raw_hash AS r_raw_hash,
            r.status AS r_status, r.is_active AS r_is_active, r.version AS r_version,
            r.profile_id AS r_profile_id, r.extracted_text AS r_extracted_text,
            r.error_message AS r_error_message, r.created_at AS r_created_at, r.updated_at AS r_updated_at,
            p.id AS p_id, p.user_id AS p_user_id, p.raw_hash AS p_raw_hash, p.full_name AS p_full_name,
            p.email AS p_email, p.phone AS p_phone, p.summary AS p_summary, p.skills AS p_skills,
            p.experience AS p_experience, p.education AS p_education, p.created_at AS p_created_at
        FROM resumes r
        LEFT JOIN candidate_profiles p ON (r.profile_id = p.id OR (r.profile_id IS NULL AND r.user_id = p.user_id))
        WHERE r.user_id = ? AND r.is_active = 1
        ORDER BY r.version DESC, r.created_at DESC
        LIMIT 1
        """
        row = await self.db.fetchone(query, (user_id,))
        if not row or not row.get("r_id"):
            active_res = await self.get_active_resume(user_id)
            if not active_res:
                user_resumes = await self.get_user_resumes(user_id)
                active_res = user_resumes[0] if user_resumes else None
            if not active_res:
                prof = await self.resolve_profile(user_id=user_id)
                return None, prof
            prof = await self.resolve_profile(identifier=active_res.profile_id, user_id=user_id)
            return active_res, prof

        resume = ResumeRecord(
            id=row["r_id"],
            user_id=row["r_user_id"],
            filename=row["r_filename"],
            storage_key=row["r_storage_key"],
            file_size=int(row["r_file_size"]),
            mime_type=row["r_mime_type"],
            raw_hash=row["r_raw_hash"],
            status=row.get("r_status", "completed"),
            is_active=bool(row.get("r_is_active", 1)),
            version=int(row.get("r_version", 1)),
            profile_id=row.get("r_profile_id"),
            extracted_text=row.get("r_extracted_text"),
            error_message=row.get("r_error_message"),
            created_at=datetime.fromisoformat(row["r_created_at"]) if isinstance(row["r_created_at"], str) else row["r_created_at"],
            updated_at=datetime.fromisoformat(row["r_updated_at"]) if isinstance(row["r_updated_at"], str) else row["r_updated_at"],
        )

        profile = None
        if row.get("p_id"):
            raw_skills = json.loads(row["p_skills"]) if isinstance(row["p_skills"], str) else (row["p_skills"] or [])
            raw_exp = json.loads(row["p_experience"]) if isinstance(row["p_experience"], str) else (row["p_experience"] or [])
            raw_edu = json.loads(row["p_education"]) if isinstance(row["p_education"], str) else (row["p_education"] or [])
            profile = CandidateProfile(
                id=row["p_id"],
                user_id=row["p_user_id"],
                raw_hash=row["p_raw_hash"],
                full_name=row["p_full_name"],
                headline=row.get("p_phone") or row.get("headline"),
                email=row["p_email"],
                summary=row["p_summary"],
                skills=[SkillItem.model_validate(s) for s in raw_skills],
                experience=[ExperienceItem.model_validate(e) for e in raw_exp],
                education=[EducationItem.model_validate(ed) for ed in raw_edu],
                created_at=datetime.fromisoformat(row["p_created_at"]) if isinstance(row["p_created_at"], str) else row["p_created_at"],
            )

        return resume, profile

    async def set_active_resume(self, user_id: str, resume_id: str) -> bool:
        resume = await self.get_resume(resume_id)
        if not resume or resume.user_id != user_id:
            return False

        now_iso = datetime.now(timezone.utc).isoformat()
        await self.db.execute("UPDATE resumes SET is_active = 0, updated_at = ? WHERE user_id = ?", (now_iso, user_id))
        rows = await self.db.execute(
            "UPDATE resumes SET is_active = 1, updated_at = ? WHERE id = ? AND user_id = ?",
            (now_iso, resume_id, user_id),
        )
        return rows > 0

    async def delete_resume(self, resume_id: str) -> bool:
        target = await self.get_resume(resume_id)
        rows = await self.db.execute("DELETE FROM resumes WHERE id = ?", (resume_id,))
        if rows > 0 and target and target.is_active:
            remaining = await self.get_user_resumes(target.user_id)
            if remaining:
                await self.set_active_resume(target.user_id, remaining[0].id)
        return rows > 0

    async def get_resume_by_hash(self, user_id: str, raw_hash: str) -> Optional[ResumeRecord]:
        row = await self.db.fetchone(
            "SELECT * FROM resumes WHERE user_id = ? AND raw_hash = ? ORDER BY created_at DESC LIMIT 1",
            (user_id, raw_hash),
        )
        return self._to_resume_record(row) if row else None

    def _to_resume_record(self, row: Dict[str, Any]) -> ResumeRecord:
        created = datetime.fromisoformat(row["created_at"]) if isinstance(row["created_at"], str) else row["created_at"]
        updated = datetime.fromisoformat(row["updated_at"]) if isinstance(row["updated_at"], str) else row["updated_at"]
        return ResumeRecord(
            id=row["id"],
            user_id=row["user_id"],
            filename=row["filename"],
            storage_key=row["storage_key"],
            file_size=int(row["file_size"]),
            mime_type=row["mime_type"],
            raw_hash=row["raw_hash"],
            status=row.get("status", "completed"),
            is_active=bool(row.get("is_active", 1)),
            version=int(row.get("version", 1)),
            profile_id=row.get("profile_id"),
            extracted_text=row.get("extracted_text"),
            error_message=row.get("error_message"),
            created_at=created,
            updated_at=updated,
        )


class SQLMatchRepository(IMatchRepository):
    """Persistent SQL-backed repository for match records, score breakdowns, and audit trails."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or get_db_manager()

    async def save_match(self, match: MatchResult) -> MatchResult:
        now_iso = datetime.now(timezone.utc).isoformat()
        existing = await self.get_match(match.candidate_id, match.job_id)
        if existing:
            query = """
            UPDATE match_records
            SET overall_score = ?, score_breakdown = ?, matched_skills = ?,
                missing_skills = ?, caveat = ?, created_at = ?
            WHERE candidate_id = ? AND job_id = ?
            """
            params = (
                match.overall_score,
                json.dumps(match.breakdown.model_dump(mode="json")),
                json.dumps(match.matched_skills),
                json.dumps(match.missing_skills),
                match.caveat,
                now_iso,
                match.candidate_id,
                match.job_id,
            )
            await self.db.execute(query, params)
            return match

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

    async def get_ranked_matches_with_jobs(self, candidate_id: str, limit: int = 50) -> List[Any]:
        from app.schemas.matching import JobMatchItem, MatchResult, ScoreBreakdown
        from app.schemas.job import Job

        query = """
        SELECT 
            m.id AS match_id, m.candidate_id, m.job_id, m.overall_score,
            m.score_breakdown, m.matched_skills, m.missing_skills, m.caveat, m.created_at AS match_created_at,
            j.id AS j_id, j.title, j.company, j.location, j.description,
            j.required_skills, j.preferred_skills, j.experience_level, j.salary,
            j.experience_years_required, j.education_required, j.responsibilities,
            j.domain, j.source, j.source_platform, j.external_job_id, j.department,
            j.employment_type, j.workplace_type, j.source_url, j.content_hash,
            j.collected_at, j.last_seen_at, j.posted_at, j.updated_at, j.is_active
        FROM match_records m
        INNER JOIN jobs j ON m.job_id = j.id
        WHERE m.candidate_id = ? AND j.is_active = 1
        ORDER BY m.overall_score DESC, m.created_at DESC
        LIMIT ?
        """
        rows = await self.db.fetchall(query, (candidate_id, limit))
        seen_jobs = set()
        results: List[JobMatchItem] = []
        for r in rows:
            job_id = r["job_id"]
            if job_id in seen_jobs:
                continue
            seen_jobs.add(job_id)

            raw_bd = json.loads(r["score_breakdown"]) if isinstance(r["score_breakdown"], str) else (r["score_breakdown"] or {})
            raw_matched = json.loads(r["matched_skills"]) if isinstance(r["matched_skills"], str) else (r["matched_skills"] or [])
            raw_missing = json.loads(r["missing_skills"]) if isinstance(r["missing_skills"], str) else (r["missing_skills"] or [])

            match_res = MatchResult(
                candidate_id=r["candidate_id"],
                job_id=job_id,
                overall_score=float(r["overall_score"]),
                breakdown=ScoreBreakdown.model_validate(raw_bd),
                matched_skills=raw_matched,
                missing_skills=raw_missing,
                explanation=r.get("caveat") or "Deterministic match based on extracted skills and experience.",
                caveat=r.get("caveat") or "Deterministic match based on extracted skills and experience.",
            )

            job_dict = {
                "id": r["j_id"],
                "title": r["title"],
                "company": r["company"],
                "location": r["location"],
                "description": r["description"],
                "required_skills": json.loads(r["required_skills"]) if isinstance(r["required_skills"], str) else (r["required_skills"] or []),
                "preferred_skills": json.loads(r["preferred_skills"]) if isinstance(r["preferred_skills"], str) else (r["preferred_skills"] or []),
                "experience_level": r.get("experience_level"),
                "salary": r.get("salary"),
                "experience_years_required": r.get("experience_years_required"),
                "education_required": r.get("education_required"),
                "responsibilities": json.loads(r["responsibilities"]) if r.get("responsibilities") and isinstance(r["responsibilities"], str) else (r.get("responsibilities") or []),
                "domain": r.get("domain"),
                "source": r.get("source", "firecrawl"),
                "source_platform": r.get("source_platform"),
                "external_job_id": r.get("external_job_id"),
                "department": r.get("department"),
                "employment_type": r.get("employment_type"),
                "workplace_type": r.get("workplace_type"),
                "source_url": r.get("source_url"),
                "content_hash": r.get("content_hash"),
                "is_active": bool(r.get("is_active", 1)),
            }
            if r.get("collected_at"):
                job_dict["collected_at"] = datetime.fromisoformat(r["collected_at"]) if isinstance(r["collected_at"], str) else r["collected_at"]
            if r.get("posted_at"):
                job_dict["posted_at"] = datetime.fromisoformat(r["posted_at"]) if isinstance(r["posted_at"], str) else r["posted_at"]

            job_obj = Job.model_validate(job_dict)
            results.append(JobMatchItem(match=match_res, job=job_obj))
            if len(results) >= limit:
                break

        return results

    async def get_match(self, candidate_id: str, job_id: str) -> Optional[Dict[str, Any]]:
        row = await self.db.fetchone(
            "SELECT * FROM match_records WHERE candidate_id = ? AND job_id = ? ORDER BY created_at DESC LIMIT 1",
            (candidate_id, job_id),
        )
        return row

    async def clear(self) -> None:
        await self.db.execute("DELETE FROM match_records")
