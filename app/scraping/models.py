"""Data models and schemas for the dynamic job scraping pipeline."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class JobPlatform(str, Enum):
    """Permitted job platforms allowlist."""
    GREENHOUSE = "greenhouse"
    LEVER = "lever"
    ASHBY = "ashby"


class ScrapingStatus(str, Enum):
    """Execution status of a scraping run."""
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    PARTIAL_SUCCESS = "partial_success"
    FAILED = "failed"


class ScrapingSource(BaseModel):
    """Configured or discovered company job board source."""
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(description="Unique source identifier")
    platform: JobPlatform = Field(description="Permitted ATS platform: greenhouse, lever, ashby")
    company_name: str = Field(description="Display name of the company or organization")
    board_url: str = Field(description="Canonical public board or careers URL")
    board_token: str = Field(description="Organization or board token for public API access")
    is_active: bool = Field(default=True, description="Whether this source is active for weekly scraping")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    last_scraped_at: Optional[datetime] = Field(default=None, description="Timestamp of most recent scraping run")


class ScrapingRun(BaseModel):
    """Execution audit and metrics record for a scraping run."""
    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(description="Unique run execution identifier")
    platform: str = Field(description="Platform name or 'all' for multi-platform scheduled run")
    status: ScrapingStatus = Field(default=ScrapingStatus.PENDING, description="Current run lifecycle status")
    started_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    completed_at: Optional[datetime] = Field(default=None)
    jobs_discovered: int = Field(default=0, ge=0)
    jobs_inserted: int = Field(default=0, ge=0)
    jobs_updated: int = Field(default=0, ge=0)
    jobs_skipped: int = Field(default=0, ge=0)
    jobs_marked_inactive: int = Field(default=0, ge=0)
    retry_count: int = Field(default=0, ge=0)
    error_category: Optional[str] = Field(default=None, description="Classified error category if failed")
    error_details: Optional[str] = Field(default=None, description="Safe redacted error message without credentials")


class RegisterSourceRequest(BaseModel):
    """Payload to add a new dynamic job source to the scraping registry."""
    url: str = Field(min_length=5, description="Job board URL from Greenhouse, Lever, or Ashby")
    company_name: Optional[str] = Field(default=None, description="Optional override for company name")


class TriggerScrapeRequest(BaseModel):
    """Payload to trigger an on-demand scraping execution."""
    platform: Optional[JobPlatform] = Field(default=None, description="Optionally restrict run to one platform")
    source_ids: Optional[List[str]] = Field(default=None, description="Optionally restrict run to specific source IDs")


class TriggerScrapeResponse(BaseModel):
    """Response returned upon triggering a scraping run."""
    run_id: str
    status: str
    message: str
    started_at: datetime
