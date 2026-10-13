"""Firecrawl adapter interface and implementations for approved job source ingestion."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import ipaddress
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import httpx

from app.core.config import Settings, get_settings


from app.integrations.firecrawl.source_adapter import (
    DomainNotApprovedError,
    FirecrawlError,
    FirecrawlSourceAdapter,
    IFirecrawlAdapter,
    SSRFBlockedError,
    SourceMalformedError,
    SourceRateLimitError,
    SourceTimeoutError,
    validate_source_url,
)


class FirecrawlAdapter(FirecrawlSourceAdapter):
    """HTTP client adapter communicating with Firecrawl API or target job source with timeouts and rate limits."""
    pass


class MockFirecrawlAdapter(IFirecrawlAdapter):
    """Configurable mock adapter for deterministic integration and unit testing."""

    def __init__(
        self,
        canned_jobs: Optional[Dict[str, Dict[str, Any]]] = None,
        simulate_timeout: bool = False,
        simulate_rate_limit: bool = False,
        simulate_malformed: bool = False,
        approved_domains: Optional[List[str]] = None,
    ) -> None:
        self.canned_jobs = canned_jobs or {}
        self.simulate_timeout = simulate_timeout
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_malformed = simulate_malformed
        self.approved_domains = approved_domains or [
            "approved-board.example.com",
            "careers.techcompany.com",
        ]

    async def scrape_job(self, url: str) -> Dict[str, Any]:
        validate_source_url(url, self.approved_domains)

        if self.simulate_timeout:
            raise SourceTimeoutError(f"Mock timeout triggered for {url}")

        if self.simulate_rate_limit:
            raise SourceRateLimitError(f"Mock 429 rate limit triggered for {url}")

        if self.simulate_malformed:
            raise SourceMalformedError(f"Mock malformed data returned for {url}")

        if url in self.canned_jobs:
            job_data = self.canned_jobs[url]
            if not job_data.get("title") or len(job_data.get("description", "")) < 10:
                raise SourceMalformedError("Listing missing title or description too short.")
            return job_data

        # Default fallback mock listing
        return {
            "title": "Software Engineer",
            "company": "Tech Company",
            "location": "Remote",
            "description": "We are seeking a Software Engineer with Python and FastAPI experience.",
            "required_skills": ["Python", "FastAPI"],
            "preferred_skills": ["Docker", "PostgreSQL"],
            "experience_level": "mid",
            "source_url": url,
            "source": "firecrawl",
        }
