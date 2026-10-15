"""ATS Platform Adapters package (Greenhouse, Lever, Ashby)."""

from app.scraping.adapters.base import BasePlatformAdapter
from app.scraping.adapters.greenhouse import GreenhouseAdapter
from app.scraping.adapters.lever import LeverAdapter
from app.scraping.adapters.ashby import AshbyAdapter

__all__ = [
    "BasePlatformAdapter",
    "GreenhouseAdapter",
    "LeverAdapter",
    "AshbyAdapter",
]
