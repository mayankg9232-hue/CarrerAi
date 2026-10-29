"""Authentication endpoints conforming to docs/api-contracts.md."""

from fastapi import APIRouter, Depends, status

from app.authentication.dependencies import get_current_user
from app.authentication.service import AuthenticationService, get_auth_service
from app.authorization.dependencies import RequireRole, VerifyResourceOwnership
from app.authorization.policies import assert_resource_owner, require_role
from app.core.config import get_settings
from app.core.rate_limiter import RateLimiterDependency
from app.schemas.auth import (
    AuthenticatedPrincipal,
    FirebaseConfigResponse,
    FirebaseLoginRequest,
    SendVerificationEmailRequest,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
    VerificationLinkResponse,
)
from app.schemas.common import StandardResponse

router = APIRouter(prefix="/auth", tags=["Authentication"])
settings = get_settings()

auth_rate_limiter = RateLimiterDependency(
    requests_limit=settings.RATE_LIMIT_LOGIN_MAX,
    window_seconds=settings.RATE_LIMIT_LOGIN_WINDOW_SECONDS,
)
strict_test_rate_limiter = RateLimiterDependency(
    requests_limit=3,
    window_seconds=10,
)


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
    dependencies=[Depends(auth_rate_limiter)],
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
    summary="Role-protected test route for recruiters",
)
async def recruiter_only(
    current_user: AuthenticatedPrincipal = Depends(RequireRole("recruiter", "admin")),
) -> StandardResponse[dict]:
    """Protected endpoint verifying role-based authorization check."""
    return StandardResponse[dict](
        data={"access": "granted", "role": current_user.role},
        message="Recruiter authorization confirmed",
    )


@router.get(
    "/admin-only",
    response_model=StandardResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="Admin role-protected test route",
)
async def admin_only(
    current_user: AuthenticatedPrincipal = Depends(RequireRole("admin")),
) -> StandardResponse[dict]:
    """Protected endpoint verifying admin role authorization."""
    return StandardResponse[dict](
        data={"access": "granted", "role": current_user.role},
        message="Admin authorization confirmed",
    )


@router.get(
    "/rate-limit-test",
    response_model=StandardResponse[dict],
    status_code=status.HTTP_200_OK,
    dependencies=[Depends(strict_test_rate_limiter)],
    summary="Rate limiting diagnostic endpoint",
)
async def rate_limit_test() -> StandardResponse[dict]:
    """Diagnostic route verifying sliding-window rate throttling."""
    return StandardResponse[dict](
        data={"status": "permitted"},
        message="Request within rate limits",
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
        apiKey=settings.FIREBASE_API_KEY or "AIzaSyBcuGpHGfq2dO0-3o8j1qtc2ctWbgxK9Uo",
        authDomain=settings.FIREBASE_AUTH_DOMAIN or "aihiring-3d070.firebaseapp.com",
        projectId=settings.FIREBASE_PROJECT_ID or "aihiring-3d070",
        storageBucket=settings.FIREBASE_STORAGE_BUCKET or "aihiring-3d070.firebasestorage.app",
        messagingSenderId=settings.FIREBASE_MESSAGING_SENDER_ID or "14319993219",
        appId=settings.FIREBASE_APP_ID or "1:14319993219:web:39f05a13de221b4bfb36b6",
        measurementId=settings.FIREBASE_MEASUREMENT_ID or "G-EMPGKM4ZG6",
    )
    return StandardResponse[FirebaseConfigResponse](
        data=config,
        message="Firebase configuration retrieved",
    )


@router.post(
    "/send-verification",
    response_model=StandardResponse[dict],
    status_code=status.HTTP_200_OK,
    summary="Trigger Firebase verification email via Identity Toolkit",
)
async def send_verification(
    payload: SendVerificationEmailRequest,
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> StandardResponse[dict]:
    """Dispatch verification email to candidate via Google Identity Toolkit."""
    if not payload.id_token and not payload.email:
        return StandardResponse[dict](
            data={"status": "error"},
            message="Either id_token or email is required",
        )

    if payload.id_token:
        result = await auth_service.send_verification_email(payload.id_token)
    else:
        result = await auth_service.generate_verification_link(str(payload.email))

    return StandardResponse[dict](
        data=result,
        message="Verification email dispatched" if result.get("status") == "sent" else result.get("message", "Processing completed"),
    )


@router.post(
    "/get-verification-link",
    response_model=StandardResponse[VerificationLinkResponse],
    status_code=status.HTTP_200_OK,
    summary="Generate official Firebase verification link using service account",
)
async def get_verification_link(
    payload: SendVerificationEmailRequest,
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> StandardResponse[VerificationLinkResponse]:
    """Generate official Firebase verification link for development, diagnostics, and testing."""
    if not payload.email:
        return StandardResponse[VerificationLinkResponse](
            data=VerificationLinkResponse(status="error", email="", message="Email is required"),
            message="Email address is required",
        )

    result = await auth_service.generate_verification_link(str(payload.email))
    return StandardResponse[VerificationLinkResponse](
        data=VerificationLinkResponse(
            status=result.get("status", "error"),
            email=str(payload.email),
            verification_link=result.get("verification_link"),
            message=result.get("message"),
        ),
        message="Verification link generated successfully" if result.get("status") == "success" else "Link generation notice",
    )
