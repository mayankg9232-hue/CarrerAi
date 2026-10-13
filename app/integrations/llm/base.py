"""Base abstractions, exceptions, metadata, and JSON extraction for LLM providers."""

from abc import ABC, abstractmethod
import json
import re
import time
from typing import Any, Dict, Optional, Tuple, Type, TypeVar
from pydantic import BaseModel, ValidationError

from app.core.logging import logger
from app.schemas.llm import LLMResponseMetadata

T = TypeVar("T", bound=BaseModel)


# ==============================================================================
# EXCEPTION HIERARCHY
# ==============================================================================

class LLMError(Exception):
    """Base exception for LLM provider errors."""

    def __init__(self, message: str, provider: Optional[str] = None, model: Optional[str] = None):
        super().__init__(message)
        self.message = message
        self.provider = provider
        self.model = model


class LLMCredentialsMissingError(LLMError):
    """Raised when an API key or authentication secret is missing or empty."""
    pass


class LLMTimeoutError(LLMError):
    """Raised when request to external model provider exceeds caller timeout."""
    pass


class LLMRateLimitError(LLMError):
    """Raised when upstream model provider returns HTTP 429 Too Many Requests."""
    pass


class LLMUnavailableError(LLMError):
    """Raised when upstream provider experiences 5xx errors or connectivity failure."""
    pass


class LLMMalformedOutputError(LLMError):
    """Raised when model returns unparseable JSON or output violating Pydantic schema."""

    def __init__(
        self,
        message: str,
        raw_output: Optional[str] = None,
        provider: Optional[str] = None,
        model: Optional[str] = None,
    ):
        super().__init__(message, provider=provider, model=model)
        self.raw_output = raw_output


class LLMAuthenticationError(LLMError):
    """Raised when upstream provider rejects API key with HTTP 401/403."""
    pass


# ==============================================================================
# JSON EXTRACTION AND SANITIZATION UTILITIES
# ==============================================================================

def extract_json_from_text(raw_text: str) -> Dict[str, Any]:
    """Extract and parse a JSON dictionary from raw model text.

    Handles outputs wrapped in markdown ```json ... ``` blocks, extracts
    enclosed JSON objects, and raises LLMMalformedOutputError on malformed text.
    """
    if not raw_text or not isinstance(raw_text, str):
        raise LLMMalformedOutputError("LLM returned empty or non-string response.")

    text = raw_text.strip()

    # Strip markdown code blocks: ```json ... ``` or ``` ... ```
    match = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", text, re.IGNORECASE)
    if match:
        text = match.group(1).strip()

    # If text is not wrapped in code blocks, search for first '{' and matching '}'
    if not (text.startswith("{") and text.endswith("}")):
        first_brace = text.find("{")
        last_brace = text.rfind("}")
        if first_brace != -1 and last_brace != -1 and last_brace > first_brace:
            text = text[first_brace : last_brace + 1]

    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise LLMMalformedOutputError(
                f"LLM response parsed into {type(data).__name__}, expected JSON dictionary.",
                raw_output=raw_text[:200],
            )
        return data
    except json.JSONDecodeError as exc:
        raise LLMMalformedOutputError(
            f"Failed to parse model output as JSON: {str(exc)}",
            raw_output=raw_text[:200],
        ) from exc


def log_llm_execution(
    metadata: LLMResponseMetadata,
    system_prompt_len: int,
    user_prompt_len: int,
    success: bool = True,
    error_msg: Optional[str] = None,
) -> None:
    """Safely log provider invocation metadata without leaking prompts or secrets."""
    status = "SUCCESS" if success else "FAILED"
    tokens_info = f"tokens: [prompt={metadata.prompt_tokens}, comp={metadata.completion_tokens}, total={metadata.total_tokens}]"
    latency_info = f"latency: {metadata.latency_ms:.1f}ms"
    sizes_info = f"prompt_lens: [sys={system_prompt_len}, user={user_prompt_len}]"
    fallback_info = f"fallback={metadata.fallback_triggered}"

    if success:
        logger.info(
            f"LLM call {status} | provider={metadata.provider} model={metadata.model} "
            f"task={metadata.task_name or 'unnamed'} {latency_info} {tokens_info} {sizes_info} {fallback_info}"
        )
    else:
        logger.warning(
            f"LLM call {status} | provider={metadata.provider} model={metadata.model} "
            f"task={metadata.task_name or 'unnamed'} {latency_info} {sizes_info} error={error_msg}"
        )


# ==============================================================================
# ABSTRACT PROVIDER INTERFACE
# ==============================================================================

class LLMProviderInterface(ABC):
    """Standardized abstraction for replaceable LLM model providers."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Name of the model provider (e.g., 'openai', 'gemini', 'mock')."""
        pass

    @property
    @abstractmethod
    def model_name(self) -> str:
        """Model identifier (e.g., 'gpt-4o-mini', 'gemini-1.5-flash')."""
        pass

    @abstractmethod
    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> T:
        """Generates validated structured data conforming to response_model."""
        pass

    @abstractmethod
    async def generate_structured_with_meta(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2000,
        task_name: Optional[str] = None,
    ) -> Tuple[T, LLMResponseMetadata]:
        """Generates validated structured data along with provider execution metadata."""
        pass

    @abstractmethod
    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        task_name: Optional[str] = None,
    ) -> str:
        """Generates plain text output with sanitization."""
        pass
