"""External service integrations package (Firecrawl, LLM, etc.)."""

from app.integrations.firecrawl import (
    FirecrawlClient,
    FirecrawlSourceAdapter,
    IFirecrawlAdapter,
    MockFirecrawlAdapter,
    MockFirecrawlClient,
)
from app.integrations.llm import (
    LLMAuthenticationError,
    LLMCredentialsMissingError,
    LLMError,
    LLMMalformedOutputError,
    LLMProviderInterface,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
    MockLLMAdapter,
    PrimaryLLMAdapter,
    SecondaryLLMAdapter,
    TwoModelLLMService,
    get_llm_service,
    reset_llm_service,
    set_llm_service,
)

__all__ = [
    # Firecrawl
    "FirecrawlClient",
    "MockFirecrawlClient",
    "IFirecrawlAdapter",
    "FirecrawlSourceAdapter",
    "MockFirecrawlAdapter",
    # LLM
    "LLMProviderInterface",
    "PrimaryLLMAdapter",
    "SecondaryLLMAdapter",
    "MockLLMAdapter",
    "TwoModelLLMService",
    "get_llm_service",
    "set_llm_service",
    "reset_llm_service",
    # LLM Exceptions
    "LLMError",
    "LLMCredentialsMissingError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMUnavailableError",
    "LLMMalformedOutputError",
    "LLMAuthenticationError",
]
