"""Repositories package."""

from app.repositories.user_repo import (
    IUserRepository,
    InMemoryUserRepository,
    UserRecord,
    get_user_repository,
)

__all__ = [
    "IUserRepository",
    "InMemoryUserRepository",
    "UserRecord",
    "get_user_repository",
]
