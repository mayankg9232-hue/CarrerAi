"""Matching engine, gap analysis, and explainability package (Developer B)."""

from app.matching.engine import MatchingEngine
from app.matching.explain import MatchExplainer
from app.matching.gap_analysis import GapAnalysisResult, GapAnalyzer
from app.matching.scorer import DeterministicScorer

__all__ = [
    "DeterministicScorer",
    "GapAnalyzer",
    "GapAnalysisResult",
    "MatchExplainer",
    "MatchingEngine",
]
