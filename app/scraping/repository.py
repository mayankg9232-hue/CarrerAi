"""Database persistence repository for scraping runs, source registry, and idempotent job upserts."""

from datetime import datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from app.db.connection import DatabaseManager, get_db_manager
from app.schemas.job import Job
from app.scraping.models import JobPlatform, ScrapingRun, ScrapingSource, ScrapingStatus

logger = logging.getLogger("ai_job_matcher.scraping")


class ScrapingRepository:
    """Persistent storage adapter for scraping operations in PostgreSQL / Neon and SQLite."""

    def __init__(self, db_manager: Optional[DatabaseManager] = None) -> None:
        self.db = db_manager or get_db_manager()

    async def save_source(self, source: ScrapingSource) -> ScrapingSource:
        """Persist or update a company job board source idempotently."""
        query = """
        INSERT INTO scraping_sources (
            id, platform, company_name, board_url, board_token, is_active, created_at, last_scraped_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(platform, board_token) DO UPDATE SET
            company_name = excluded.company_name,
            board_url = excluded.board_url,
            is_active = excluded.is_active,
            last_scraped_at = COALESCE(excluded.last_scraped_at, scraping_sources.last_scraped_at)
        """
        params = (
            source.id,
            source.platform.value,
            source.company_name,
            source.board_url,
            source.board_token,
            1 if source.is_active else 0,
            source.created_at.isoformat(),
            source.last_scraped_at.isoformat() if source.last_scraped_at else None,
        )
        await self.db.execute(query, params)
        return source

    async def get_sources(
        self,
        platform: Optional[str] = None,
        active_only: bool = True,
    ) -> List[ScrapingSource]:
        """Fetch scraping sources with optional platform and active filters."""
        where_clauses = []
        params = []
        if active_only:
            where_clauses.append("is_active = 1")
        if platform:
            where_clauses.append("platform = ?")
            params.append(platform)

        where_sql = f"WHERE {' AND '.join(where_clauses)}" if where_clauses else ""
        query = f"SELECT * FROM scraping_sources {where_sql} ORDER BY created_at ASC"
        rows = await self.db.fetchall(query, tuple(params))
        sources: List[ScrapingSource] = []
        for r in rows:
            created = datetime.fromisoformat(r["created_at"]) if isinstance(r["created_at"], str) else r["created_at"]
            last_scraped = datetime.fromisoformat(r["last_scraped_at"]) if r.get("last_scraped_at") and isinstance(r["last_scraped_at"], str) else None
            sources.append(
                ScrapingSource(
                    id=r["id"],
                    platform=JobPlatform(r["platform"]),
                    company_name=r["company_name"],
                    board_url=r["board_url"],
                    board_token=r["board_token"],
                    is_active=bool(r["is_active"]),
                    created_at=created,
                    last_scraped_at=last_scraped,
                )
            )
        return sources

    async def update_source_last_scraped(self, source_id: str, last_scraped_at: datetime) -> None:
        """Update last_scraped_at timestamp for a source."""
        query = "UPDATE scraping_sources SET last_scraped_at = ? WHERE id = ?"
        await self.db.execute(query, (last_scraped_at.isoformat(), source_id))

    async def create_run(self, run: ScrapingRun) -> ScrapingRun:
        """Create a new scraping run record."""
        query = """
        INSERT INTO scraping_runs (
            id, platform, status, started_at, completed_at, jobs_discovered,
            jobs_inserted, jobs_updated, jobs_skipped, jobs_marked_inactive,
            retry_count, error_category, error_details
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        params = (
            run.id,
            run.platform,
            run.status.value,
            run.started_at.isoformat(),
            run.completed_at.isoformat() if run.completed_at else None,
            run.jobs_discovered,
            run.jobs_inserted,
            run.jobs_updated,
            run.jobs_skipped,
            run.jobs_marked_inactive,
            run.retry_count,
            run.error_category,
            run.error_details,
        )
        await self.db.execute(query, params)
        return run

    async def update_run(self, run: ScrapingRun) -> ScrapingRun:
        """Update status and metrics of an existing scraping run."""
        query = """
        UPDATE scraping_runs SET
            status = ?,
            completed_at = ?,
            jobs_discovered = ?,
            jobs_inserted = ?,
            jobs_updated = ?,
            jobs_skipped = ?,
            jobs_marked_inactive = ?,
            retry_count = ?,
            error_category = ?,
            error_details = ?
        WHERE id = ?
        """
        params = (
            run.status.value,
            run.completed_at.isoformat() if run.completed_at else None,
            run.jobs_discovered,
            run.jobs_inserted,
            run.jobs_updated,
            run.jobs_skipped,
            run.jobs_marked_inactive,
            run.retry_count,
            run.error_category,
            run.error_details,
            run.id,
        )
        await self.db.execute(query, params)
        return run

    async def get_run(self, run_id: str) -> Optional[ScrapingRun]:
        """Fetch a specific run record by ID."""
        row = await self.db.fetchone("SELECT * FROM scraping_runs WHERE id = ?", (run_id,))
        if not row:
            return None
        started = datetime.fromisoformat(row["started_at"]) if isinstance(row["started_at"], str) else row["started_at"]
        completed = datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") and isinstance(row["completed_at"], str) else None
        return ScrapingRun(
            id=row["id"],
            platform=row["platform"],
            status=ScrapingStatus(row["status"]),
            started_at=started,
            completed_at=completed,
            jobs_discovered=row.get("jobs_discovered", 0),
            jobs_inserted=row.get("jobs_inserted", 0),
            jobs_updated=row.get("jobs_updated", 0),
            jobs_skipped=row.get("jobs_skipped", 0),
            jobs_marked_inactive=row.get("jobs_marked_inactive", 0),
            retry_count=row.get("retry_count", 0),
            error_category=row.get("error_category"),
            error_details=row.get("error_details"),
        )

    async def list_runs(self, limit: int = 50, offset: int = 0) -> List[ScrapingRun]:
        """Fetch historical scraping runs ordered by started_at descending."""
        rows = await self.db.fetchall(
            "SELECT * FROM scraping_runs ORDER BY started_at DESC LIMIT ? OFFSET ?",
            (limit, offset),
        )
        runs: List[ScrapingRun] = []
        for row in rows:
            started = datetime.fromisoformat(row["started_at"]) if isinstance(row["started_at"], str) else row["started_at"]
            completed = datetime.fromisoformat(row["completed_at"]) if row.get("completed_at") and isinstance(row["completed_at"], str) else None
            runs.append(
                ScrapingRun(
                    id=row["id"],
                    platform=row["platform"],
                    status=ScrapingStatus(row["status"]),
                    started_at=started,
                    completed_at=completed,
                    jobs_discovered=row.get("jobs_discovered", 0),
                    jobs_inserted=row.get("jobs_inserted", 0),
                    jobs_updated=row.get("jobs_updated", 0),
                    jobs_skipped=row.get("jobs_skipped", 0),
                    jobs_marked_inactive=row.get("jobs_marked_inactive", 0),
                    retry_count=row.get("retry_count", 0),
                    error_category=row.get("error_category"),
                    error_details=row.get("error_details"),
                )
            )
        return runs

    async def upsert_job(self, job: Job) -> Tuple[Job, str]:
        """Idempotently insert or update a job record in the database.

        Returns:
            Tuple of (Job, action) where action is 'inserted' or 'updated'.
        """
        # 1. Primary deduplication: (source_platform, external_job_id)
        existing: Optional[Dict[str, Any]] = None
        if job.source_platform and job.external_job_id:
            existing = await self.db.fetchone(
                "SELECT * FROM jobs WHERE source_platform = ? AND external_job_id = ?",
                (job.source_platform, job.external_job_id),
            )

        # 2. Secondary fallback deduplication: content_hash
        if not existing and job.content_hash:
            existing = await self.db.fetchone(
                "SELECT * FROM jobs WHERE content_hash = ?",
                (job.content_hash,),
            )

        # 3. Tertiary fallback deduplication: source_url
        if not existing and job.source_url:
            existing = await self.db.fetchone(
                "SELECT * FROM jobs WHERE source_url = ?",
                (job.source_url,),
            )

        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        if existing:
            # Job already exists: preserve original collected_at (first-seen)
            original_collected = existing["collected_at"]
            job.collected_at = datetime.fromisoformat(original_collected) if isinstance(original_collected, str) else original_collected
            job.last_seen_at = now
            job.is_active = True

            # Update mutable fields
            update_query = """
            UPDATE jobs SET
                title = ?,
                company = ?,
                location = ?,
                description = ?,
                required_skills = ?,
                preferred_skills = ?,
                experience_level = ?,
                source = ?,
                source_platform = ?,
                external_job_id = ?,
                department = ?,
                employment_type = ?,
                workplace_type = ?,
                source_url = ?,
                content_hash = ?,
                posted_at = ?,
                updated_at = ?,
                salary = ?,
                experience_years_required = ?,
                education_required = ?,
                responsibilities = ?,
                domain = ?,
                last_seen_at = ?,
                is_active = 1
            WHERE id = ?
            """
            params = (
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
                job.content_hash,
                job.posted_at.isoformat() if job.posted_at else None,
                job.updated_at.isoformat() if job.updated_at else None,
                job.salary,
                job.experience_years_required,
                job.education_required,
                json.dumps(job.responsibilities),
                job.domain,
                now_iso,
                existing["id"],
            )
            await self.db.execute(update_query, params)
            job.id = existing["id"]
            return job, "updated"

        # 4. Job is new: insert with collected_at = now, last_seen_at = now
        job.collected_at = now
        job.last_seen_at = now
        job.is_active = True

        insert_query = """
        INSERT INTO jobs (
            id, title, company, location, description, required_skills, preferred_skills,
            experience_level, source, source_platform, external_job_id, department,
            employment_type, workplace_type, source_url, content_hash, collected_at,
            posted_at, updated_at, salary, experience_years_required, education_required,
            responsibilities, domain, last_seen_at, is_active
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            job.content_hash,
            now_iso,
            job.posted_at.isoformat() if job.posted_at else None,
            job.updated_at.isoformat() if job.updated_at else None,
            job.salary,
            job.experience_years_required,
            job.education_required,
            json.dumps(job.responsibilities),
            job.domain,
            now_iso,
            1,
        )
        await self.db.execute(insert_query, params)
        return job, "inserted"

    async def mark_source_jobs_inactive_if_not_seen(
        self,
        source_platform: str,
        company_name: str,
        seen_external_ids: List[str],
        run_started_at: datetime,
    ) -> int:
        """Mark jobs belonging to this verified company board inactive if they were not seen in this run.

        Only executed when the board scrape succeeded with 100% confidence.
        """
        if not seen_external_ids:
            return 0  # Safety: do not deactivate everything if 0 jobs returned

        started_iso = run_started_at.isoformat()
        # Find active jobs for this company/platform that were NOT verified in this run
        placeholders = ", ".join("?" for _ in seen_external_ids)
        query = f"""
        UPDATE jobs
        SET is_active = 0
        WHERE source_platform = ?
          AND LOWER(company) = LOWER(?)
          AND is_active = 1
          AND last_seen_at < ?
          AND external_job_id NOT IN ({placeholders})
        """
        params = [source_platform, company_name, started_iso] + seen_external_ids
        return await self.db.execute(query, tuple(params))
