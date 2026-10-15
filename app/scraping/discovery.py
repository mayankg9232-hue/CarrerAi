"""Dynamic source discovery and validation service strictly restricted to Greenhouse, Lever, and Ashby."""

import ipaddress
import re
from typing import Any, Dict, List, Optional, Set, Tuple
from urllib.parse import urlparse

from app.core.config import Settings, get_settings
from app.scraping.exceptions import (
    InvalidSourceURLError,
    SSRFSecurityError,
    UnsupportedPlatformError,
)
from app.scraping.models import JobPlatform, ScrapingSource


# Strict centralized allowlist of permitted platforms and domains
PERMITTED_PLATFORM_DOMAINS: Dict[JobPlatform, Set[str]] = {
    JobPlatform.GREENHOUSE: {
        "boards.greenhouse.io",
        "greenhouse.io",
        "boards-api.greenhouse.io",
    },
    JobPlatform.LEVER: {
        "jobs.lever.co",
        "lever.co",
        "api.lever.co",
    },
    JobPlatform.ASHBY: {
        "jobs.ashbyhq.com",
        "ashbyhq.com",
        "api.ashbyhq.com",
    },
}

ALL_PERMITTED_DOMAINS: Set[str] = set.union(*PERMITTED_PLATFORM_DOMAINS.values())


def parse_and_validate_source_url(url: str) -> Tuple[JobPlatform, str, str]:
    """Parse, validate, and extract platform and board token from a job-board or posting URL.

    Strict security rules enforced:
    1. Only HTTP / HTTPS schemes allowed.
    2. Loopback, private networks, link-local, and AWS metadata addresses are rejected.
    3. Hostname MUST belong to Greenhouse, Lever, or Ashby. All other domains (e.g., LinkedIn, Indeed) are rejected.
    4. Board token / company slug must be present in the path.

    Returns:
        Tuple of (JobPlatform, board_token, canonical_board_url)
    """
    if not url or not isinstance(url, str):
        raise InvalidSourceURLError("Source URL must be a non-empty string.")

    raw_clean = url.strip()
    # Add scheme if missing for parsing
    if not raw_clean.startswith(("http://", "https://")):
        raw_clean = f"https://{raw_clean}"

    parsed = urlparse(raw_clean)
    if parsed.scheme not in ("http", "https"):
        raise InvalidSourceURLError(f"Unsupported scheme '{parsed.scheme}'. Only HTTP and HTTPS are permitted.")

    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise InvalidSourceURLError(f"Invalid URL '{url}': hostname is missing.")

    # 1. SSRF prevention
    if hostname in ("localhost", "127.0.0.1", "::1", "169.254.169.254", "0.0.0.0"):
        raise SSRFSecurityError(f"Access to loopback or metadata host '{hostname}' is blocked.")

    try:
        ip = ipaddress.ip_address(hostname)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved:
            raise SSRFSecurityError(f"Access to private IP range '{hostname}' is blocked.")
    except ValueError:
        pass  # Standard DNS name

    # 2. Strict Platform Allowlist Enforcement
    matched_platform: Optional[JobPlatform] = None
    for platform, domains in PERMITTED_PLATFORM_DOMAINS.items():
        if any(hostname == d or hostname.endswith(f".{d}") for d in domains):
            matched_platform = platform
            break

    if not matched_platform:
        raise UnsupportedPlatformError(
            f"Domain '{hostname}' is not permitted. Job scraping is strictly restricted to "
            f"Greenhouse, Lever, and Ashby (per project rulebook). LinkedIn, Indeed, and external boards are rejected."
        )

    # 3. Extract company / board token from path
    path_segments = [seg for seg in parsed.path.strip("/").split("/") if seg]
    if not path_segments:
        raise InvalidSourceURLError(
            f"URL '{url}' does not contain an organization or board token in path (e.g. 'https://boards.greenhouse.io/company')."
        )

    # First meaningful path segment represents the organization token
    board_token = path_segments[0].lower()
    # Handle api paths like /v1/boards/{token}/jobs or /v0/postings/{token}
    if board_token in ("v1", "v0", "posting-api"):
        if len(path_segments) >= 3 and path_segments[1] in ("boards", "postings", "job-board"):
            board_token = path_segments[2].lower()
        else:
            raise InvalidSourceURLError(f"Cannot resolve board token from API URL '{url}'.")

    # Sanitize token (alphanumeric, hyphens, underscores)
    if not re.match(r"^[a-zA-Z0-9_-]+$", board_token):
        raise InvalidSourceURLError(f"Invalid board token '{board_token}' extracted from URL '{url}'.")

    # Canonical base URL for board
    if matched_platform == JobPlatform.GREENHOUSE:
        canonical_url = f"https://boards.greenhouse.io/{board_token}"
    elif matched_platform == JobPlatform.LEVER:
        canonical_url = f"https://jobs.lever.co/{board_token}"
    else:
        canonical_url = f"https://jobs.ashbyhq.com/{board_token}"

    return matched_platform, board_token, canonical_url


class SourceDiscoveryService:
    """Service for discovering, validating, registering, and managing company job boards."""

    def __init__(self, repository: Optional[Any] = None, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self.repository = repository

    async def register_source_url(self, url: str, company_name: Optional[str] = None) -> ScrapingSource:
        """Validate source URL and register as an active scraping source."""
        platform, board_token, canonical_url = parse_and_validate_source_url(url)
        name = company_name.strip() if company_name and company_name.strip() else board_token.replace("-", " ").title()

        source_id = f"src_{platform.value}_{board_token}"
        source = ScrapingSource(
            id=source_id,
            platform=platform,
            company_name=name,
            board_url=canonical_url,
            board_token=board_token,
            is_active=True,
        )

        if self.repository:
            await self.repository.save_source(source)

        return source

    async def get_or_seed_sources(self) -> List[ScrapingSource]:
        """Fetch all active sources from persistent store or seed from configuration."""
        if not self.repository:
            return []

        existing = await self.repository.get_sources(active_only=True)
        if existing:
            return existing

        # Seed from configurable SCRAPING_SEED_SOURCES setting
        seed_urls = [s.strip() for s in self.settings.SCRAPING_SEED_SOURCES.split(",") if s.strip()]
        seeded: List[ScrapingSource] = []
        for seed_url in seed_urls:
            try:
                src = await self.register_source_url(seed_url)
                seeded.append(src)
            except Exception:
                continue

        return seeded
