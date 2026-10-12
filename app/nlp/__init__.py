"""Developer B NLP package: Normalization, taxonomy skill extraction, and resume parsing."""

from app.nlp.normalizer import (
    CANONICAL_SKILL_ALIASES,
    canonicalize_skill_name,
    compute_sha256,
    normalize_date_range,
    sanitize_text,
    wrap_untrusted_content,
)
from app.nlp.resume_parser import (
    IResumeParser,
    NLPResumeParser,
    ResumeParsingError,
    UnreadableCVError,
    UnsupportedFileFormatError,
)
from app.nlp.skill_extractor import SKILL_TAXONOMY, SkillExtractor

__all__ = [
    "CANONICAL_SKILL_ALIASES",
    "canonicalize_skill_name",
    "compute_sha256",
    "normalize_date_range",
    "sanitize_text",
    "wrap_untrusted_content",
    "SKILL_TAXONOMY",
    "SkillExtractor",
    "IResumeParser",
    "NLPResumeParser",
    "ResumeParsingError",
    "UnreadableCVError",
    "UnsupportedFileFormatError",
]
