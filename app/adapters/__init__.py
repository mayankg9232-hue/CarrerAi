"""External service and parser adapters package."""

from app.adapters.firecrawl_adapter import (
    DomainNotApprovedError,
    FirecrawlAdapter,
    FirecrawlError,
    IFirecrawlAdapter,
    MockFirecrawlAdapter,
    SSRFBlockedError,
    SourceMalformedError,
    SourceRateLimitError,
    SourceTimeoutError,
    validate_source_url,
)
from app.adapters.resume_parser import (
    DefaultResumeParser,
    IResumeParser,
    MockResumeParser,
    ResumeParsingError,
    UnreadableCVError,
    UnsupportedFileFormatError,
)

__all__ = [
    "IFirecrawlAdapter",
    "FirecrawlAdapter",
    "MockFirecrawlAdapter",
    "FirecrawlError",
    "SourceTimeoutError",
    "SourceRateLimitError",
    "SourceMalformedError",
    "SSRFBlockedError",
    "DomainNotApprovedError",
    "validate_source_url",
    "IResumeParser",
    "DefaultResumeParser",
    "MockResumeParser",
    "ResumeParsingError",
    "UnreadableCVError",
    "UnsupportedFileFormatError",
]
