"""User repository interface and in-memory implementation."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid


@dataclass
class UserRecord:
    """Internal persisted user domain entity."""

    id: str
    email: str
    hashed_password: str
    full_name: str
    role: str = "candidate"
    is_active: bool = True
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class IUserRepository(ABC):
    """Abstract interface for user data storage."""

    @abstractmethod
    async def create_user(
        self,
        email: str,
        hashed_password: str,
        full_name: str,
        role: str = "candidate",
    ) -> UserRecord:
        """Create and persist a new user record."""
        pass

    @abstractmethod
    async def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        """Fetch a user by unique identifier."""
        pass

    @abstractmethod
    async def get_by_email(self, email: str) -> Optional[UserRecord]:
        """Fetch a user by email address."""
        pass

    @abstractmethod
    async def list_users(self) -> List[UserRecord]:
        """List all users."""
        pass

    @abstractmethod
    async def update_user(self, user_id: str, **updates) -> Optional[UserRecord]:
        """Update fields of an existing user record."""
        pass

    @abstractmethod
    async def clear(self) -> None:
        """Clear all stored users (primarily for testing fixtures)."""
        pass


class InMemoryUserRepository(IUserRepository):
    """Thread-safe in-memory user repository for MVP and test isolation."""

    def __init__(self) -> None:
        self._users_by_id: Dict[str, UserRecord] = {}
        self._id_by_email: Dict[str, str] = {}

    async def create_user(
        self,
        email: str,
        hashed_password: str,
        full_name: str,
        role: str = "candidate",
    ) -> UserRecord:
        norm_email = email.lower().strip()
        if norm_email in self._id_by_email:
            raise ValueError(f"User with email {norm_email} already exists")

        user_id = f"usr_{uuid.uuid4().hex[:12]}"
        user = UserRecord(
            id=user_id,
            email=norm_email,
            hashed_password=hashed_password,
            full_name=full_name,
            role=role,
            is_active=True,
            created_at=datetime.now(timezone.utc),
        )
        self._users_by_id[user_id] = user
        self._id_by_email[norm_email] = user_id
        return user

    async def get_by_id(self, user_id: str) -> Optional[UserRecord]:
        return self._users_by_id.get(user_id)

    async def get_by_email(self, email: str) -> Optional[UserRecord]:
        norm_email = email.lower().strip()
        user_id = self._id_by_email.get(norm_email)
        if not user_id:
            return None
        return self._users_by_id.get(user_id)

    async def list_users(self) -> List[UserRecord]:
        return list(self._users_by_id.values())

    async def update_user(self, user_id: str, **updates) -> Optional[UserRecord]:
        user = self._users_by_id.get(user_id)
        if not user:
            return None
        for key, value in updates.items():
            if hasattr(user, key):
                setattr(user, key, value)
        return user

    async def clear(self) -> None:
        self._users_by_id.clear()
        self._id_by_email.clear()


_user_repository: Optional[IUserRepository] = None


def get_user_repository() -> IUserRepository:
    """Dependency provider returning singleton user repository based on configured backend."""
    global _user_repository
    if _user_repository is None:
        from app.core.config import get_settings
        settings = get_settings()
        if settings.REPOSITORY_BACKEND == "sql":
            from app.db.sql_repositories import SQLUserRepository
            _user_repository = SQLUserRepository()
        else:
            _user_repository = InMemoryUserRepository()
    return _user_repository


def reset_user_repository(repo: Optional[IUserRepository] = None) -> None:
    """Reset singleton repository for test isolation."""
    global _user_repository
    if repo is not None:
        _user_repository = repo
    elif _user_repository is not None and isinstance(_user_repository, InMemoryUserRepository):
        _user_repository._users_by_id.clear()
        _user_repository._id_by_email.clear()
    else:
        _user_repository = InMemoryUserRepository()
