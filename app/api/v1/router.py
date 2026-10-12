"""Aggregated router for API version 1."""

from fastapi import APIRouter
from app.api.v1.endpoints import health

api_v1_router = APIRouter()

# Register health and diagnostics
api_v1_router.include_router(health.router)
