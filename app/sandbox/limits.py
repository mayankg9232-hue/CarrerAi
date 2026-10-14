"""Limits, bounds, and language allowlists for candidate code execution (Phase 7)."""

from typing import Dict, Optional, Tuple
from app.schemas.assessment import AssessmentLimits, SupportedLanguage

# Wall-clock execution timeout bounds
DEFAULT_TIMEOUT_SECONDS: float = 5.0
MIN_TIMEOUT_SECONDS: float = 0.5
MAX_TIMEOUT_SECONDS: float = 10.0

# Memory limits in Megabytes
DEFAULT_MEMORY_MB: int = 128
MIN_MEMORY_MB: int = 32
MAX_MEMORY_MB: int = 256

# Capture bounds
DEFAULT_MAX_OUTPUT_BYTES: int = 10 * 1024      # 10 KB
HARD_MAX_OUTPUT_BYTES: int = 64 * 1024         # 64 KB
MAX_CODE_BYTES: int = 64 * 1024                # 64 KB
MAX_TEST_CASES: int = 20

# Canonical supported language specifications
SUPPORTED_LANGUAGE_SPECS: Dict[str, Dict[str, object]] = {
    SupportedLanguage.PYTHON.value: {
        "display_name": "Python 3.11",
        "version": "3.11.14",
        "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_memory_mb": DEFAULT_MEMORY_MB,
        "is_active": True,
    },
    SupportedLanguage.JAVASCRIPT.value: {
        "display_name": "JavaScript (Node.js)",
        "version": "20.11.0",
        "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_memory_mb": DEFAULT_MEMORY_MB,
        "is_active": True,
    },
    SupportedLanguage.TYPESCRIPT.value: {
        "display_name": "TypeScript",
        "version": "5.3.3",
        "default_timeout_seconds": DEFAULT_TIMEOUT_SECONDS,
        "max_memory_mb": DEFAULT_MEMORY_MB,
        "is_active": True,
    },
}


def validate_language(language: str) -> str:
    """Validate that requested language identifier is allowlisted."""
    norm = language.strip().lower()
    if norm not in SUPPORTED_LANGUAGE_SPECS:
        allowed = list(SUPPORTED_LANGUAGE_SPECS.keys())
        raise ValueError(
            f"Unsupported language '{language}'. Allowed languages: {', '.join(allowed)}"
        )
    return norm


def validate_code_payload(code: str) -> None:
    """Validate that candidate code is present and within size limits."""
    if not code or not code.strip():
        raise ValueError("Submitted code cannot be empty or whitespace only")
    raw_bytes = len(code.encode("utf-8"))
    if raw_bytes > MAX_CODE_BYTES:
        raise ValueError(
            f"Code size ({raw_bytes} bytes) exceeds maximum limit of {MAX_CODE_BYTES} bytes"
        )


def clamp_limits(
    timeout_seconds: Optional[float] = None,
    memory_mb: Optional[int] = None,
    max_output_bytes: Optional[int] = None,
) -> AssessmentLimits:
    """Clamp raw numerical limits into safe system bounds, returning a validated AssessmentLimits instance."""
    timeout = (
        DEFAULT_TIMEOUT_SECONDS
        if timeout_seconds is None
        else max(MIN_TIMEOUT_SECONDS, min(float(timeout_seconds), MAX_TIMEOUT_SECONDS))
    )
    memory = (
        DEFAULT_MEMORY_MB
        if memory_mb is None
        else max(MIN_MEMORY_MB, min(int(memory_mb), MAX_MEMORY_MB))
    )
    output = (
        DEFAULT_MAX_OUTPUT_BYTES
        if max_output_bytes is None
        else max(256, min(int(max_output_bytes), HARD_MAX_OUTPUT_BYTES))
    )
    return AssessmentLimits(
        timeout_seconds=timeout,
        memory_mb=memory,
        max_output_bytes=output,
    )


def sanitize_limits(custom_limits: Optional[AssessmentLimits] = None) -> AssessmentLimits:
    """Sanitize and clamp execution limits to strict hard system bounds."""
    if custom_limits is None:
        return AssessmentLimits(
            timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
            memory_mb=DEFAULT_MEMORY_MB,
            max_output_bytes=DEFAULT_MAX_OUTPUT_BYTES,
        )

    return clamp_limits(
        timeout_seconds=custom_limits.timeout_seconds,
        memory_mb=custom_limits.memory_mb,
        max_output_bytes=custom_limits.max_output_bytes,
    )


def truncate_output(text: str, max_bytes: int) -> Tuple[str, bool]:
    """Deterministically truncate stdout/stderr to max_bytes with truncation notice."""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False

    truncated = encoded[:max_bytes].decode("utf-8", errors="ignore")
    notice = f"\n[OUTPUT TRUNCATED: exceeded limit of {max_bytes} bytes]"
    return truncated + notice, True
