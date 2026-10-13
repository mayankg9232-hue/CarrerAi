"""Central export of all application schemas conforming to docs/api-contracts.md."""

from app.schemas.common import (
    ErrorDetail,
    ErrorResponse,
    HealthResponse,
    StandardResponse,
)
from app.schemas.auth import (
    AuthenticatedPrincipal,
    FirebaseConfigResponse,
    FirebaseLoginRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.schemas.resume import (
    CandidateProfile,
    EducationItem,
    ExperienceItem,
    SkillItem,
)
from app.schemas.job import (
    Job,
    JobIngestRequest,
    JobIngestResponse,
    JobSourceProvenance,
)
from app.schemas.matching import (
    MatchRequest,
    MatchResult,
    RecommendedMatchesResponse,
    ScoreBreakdown,
)
from app.schemas.llm import (
    LLMMatchExplanationResult,
    LLMResponseMetadata,
    LLMResumeExtractionResult,
    LLMReviewResponse,
    LLMReviewVerdict,
    LLMSkillExtractionItem,
)

__all__ = [
    # Common
    "StandardResponse",
    "ErrorResponse",
    "ErrorDetail",
    "HealthResponse",
    # Auth
    "UserRegisterRequest",
    "UserLoginRequest",
    "UserResponse",
    "TokenResponse",
    "AuthenticatedPrincipal",
    # Resume
    "SkillItem",
    "ExperienceItem",
    "EducationItem",
    "CandidateProfile",
    # Job
    "Job",
    "JobSourceProvenance",
    "JobIngestRequest",
    "JobIngestResponse",
    # Matching
    "ScoreBreakdown",
    "MatchResult",
    "MatchRequest",
    "RecommendedMatchesResponse",
    # LLM
    "LLMResponseMetadata",
    "LLMReviewVerdict",
    "LLMReviewResponse",
    "LLMSkillExtractionItem",
    "LLMResumeExtractionResult",
    "LLMMatchExplanationResult",
]

