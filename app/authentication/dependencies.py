"""FastAPI authentication dependencies for token extraction and validation."""

from typing import Optional
from fastapi import Depends, Request
from fastapi.security import OAuth2PasswordBearer

from app.authentication.service import AuthenticationService, get_auth_service
from app.core.config import get_settings
from app.core.exceptions import AuthenticationError
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
    """Dependency injecting verified AuthenticatedPrincipal into protected route handlers."""
    return auth_service.decode_access_token(token)


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
