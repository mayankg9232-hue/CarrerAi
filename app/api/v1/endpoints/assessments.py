"""Candidate assessment execution and sandbox introspection endpoints (Phase 7 Developer A)."""

from typing import List
from fastapi import APIRouter, Body, Depends, Path, status

from app.authentication.dependencies import get_current_user
from app.authorization.policies import require_role
from app.schemas.assessment import (
    AssessmentResult,
    AssessmentSubmissionRequest,
    SupportedLanguageSpec,
)
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.services.assessment_service import (
    AssessmentApplicationService,
    get_assessment_service,
)

router = APIRouter()


@router.get(
    "/languages",
    response_model=StandardResponse[List[SupportedLanguageSpec]],
    status_code=status.HTTP_200_OK,
    summary="List supported assessment programming languages",
    description="Retrieve allowlisted programming languages, runtimes, and baseline resource constraints.",
)
async def list_supported_languages(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: AssessmentApplicationService = Depends(get_assessment_service),
) -> StandardResponse[List[SupportedLanguageSpec]]:
    """Return allowlisted programming languages and execution limits."""
    languages = service.get_supported_languages()
    return StandardResponse(
        data=languages,
        message=f"Retrieved {len(languages)} supported programming language(s).",
    )


@router.post(
    "/submit",
    response_model=StandardResponse[AssessmentResult],
    status_code=status.HTTP_200_OK,
    summary="Submit candidate code for sandboxed assessment execution",
    description="Validates language, applies hard resource limits, executes inside isolated workspace, and records metrics.",
)
async def submit_assessment(
    payload: AssessmentSubmissionRequest = Body(..., embed=False),
    current_user: AuthenticatedPrincipal = Depends(require_role("candidate", "recruiter", "admin")),
    service: AssessmentApplicationService = Depends(get_assessment_service),
) -> StandardResponse[AssessmentResult]:
    """Execute candidate assessment code submission in isolated sandbox environment."""
    result = await service.submit_assessment(request=payload, principal=current_user)
    return StandardResponse(
        data=result,
        message=f"Assessment executed with status '{result.status}'. Passed {result.passed_tests}/{result.total_tests} tests.",
    )


@router.get(
    "/{submission_id}",
    response_model=StandardResponse[AssessmentResult],
    status_code=status.HTTP_200_OK,
    summary="Get assessment submission result",
    description="Retrieve assessment execution metrics, pass/fail status, and stdout/stderr output.",
)
async def get_assessment(
    submission_id: str = Path(..., min_length=1, description="Unique assessment submission ID"),
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: AssessmentApplicationService = Depends(get_assessment_service),
) -> StandardResponse[AssessmentResult]:
    """Retrieve candidate assessment submission result enforcing ownership authorization."""
    result = await service.get_assessment(submission_id=submission_id, principal=current_user)
    return StandardResponse(
        data=result,
        message=f"Retrieved assessment submission '{submission_id}'.",
    )
