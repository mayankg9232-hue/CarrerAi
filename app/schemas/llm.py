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
