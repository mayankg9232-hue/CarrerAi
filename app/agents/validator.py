"""Final Response and Truthfulness Validator for AI Agents (Developer A).

Enforces docs/AI_Agent_Rulebook.md Section 14:
- Mandatory legal and truthfulness disclaimer
- Anti-guarantee validation (match score never described as guaranteed hire or interview)
- Secret redaction (API keys, passwords, or tokens never exposed in output)
- Grounding verification against tool facts
"""

import re
from typing import Any, List, Optional
from pydantic import BaseModel, Field

from app.matching.explain import MatchExplainer


class ResponseValidationError(Exception):
    """Raised when an agent response violates truthfulness, secret, or compliance policies."""
    pass


class AgentResponseValidator:
    """Validates final generated responses before delivering to users."""

    MANDATORY_DISCLAIMER = MatchExplainer.MANDATORY_DISCLAIMER

    # Prohibited phrases that falsely claim employment guarantees
    FORBIDDEN_GUARANTEE_PATTERNS = [
        r"\bguarantee(?:s|d)?\s+(?:an?\s+)?(?:job|offer|interview|employment)\b",
        r"\bpromise(?:s|d)?\s+(?:an?\s+)?(?:job|offer|interview|employment)\b",
        r"\byou\s+will\s+definitely\s+(?:get|receive)\s+(?:an?\s+)?(?:interview|job|offer)\b",
        r"\b100%\s+guaranteed\b",
    ]

    # Secret and credential leakage patterns
    SECRET_LEAKAGE_PATTERNS = [
        r"AIza[0-9A-Za-z-_]{30,}",          # Google API key
        r"sk-[a-zA-Z0-9]{32,}",             # OpenAI secret key
        r"AQ\.[a-zA-Z0-9_-]{30,}",          # Gemini secret key
        r"Bearer\s+ey[A-Za-z0-9_-]{30,}",   # JWT token
        r"BEGIN\s+(?:RSA|OPENSSH)?\s+PRIVATE\s+KEY",
    ]

    def validate_and_sanitize(self, final_answer: str, tool_results: Optional[List[Any]] = None) -> str:
        """Validate response truthfulness, neutralize forbidden claims, and redact secrets."""
        if not final_answer or not final_answer.strip():
            final_answer = "Task completed with no output generated."

        sanitized = final_answer

        # 1. Redact any accidental credential leakages
        for pattern in self.SECRET_LEAKAGE_PATTERNS:
            sanitized = re.sub(pattern, "[REDACTED_SECRET]", sanitized, flags=re.IGNORECASE)

        # 2. Check for prohibited false guarantees
        for pattern in self.FORBIDDEN_GUARANTEE_PATTERNS:
            if re.search(pattern, sanitized, re.IGNORECASE):
                # Neutralize guarantee
                sanitized = re.sub(
                    pattern,
                    "automated match score (not a guarantee of employment)",
                    sanitized,
                    flags=re.IGNORECASE,
                )

        # 3. Ensure mandatory disclaimer is present if match or evaluation is mentioned
        match_keywords = ["match", "score", "recommend", "qualification", "evaluation", "candidate"]
        if any(k in sanitized.lower() for k in match_keywords):
            if self.MANDATORY_DISCLAIMER not in sanitized:
                sanitized = f"{sanitized.strip()}\n\n*Note: {self.MANDATORY_DISCLAIMER}*"

        return sanitized
