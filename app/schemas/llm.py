"""Pydantic schemas for LLM structured output, metadata, and review workflows."""

from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class LLMReviewVerdict(str, Enum):
    """Verdict from secondary model review/evaluation."""
    APPROVED = "APPROVED"
    FLAGGED = "FLAGGED"
    REJECTED = "REJECTED"
    UNCERTAIN = "UNCERTAIN"


class LLMResponseMetadata(BaseModel):
    """Execution metadata for an LLM provider call without secrets or raw CV contents."""

    provider: str = Field(..., description="Provider name (e.g. 'openai', 'gemini', 'mock')")
    model: str = Field(..., description="Model identifier (e.g. 'gpt-4o-mini', 'gemini-1.5-flash')")
    task_name: Optional[str] = Field(None, description="Logical task name for audit logging")
    prompt_tokens: Optional[int] = Field(None, description="Count of prompt tokens if reported by provider")
    completion_tokens: Optional[int] = Field(None, description="Count of completion tokens if reported by provider")
    total_tokens: Optional[int] = Field(None, description="Total tokens consumed")
    latency_ms: float = Field(0.0, description="End-to-end provider roundtrip latency in milliseconds")
    fallback_triggered: bool = Field(False, description="Whether fallback from primary to secondary occurred")
    secondary_used: bool = Field(False, description="Whether secondary provider handled this request")


class LLMReviewResponse(BaseModel):
    """Structured response for secondary model evaluation / review tasks."""

    verdict: LLMReviewVerdict = Field(
        ...,
        description="Review verdict regarding output truthfulness and quality",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score in the evaluation (0.0 to 1.0)",
    )
    critique: str = Field(
        ...,
        description="Concise rationale explaining the evaluation verdict",
    )
    flagged_issues: List[str] = Field(
        default_factory=list,
        description="Specific concerns, unsupported claims, or inconsistencies detected",
    )


class LLMSkillExtractionItem(BaseModel):
    """Individual skill extracted by LLM parser."""

    name: str = Field(..., min_length=1, max_length=100)
    category: str = Field(default="general", max_length=50)
    confidence: float = Field(default=0.9, ge=0.0, le=1.0)
    years_experience: Optional[float] = Field(default=None, ge=0.0, le=70.0)


class LLMResumeExtractionResult(BaseModel):
    """Validated structured resume data extracted via LLM."""

    full_name: Optional[str] = Field(None, max_length=100)
    email: Optional[str] = Field(None, max_length=100)
    summary: Optional[str] = Field(None, max_length=2000)
    skills: List[LLMSkillExtractionItem] = Field(default_factory=list)
    years_experience_total: Optional[float] = Field(default=None, ge=0.0, le=70.0)


class LLMMatchExplanationResult(BaseModel):
    """Structured match explanation and gap analysis produced by LLM."""

    overall_fit: str = Field(..., description="High-level evaluation of candidate fit")
    matched_skills: List[str] = Field(default_factory=list)
    missing_skills: List[str] = Field(default_factory=list)
    uncertain_skills: List[str] = Field(default_factory=list)
    improvement_suggestions: List[str] = Field(
        default_factory=list,
        description="Truthful recommendations to articulate existing verified experience",
    )
    disclaimer: str = Field(
        default="This match score is an automated estimate and does not guarantee an interview or employment offer.",
        description="Mandatory caveat required by rulebook",
    )


# ==============================================================================
# API REQUEST & RESPONSE ENVELOPES (Developer A)
# ==============================================================================

class LLMResumeExtractionRequest(BaseModel):
    """Request payload to perform LLM structured extraction from resume."""

    resume_text: Optional[str] = Field(None, max_length=50000, description="Raw CV/resume text to extract")
    resume_id: Optional[str] = Field(None, description="Profile ID of previously stored resume")
    task_name: Optional[str] = Field("resume_extraction", description="Logical task name for audit logging")


class LLMMatchExplanationRequest(BaseModel):
    """Request payload to produce grounded LLM match explanation and gap analysis."""

    resume_id: Optional[str] = Field(None, description="Profile ID of candidate resume")
    job_id: Optional[str] = Field(None, description="Job listing ID")
    resume_text: Optional[str] = Field(None, max_length=50000, description="Inline CV text if not using resume_id")
    job_description: Optional[str] = Field(None, max_length=50000, description="Inline job description if not using job_id")
    task_name: Optional[str] = Field("match_explanation", description="Logical task name for audit logging")


class LLMReviewRequest(BaseModel):
    """Request payload to invoke secondary model for dedicated critique/review."""

    task_context: str = Field(..., min_length=5, max_length=20000, description="Context or baseline requirements")
    output_to_review: Dict[str, Any] = Field(..., description="Structured dictionary or JSON output to evaluate")
    task_name: Optional[str] = Field("secondary_review", description="Logical task name for audit logging")


class LLMServiceStatus(BaseModel):
    """Diagnostic status for LLM service and providers."""

    primary_provider: str = Field(..., description="Configured primary model provider name")
    primary_model: str = Field(..., description="Configured primary model name")
    secondary_provider: Optional[str] = Field(None, description="Configured secondary provider name")
    secondary_model: Optional[str] = Field(None, description="Configured secondary model name")
    fallback_enabled: bool = Field(False, description="Whether fallback to secondary is enabled on outage")
    status: str = Field("ready", description="Operational status ('ready', 'unconfigured', 'degraded')")


class LLMResultEnvelope(BaseModel):
    """Response envelope pairing structured output with provider execution metadata."""

    result: Any = Field(..., description="Parsed and validated domain model output")
    metadata: LLMResponseMetadata = Field(..., description="Provider, model, token, and latency metadata")

