"""LLM integration package providing primary, secondary, and mock adapters with two-model orchestration."""

from app.integrations.llm.base import (
    LLMAuthenticationError,
    LLMCredentialsMissingError,
    LLMError,
    LLMMalformedOutputError,
    LLMProviderInterface,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
    extract_json_from_text,
    log_llm_execution,
)
from app.integrations.llm.mock_provider import MockLLMAdapter
from app.integrations.llm.primary_provider import PrimaryLLMAdapter
from app.integrations.llm.secondary_provider import SecondaryLLMAdapter
from app.integrations.llm.service import (
    TwoModelLLMService,
    get_llm_service,
    reset_llm_service,
    set_llm_service,
)

__all__ = [
    # Base Abstraction & Helpers
    "LLMProviderInterface",
    "extract_json_from_text",
    "log_llm_execution",
    # Exceptions
    "LLMError",
    "LLMCredentialsMissingError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMUnavailableError",
    "LLMMalformedOutputError",
    "LLMAuthenticationError",
    # Adapters
    "PrimaryLLMAdapter",
    "SecondaryLLMAdapter",
    "MockLLMAdapter",
    # Orchestrator
    "TwoModelLLMService",
    "get_llm_service",
    "set_llm_service",
    "reset_llm_service",
]
