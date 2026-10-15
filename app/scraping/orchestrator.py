"""Scraping pipeline orchestrator with distributed locking, concurrency, and failure isolation."""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from app.cache.service import get_cache_service
from app.core.config import Settings, get_settings
from app.db.connection import DatabaseManager, get_db_manager
from app.schemas.job import Job
from app.scraping.adapters.ashby import AshbyAdapter
from app.scraping.adapters.base import BasePlatformAdapter
from app.scraping.adapters.greenhouse import GreenhouseAdapter
from app.scraping.adapters.lever import LeverAdapter
from app.scraping.discovery import SourceDiscoveryService
from app.scraping.exceptions import ScraperLockContentionError, ScrapingError
from app.scraping.models import (
    JobPlatform,
    ScrapingRun,
    ScrapingSource,
    ScrapingStatus,
)
from app.scraping.repository import ScrapingRepository

logger = logging.getLogger("ai_job_matcher.scraping")

SCRAPING_LOCK_NAME = "weekly_job_scraping_lock"


class ScrapingOrchestrator:
    """Orchestrates multi-platform weekly job collection across Greenhouse, Lever, and Ashby."""

    def __init__(
        self,
        repository: Optional[ScrapingRepository] = None,
        discovery_service: Optional[SourceDiscoveryService] = None,
        db_manager: Optional[DatabaseManager] = None,
        settings: Optional[Settings] = None,
        adapters: Optional[Dict[JobPlatform, BasePlatformAdapter]] = None,
        cache_service: Optional[Any] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.db = db_manager or get_db_manager()
        self.repo = repository or ScrapingRepository(db_manager=self.db)
        self.discovery = discovery_service or SourceDiscoveryService(repository=self.repo, settings=self.settings)
        self.cache = cache_service or get_cache_service()

        self.adapters: Dict[JobPlatform, BasePlatformAdapter] = adapters or {
            JobPlatform.GREENHOUSE: GreenhouseAdapter(settings=self.settings),
            JobPlatform.LEVER: LeverAdapter(settings=self.settings),
            JobPlatform.ASHBY: AshbyAdapter(settings=self.settings),
        }

    async def run_pipeline(
        self,
        target_platform: Optional[JobPlatform] = None,
        source_ids: Optional[List[str]] = None,
    ) -> ScrapingRun:
        """Execute weekly scraping across all permitted platforms or a designated platform.

        Enforces:
        1. Distributed advisory locking (no concurrent overlapping runs).
        2. Platform failure isolation (a failure in Lever does not abort Greenhouse or Ashby).
        3. Idempotent upserting and duplicate prevention.
        4. Cache invalidation on completion.
        """
        # 1. Acquire distributed advisory lock
        lock_acquired = await self.db.try_acquire_advisory_lock(SCRAPING_LOCK_NAME, timeout_seconds=1800)
        if not lock_acquired:
            raise ScraperLockContentionError(
                "Another scraping execution is actively in progress. Overlapping run prevented."
            )

        run_id = f"run_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}"
        run_record = ScrapingRun(
            id=run_id,
            platform=target_platform.value if target_platform else "all",
            status=ScrapingStatus.RUNNING,
            started_at=datetime.now(timezone.utc),
        )

        try:
            await self.repo.create_run(run_record)
            logger.info("Starting scraping run %s for platform=%s", run_id, run_record.platform)

            # 2. Retrieve or seed active sources
            all_sources = await self.discovery.get_or_seed_sources()
            if target_platform:
                sources = [s for s in all_sources if s.platform == target_platform and s.is_active]
            else:
                sources = [s for s in all_sources if s.is_active]

            if source_ids:
                sources = [s for s in sources if s.id in source_ids]

            platform_errors: Dict[str, str] = {}
            total_discovered = 0
            total_inserted = 0
            total_updated = 0
            total_skipped = 0
            total_deactivated = 0

            # 3. Process each source with bounded concurrency
            semaphore = asyncio.Semaphore(self.settings.SCRAPING_CONCURRENCY_LIMIT)

            async def _process_source(source: ScrapingSource) -> None:
                nonlocal total_discovered, total_inserted, total_updated, total_skipped, total_deactivated
                adapter = self.adapters.get(source.platform)
                if not adapter:
                    logger.warning("No adapter registered for platform: %s", source.platform)
                    return

                async with semaphore:
                    try:
                        logger.info("Scraping source: %s (%s)", source.company_name, source.platform.value)
                        jobs = await adapter.fetch_jobs_for_source(
                            source=source,
                            max_jobs=self.settings.SCRAPING_MAX_JOBS_PER_SOURCE,
                        )
                        total_discovered += len(jobs)
                        seen_ids: List[str] = []

                        for job in jobs:
                            if job.external_job_id:
                                seen_ids.append(job.external_job_id)

                            _, action = await self.repo.upsert_job(job)
                            if action == "inserted":
                                total_inserted += 1
                            elif action == "updated":
                                total_updated += 1
                            else:
                                total_skipped += 1

                        # Cleanly mark missing listings inactive if verification succeeded
                        deactivated = await self.repo.mark_source_jobs_inactive_if_not_seen(
                            source_platform=source.platform.value,
                            company_name=source.company_name,
                            seen_external_ids=seen_ids,
                            run_started_at=run_record.started_at,
                        )
                        total_deactivated += deactivated
                        await self.repo.update_source_last_scraped(source.id, datetime.now(timezone.utc))

                    except Exception as exc:
                        logger.error(
                            "Platform failure scraping source %s (%s): %s",
                            source.company_name,
                            source.platform.value,
                            exc,
                        )
                        platform_errors[f"{source.platform.value}:{source.board_token}"] = str(exc)

            # Process sources grouped by platform to maintain clear metrics
            tasks = [_process_source(s) for s in sources]
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            # 4. Invalidate jobs cache upon database mutations
            if self.cache:
                try:
                    await self.cache.invalidate_jobs_cache()
                except Exception:
                    pass

            # 5. Classify final run status
            run_record.completed_at = datetime.now(timezone.utc)
            run_record.jobs_discovered = total_discovered
            run_record.jobs_inserted = total_inserted
            run_record.jobs_updated = total_updated
            run_record.jobs_skipped = total_skipped
            run_record.jobs_marked_inactive = total_deactivated

            if not platform_errors:
                run_record.status = ScrapingStatus.SUCCESS
            elif len(platform_errors) < len(sources) or total_inserted > 0 or total_updated > 0:
                run_record.status = ScrapingStatus.PARTIAL_SUCCESS
                run_record.error_category = "PartialPlatformFailure"
                run_record.error_details = f"Failures on {len(platform_errors)} sources: {list(platform_errors.keys())}"
            else:
                run_record.status = ScrapingStatus.FAILED
                run_record.error_category = "AllSourcesFailed"
                run_record.error_details = str(platform_errors)

            await self.repo.update_run(run_record)
            logger.info(
                "Completed scraping run %s: status=%s discovered=%d inserted=%d updated=%d",
                run_id,
                run_record.status.value,
                total_discovered,
                total_inserted,
                total_updated,
            )
            return run_record

        finally:
            # Always guarantee release of the advisory lock
            await self.db.release_advisory_lock(SCRAPING_LOCK_NAME)
