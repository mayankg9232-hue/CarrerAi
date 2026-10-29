"""Matching and gap analysis schemas conforming to docs/api-contracts.md."""

from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, ConfigDict, Field


class FactorScoreDetail(BaseModel):
    """Detailed score and weight analysis for a single matching factor."""

    name: str = Field(description="Display name of the factor")
    score: float = Field(ge=0.0, le=100.0, description="Factor score on 0-100 scale")
    weight: float = Field(ge=0.0, le=1.0, description="Nominal configured weight (e.g. 0.40)")
    effective_weight: float = Field(ge=0.0, le=1.0, description="Effective weight after renormalization")
    weighted_score: float = Field(ge=0.0, le=100.0, description="Contribution to final score (score * effective_weight)")
    available: bool = Field(default=True, description="Whether factor data was evaluable in job description")
    reason: Optional[str] = Field(default=None, description="Explanation or reasoning for score or unavailability")


class MatchingWeights(BaseModel):
    """Configurable weights for the 5-factor deterministic matching engine."""

    technical_skills: float = Field(default=0.40, ge=0.0, le=1.0)
    relevant_experience: float = Field(default=0.25, ge=0.0, le=1.0)
    education: float = Field(default=0.10, ge=0.0, le=1.0)
    role_alignment: float = Field(default=0.15, ge=0.0, le=1.0)
    projects: float = Field(default=0.10, ge=0.0, le=1.0)
    required_skills_split: float = Field(default=0.80, ge=0.0, le=1.0)
    preferred_skills_split: float = Field(default=0.20, ge=0.0, le=1.0)


class ScoreBreakdown(BaseModel):
    """Component scoring breakdown contributing to overall match score."""

    # Backward-compatible fields
    skill_score: float = Field(ge=0.0, le=100.0, description="Technical skill overlap score (0-100)")
    experience_score: float = Field(ge=0.0, le=100.0, description="Years of experience and seniority score (0-100)")
    education_score: float = Field(ge=0.0, le=100.0, description="Educational qualification alignment score (0-100)")
    role_alignment_score: float = Field(default=70.0, ge=0.0, le=100.0, description="Role & responsibility alignment (0-100)")
    projects_score: float = Field(default=60.0, ge=0.0, le=100.0, description="Projects and domain relevance score (0-100)")

    # 5-factor detailed breakdown
    technical_skills: Optional[FactorScoreDetail] = None
    relevant_experience: Optional[FactorScoreDetail] = None
    education: Optional[FactorScoreDetail] = None
    role_alignment: Optional[FactorScoreDetail] = None
    projects: Optional[FactorScoreDetail] = None
    factors: List[FactorScoreDetail] = Field(default_factory=list)

    # Policy flags
    reduced_confidence: bool = Field(default=False, description="True if any factor was unavailable and weights were renormalized")
    is_provisional: bool = Field(default=False, description="True if < 50% of nominal factor weight was evaluable")
    confidence_level: str = Field(default="high", description="Confidence tier: high, medium, low")
    scoring_version: str = Field(default="weighted_v2", description="Engine version identifier")


class MatchResult(BaseModel):
    """Explainable job match assessment with gap analysis and truthful recommendations."""

    model_config = ConfigDict(str_strip_whitespace=True)

    candidate_id: str = Field(description="Unique candidate profile identifier")
    job_id: str = Field(description="Unique job listing identifier")
    overall_score: float = Field(ge=0.0, le=100.0, description="Weighted composite match score (0-100)")
    rank: Optional[int] = Field(default=None, description="1-based match rank sorted by overall score descending")
    breakdown: ScoreBreakdown = Field(description="Sub-scores for the 5 evaluable matching factors")
    matched_skills: List[str] = Field(default_factory=list, description="Skills possessed by candidate required by job")
    missing_skills: List[str] = Field(default_factory=list, description="Job required skills absent from candidate profile")
    uncertain_skills: List[str] = Field(default_factory=list, description="Skills mentioned ambiguously or requiring clarification")
    experience_comparison: Optional[str] = Field(default=None, description="Comparison of candidate years vs required years")
    strong_matching_points: List[str] = Field(default_factory=list, description="Key alignment highlights")
    missing_qualifications: List[str] = Field(default_factory=list, description="Missing or insufficiently demonstrated qualifications")
    explanation: str = Field(min_length=1, description="Transparent, human-readable rationale explaining the match score")
    improvement_suggestions: List[str] = Field(default_factory=list, description="Actionable, truthful resume improvement advice")
    caveat: str = Field(
        default="This match score is an automated estimate and does not guarantee an interview or employment offer.",
        description="Mandatory legal and truthfulness disclaimer"
    )
    is_provisional: bool = Field(default=False, description="True if provisional due to missing information")
    reduced_confidence: bool = Field(default=False, description="True if confidence is reduced due to unstated factors")
    job: Optional[Any] = Field(default=None, description="Optional attached Job listing entity")

    @property
    def overall_match_percentage(self) -> float:
        """Alias for overall_score ensuring consistency with user-facing percent nomenclature."""
        return self.overall_score


class MatchRequest(BaseModel):
    """Request payload to calculate match between candidate profile and job."""

    candidate_id: str = Field(description="Candidate profile ID")
    job_id: str = Field(description="Job listing ID")


class RecommendedMatchesResponse(BaseModel):
    """Response payload containing ranked job recommendations for candidate."""

    candidate_id: str = Field(description="Candidate profile ID")
    matches: List[MatchResult] = Field(default_factory=list, description="Ranked list of job matches")
    total_evaluated: int = Field(ge=0, description="Total number of active job listings evaluated")


from app.schemas.job import Job
from app.schemas.resume import CandidateProfile


class JobMatchItem(BaseModel):
    """Detailed job match item with job metadata for immediate UI presentation."""

    match: MatchResult
    job: Optional[Job] = None


class UnifiedPipelineResponse(BaseModel):
    """Unified response containing extracted profile, pipeline metadata, and ranked match results."""

    profile: CandidateProfile
    embedding_meta: dict
    storage_meta: dict
    scraping_meta: dict
    ranked_matches: List[JobMatchItem]
    total_matches: int


class RankedMatchesSummaryResponse(BaseModel):
    """Ranked job matches response payload for authenticated user's active resume."""

    status: str = Field(description="Match state: completed, processing, failed, no_resume, empty_catalog")
    candidate_id: Optional[str] = Field(default=None, description="Authoritative candidate profile identifier")
    resume_id: Optional[str] = Field(default=None, description="Active resume identifier")
    ranked_matches: List[JobMatchItem] = Field(default_factory=list, description="Ranked matching job items")
    total_matches: int = Field(default=0, ge=0, description="Total matching roles evaluated")
    roles_evaluated: int = Field(default=0, ge=0, description="Total active catalog roles evaluated during matching run")
    is_cached: bool = Field(default=False, description="Whether results were retrieved directly from database storage")
    message: Optional[str] = Field(default=None, description="Human-readable informational or error notice")
    calculated_at: Optional[datetime] = Field(default=None, description="Timestamp when matching was calculated")


MatchResult.model_rebuild()
JobMatchItem.model_rebuild()
RankedMatchesSummaryResponse.model_rebuild()

