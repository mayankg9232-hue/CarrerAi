"""Schemas and validation models for candidate code assessments and sandbox execution (Phase 7)."""

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid
from pydantic import BaseModel, Field, field_validator


class SupportedLanguage(str, Enum):
    """Allowed programming languages for sandbox assessment execution."""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"


class ExecutionStatus(str, Enum):
    """Lifecycle and terminal execution statuses for assessment submissions."""
    COMPLETED = "completed"
    TIMEOUT = "timeout"
    MEMORY_EXCEEDED = "memory_exceeded"
    OUTPUT_LIMIT_EXCEEDED = "output_limit_exceeded"
    ERROR = "error"
    REJECTED = "rejected"


class AssessmentLimits(BaseModel):
    """Configurable resource and runtime limits for assessment execution."""
    timeout_seconds: float = Field(
        default=5.0,
        ge=0.5,
        le=10.0,
        description="Maximum execution wall-clock time in seconds (0.5 - 10.0s)",
    )
    memory_mb: int = Field(
        default=128,
        ge=32,
        le=256,
        description="Maximum memory ceiling in Megabytes (32 - 256 MB)",
    )
    max_output_bytes: int = Field(
        default=10 * 1024,
        ge=256,
        le=64 * 1024,
        description="Maximum combined stdout/stderr capture size in bytes (256 B - 64 KB)",
    )


class TestCase(BaseModel):
    """Deterministic input/output test case definition."""
    __test__ = False
    id: str = Field(default_factory=lambda: f"tc_{uuid.uuid4().hex[:8]}")
    input_data: str = Field(
        default="",
        max_length=4096,
        description="Standard input or parameter data passed to the solution",
    )
    expected_output: str = Field(
        default="",
        max_length=4096,
        description="Expected standard output or return value",
    )
    is_hidden: bool = Field(
        default=False,
        description="Whether this test case is hidden from candidate feedback",
    )


class TestCaseResult(BaseModel):
    """Result of evaluating a single test case."""
    __test__ = False
    test_case_id: str
    passed: bool
    actual_output: Optional[str] = None
    expected_output: Optional[str] = None
    error_message: Optional[str] = None
    execution_time_ms: float = 0.0


class AssessmentSubmissionRequest(BaseModel):
    """Payload for submitting candidate code for assessment execution."""
    candidate_id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="ID of candidate profile being assessed",
    )
    skill: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Target skill being verified (e.g. 'Python', 'Algorithms')",
    )
    language: str = Field(
        ...,
        description="Programming language identifier (must match supported allowlist)",
    )
    code: str = Field(
        ...,
        min_length=1,
        max_length=64 * 1024,
        description="Candidate source code submission (max 64 KB)",
    )
    test_cases: List[TestCase] = Field(
        default_factory=list,
        max_length=20,
        description="List of test cases to verify the submission against (max 20)",
    )
    custom_limits: Optional[AssessmentLimits] = None

    @field_validator("language")
    @classmethod
    def validate_language(cls, v: str) -> str:
        norm = v.strip().lower()
        allowed = [lang.value for lang in SupportedLanguage]
        if norm not in allowed:
            raise ValueError(
                f"Unsupported language '{v}'. Allowed languages: {', '.join(allowed)}"
            )
        return norm

    @field_validator("code")
    @classmethod
    def validate_code_not_blank(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Submitted code cannot be empty or whitespace only")
        return v


class AssessmentResult(BaseModel):
    """Standardized response schema for completed assessment execution."""
    submission_id: str
    candidate_id: str
    skill: str
    language: str
    status: ExecutionStatus
    exit_code: int = 0
    stdout: str = ""
    stderr: str = ""
    passed_tests: int = 0
    total_tests: int = 0
    test_results: List[TestCaseResult] = Field(default_factory=list)
    execution_time_ms: float = 0.0
    memory_used_mb: float = 0.0
    is_truncated: bool = False
    sandbox_provider: str = "mock"
    disclaimer: str = (
        "Assessment results verify candidate skill submissions within an isolated environment. "
        "Test metrics represent heuristic indicators and are not unconditional qualification guarantees."
    )
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class SupportedLanguageSpec(BaseModel):
    """Specification metadata for a supported programming language runtime."""
    language: str
    display_name: str
    version: str
    default_timeout_seconds: float
    max_memory_mb: int
    is_active: bool
