"""Sliding-window rate limiter for brute-force defense and API throttling (Developer A)."""

from collections import defaultdict
import time
from typing import Dict, List
from fastapi import Request

from app.core.exceptions import RateLimitError


class InMemoryRateLimiter:
    """In-memory sliding window rate limiter."""

    def __init__(self, requests_limit: int = 10, window_seconds: int = 60):
        self.requests_limit = requests_limit
        self.window_seconds = window_seconds
        # Maps key -> list of request timestamps
        self._history: Dict[str, List[float]] = defaultdict(list)

    def is_allowed(self, key: str) -> bool:
        """Check if request is within rate limit and record timestamp."""
        now = time.time()
        cutoff = now - self.window_seconds

        # Clean timestamps outside window
        self._history[key] = [t for t in self._history[key] if t > cutoff]

        if len(self._history[key]) >= self.requests_limit:
            return False

        self._history[key].append(now)
        return True

    def get_retry_after(self, key: str) -> int:
        """Calculate seconds until oldest entry leaves the sliding window."""
        now = time.time()
        cutoff = now - self.window_seconds
        valid_timestamps = [t for t in self._history[key] if t > cutoff]
        if not valid_timestamps:
            return 0
        oldest = min(valid_timestamps)
        return max(1, int(oldest + self.window_seconds - now))

    def reset(self) -> None:
        """Clear all rate limit history (useful for test isolation)."""
        self._history.clear()


class RateLimiterDependency:
    """FastAPI callable dependency guard for route rate limiting."""

    def __init__(self, requests_limit: int = 10, window_seconds: int = 60):
        self.limiter = InMemoryRateLimiter(requests_limit=requests_limit, window_seconds=window_seconds)

    def __call__(self, request: Request) -> None:
        # Determine client key from IP address or forwarded header
        client_ip = request.client.host if request.client else "127.0.0.1"
        forwarded_for = request.headers.get("X-Forwarded-For")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()

        key = f"{client_ip}:{request.url.path}"
        if not self.limiter.is_allowed(key):
            retry_after = self.limiter.get_retry_after(key)
            raise RateLimitError(
                message=f"Rate limit exceeded. Maximum {self.limiter.requests_limit} requests per {self.limiter.window_seconds}s. Please retry in {retry_after}s."
            )
