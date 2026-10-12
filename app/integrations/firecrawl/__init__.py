"""Firecrawl integration package for external job scraping and source adapters."""

from app.integrations.firecrawl.client import (
    FirecrawlAuthenticationError,
    FirecrawlClient,
    FirecrawlClientError,
    FirecrawlMalformedResponseError,
    FirecrawlRateLimitError,
    FirecrawlTimeoutError,
    MockFirecrawlClient,
)
from app.integrations.firecrawl.source_adapter import (
    DomainNotApprovedError,
    FirecrawlError,
    FirecrawlSourceAdapter,
    IFirecrawlAdapter,
    MockFirecrawlAdapter,
    SSRFBlockedError,
    SourceMalformedError,
    SourceRateLimitError,
    SourceTimeoutError,
    validate_source_url,
)

__all__ = [
    "FirecrawlClient",
    "MockFirecrawlClient",
    "FirecrawlClientError",
    "FirecrawlTimeoutError",
    "FirecrawlRateLimitError",
    "FirecrawlAuthenticationError",
    "FirecrawlMalformedResponseError",
    "IFirecrawlAdapter",
    "FirecrawlSourceAdapter",
    "MockFirecrawlAdapter",
    "FirecrawlError",
    "SourceTimeoutError",
    "SourceRateLimitError",
    "SourceMalformedError",
    "SSRFBlockedError",
    "DomainNotApprovedError",
    "validate_source_url",
]
