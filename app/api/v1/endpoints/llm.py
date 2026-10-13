"""LLM Provider and Structured Output API Endpoints (Developer A)."""

from fastapi import APIRouter, Body, Depends, status

from app.authentication.dependencies import get_current_user
from app.authorization.dependencies import RequireRole
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.schemas.llm import (
    LLMMatchExplanationRequest,
    LLMMatchExplanationResult,
    LLMResponseMetadata,
    LLMResultEnvelope,
    LLMResumeExtractionRequest,
    LLMResumeExtractionResult,
    LLMReviewRequest,
    LLMReviewResponse,
    LLMServiceStatus,
)
from app.services.llm_service import LLMApplicationService, get_llm_app_service

router = APIRouter()


@router.post(
    "/extract-resume",
    response_model=StandardResponse[LLMResultEnvelope],
    status_code=status.HTTP_200_OK,
    summary="Extract structured candidate profile from resume text via LLM",
    description="Uses primary LLM provider to safely parse and extract skills, experience, and contact info from untrusted resume text.",
)
async def extract_resume(
    payload: LLMResumeExtractionRequest = Body(..., embed=False),
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: LLMApplicationService = Depends(get_llm_app_service),
) -> StandardResponse[LLMResultEnvelope]:
    """Extract structured resume fields with prompt-shielding and model metadata."""
    result, metadata = await service.extract_resume(payload, current_user)
    return StandardResponse(
        data=LLMResultEnvelope(result=result, metadata=metadata),
        message="Structured resume extraction completed successfully.",
    )


@router.post(
    "/explain-match",
    response_model=StandardResponse[LLMResultEnvelope],
    status_code=status.HTTP_200_OK,
    summary="Generate explainable match analysis and truthful resume suggestions",
    description="Uses LLM to produce structured comparison, matched skills, missing skills, and ethical improvement suggestions.",
)
async def explain_match(
    payload: LLMMatchExplanationRequest = Body(..., embed=False),
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: LLMApplicationService = Depends(get_llm_app_service),
) -> StandardResponse[LLMResultEnvelope]:
    """Generate grounded match explanation and gap analysis."""
    result, metadata = await service.explain_match(payload, current_user)
    return StandardResponse(
        data=LLMResultEnvelope(result=result, metadata=metadata),
        message="Match explanation and gap analysis produced successfully.",
    )


@router.post(
    "/review",
    response_model=StandardResponse[LLMResultEnvelope],
    status_code=status.HTTP_200_OK,
    summary="Execute dedicated secondary model critique and quality review",
    description="Invokes secondary LLM provider to independently review and evaluate primary output against source context.",
)
async def review_output(
    payload: LLMReviewRequest = Body(..., embed=False),
    current_user: AuthenticatedPrincipal = Depends(RequireRole("admin", "recruiter")),
    service: LLMApplicationService = Depends(get_llm_app_service),
) -> StandardResponse[LLMResultEnvelope]:

    """Trigger secondary model review workflow. Enforces recruiter/admin role access."""
    result, metadata = await service.review_output(payload, current_user)
    return StandardResponse(
        data=LLMResultEnvelope(result=result, metadata=metadata),
        message=f"Secondary model evaluation verdict: {result.verdict.value}.",
    )


@router.get(
    "/status",
    response_model=StandardResponse[LLMServiceStatus],
    status_code=status.HTTP_200_OK,
    summary="Check LLM provider configuration and operational status",
    description="Returns primary provider, secondary provider, and fallback readiness without leaking secret credentials.",
)
async def get_service_status(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: LLMApplicationService = Depends(get_llm_app_service),
) -> StandardResponse[LLMServiceStatus]:
    """Return diagnostic LLM service provider status."""
    status_info = service.get_status()
    return StandardResponse(
        data=status_info,
        message="LLM service status retrieved.",
    )
