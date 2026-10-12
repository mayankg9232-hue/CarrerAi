"""Authorization policies and resource ownership access control (Developer A coordination)."""

from typing import Callable, Sequence
from fastapi import Depends

from app.authentication.dependencies import get_current_user
from app.core.exceptions import AuthorizationError
from app.schemas.auth import AuthenticatedPrincipal


def require_role(*allowed_roles: str) -> Callable[[AuthenticatedPrincipal], AuthenticatedPrincipal]:
    """Dependency factory checking that authenticated principal possesses an allowed role."""

    def role_checker(
        current_user: AuthenticatedPrincipal = Depends(get_current_user),
    ) -> AuthenticatedPrincipal:
        if current_user.role not in allowed_roles:
            raise AuthorizationError(
                f"Insufficient role permissions. Allowed roles: {', '.join(allowed_roles)}"
            )
        return current_user

    return role_checker


def assert_resource_owner(
    resource_user_id: str,
    principal: AuthenticatedPrincipal,
    allow_admin: bool = True,
) -> None:
    """Validate that the authenticated principal owns the target resource (IDOR defense)."""
    if allow_admin and principal.role == "admin":
        return

    if principal.id != resource_user_id:
        raise AuthorizationError(
            "Access forbidden: caller does not have permission to access this resource"
        )
