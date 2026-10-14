"""Job listing and ingestion schemas conforming to docs/api-contracts.md."""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field, HttpUrl


class JobSourceProvenance(BaseModel):
    """Provenance and audit metadata tracking origin of scraped job listing."""

    source_name: str = Field(description="Name of source provider (e.g. firecrawl, direct)")
    source_url: str = Field(description="Canonical source URL of original posting")
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc), description="UTC timestamp of ingestion")
    content_hash: str = Field(description="SHA-256 hash of title + company + description for deduplication")


class Job(BaseModel):
    """Normalized job listing entity ingested from approved sources."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(description="Unique job listing identifier")
    title: str = Field(min_length=1, max_length=255, description="Position title")
    company: str = Field(min_length=1, max_length=255, description="Employer / organization name")
    location: str = Field(default="Remote", description="Job location or Remote status")
    description: str = Field(min_length=10, description="Full job description text")
    required_skills: List[str] = Field(default_factory=list, description="Mandatory technical and professional skills")
    preferred_skills: List[str] = Field(default_factory=list, description="Optional or advantageous skills")
    experience_level: Optional[str] = Field(default=None, description="Experience tier (junior, mid, senior, lead)")
    salary: Optional[str] = Field(default=None, description="Disclosed compensation range if available")
    experience_years_required: Optional[float] = Field(default=None, description="Minimum required years of experience")
    education_required: Optional[str] = Field(default=None, description="Minimum education qualification (e.g. Bachelor's, Master's)")
    responsibilities: List[str] = Field(default_factory=list, description="Key job responsibilities and duties")
    domain: Optional[str] = Field(default=None, description="Industry or engineering domain (e.g. AI/ML, Cloud, Backend)")
    source: str = Field(default="firecrawl", description="Source provider")
    source_url: Optional[str] = Field(default=None, description="Source URL")
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    content_hash: Optional[str] = Field(default=None, description="SHA-256 hash of title+company+description for deduplication")
    last_seen_at: Optional[datetime] = Field(default=None, description="UTC timestamp when last verified in source scrape")
    is_active: bool = Field(default=True, description="Active status (stale listings marked false)")


class JobIngestRequest(BaseModel):
    """Request payload to initiate job scraping and ingestion."""

    source_urls: List[str] = Field(min_length=1, description="List of job posting URLs from approved domains")
    max_pages: int = Field(default=1, ge=1, le=10, description="Maximum pages to scrape")


class JobIngestResponse(BaseModel):
    """Response payload detailing job ingestion outcome."""

    ingested_count: int = Field(ge=0, description="Number of newly ingested and normalized listings")
    skipped_duplicates: int = Field(ge=0, description="Number of duplicate listings skipped")
    jobs: List[Job] = Field(default_factory=list, description="List of processed jobs")
