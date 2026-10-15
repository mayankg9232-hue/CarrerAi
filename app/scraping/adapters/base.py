"""Abstract base class and shared networking/retry logic for platform adapters."""

from abc import ABC, abstractmethod
import asyncio
import logging
from typing import Any, Dict, List, Optional, Set
import httpx

from app.core.config import Settings, get_settings
from app.integrations.firecrawl.client import FirecrawlClient
from app.schemas.job import Job
from app.scraping.discovery import PERMITTED_PLATFORM_DOMAINS
from app.scraping.exceptions import (
    PlatformAuthError,
    PlatformMalformedResponseError,
    PlatformRateLimitError,
    PlatformTimeoutError,
    ScrapingError,
    UnsupportedPlatformError,
)
from app.scraping.models import JobPlatform, ScrapingSource
from app.scraping.normalizer import JobNormalizer

logger = logging.getLogger("ai_job_matcher.scraping")


class BasePlatformAdapter(ABC):
    """Abstract adapter providing resilient HTTP execution and normalized job extraction."""

    platform: JobPlatform

    def __init__(
        self,
        firecrawl_client: Optional[FirecrawlClient] = None,
        normalizer: Optional[JobNormalizer] = None,
        settings: Optional[Settings] = None,
        http_client: Optional[httpx.AsyncClient] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.allowed_domains = PERMITTED_PLATFORM_DOMAINS[self.platform]
        self.normalizer = normalizer or JobNormalizer()
        self.firecrawl = firecrawl_client or (
            FirecrawlClient(
                api_key=self.settings.FIRECRAWL_API_KEY,
                base_url=self.settings.FIRECRAWL_BASE_URL,
                timeout=self.settings.FIRECRAWL_TIMEOUT_SECONDS,
            )
            if self.settings.FIRECRAWL_API_KEY
            else None
        )
        self._custom_http_client = http_client

    def validate_domain(self, hostname: str) -> None:
        """Ensure hostname belongs strictly to this platform's allowlisted domains."""
        host_lower = hostname.lower()
        if not any(host_lower == d or host_lower.endswith(f".{d}") for d in self.allowed_domains):
            raise UnsupportedPlatformError(
                f"Domain '{hostname}' is not valid for {self.platform.value} adapter. "
                f"Allowed domains: {sorted(self.allowed_domains)}"
            )

    async def execute_request_with_retry(
        self,
        method: str,
        url: str,
        params: Optional[Dict[str, Any]] = None,
        json_body: Optional[Dict[str, Any]] = None,
        headers: Optional[Dict[str, str]] = None,
        max_retries: int = 3,
        timeout: float = 15.0,
    ) -> httpx.Response:
        """Execute HTTP request with caller timeouts, bounded exponential backoff, and error mapping."""
        req_headers = {"User-Agent": "AIJobMatcher-Bot/1.0", "Accept": "application/json"}
        if headers:
            req_headers.update(headers)

        last_exc: Optional[Exception] = None
        for attempt in range(max_retries + 1):
            try:
                if self._custom_http_client:
                    response = await self._custom_http_client.request(
                        method=method,
                        url=url,
                        params=params,
                        json=json_body,
                        headers=req_headers,
                    )
                else:
                    async with httpx.AsyncClient(timeout=timeout) as client:
                        response = await client.request(
                            method=method,
                            url=url,
                            params=params,
                            json=json_body,
                            headers=req_headers,
                        )

                if response.status_code == 429:
                    if attempt < max_retries:
                        backoff = 0.5 * (2 ** attempt)
                        await asyncio.sleep(backoff)
                        continue
                    raise PlatformRateLimitError(
                        f"Upstream {self.platform.value} rate limit (HTTP 429) exceeded for {url}"
                    )

                if response.status_code in (401, 403):
                    raise PlatformAuthError(
                        f"Access unauthorized (HTTP {response.status_code}) from {self.platform.value} for {url}"
                    )

                if response.status_code >= 500:
                    if attempt < max_retries:
                        backoff = 0.5 * (2 ** attempt)
                        await asyncio.sleep(backoff)
                        continue
                    raise PlatformMalformedResponseError(
                        f"{self.platform.value} upstream server error HTTP {response.status_code}."
                    )

                response.raise_for_status()
                return response

            except httpx.TimeoutException as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise PlatformTimeoutError(f"Timeout ({timeout}s) connecting to {self.platform.value}: {url}")
            except (PlatformRateLimitError, PlatformAuthError, PlatformMalformedResponseError):
                raise
            except httpx.RequestError as exc:
                last_exc = exc
                if attempt < max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise PlatformMalformedResponseError(f"Network error communicating with {self.platform.value}: {exc}")

        raise PlatformMalformedResponseError(f"Failed to fetch from {self.platform.value} after {max_retries} retries: {last_exc}")

    @abstractmethod
    async def fetch_jobs_for_source(self, source: ScrapingSource, max_jobs: int = 50) -> List[Job]:
        """Fetch and normalize job postings for a company board."""
        pass

    @abstractmethod
    async def fetch_single_job(self, url: str) -> Optional[Job]:
        """Fetch and normalize a single job posting by URL."""
        pass
