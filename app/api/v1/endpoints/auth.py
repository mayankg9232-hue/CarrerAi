"""Authentication endpoints conforming to docs/api-contracts.md."""

from fastapi import APIRouter, Depends, status

from app.authentication.dependencies import get_current_user
from app.authentication.service import AuthenticationService, get_auth_service
from app.authorization.policies import assert_resource_owner, require_role
from app.core.config import get_settings
from app.schemas.auth import (
    AuthenticatedPrincipal,
    FirebaseConfigResponse,
    FirebaseLoginRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.schemas.common import StandardResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])


@router.post(
    "/register",
    response_model=StandardResponse[UserResponse],
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
)
async def register(
    payload: UserRegisterRequest,
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> StandardResponse[UserResponse]:
    """Register a candidate or recruiter account with hashed credentials."""
    user = await auth_service.register_user(payload)
    return StandardResponse[UserResponse](
        data=user,
        message="User registered successfully",
    )


@router.post(
    "/login",
    response_model=StandardResponse[TokenResponse],
    status_code=status.HTTP_200_OK,
    summary="Authenticate and receive JWT access token",
)
async def login(
    payload: UserLoginRequest,
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> StandardResponse[TokenResponse]:
    """Authenticate with email and password to receive signed JWT bearer token."""
    token_data = await auth_service.authenticate_user(payload)
    return StandardResponse[TokenResponse](
        data=token_data,
        message="Authentication successful",
    )


@router.get(
    "/me",
    response_model=StandardResponse[AuthenticatedPrincipal],
    status_code=status.HTTP_200_OK,
    summary="Get authenticated principal details",
)
async def get_me(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
) -> StandardResponse[AuthenticatedPrincipal]:
    """Return identity details of the authenticated caller."""
    return StandardResponse[AuthenticatedPrincipal](
        data=current_user,
        message="Principal profile retrieved successfully",
    )


@router.get(
    "/recruiter-only",
    response_model=StandardResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="Role-protected test route",
)
async def recruiter_only(
    current_user: AuthenticatedPrincipal = Depends(require_role("recruiter")),
) -> StandardResponse[dict]:
    """Protected endpoint verifying role-based authorization check."""
    return StandardResponse[dict](
        data={"access": "granted", "role": current_user.role},
        message="Recruiter authorization confirmed",
    )


@router.get(
    "/users/{user_id}/data",
    response_model=StandardResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="Resource-ownership protected test route",
)
async def get_user_data(
    user_id: str,
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
) -> StandardResponse[dict]:
    """Protected endpoint verifying per-user resource ownership (IDOR defense)."""
    assert_resource_owner(user_id, current_user)
    return StandardResponse[dict](
        data={"user_id": user_id, "resource": "private_candidate_record"},
        message="Resource ownership verified",
    )


@router.post(
    "/firebase-login",
    response_model=StandardResponse[TokenResponse],
    status_code=status.HTTP_200_OK,
    summary="Authenticate with Firebase ID token from client SDK",
)
async def firebase_login(
    payload: FirebaseLoginRequest,
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> StandardResponse[TokenResponse]:
    """Verify Firebase ID token, provision or sync candidate record, and issue session token."""
    token_response = await auth_service.authenticate_firebase_user(payload)
    return StandardResponse[TokenResponse](
        data=token_response,
        message="Firebase authentication successful",
    )


@router.get(
    "/firebase-config",
    response_model=StandardResponse[FirebaseConfigResponse],
    status_code=status.HTTP_200_OK,
    summary="Get public Firebase Web client configuration",
)
async def get_firebase_config() -> StandardResponse[FirebaseConfigResponse]:
    """Return public Firebase client configuration needed by Web SDK."""
    settings = get_settings()
    config = FirebaseConfigResponse(
        apiKey=settings.FIREBASE_API_KEY,
        authDomain=settings.FIREBASE_AUTH_DOMAIN,
        projectId=settings.FIREBASE_PROJECT_ID,
        storageBucket=settings.FIREBASE_STORAGE_BUCKET,
        messagingSenderId=settings.FIREBASE_MESSAGING_SENDER_ID,
        appId=settings.FIREBASE_APP_ID,
        measurementId=settings.FIREBASE_MEASUREMENT_ID,
    )
    return StandardResponse[FirebaseConfigResponse](
        data=config,
        message="Firebase configuration retrieved",
    )
