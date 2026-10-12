"""Matching and gap analysis schemas conforming to docs/api-contracts.md."""

from typing import List
from pydantic import BaseModel, ConfigDict, Field


class ScoreBreakdown(BaseModel):
    """Component scoring breakdown contributing to overall match score."""

    skill_score: float = Field(ge=0.0, le=100.0, description="Hard and soft skill overlap score (0-100)")
    experience_score: float = Field(ge=0.0, le=100.0, description="Years of experience and seniority score (0-100)")
    education_score: float = Field(ge=0.0, le=100.0, description="Educational qualification alignment score (0-100)")


class MatchResult(BaseModel):
    """Explainable job match assessment with gap analysis and truthful recommendations."""

    model_config = ConfigDict(str_strip_whitespace=True)

    candidate_id: str = Field(description="Unique candidate profile identifier")
    job_id: str = Field(description="Unique job listing identifier")
    overall_score: float = Field(ge=0.0, le=100.0, description="Weighted composite match score (0-100)")
    breakdown: ScoreBreakdown = Field(description="Sub-scores for skills, experience, and education")
    matched_skills: List[str] = Field(default_factory=list, description="Skills possessed by candidate required by job")
    missing_skills: List[str] = Field(default_factory=list, description="Job required skills absent from candidate profile")
    uncertain_skills: List[str] = Field(default_factory=list, description="Skills mentioned ambiguously or requiring clarification")
    explanation: str = Field(min_length=1, description="Transparent, human-readable rationale explaining the match score")
    improvement_suggestions: List[str] = Field(default_factory=list, description="Actionable, truthful resume improvement advice")
    caveat: str = Field(
        default="This match score is an automated estimate and does not guarantee an interview or employment offer.",
        description="Mandatory legal and truthfulness disclaimer"
    )


class MatchRequest(BaseModel):
    """Request payload to calculate match between candidate profile and job."""

    candidate_id: str = Field(description="Candidate profile ID")
    job_id: str = Field(description="Job listing ID")


class RecommendedMatchesResponse(BaseModel):
    """Response payload containing ranked job recommendations for candidate."""

    candidate_id: str = Field(description="Candidate profile ID")
    matches: List[MatchResult] = Field(default_factory=list, description="Ranked list of job matches")
    total_evaluated: int = Field(ge=0, description="Total number of active job listings evaluated")
