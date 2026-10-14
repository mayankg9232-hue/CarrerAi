"""Database package exporting manager and repositories."""

from app.db.connection import (
    DatabaseError,
    DatabaseManager,
    DatabaseTransaction,
    UniqueConstraintViolationError,
    get_db_manager,
)
from app.db.sql_repositories import (
    SQLJobRepository,
    SQLMatchRepository,
    SQLResumeRepository,
    SQLUserRepository,
)

__all__ = [
    "DatabaseError",
    "DatabaseManager",
    "DatabaseTransaction",
    "UniqueConstraintViolationError",
    "get_db_manager",
    "SQLJobRepository",
    "SQLMatchRepository",
    "SQLResumeRepository",
    "SQLUserRepository",
]
