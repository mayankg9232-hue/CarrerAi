"""Domain-specific typed exceptions for the job scraping pipeline."""


class ScrapingError(Exception):
    """Base exception for all job scraping operations."""
    pass


class UnsupportedPlatformError(ScrapingError):
    """Raised when target domain does not belong to Greenhouse, Lever, or Ashby."""
    pass


class InvalidSourceURLError(ScrapingError):
    """Raised when a provided job-board or posting URL is syntactically invalid or malformed."""
    pass


class SSRFSecurityError(ScrapingError):
    """Raised when a URL attempts to connect to private, loopback, or cloud-metadata IP addresses."""
    pass


class PlatformRateLimitError(ScrapingError):
    """Raised when upstream ATS platform returns HTTP 429 Too Many Requests."""
    pass


class PlatformTimeoutError(ScrapingError):
    """Raised when an ATS API or Firecrawl call exceeds the configured timeout."""
    pass


class PlatformAuthError(ScrapingError):
    """Raised when Firecrawl or upstream service authentication fails (HTTP 401 / 403)."""
    pass


class PlatformMalformedResponseError(ScrapingError):
    """Raised when an ATS platform or Firecrawl returns non-JSON or invalid data structure."""
    pass


class ScraperLockContentionError(ScrapingError):
    """Raised when another scraping run is already actively executing (preventing overlap)."""
    pass
