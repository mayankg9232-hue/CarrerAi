"""Authorization policies and resource ownership access control (Developer A lead)."""

from typing import Callable, Sequence, Set
from fastapi import Depends

from app.authentication.dependencies import get_current_user
from app.core.exceptions import AuthorizationError
from app.schemas.auth import AuthenticatedPrincipal


def require_role(*allowed_roles: str) -> Callable[[AuthenticatedPrincipal], AuthenticatedPrincipal]:
    """Dependency factory checking that authenticated principal possesses an allowed role."""

    def role_checker(
        current_user: AuthenticatedPrincipal = Depends(get_current_user),
    ) -> AuthenticatedPrincipal:
        # Enforce account is active
        if not current_user.is_active:
            raise AuthorizationError("Account is inactive or disabled")

        if current_user.role not in allowed_roles:
            raise AuthorizationError(
                f"Insufficient role permissions. Allowed roles: {', '.join(allowed_roles)}"
            )
        return current_user

    return role_checker


def require_active_user(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
) -> AuthenticatedPrincipal:
    """Dependency ensuring caller's account is currently active."""
    if not current_user.is_active:
        raise AuthorizationError("Account is inactive or disabled")
    return current_user


def assert_resource_owner(
    resource_user_id: str,
    principal: AuthenticatedPrincipal,
    allow_admin: bool = True,
    allowed_roles: Sequence[str] = (),
) -> None:
    """Validate that the authenticated principal owns the target resource (IDOR defense)."""
    # Verify active account
    if not principal.is_active:
        raise AuthorizationError("Account is inactive or disabled")

    # Check allowed elevated roles (e.g. recruiter or admin)
    if allowed_roles and principal.role in allowed_roles:
        return

    # Admins optionally bypass individual resource ownership check
    if allow_admin and principal.role == "admin":
        return

    if principal.id != resource_user_id:
        raise AuthorizationError(
            "Access forbidden: caller does not have permission to access this resource"
        )
