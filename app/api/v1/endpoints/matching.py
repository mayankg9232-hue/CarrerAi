"""Matching, recommendations, and explainability endpoints (Phase 5 Developer A)."""

from typing import Dict
from fastapi import APIRouter, Body, Depends, Query, status

from app.authentication.dependencies import get_current_user
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.schemas.matching import (
    MatchRequest,
    MatchResult,
    RecommendedMatchesResponse,
)
from app.services.matching_service import (
    MatchingApplicationService,
    get_matching_app_service,
)

router = APIRouter()


@router.post(
    "/{candidate_id}/jobs/{job_id}",
    response_model=StandardResponse[MatchResult],
    status_code=status.HTTP_200_OK,
    summary="Match candidate profile to specific job",
    description="Calculate explainable match score, breakdown, overlaps, and gaps between candidate and job.",
)
async def match_candidate_to_job(
    candidate_id: str,
    job_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: MatchingApplicationService = Depends(get_matching_app_service),
) -> StandardResponse[MatchResult]:
    """Calculate explainable candidate-job match evaluation with score breakdown and gap analysis."""
    result = await service.match_candidate_to_job(
        candidate_id=candidate_id,
        job_id=job_id,
        principal=current_user,
    )
    return StandardResponse(
        data=result,
        message="Candidate job match evaluated successfully.",
    )


@router.post(
    "/match",
    response_model=StandardResponse[MatchResult],
    status_code=status.HTTP_200_OK,
    summary="Match candidate profile to specific job (request body)",
    description="Body-based match endpoint supporting MatchRequest payload.",
)
async def match_candidate_to_job_body(
    payload: MatchRequest = Body(..., embed=False),
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: MatchingApplicationService = Depends(get_matching_app_service),
) -> StandardResponse[MatchResult]:
    """Calculate match score from body request containing candidate_id and job_id."""
    result = await service.match_candidate_to_job(
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        principal=current_user,
    )
    return StandardResponse(
        data=result,
        message="Candidate job match evaluated successfully.",
    )


@router.get(
    "/{candidate_id}/recommendations",
    response_model=StandardResponse[RecommendedMatchesResponse],
    status_code=status.HTTP_200_OK,
    summary="Get recommended jobs for candidate profile",
    description="Retrieve ranked active job listings matching candidate profile above a minimum score threshold.",
)
async def get_candidate_recommendations(
    candidate_id: str,
    min_score: float = Query(60.0, ge=0.0, le=100.0, description="Minimum overall match score (0-100)"),
    limit: int = Query(10, ge=1, le=50, description="Maximum number of recommendations to return"),
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: MatchingApplicationService = Depends(get_matching_app_service),
) -> StandardResponse[RecommendedMatchesResponse]:
    """Retrieve ranked job recommendations for candidate profile."""
    result = await service.recommend_jobs_for_candidate(
        candidate_id=candidate_id,
        principal=current_user,
        min_score=min_score,
        limit=limit,
    )
    return StandardResponse(
        data=result,
        message=f"Found {len(result.matches)} recommended jobs for candidate.",
    )


@router.get(
    "/{candidate_id}/jobs/{job_id}/rag-context",
    response_model=StandardResponse[Dict[str, str]],
    status_code=status.HTTP_200_OK,
    summary="Retrieve grounded RAG context for candidate and job",
    description="Assemble prompt-injection shielded XML representations of candidate profile and job requirements.",
)
async def get_rag_context(
    candidate_id: str,
    job_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: MatchingApplicationService = Depends(get_matching_app_service),
) -> StandardResponse[Dict[str, str]]:
    """Assemble injection-shielded RAG context for candidate and job."""
    context = await service.get_grounded_rag_context(
        candidate_id=candidate_id,
        job_id=job_id,
        principal=current_user,
    )
    return StandardResponse(
        data=context,
        message="Grounded RAG context assembled successfully.",
    )


@router.get(
    "/{candidate_id}/history",
    response_model=StandardResponse[list],
    status_code=status.HTTP_200_OK,
    summary="Get persisted match history for candidate",
    description="Retrieve all previously evaluated and persisted match records for candidate from database.",
)
async def get_candidate_match_history(
    candidate_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: MatchingApplicationService = Depends(get_matching_app_service),
) -> StandardResponse[list]:
    """Retrieve persisted match history for candidate profile."""
    records = await service.get_candidate_matches(
        candidate_id=candidate_id,
        principal=current_user,
    )
    return StandardResponse(
        data=records,
        message=f"Retrieved {len(records)} persisted match record(s).",
    )

