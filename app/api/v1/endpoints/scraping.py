"""API v1 endpoints for dynamic job scraping, monitoring, and source registry."""

import asyncio
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.core.config import get_settings
from app.db.connection import get_db_manager
from app.integrations.firecrawl.client import FirecrawlClient
from app.scraping.discovery import SourceDiscoveryService
from app.scraping.exceptions import (
    ScraperLockContentionError,
    UnsupportedPlatformError,
)
from app.scraping.models import (
    JobPlatform,
    RegisterSourceRequest,
    ScrapingRun,
    ScrapingSource,
    TriggerScrapeRequest,
    TriggerScrapeResponse,
)
from app.scraping.orchestrator import ScrapingOrchestrator
from app.scraping.repository import ScrapingRepository
from app.scraping.scheduler import get_scraping_scheduler

router = APIRouter(prefix="/scraping", tags=["Job Scraping"])


@router.post(
    "/trigger",
    response_model=TriggerScrapeResponse,
    summary="Trigger on-demand scraping execution",
)
async def trigger_scraping(
    request: Optional[TriggerScrapeRequest] = None,
) -> TriggerScrapeResponse:
    """Trigger a manual or on-demand scraping execution across permitted platforms."""
    orchestrator = ScrapingOrchestrator()
    platform = request.platform if request else None
    source_ids = request.source_ids if request else None

    try:
        # Run pipeline in background or inline
        run_record = await orchestrator.run_pipeline(
            target_platform=platform,
            source_ids=source_ids,
        )
        return TriggerScrapeResponse(
            run_id=run_record.id,
            status=run_record.status.value,
            message=f"Scraping run completed with status '{run_record.status.value}'. Discovered {run_record.jobs_discovered} jobs, inserted {run_record.jobs_inserted}, updated {run_record.jobs_updated}.",
            started_at=run_record.started_at,
        )
    except ScraperLockContentionError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Scraping execution failed: {str(exc)}",
        )


@router.get(
    "/status",
    summary="Get scheduler and scraping pipeline status",
)
async def get_scraping_status() -> Dict[str, Any]:
    """Retrieve status of the weekly scheduler, database connectivity, and recent runs."""
    scheduler = get_scraping_scheduler()
    db = get_db_manager()
    db_status = await db.check_connectivity()

    return {
        "status": "operational",
        "database": db_status,
        "scheduler": scheduler.get_status(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }


@router.get(
    "/history",
    response_model=List[ScrapingRun],
    summary="Get scraping execution history",
)
async def get_scraping_history(
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
) -> List[ScrapingRun]:
    """Retrieve historical scraping runs and audit metrics."""
    repo = ScrapingRepository()
    return await repo.list_runs(limit=limit, offset=offset)


@router.get(
    "/sources",
    response_model=List[ScrapingSource],
    summary="List registered company job board sources",
)
async def list_sources(
    platform: Optional[str] = Query(default=None),
    active_only: bool = Query(default=True),
) -> List[ScrapingSource]:
    """List all registered company job boards for Greenhouse, Lever, and Ashby."""
    repo = ScrapingRepository()
    discovery = SourceDiscoveryService(repository=repo)
    # Ensure seed sources exist if table is empty
    return await discovery.get_or_seed_sources()


@router.post(
    "/sources",
    response_model=ScrapingSource,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new company job board source",
)
async def register_source(
    request: RegisterSourceRequest,
) -> ScrapingSource:
    """Register a new job board URL strictly restricted to Greenhouse, Lever, or Ashby."""
    repo = ScrapingRepository()
    discovery = SourceDiscoveryService(repository=repo)
    try:
        return await discovery.register_source_url(
            url=request.url,
            company_name=request.company_name,
        )
    except UnsupportedPlatformError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid job source URL: {str(exc)}",
        )


@router.get(
    "/verify-connectivity",
    summary="Verify Firecrawl API and Neon database connectivity",
)
async def verify_connectivity() -> Dict[str, Any]:
    """Check connectivity to both Firecrawl API and Neon PostgreSQL safely."""
    settings = get_settings()
    db = get_db_manager()

    # 1. Database check
    db_result = await db.check_connectivity()

    # 2. Firecrawl check
    firecrawl_result = {"status": "unconfigured", "has_key": bool(settings.FIRECRAWL_API_KEY)}
    if settings.FIRECRAWL_API_KEY:
        try:
            fc = FirecrawlClient(
                api_key=settings.FIRECRAWL_API_KEY,
                base_url=settings.FIRECRAWL_BASE_URL,
                timeout=8.0,
            )
            # Safe probe request
            await fc.scrape_url("https://boards.greenhouse.io/gitlab")
            firecrawl_result["status"] = "connected"
        except Exception as exc:
            firecrawl_result["status"] = "error"
            firecrawl_result["error"] = str(exc)

    return {
        "database": db_result,
        "firecrawl": firecrawl_result,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
