"""Authorization package."""

from app.authorization.dependencies import (
    RequireRole,
    VerifyResourceOwnership,
)
from app.authorization.policies import (
    assert_resource_owner,
    require_active_user,
    require_role,
)

__all__ = [
    "require_role",
    "require_active_user",
    "assert_resource_owner",
    "RequireRole",
    "VerifyResourceOwnership",
]
