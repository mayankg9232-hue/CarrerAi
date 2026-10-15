"""Lever platform adapter using public postings API and Firecrawl scraping."""

from datetime import datetime, timezone
import hashlib
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.schemas.job import Job
from app.scraping.adapters.base import BasePlatformAdapter
from app.scraping.exceptions import (
    PlatformMalformedResponseError,
    UnsupportedPlatformError,
)
from app.scraping.models import JobPlatform, ScrapingSource


class LeverAdapter(BasePlatformAdapter):
    """Adapter for extracting job listings from Lever ATS."""

    platform = JobPlatform.LEVER

    async def fetch_jobs_for_source(self, source: ScrapingSource, max_jobs: int = 50) -> List[Job]:
        """Fetch job postings for a Lever company board using public postings API."""
        endpoint = f"https://api.lever.co/v0/postings/{source.board_token}?mode=json"

        response = await self.execute_request_with_retry(
            method="GET",
            url=endpoint,
            timeout=self.settings.SCRAPING_TIMEOUT_SECONDS,
            max_retries=self.settings.SCRAPING_MAX_RETRIES,
        )

        try:
            raw_postings = response.json()
        except Exception as exc:
            raise PlatformMalformedResponseError(f"Lever API returned non-JSON for '{source.board_token}': {exc}")

        if not isinstance(raw_postings, list):
            raise PlatformMalformedResponseError(f"Lever API response envelope was not a list for '{source.board_token}'.")

        normalized_jobs: List[Job] = []
        for item in raw_postings[:max_jobs]:
            try:
                external_id = str(item.get("id"))
                title = str(item.get("text", "")).strip()
                if not external_id or not title:
                    continue

                categories = item.get("categories", {}) or {}
                location = categories.get("location") or "Remote"
                team = categories.get("team") or categories.get("department")
                commitment = categories.get("commitment")
                workplace_type = item.get("workplaceType")

                # Prefer descriptionPlain if present, fallback to raw HTML description
                raw_desc = item.get("descriptionPlain") or item.get("description", "")
                posting_url = item.get("hostedUrl") or f"https://jobs.lever.co/{source.board_token}/{external_id}"

                posted_at = None
                created_epoch = item.get("createdAt")
                if isinstance(created_epoch, (int, float)):
                    try:
                        posted_at = datetime.fromtimestamp(created_epoch / 1000.0, tz=timezone.utc)
                    except Exception:
                        pass

                job = self.normalizer.normalize(
                    platform=self.platform,
                    external_id=external_id,
                    title=title,
                    company=source.company_name,
                    raw_description=raw_desc or f"Job listing for {title} at {source.company_name}.",
                    source_url=posting_url,
                    location=location,
                    is_remote=(workplace_type == "remote" or "remote" in location.lower()),
                    department=team,
                    employment_type=commitment,
                    workplace_type=workplace_type,
                    posted_at=posted_at,
                )
                normalized_jobs.append(job)
            except Exception:
                continue

        return normalized_jobs

    async def fetch_single_job(self, url: str) -> Optional[Job]:
        """Fetch a single Lever job listing via public posting endpoint or Firecrawl."""
        parsed = urlparse(url)
        self.validate_domain(parsed.hostname or "")

        parts = [p for p in parsed.path.strip("/").split("/") if p]
        company = parts[0] if parts else None
        posting_id = parts[1] if len(parts) > 1 else None

        if company and posting_id:
            try:
                api_url = f"https://api.lever.co/v0/postings/{company}/{posting_id}"
                resp = await self.execute_request_with_retry("GET", api_url, timeout=10.0)
                item = resp.json()
                categories = item.get("categories", {}) or {}
                return self.normalizer.normalize(
                    platform=self.platform,
                    external_id=posting_id,
                    title=item.get("text", ""),
                    company=company.replace("-", " ").title(),
                    raw_description=item.get("descriptionPlain") or item.get("description", ""),
                    source_url=url,
                    location=categories.get("location", "Remote"),
                    department=categories.get("team"),
                    employment_type=categories.get("commitment"),
                )
            except Exception:
                pass

        if self.firecrawl:
            data = await self.firecrawl.scrape_url(url)
            meta = data.get("metadata", {})
            title = meta.get("title") or "Position"
            company_name = meta.get("og:site_name") or (company.title() if company else "Company")
            desc = data.get("markdown") or data.get("content") or ""
            return self.normalizer.normalize(
                platform=self.platform,
                external_id=posting_id or hashlib.md5(url.encode()).hexdigest()[:8],
                title=title,
                company=company_name,
                raw_description=desc,
                source_url=url,
            )

        return None
