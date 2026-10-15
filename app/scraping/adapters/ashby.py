"""Ashby platform adapter using public posting-api and Firecrawl scraping."""

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


class AshbyAdapter(BasePlatformAdapter):
    """Adapter for extracting job listings from Ashby ATS."""

    platform = JobPlatform.ASHBY

    async def fetch_jobs_for_source(self, source: ScrapingSource, max_jobs: int = 50) -> List[Job]:
        """Fetch job listings for an Ashby company board using the public posting API."""
        endpoint = f"https://api.ashbyhq.com/posting-api/job-board/{source.board_token}"

        response = await self.execute_request_with_retry(
            method="GET",
            url=endpoint,
            timeout=self.settings.SCRAPING_TIMEOUT_SECONDS,
            max_retries=self.settings.SCRAPING_MAX_RETRIES,
        )

        try:
            payload = response.json()
        except Exception as exc:
            raise PlatformMalformedResponseError(f"Ashby API returned non-JSON for '{source.board_token}': {exc}")

        raw_jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        normalized_jobs: List[Job] = []

        for item in raw_jobs[:max_jobs]:
            try:
                external_id = str(item.get("id"))
                title = str(item.get("title", "")).strip()
                if not external_id or not title:
                    continue

                location = item.get("location") or "Remote"
                is_remote = item.get("isRemote")
                dept = item.get("department")
                emp_type = item.get("employmentType")

                raw_desc = item.get("descriptionPlain") or item.get("descriptionHtml", "")
                posting_url = item.get("jobUrl") or f"https://jobs.ashbyhq.com/{source.board_token}/{external_id}"

                published_at = None
                raw_pub = item.get("publishedAt")
                if raw_pub:
                    try:
                        published_at = datetime.fromisoformat(raw_pub.replace("Z", "+00:00"))
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
                    is_remote=is_remote,
                    department=dept,
                    employment_type=emp_type,
                    posted_at=published_at,
                )
                normalized_jobs.append(job)
            except Exception:
                continue

        return normalized_jobs

    async def fetch_single_job(self, url: str) -> Optional[Job]:
        """Fetch a single Ashby job listing via Firecrawl or board extraction."""
        parsed = urlparse(url)
        self.validate_domain(parsed.hostname or "")

        parts = [p for p in parsed.path.strip("/").split("/") if p]
        org_token = parts[0] if parts else "Organization"
        posting_id = parts[1] if len(parts) > 1 else None

        if self.firecrawl:
            data = await self.firecrawl.scrape_url(url)
            meta = data.get("metadata", {})
            title = meta.get("title") or "Position"
            company = meta.get("og:site_name") or org_token.replace("-", " ").title()
            desc = data.get("markdown") or data.get("content") or ""
            return self.normalizer.normalize(
                platform=self.platform,
                external_id=posting_id or hashlib.md5(url.encode()).hexdigest()[:8],
                title=title,
                company=company,
                raw_description=desc,
                source_url=url,
            )

        return None
