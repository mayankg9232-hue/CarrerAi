"""Job data normalization, sanitization, and skill enrichment pipeline component."""

from datetime import datetime, timezone
import hashlib
import html
import re
from typing import Any, Dict, List, Optional
import uuid

from app.nlp.normalizer import sanitize_text
from app.nlp.skill_extractor import SkillExtractor
from app.schemas.job import Job
from app.scraping.models import JobPlatform


class JobNormalizer:
    """Normalizes raw ATS listing payloads into verified, schema-compliant Job entities."""

    def __init__(self, skill_extractor: Optional[SkillExtractor] = None) -> None:
        self.skill_extractor = skill_extractor or SkillExtractor()

    @staticmethod
    def clean_html_to_text(raw_html: str) -> str:
        """Strip HTML tags while preserving paragraphs, bullet points, and newlines safely."""
        if not raw_html:
            return ""

        text = html.unescape(raw_html)
        # Convert break and paragraph tags to newlines
        text = re.sub(r"<(?:br|br\s*/)>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"</(?:p|div|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
        text = re.sub(r"<(?:li)>", "• ", text, flags=re.IGNORECASE)
        # Strip all remaining tags
        text = re.sub(r"<[^>]+>", "", text)
        # Collapse excessive whitespace
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n\n", text)
        return sanitize_text(text.strip())

    @staticmethod
    def compute_content_hash(title: str, company: str, description: str) -> str:
        """Compute deterministic SHA-256 fingerprint for deduplication."""
        raw_key = f"{title.strip().lower()}|{company.strip().lower()}|{description.strip().lower()}"
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()

    @staticmethod
    def infer_workplace_type(location: str, is_remote: Optional[bool] = None, explicit_type: Optional[str] = None) -> str:
        """Classify workplace type into 'remote', 'hybrid', or 'onsite'."""
        if explicit_type:
            e_lower = explicit_type.lower()
            if "remote" in e_lower:
                return "remote"
            if "hybrid" in e_lower:
                return "hybrid"
            if "onsite" in e_lower or "on-site" in e_lower:
                return "onsite"
        if is_remote is True:
            return "remote"
        loc_lower = (location or "").lower()
        if "remote" in loc_lower or "anywhere" in loc_lower:
            return "remote"
        if "hybrid" in loc_lower:
            return "hybrid"
        if "on-site" in loc_lower or "onsite" in loc_lower:
            return "onsite"
        return "onsite" if location and location.lower() != "remote" else "remote"

    @staticmethod
    def infer_employment_type(raw_type: Optional[str]) -> Optional[str]:
        """Standardize employment commitment type."""
        if not raw_type:
            return "Full-time"
        t_lower = raw_type.lower()
        if "full" in t_lower:
            return "Full-time"
        if "part" in t_lower:
            return "Part-time"
        if "contract" in t_lower or "temp" in t_lower or "freelance" in t_lower:
            return "Contract"
        if "intern" in t_lower:
            return "Internship"
        return raw_type.title()

    def normalize(
        self,
        platform: JobPlatform,
        external_id: str,
        title: str,
        company: str,
        raw_description: str,
        source_url: str,
        location: Optional[str] = None,
        is_remote: Optional[bool] = None,
        department: Optional[str] = None,
        employment_type: Optional[str] = None,
        workplace_type: Optional[str] = None,
        posted_at: Optional[datetime] = None,
        updated_at: Optional[datetime] = None,
        salary: Optional[str] = None,
        existing_skills: Optional[List[str]] = None,
    ) -> Job:
        """Construct normalized, validated Job entity with skill extraction and deduplication hash."""
        clean_title = sanitize_text(title.strip())
        clean_company = sanitize_text(company.strip())
        description = self.clean_html_to_text(raw_description)

        if not clean_title or not clean_company or len(description) < 10:
            raise ValueError("Job title, company name, and minimum 10 characters description are required.")

        loc = sanitize_text(location.strip()) if location and location.strip() else "Remote"
        workplace = self.infer_workplace_type(loc, is_remote, workplace_type)
        emp_type = self.infer_employment_type(employment_type)

        # NLP Skill Extraction
        required_skills = [s.strip() for s in (existing_skills or []) if s.strip()]
        preferred_skills: List[str] = []
        if not required_skills:
            inferred = self.skill_extractor.extract_skills(description)
            required_skills = [s["name"] for s in inferred[:8]]
            if len(inferred) > 8:
                preferred_skills = [s["name"] for s in inferred[8:14]]

        # Salary extraction if not supplied
        if not salary:
            salary_match = re.search(
                r"(\$\s*\d{2,3}(?:,\d{3})*(?:\s*[-–—to]+\s*\$?\s*\d{2,3}(?:,\d{3})*)?\s*(?:(?:k|K)\b|/(?:yr|year|hr|hour|annum))?)",
                description,
                re.IGNORECASE,
            )
            if salary_match:
                salary = salary_match.group(1).strip()

        # Experience years extraction
        experience_years: Optional[float] = None
        exp_match = re.search(
            r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)(?:\s+of)?(?:\s+[\w/]+){0,3}\s+experience",
            description,
            re.IGNORECASE,
        )
        if exp_match:
            try:
                experience_years = float(exp_match.group(1))
            except ValueError:
                pass

        # Education extraction
        education_required: Optional[str] = None
        desc_lower = description.lower()
        if any(term in desc_lower for term in ["ph.d", "phd", "doctorate"]):
            education_required = "Ph.D. or Doctorate"
        elif any(term in desc_lower for term in ["master's", "masters", "m.s.", "msc"]):
            education_required = "Master's Degree"
        elif any(term in desc_lower for term in ["bachelor's", "bachelors", "b.s.", "bsc", "degree in computer science"]):
            education_required = "Bachelor's Degree"

        # Domain classification
        combined_text = f"{clean_title} {description}".lower()
        if any(d in combined_text for d in ["machine learning", "ai", "artificial intelligence", "deep learning", "llm"]):
            domain = "AI & Machine Learning"
        elif any(d in combined_text for d in ["cloud", "devops", "kubernetes", "infrastructure", "sre"]):
            domain = "Cloud & DevOps"
        elif any(d in combined_text for d in ["frontend", "ui", "ux", "react", "vue"]):
            domain = "Frontend Engineering"
        elif any(d in combined_text for d in ["backend", "api", "microservices", "distributed"]):
            domain = "Backend Engineering"
        elif any(d in combined_text for d in ["data engineer", "etl", "spark", "warehouse"]):
            domain = "Data Engineering"
        else:
            domain = "Software Engineering"

        now = datetime.now(timezone.utc)
        content_hash = self.compute_content_hash(clean_title, clean_company, description)
        deterministic_id = f"job_{platform.value[:2]}_{external_id}"

        return Job(
            id=deterministic_id,
            title=clean_title,
            company=clean_company,
            location=loc,
            description=description,
            required_skills=required_skills,
            preferred_skills=preferred_skills,
            experience_level="mid" if experience_years and 2 <= experience_years <= 5 else ("senior" if experience_years and experience_years > 5 else "entry"),
            salary=salary,
            experience_years_required=experience_years,
            education_required=education_required,
            responsibilities=[],
            domain=domain,
            source=platform.value,
            source_platform=platform.value,
            external_job_id=str(external_id),
            department=department.strip() if department else None,
            employment_type=emp_type,
            workplace_type=workplace,
            source_url=source_url.strip(),
            collected_at=now,
            posted_at=posted_at,
            updated_at=updated_at,
            last_seen_at=now,
            content_hash=content_hash,
            is_active=True,
        )
