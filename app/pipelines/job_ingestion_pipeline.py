"""Job ingestion pipeline orchestrating Firecrawl scraping, deduplication, normalization, and freshness filtering."""

from datetime import datetime, timezone
import hashlib
from typing import Any, List, Optional
import uuid

from app.adapters.firecrawl_adapter import (
    DomainNotApprovedError,
    FirecrawlAdapter,
    IFirecrawlAdapter,
    SSRFBlockedError,
    SourceMalformedError,
    SourceRateLimitError,
    SourceTimeoutError,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    BadRequestError,
    ExternalProviderUnavailableError,
    ExternalTimeoutError,
    RateLimitError,
    ValidationAppError,
)
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.schemas.job import Job, JobIngestRequest, JobIngestResponse


class JobIngestionPipeline:
    """Orchestrates job scraping from approved sources, deduplication, normalization, and freshness filtering."""

    def __init__(
        self,
        adapter: Optional[IFirecrawlAdapter] = None,
        repo: Optional[IJobRepository] = None,
        settings: Optional[Settings] = None,
        cache: Optional[Any] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.adapter = adapter or FirecrawlAdapter(settings=self.settings)
        self.repo = repo or get_job_repository()
        self.cache = cache

    @staticmethod
    def compute_content_hash(title: str, company: str, description: str) -> str:
        """Compute deterministic SHA-256 hash of normalized core job fields for deduplication."""
        raw_key = f"{title.strip().lower()}|{company.strip().lower()}|{description.strip().lower()}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    async def ingest_jobs(self, request: JobIngestRequest) -> JobIngestResponse:
        """Process list of job posting URLs through scraping, normalization, deduplication, and persistence."""
        if not request.source_urls:
            raise BadRequestError("Job ingestion requires at least one source URL.", field="source_urls")

        ingested_jobs: List[Job] = []
        skipped_duplicates = 0
        now = datetime.now(timezone.utc)

        # Process each URL up to max_pages
        urls_to_process = request.source_urls[: request.max_pages]

        for url in urls_to_process:
            try:
                raw_data = await self.adapter.scrape_job(url)
            except DomainNotApprovedError as exc:
                raise BadRequestError(f"Rejected unapproved job source domain: {str(exc)}", field="source_urls")
            except SSRFBlockedError as exc:
                raise BadRequestError(f"Security boundary violation: {str(exc)}", field="source_urls")
            except SourceTimeoutError as exc:
                raise ExternalTimeoutError(f"Timeout while collecting from source: {str(exc)}")
            except SourceRateLimitError as exc:
                raise RateLimitError(f"Upstream provider rate limited request: {str(exc)}")
            except SourceMalformedError as exc:
                if len(urls_to_process) == 1:
                    raise BadRequestError(f"Malformed job listing at '{url}': {str(exc)}")
                # For multi-URL batches, skip individual malformed listing
                continue
            except Exception as exc:
                raise ExternalProviderUnavailableError(f"Scraper provider failure: {str(exc)}")

            # Normalization and Validation
            title = str(raw_data.get("title", "")).strip()
            company = str(raw_data.get("company", "")).strip()
            description = str(raw_data.get("description", "")).strip()

            if not title or not company or len(description) < 10:
                if len(urls_to_process) == 1:
                    raise BadRequestError(f"Malformed job listing: title and company required, description min 10 characters.")
                continue

            content_hash = self.compute_content_hash(title, company, description)

            # Deduplication Check (by content hash or source URL)
            existing_job = await self.repo.get_by_content_hash(content_hash)
            if not existing_job and url:
                existing_job = await self.repo.get_by_source_url(url)

            if existing_job:
                # Update existing listing timestamp and ensure active
                await self.repo.update_job(
                    existing_job.id,
                    last_seen_at=now,
                    is_active=True,
                )
                skipped_duplicates += 1
                continue

            # Construct new normalized Job entity
            job_id = f"job_{uuid.uuid4().hex[:8]}"
            job = Job(
                id=job_id,
                title=title,
                company=company,
                location=str(raw_data.get("location", "Remote")).strip() or "Remote",
                description=description,
                required_skills=[str(s).strip() for s in raw_data.get("required_skills", []) if str(s).strip()],
                preferred_skills=[str(s).strip() for s in raw_data.get("preferred_skills", []) if str(s).strip()],
                experience_level=raw_data.get("experience_level"),
                source=raw_data.get("source", "firecrawl"),
                source_url=url,
                collected_at=now,
                last_seen_at=now,
                content_hash=content_hash,
                is_active=True,
            )

            saved_job = await self.repo.create_job(job)
            ingested_jobs.append(saved_job)

        # Apply freshness filter: mark listings older than threshold days as inactive
        await self.repo.mark_stale_jobs(self.settings.STALE_JOB_DAYS_THRESHOLD)

        # Invalidate cached job queries upon write/mutation
        from app.cache.service import get_cache_service
        cache_svc = self.cache or get_cache_service()
        await cache_svc.invalidate_jobs_cache()

        return JobIngestResponse(
            ingested_count=len(ingested_jobs),
            skipped_duplicates=skipped_duplicates,
            jobs=ingested_jobs,
        )
