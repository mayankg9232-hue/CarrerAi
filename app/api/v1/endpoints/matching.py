"""Matching, recommendations, and explainability endpoints (Phase 5 Developer A)."""

import time
from typing import Dict, List, Optional
from fastapi import APIRouter, Body, Depends, Query, Response, status

from app.authentication.dependencies import get_current_user, require_verified_user
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.schemas.matching import (
    MatchRequest,
    MatchResult,
    RecommendedMatchesResponse,
    RankedMatchesSummaryResponse,
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
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
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
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
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
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
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
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
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
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
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


@router.get(
    "",
    response_model=StandardResponse[RankedMatchesSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="Get user's ranked job matches",
    description="Retrieve persisted ranked matches for authenticated user's active resume, or calculate if missing.",
)
async def get_user_ranked_matches(
    response: Response,
    resume_id: Optional[str] = Query(None, description="Optional resume ID; defaults to user's active resume"),
    force_refresh: bool = Query(False, description="Whether to recalculate matches against available catalog jobs"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of recommendations to return"),
    async_matching: bool = Query(False, description="Whether to return immediately with status='matching' if matches are not yet computed"),
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    service: MatchingApplicationService = Depends(get_matching_app_service),
) -> StandardResponse[RankedMatchesSummaryResponse]:
    """Retrieve ranked job recommendations for candidate's active resume."""
    t_start = time.perf_counter()
    result = await service.get_or_compute_ranked_matches(
        principal=current_user,
        resume_id=resume_id,
        force_refresh=force_refresh,
        limit=limit,
        async_matching=async_matching,
    )
    elapsed_ms = (time.perf_counter() - t_start) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    return StandardResponse(
        data=result,
        message=result.message or "Ranked job recommendations retrieved successfully.",
    )


@router.post(
    "/refresh",
    response_model=StandardResponse[RankedMatchesSummaryResponse],
    status_code=status.HTTP_200_OK,
    summary="Refresh user's ranked job matches",
    description="Explicitly re-run matching for authenticated user's active resume against current jobs catalog.",
)
async def refresh_user_ranked_matches(
    response: Response,
    resume_id: Optional[str] = Query(None, description="Optional resume ID; defaults to user's active resume"),
    limit: int = Query(50, ge=1, le=100, description="Maximum number of recommendations to return"),
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    service: MatchingApplicationService = Depends(get_matching_app_service),
) -> StandardResponse[RankedMatchesSummaryResponse]:
    """Force re-calculation of ranked matches for active resume."""
    t_start = time.perf_counter()
    result = await service.get_or_compute_ranked_matches(
        principal=current_user,
        resume_id=resume_id,
        force_refresh=True,
        limit=limit,
    )
    elapsed_ms = (time.perf_counter() - t_start) * 1000
    response.headers["X-Process-Time-Ms"] = f"{elapsed_ms:.1f}"
    return StandardResponse(
        data=result,
        message=result.message or "Ranked job matches refreshed successfully.",
    )

