"""Authentication package."""

from app.authentication.dependencies import (
    get_current_user,
    get_optional_current_user,
    oauth2_scheme,
)
from app.authentication.service import (
    AuthenticationService,
    get_auth_service,
)

__all__ = [
    "AuthenticationService",
    "get_auth_service",
    "get_current_user",
    "get_optional_current_user",
    "oauth2_scheme",
]
