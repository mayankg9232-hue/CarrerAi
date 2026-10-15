"""Endpoints Package."""

from app.api.v1.endpoints import (
    agent,
    assessments,
    auth,
    health,
    jobs,
    llm,
    matching,
    resumes,
    scraping,
)

__all__ = [
    "agent",
    "assessments",
    "auth",
    "health",
    "jobs",
    "llm",
    "matching",
    "resumes",
    "scraping",
]
