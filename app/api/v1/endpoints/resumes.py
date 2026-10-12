"""Candidate resume upload and profile retrieval endpoints."""

from typing import List
from fastapi import APIRouter, Depends, File, UploadFile, status

from app.authentication.dependencies import get_current_user
from app.authorization.dependencies import assert_resource_owner
from app.core.exceptions import NotFoundError
from app.pipelines.resume_pipeline import ResumePipeline
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.schemas.resume import CandidateProfile

router = APIRouter()


@router.post(
    "/upload",
    response_model=StandardResponse[CandidateProfile],
    status_code=status.HTTP_201_CREATED,
    summary="Upload and parse candidate CV document",
    description="Accepts PDF, DOCX, or TXT file up to 5 MB, parses text safely, and extracts structured profile.",
)
async def upload_resume(
    file: UploadFile = File(...),
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[CandidateProfile]:
    """Upload candidate CV document and extract structured candidate profile."""
    content = await file.read()
    pipeline = ResumePipeline(repo=repo)
    profile = await pipeline.process_resume(
        file_bytes=content,
        filename=file.filename or "resume.pdf",
        content_type=file.content_type,
        user_id=current_user.id,
    )
    return StandardResponse(
        data=profile,
        message="Resume uploaded and structured profile extracted successfully.",
    )


@router.get(
    "/{profile_id}",
    response_model=StandardResponse[CandidateProfile],
    summary="Get candidate profile by ID",
    description="Retrieve extracted candidate profile. Enforces candidate ownership isolation.",
)
async def get_candidate_profile(
    profile_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[CandidateProfile]:
    """Retrieve candidate profile with server-side ownership authorization check."""
    profile = await repo.get_by_id(profile_id)
    if not profile:
        raise NotFoundError(f"Candidate profile '{profile_id}' not found.")

    # Candidate isolation: only owner, recruiter, or admin can access
    assert_resource_owner(
        resource_user_id=profile.user_id,
        principal=current_user,
        allowed_roles=["admin", "recruiter"],
    )

    return StandardResponse(
        data=profile,
        message="Candidate profile retrieved successfully.",
    )


@router.get(
    "",
    response_model=StandardResponse[List[CandidateProfile]],
    summary="List candidate profiles",
    description="List profiles belonging to the current user (or all profiles for recruiters/admins).",
)
async def list_candidate_profiles(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    repo: IResumeRepository = Depends(get_resume_repository),
) -> StandardResponse[List[CandidateProfile]]:
    """List profiles belonging to authenticated principal."""
    if current_user.role in ("admin", "recruiter"):
        profiles = await repo.list_all()
    else:
        profiles = await repo.get_by_user_id(current_user.id)

    return StandardResponse(
        data=profiles,
        message="Candidate profiles retrieved successfully.",
    )
