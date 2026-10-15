"""Greenhouse platform adapter using public board APIs and Firecrawl scraping."""

from datetime import datetime, timezone
import hashlib
import re
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

from app.schemas.job import Job
from app.scraping.adapters.base import BasePlatformAdapter
from app.scraping.exceptions import (
    PlatformMalformedResponseError,
    UnsupportedPlatformError,
)
from app.scraping.models import JobPlatform, ScrapingSource


class GreenhouseAdapter(BasePlatformAdapter):
    """Adapter for extracting job listings from Greenhouse ATS."""

    platform = JobPlatform.GREENHOUSE

    async def fetch_jobs_for_source(self, source: ScrapingSource, max_jobs: int = 50) -> List[Job]:
        """Fetch job postings for a Greenhouse company board using public board API."""
        endpoint = f"https://boards-api.greenhouse.io/v1/boards/{source.board_token}/jobs?content=true"

        response = await self.execute_request_with_retry(
            method="GET",
            url=endpoint,
            timeout=self.settings.SCRAPING_TIMEOUT_SECONDS,
            max_retries=self.settings.SCRAPING_MAX_RETRIES,
        )

        try:
            payload = response.json()
        except Exception as exc:
            raise PlatformMalformedResponseError(f"Greenhouse API returned non-JSON for '{source.board_token}': {exc}")

        raw_jobs = payload.get("jobs", []) if isinstance(payload, dict) else []
        normalized_jobs: List[Job] = []

        for item in raw_jobs[:max_jobs]:
            try:
                external_id = str(item.get("id"))
                title = str(item.get("title", "")).strip()
                if not external_id or not title:
                    continue

                raw_desc = item.get("content", "")
                posting_url = item.get("absolute_url") or f"https://boards.greenhouse.io/{source.board_token}/jobs/{external_id}"

                loc = "Remote"
                if isinstance(item.get("location"), dict):
                    loc = item["location"].get("name", "Remote")
                elif isinstance(item.get("location"), str):
                    loc = item["location"]

                dept_name = None
                depts = item.get("departments", [])
                if isinstance(depts, list) and depts and isinstance(depts[0], dict):
                    dept_name = depts[0].get("name")

                updated_at_val = None
                raw_updated = item.get("updated_at")
                if raw_updated:
                    try:
                        updated_at_val = datetime.fromisoformat(raw_updated.replace("Z", "+00:00"))
                    except Exception:
                        pass

                job = self.normalizer.normalize(
                    platform=self.platform,
                    external_id=external_id,
                    title=title,
                    company=source.company_name,
                    raw_description=raw_desc or f"Job posting for {title} at {source.company_name}.",
                    source_url=posting_url,
                    location=loc,
                    department=dept_name,
                    updated_at=updated_at_val,
                )
                normalized_jobs.append(job)
            except Exception:
                continue

        return normalized_jobs

    async def fetch_single_job(self, url: str) -> Optional[Job]:
        """Fetch a single Greenhouse job listing via API or Firecrawl."""
        parsed = urlparse(url)
        self.validate_domain(parsed.hostname or "")

        # Extract board and job_id from path /boards/{token}/jobs/{id}
        parts = [p for p in parsed.path.strip("/").split("/") if p]
        board_token = parts[0] if parts else None
        job_id = None
        if "jobs" in parts:
            idx = parts.index("jobs")
            if len(parts) > idx + 1:
                job_id = parts[idx + 1]

        if board_token and job_id:
            try:
                api_url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{job_id}"
                resp = await self.execute_request_with_retry("GET", api_url, timeout=10.0)
                item = resp.json()
                return self.normalizer.normalize(
                    platform=self.platform,
                    external_id=job_id,
                    title=item.get("title", ""),
                    company=board_token.replace("-", " ").title(),
                    raw_description=item.get("content", ""),
                    source_url=url,
                    location=item.get("location", {}).get("name", "Remote") if isinstance(item.get("location"), dict) else "Remote",
                )
            except Exception:
                pass

        # Fallback to Firecrawl if available
        if self.firecrawl:
            data = await self.firecrawl.scrape_url(url)
            meta = data.get("metadata", {})
            title = meta.get("title") or "Position"
            company = meta.get("og:site_name") or (board_token.title() if board_token else "Company")
            desc = data.get("markdown") or data.get("content") or ""
            return self.normalizer.normalize(
                platform=self.platform,
                external_id=job_id or hashlib.md5(url.encode()).hexdigest()[:8],
                title=title,
                company=company,
                raw_description=desc,
                source_url=url,
            )

        return None
