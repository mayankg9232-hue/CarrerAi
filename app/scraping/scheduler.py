"""Automated weekly job scraping scheduler with timezone support and duplicate job prevention."""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from app.core.config import Settings, get_settings
from app.scraping.models import ScrapingRun
from app.scraping.orchestrator import ScrapingOrchestrator

logger = logging.getLogger("ai_job_matcher.scraping")

SCHEDULER_JOB_ID = "weekly_job_scraper"


class WeeklyScrapingScheduler:
    """Manages periodic weekly scraping jobs using AsyncIOScheduler and persistent locks."""

    def __init__(
        self,
        orchestrator: Optional[ScrapingOrchestrator] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.orchestrator = orchestrator or ScrapingOrchestrator(settings=self.settings)
        self._scheduler: Optional[AsyncIOScheduler] = None
        self._last_run: Optional[ScrapingRun] = None
        self._is_running = False

    def _get_tz(self) -> ZoneInfo:
        """Resolve configured timezone with fallback to UTC."""
        try:
            return ZoneInfo(self.settings.SCRAPING_TIMEZONE)
        except Exception:
            logger.warning("Invalid timezone '%s', falling back to Asia/Kolkata", self.settings.SCRAPING_TIMEZONE)
            return ZoneInfo("Asia/Kolkata")

    async def _scheduled_task_wrapper(self) -> None:
        """Scheduled execution wrapper catching errors and recording last run."""
        logger.info("Executing scheduled weekly scraping cycle in %s timezone", self.settings.SCRAPING_TIMEZONE)
        try:
            run_result = await self.orchestrator.run_pipeline()
            self._last_run = run_result
            logger.info("Scheduled weekly scraping completed: status=%s", run_result.status.value)
        except Exception as exc:
            logger.error("Scheduled weekly scraping run failed: %s", exc)

    def start(self) -> None:
        """Start the background scheduler and register the weekly cron trigger."""
        if not self.settings.SCRAPING_ENABLED:
            logger.info("Weekly job scraping is disabled via SCRAPING_ENABLED=False.")
            return

        if self._scheduler and self._scheduler.running:
            logger.warning("WeeklyScrapingScheduler is already running.")
            return

        tz = self._get_tz()
        self._scheduler = AsyncIOScheduler(timezone=tz)

        # Parse cron expression (e.g. '0 0 * * 0' for Sunday at midnight)
        cron_expr = self.settings.SCRAPING_SCHEDULE_CRON.strip()
        cron_parts = cron_expr.split()
        if len(cron_parts) == 5:
            trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
        else:
            # Fallback to standard weekly on Sunday 00:00
            trigger = CronTrigger(day_of_week="sun", hour=0, minute=0, timezone=tz)

        self._scheduler.add_job(
            self._scheduled_task_wrapper,
            trigger=trigger,
            id=SCHEDULER_JOB_ID,
            replace_existing=True,
            coalesce=True,
            max_instances=1,
            misfire_grace_time=3600,
        )

        self._scheduler.start()
        self._is_running = True
        logger.info(
            "WeeklyScrapingScheduler started successfully [Cron: '%s', Timezone: '%s']",
            cron_expr,
            self.settings.SCRAPING_TIMEZONE,
        )

    def shutdown(self) -> None:
        """Gracefully shutdown the scheduler."""
        if self._scheduler and self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            self._is_running = False
            logger.info("WeeklyScrapingScheduler stopped.")

    async def trigger_now(self) -> ScrapingRun:
        """Trigger an immediate asynchronous scraping execution."""
        run_record = await self.orchestrator.run_pipeline()
        self._last_run = run_record
        return run_record

    def get_status(self) -> Dict[str, Any]:
        """Return diagnostic status information about the scheduler."""
        next_run_iso = None
        if self._scheduler and self._scheduler.running:
            job = self._scheduler.get_job(SCHEDULER_JOB_ID)
            if job and job.next_run_time:
                next_run_iso = job.next_run_time.isoformat()

        return {
            "enabled": self.settings.SCRAPING_ENABLED,
            "running": bool(self._scheduler and self._scheduler.running),
            "cron_expression": self.settings.SCRAPING_SCHEDULE_CRON,
            "timezone": self.settings.SCRAPING_TIMEZONE,
            "next_run_time": next_run_iso,
            "last_run": self._last_run.model_dump(mode="json") if self._last_run else None,
        }


_global_scheduler: Optional[WeeklyScrapingScheduler] = None


def get_scraping_scheduler() -> WeeklyScrapingScheduler:
    """Return the application singleton weekly scraping scheduler."""
    global _global_scheduler
    if _global_scheduler is None:
        _global_scheduler = WeeklyScrapingScheduler()
    return _global_scheduler
