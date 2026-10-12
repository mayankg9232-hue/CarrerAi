"""Firecrawl job source adapter with SSRF guard, domain allowlisting, NLP skill extraction, and provenance metadata."""

from abc import ABC, abstractmethod
from datetime import datetime, timezone
import hashlib
import ipaddress
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.core.config import Settings, get_settings
from app.integrations.firecrawl.client import (
    FirecrawlAuthenticationError,
    FirecrawlClient,
    FirecrawlClientError,
    FirecrawlMalformedResponseError,
    FirecrawlRateLimitError,
    FirecrawlTimeoutError,
    MockFirecrawlClient,
)
from app.nlp.normalizer import sanitize_text
from app.nlp.skill_extractor import SkillExtractor


class FirecrawlError(Exception):
    """Base exception for Firecrawl source adapter errors."""
    pass


class SourceTimeoutError(FirecrawlError):
    """Raised when upstream job source request times out."""
    pass


class SourceRateLimitError(FirecrawlError):
    """Raised when upstream job source rate limit is exceeded (HTTP 429)."""
    pass


class SourceMalformedError(FirecrawlError):
    """Raised when upstream source returns malformed or unparseable job content."""
    pass


class SSRFBlockedError(FirecrawlError):
    """Raised when a requested URL resolves to an internal or private network IP."""
    pass


class DomainNotApprovedError(FirecrawlError):
    """Raised when target domain is not in the approved job domains allowlist."""
    pass


class IFirecrawlAdapter(ABC):
    """Abstract interface for external job scraping and ingestion."""

    @abstractmethod
    async def scrape_job(self, url: str) -> Dict[str, Any]:
        """Scrape structured or semi-structured job listing from an approved URL."""
        pass


def validate_source_url(url: str, approved_domains: List[str]) -> str:
    """Validate URL syntax, ensure domain is allowlisted, and reject private/SSRF targets."""
    if not url or not isinstance(url, str):
        raise DomainNotApprovedError("Target URL must be a non-empty string.")

    parsed = urlparse(url.strip())
    if parsed.scheme not in ("http", "https"):
        raise DomainNotApprovedError(f"Unsupported URL scheme '{parsed.scheme}'. Only HTTP and HTTPS are permitted.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise DomainNotApprovedError("Invalid URL: missing hostname.")

    # Prevent SSRF: loopback, private ranges, link-local, AWS metadata
    if hostname in ("localhost", "127.0.0.1", "::1", "169.254.169.254", "0.0.0.0"):
        raise SSRFBlockedError(f"Access to private/loopback host '{hostname}' is blocked.")

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise SSRFBlockedError(f"Access to private IP range '{hostname}' is blocked.")
    except ValueError:
        # Not a raw IP address; hostname is a standard domain name
        pass

    # Check allowlist
    approved_lower = [d.lower() for d in approved_domains]
    is_approved = any(hostname == approved or hostname.endswith(f".{approved}") for approved in approved_lower)
    if not is_approved:
        raise DomainNotApprovedError(
            f"Domain '{hostname}' is not in the approved job source allowlist: {approved_domains}"
        )

    return url.strip()


class FirecrawlSourceAdapter(IFirecrawlAdapter):
    """Production Firecrawl adapter integrating API client, security filters, and NLP skill extraction."""

    def __init__(
        self,
        client: Optional[Any] = None,
        skill_extractor: Optional[SkillExtractor] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._client = client or FirecrawlClient(
            api_key=self._settings.FIRECRAWL_API_KEY,
            base_url=self._settings.FIRECRAWL_BASE_URL,
            timeout=self._settings.FIRECRAWL_TIMEOUT_SECONDS,
        )
        self._skill_extractor = skill_extractor or SkillExtractor()

    async def scrape_job(self, url: str) -> Dict[str, Any]:
        """Validate URL and scrape normalized job details with provenance and skill extraction."""
        validated_url = validate_source_url(url, self._settings.APPROVED_JOB_DOMAINS)

        try:
            result_data = await self._client.scrape_url(validated_url)
        except FirecrawlTimeoutError as exc:
            raise SourceTimeoutError(str(exc))
        except FirecrawlRateLimitError as exc:
            raise SourceRateLimitError(str(exc))
        except FirecrawlAuthenticationError as exc:
            raise SourceMalformedError(f"Authentication failure: {str(exc)}")
        except FirecrawlMalformedResponseError as exc:
            raise SourceMalformedError(str(exc))
        except Exception as exc:
            raise SourceMalformedError(f"Scraper client error: {str(exc)}")

        metadata = result_data.get("metadata", {})
        markdown = result_data.get("markdown", "")
        title = metadata.get("title") or result_data.get("title")
        company = metadata.get("og:site_name") or metadata.get("company") or "Unknown Organization"
        description = sanitize_text(markdown or result_data.get("content", ""))

        if not title or len(description) < 10:
            raise SourceMalformedError("Scraped page does not contain valid job listing content.")

        # Extract skills if not explicitly provided by upstream metadata
        required_skills = metadata.get("required_skills", [])
        preferred_skills = metadata.get("preferred_skills", [])

        if not required_skills:
            inferred_skills = self._skill_extractor.extract_skills(description)
            required_skills = [s["name"] for s in inferred_skills[:8]]
            if len(inferred_skills) > 8:
                preferred_skills = [s["name"] for s in inferred_skills[8:14]]

        return {
            "title": title.strip(),
            "company": company.strip(),
            "location": metadata.get("location", "Remote"),
            "description": description,
            "required_skills": required_skills,
            "preferred_skills": preferred_skills,
            "experience_level": metadata.get("experience_level"),
            "source_url": validated_url,
            "source": "firecrawl",
            "collected_at": datetime.now(timezone.utc),
        }


class MockFirecrawlAdapter(IFirecrawlAdapter):
    """Configurable mock adapter for deterministic unit and integration tests."""

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
            "collected_at": datetime.now(timezone.utc),
        }
