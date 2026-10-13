"""Endpoints Package."""

from app.api.v1.endpoints import (
    agent,
    auth,
    health,
    jobs,
    llm,
    matching,
    resumes,
)

__all__ = [
    "agent",
    "auth",
    "health",
    "jobs",
    "llm",
    "matching",
    "resumes",
]
