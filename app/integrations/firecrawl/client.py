"""Firecrawl API client with caller timeouts, retries with exponential backoff, and robust error mapping."""

import asyncio
from typing import Any, Dict, List, Optional
import httpx


class FirecrawlClientError(Exception):
    """Base exception for Firecrawl API client communication errors."""
    pass


class FirecrawlTimeoutError(FirecrawlClientError):
    """Raised when request to Firecrawl API exceeds configured caller timeout."""
    pass


class FirecrawlRateLimitError(FirecrawlClientError):
    """Raised when upstream Firecrawl API returns HTTP 429 Too Many Requests."""
    pass


class FirecrawlAuthenticationError(FirecrawlClientError):
    """Raised when API key is missing, invalid, or unauthorized (HTTP 401 / 403)."""
    pass


class FirecrawlMalformedResponseError(FirecrawlClientError):
    """Raised when Firecrawl API returns non-JSON or invalid/empty response payload."""
    pass


class FirecrawlClient:
    """Async HTTP client for interacting with the Firecrawl API."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: str = "https://api.firecrawl.dev",
        timeout: float = 15.0,
        max_retries: int = 1,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max(0, max_retries)

    async def scrape_url(
        self,
        target_url: str,
        formats: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Scrape raw web content from the given target URL via Firecrawl API.

        Args:
            target_url: Validated public target job listing URL.
            formats: Desired output formats (default: ['json', 'markdown']).

        Returns:
            Dictionary containing extracted data from Firecrawl API.
        """
        endpoint = f"{self.base_url}/v1/scrape"
        payload = {
            "url": target_url,
            "formats": formats or ["markdown", "html"],
        }
        headers = {
            "Accept": "application/json",
            "User-Agent": "AIJobMatcher-Crawler/1.0",
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"

        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                async with httpx.AsyncClient(timeout=self.timeout) as client:
                    response = await client.post(endpoint, json=payload, headers=headers)

                    if response.status_code == 429:
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise FirecrawlRateLimitError(
                            f"Firecrawl API rate limit exceeded while scraping '{target_url}'."
                        )

                    if response.status_code in (401, 403):
                        raise FirecrawlAuthenticationError(
                            "Firecrawl API authentication failure: invalid or unauthorized API key."
                        )

                    if response.status_code in (408, 504):
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise FirecrawlTimeoutError(
                            f"Firecrawl API timed out while crawling '{target_url}'."
                        )

                    if response.status_code >= 500:
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise FirecrawlMalformedResponseError(
                            f"Upstream Firecrawl server error HTTP {response.status_code}."
                        )

                    response.raise_for_status()

                    try:
                        data = response.json()
                    except Exception as exc:
                        raise FirecrawlMalformedResponseError(
                            f"Firecrawl API returned non-JSON response: {str(exc)}"
                        )

                    result_data = data.get("data", {})
                    if not result_data:
                        raise FirecrawlMalformedResponseError(
                            "Firecrawl API returned an empty 'data' envelope."
                        )

                    return result_data

            except httpx.TimeoutException:
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise FirecrawlTimeoutError(
                    f"Timeout ({self.timeout}s) exceeded connecting to Firecrawl API."
                )
            except (FirecrawlClientError,):
                raise
            except httpx.RequestError as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise FirecrawlMalformedResponseError(
                    f"Network connection error communicating with Firecrawl: {str(exc)}"
                )

        raise FirecrawlMalformedResponseError("Failed to scrape target URL after retry attempts.")


class MockFirecrawlClient:
    """Configurable mock Firecrawl client for reproducible local and unit testing."""

    def __init__(
        self,
        canned_data: Optional[Dict[str, Dict[str, Any]]] = None,
        simulate_timeout: bool = False,
        simulate_rate_limit: bool = False,
        simulate_auth_error: bool = False,
        simulate_malformed: bool = False,
    ) -> None:
        self.canned_data = canned_data or {}
        self.simulate_timeout = simulate_timeout
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_auth_error = simulate_auth_error
        self.simulate_malformed = simulate_malformed

    async def scrape_url(
        self,
        target_url: str,
        formats: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        """Simulate Firecrawl client response."""
        if self.simulate_timeout:
            raise FirecrawlTimeoutError(f"Mock timeout scraping '{target_url}'.")

        if self.simulate_rate_limit:
            raise FirecrawlRateLimitError(f"Mock rate limit scraping '{target_url}'.")

        if self.simulate_auth_error:
            raise FirecrawlAuthenticationError("Mock invalid credentials.")

        if self.simulate_malformed:
            raise FirecrawlMalformedResponseError("Mock malformed response payload.")

        if target_url in self.canned_data:
            return self.canned_data[target_url]

        return {
            "metadata": {
                "title": "Software Engineer",
                "og:site_name": "Tech Corp",
                "location": "Remote",
                "required_skills": ["Python", "FastAPI"],
                "preferred_skills": ["Docker", "Kubernetes"],
                "experience_level": "mid",
            },
            "markdown": "# Software Engineer\nWe are looking for a Python engineer skilled in FastAPI and Docker.",
        }
