"""FastAPI authentication dependencies for token extraction and validation."""

from typing import Optional
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from app.authentication.service import AuthenticationService, get_auth_service
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError, AuthorizationError
from app.schemas.auth import AuthenticatedPrincipal

settings = get_settings()

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl=f"{settings.API_V1_PREFIX}/auth/login",
    auto_error=False,
)


async def get_token_from_request(
    request: Request,
    bearer_token: Optional[str] = Depends(oauth2_scheme),
) -> str:
    """Extract token from OAuth2PasswordBearer dependency or raw Authorization header."""
    if bearer_token:
        return bearer_token

    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        return auth_header.split(" ", 1)[1].strip()

    raise AuthenticationError("Authentication credentials are required")


async def get_current_user(
    token: str = Depends(get_token_from_request),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> AuthenticatedPrincipal:
    """Dependency injecting verified AuthenticatedPrincipal with live account status."""
    principal = auth_service.decode_access_token(token)
    user = await auth_service.repository.get_by_id(principal.id)
    if user:
        principal.is_active = user.is_active
        principal.role = user.role
        if user.full_name:
            principal.full_name = user.full_name
        if user.email_verified:
            principal.email_verified = True

    if not principal.is_active:
        raise AuthorizationError("Account is inactive or disabled")

    return principal


async def require_verified_user(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
) -> AuthenticatedPrincipal:
    """Ensure that candidate accounts have a verified email address before accessing protected career features.

    Admins and recruiters bypass verification.
    """
    settings = get_settings()
    if not settings.REQUIRE_EMAIL_VERIFICATION:
        return current_user

    if current_user.role in ("admin", "recruiter"):
        return current_user

    if not current_user.email_verified:
        raise AuthorizationError(
            "Email verification required. Please verify your email to access career matching, resumes, and agent features.",
            error_code="EMAIL_NOT_VERIFIED",
        )

    return current_user


async def get_optional_current_user(
    request: Request,
    bearer_token: Optional[str] = Depends(oauth2_scheme),
    auth_service: AuthenticationService = Depends(get_auth_service),
) -> Optional[AuthenticatedPrincipal]:
    """Optional dependency returning principal if token is present and valid, else None."""
    try:
        token = await get_token_from_request(request, bearer_token)
        return auth_service.decode_access_token(token)
    except Exception:
        return None
