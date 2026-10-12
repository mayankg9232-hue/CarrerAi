"""FastAPI dependency guards for authorization and resource protection (Developer A)."""

from typing import Optional
from fastapi import Depends, Request

from app.authentication.dependencies import get_current_user
from app.authorization.policies import assert_resource_owner, require_active_user, require_role
from app.core.exceptions import AuthorizationError, NotFoundError
from app.schemas.auth import AuthenticatedPrincipal


class RequireRole:
    """Class-based dependency guard enforcing role-based access control."""

    def __init__(self, *allowed_roles: str):
        self.allowed_roles = allowed_roles

    def __call__(
        self,
        current_user: AuthenticatedPrincipal = Depends(get_current_user),
    ) -> AuthenticatedPrincipal:
        if not current_user.is_active:
            raise AuthorizationError("Account is inactive or disabled")

        if current_user.role not in self.allowed_roles:
            raise AuthorizationError(
                f"Insufficient role permissions. Required: {', '.join(self.allowed_roles)}"
            )
        return current_user


class VerifyResourceOwnership:
    """Dependency verifying that the route path parameter matches the caller's ID."""

    def __init__(self, param_name: str = "user_id", allow_admin: bool = True):
        self.param_name = param_name
        self.allow_admin = allow_admin

    def __call__(
        self,
        request: Request,
        current_user: AuthenticatedPrincipal = Depends(get_current_user),
    ) -> AuthenticatedPrincipal:
        resource_owner_id = request.path_params.get(self.param_name)
        if not resource_owner_id:
            raise AuthorizationError(f"Resource ownership parameter '{self.param_name}' missing from route")

        assert_resource_owner(
            resource_user_id=resource_owner_id,
            principal=current_user,
            allow_admin=self.allow_admin,
        )
        return current_user
