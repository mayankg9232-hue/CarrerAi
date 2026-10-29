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
    is_verified: bool = Field(default=False, description="Whether this skill has been verified via candidate code assessment")
    verified_at: Optional[datetime] = Field(default=None, description="UTC timestamp when skill was verified")
    verification_score: Optional[float] = Field(default=None, ge=0.0, le=100.0, description="Score percentage achieved on verification assessment")
    verification_submission_id: Optional[str] = Field(default=None, description="Assessment submission ID certifying this skill")


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
    headline: Optional[str] = Field(default=None, description="Professional headline / target title")
    email: Optional[str] = Field(default=None, description="Extracted candidate contact email")
    summary: Optional[str] = Field(default=None, description="Professional executive summary")
    skills: List[SkillItem] = Field(default_factory=list, description="Validated technical and soft skills")
    experience: List[ExperienceItem] = Field(default_factory=list, description="Work history entries")
    education: List[EducationItem] = Field(default_factory=list, description="Education history entries")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResumeRecord(BaseModel):
    """Persisted resume file entity associated with an authenticated candidate user."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str = Field(description="Unique resume identifier")
    user_id: str = Field(description="Authenticated owner user identifier")
    filename: str = Field(description="Original uploaded document filename")
    storage_key: str = Field(description="Private durable storage reference path")
    file_size: int = Field(ge=0, description="File size in bytes")
    mime_type: str = Field(description="MIME media type")
    raw_hash: str = Field(description="SHA-256 checksum of raw file bytes")
    status: str = Field(default="completed", description="Processing lifecycle state: uploaded, processing, completed, failed")
    is_active: bool = Field(default=True, description="Whether this version is selected as active for matching")
    version: int = Field(default=1, ge=1, description="Sequential version counter for user's CVs")
    profile_id: Optional[str] = Field(default=None, description="Linked CandidateProfile identifier")
    extracted_text: Optional[str] = Field(default=None, description="Raw text extracted from document")
    error_message: Optional[str] = Field(default=None, description="Safe error details if processing failed")
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ResumeListItem(BaseModel):
    """Lightweight resume metadata for dashboard and selection listings."""

    model_config = ConfigDict(str_strip_whitespace=True)

    id: str
    user_id: str
    filename: str
    file_size: int
    mime_type: str
    status: str
    is_active: bool
    version: int
    profile_id: Optional[str] = None
    created_at: datetime


class ResumeDetailResponse(BaseModel):
    """Full resume metadata paired with its parsed candidate profile."""

    resume: ResumeRecord
    profile: Optional[CandidateProfile] = None


class SetActiveResumeRequest(BaseModel):
    """Request model to update active resume selection."""

    is_active: bool = Field(default=True, description="Flag designating active status")

