"""Structured logging configuration and PII / secret redaction."""

import logging
import re
import sys

# Patterns to redact from logs
SENSITIVE_PATTERNS = [
    (re.compile(r'(?i)(password|secret|token|api_key|authorization)[\'"\s]*[:=][\'"\s]*([^\s,\'";]+)'), r'\1="[REDACTED]"'),
    (re.compile(r'(?i)bearer\s+[a-zA-Z0-9_\-\.]+'), 'Bearer [REDACTED]'),
]


class RedactingFormatter(logging.Formatter):
    """Custom formatter that scrubs sensitive patterns from log messages."""

    def format(self, record: logging.LogRecord) -> str:
        original = super().format(record)
        redacted = original
        for pattern, replacement in SENSITIVE_PATTERNS:
            redacted = pattern.sub(replacement, redacted)
        return redacted


def setup_logging(debug: bool = False) -> logging.Logger:
    """Configure structured console logging for the application."""
    log_level = logging.DEBUG if debug else logging.INFO
    logger = logging.getLogger("ai_job_matcher")
    logger.setLevel(log_level)

    if not logger.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setLevel(log_level)
        formatter = RedactingFormatter(
            fmt="%(asctime)s | %(levelname)-7s | %(name)s | %(filename)s:%(lineno)d | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)

    return logger


logger = setup_logging()
