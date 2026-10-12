"""Resume and candidate profile schemas conforming to docs/api-contracts.md."""

from datetime import datetime, timezone
from typing import List, Optional
from pydantic import BaseModel, ConfigDict, Field


class SkillItem(BaseModel):
    """Extracted skill entity with confidence and experience metadata."""

    name: str = Field(min_length=1, max_length=100, description="Normalized skill name")
    category: str = Field(default="general", description="Skill category (e.g. language, framework, database, tool)")
    confidence: float = Field(ge=0.0, le=1.0, default=1.0, description="Extraction confidence score between 0.0 and 1.0")
    years_experience: Optional[float] = Field(default=None, ge=0.0, description="Estimated years of experience with skill")


class ExperienceItem(BaseModel):
    """Candidate work experience entry."""

    title: str = Field(min_length=1, max_length=200, description="Job title / role")
    company: str = Field(min_length=1, max_length=200, description="Company or organization name")
    start_date: Optional[str] = Field(default=None, description="Start date (YYYY-MM or string)")
    end_date: Optional[str] = Field(default=None, description="End date (YYYY-MM or string or 'Present')")
    description: Optional[str] = Field(default=None, description="Summary of responsibilities and achievements")


class EducationItem(BaseModel):
    """Candidate educational credential."""

    institution: str = Field(min_length=1, max_length=200, description="University or educational institution")
    degree: str = Field(min_length=1, max_length=200, description="Degree name (e.g. B.S. Computer Science)")
    field_of_study: Optional[str] = Field(default=None, description="Major or area of study")
    graduation_year: Optional[int] = Field(default=None, ge=1950, le=2100, description="Year of graduation")


class CandidateProfile(BaseModel):
    """Structured candidate profile extracted from uploaded resume and CV text."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(description="Unique candidate profile identifier")
    user_id: str = Field(description="Associated user identifier (data isolation key)")
    raw_hash: str = Field(description="SHA-256 hash of original resume content for deduplication and audit")
    full_name: Optional[str] = Field(default=None, description="Extracted candidate full name")
    email: Optional[str] = Field(default=None, description="Extracted candidate contact email")
    summary: Optional[str] = Field(default=None, description="Professional executive summary")
    skills: List[SkillItem] = Field(default_factory=list, description="Validated technical and soft skills")
    experience: List[ExperienceItem] = Field(default_factory=list, description="Work history entries")
    education: List[EducationItem] = Field(default_factory=list, description="Education history entries")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
