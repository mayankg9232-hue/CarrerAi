"""Authorization package."""

from app.authorization.policies import (
    assert_resource_owner,
    require_role,
)

__all__ = [
    "require_role",
    "assert_resource_owner",
]
