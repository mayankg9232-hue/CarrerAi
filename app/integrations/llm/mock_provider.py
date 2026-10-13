"""Mock LLM adapter for deterministic, zero-cost automated tests and offline simulation."""

import time
from typing import Any, Callable, Dict, List, Optional, Tuple, Type, TypeVar, Union
from pydantic import BaseModel, ValidationError

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
from app.schemas.llm import LLMResponseMetadata

T = TypeVar("T", bound=BaseModel)


class MockLLMAdapter(LLMProviderInterface):
    """Configurable mock provider supporting simulated outcomes, errors, and call recording."""

    def __init__(
        self,
        provider_name: str = "mock-primary",
        model_name: str = "mock-model-v1",
        canned_structured: Optional[Union[BaseModel, Dict[Any, BaseModel], Callable[[Type[Any]], BaseModel]]] = None,
        canned_text: str = "Mock generated text response.",
        canned_raw_malformed: Optional[str] = None,
        simulate_timeout: bool = False,
        simulate_malformed: bool = False,
        simulate_missing_credentials: bool = False,
        simulate_rate_limit: bool = False,
        simulate_unavailable: bool = False,
        simulate_auth_error: bool = False,
        prompt_tokens: int = 120,
        completion_tokens: int = 45,
        latency_ms: float = 8.5,
    ) -> None:
        self._provider_name = provider_name
        self._model_name = model_name
        self.canned_structured = canned_structured
        self.canned_text = canned_text
        self.canned_raw_malformed = canned_raw_malformed or "Not a valid JSON payload {{{..."
        self.simulate_timeout = simulate_timeout
        self.simulate_malformed = simulate_malformed
        self.simulate_missing_credentials = simulate_missing_credentials
        self.simulate_rate_limit = simulate_rate_limit
        self.simulate_unavailable = simulate_unavailable
        self.simulate_auth_error = simulate_auth_error
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        self.latency_ms = latency_ms
        self.call_history: List[Dict[str, Any]] = []

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def _check_simulated_faults(self) -> None:
        """Trigger configured simulation errors before generating response."""
        if self.simulate_missing_credentials:
            raise LLMCredentialsMissingError(
                f"Missing API key or credentials for provider '{self.provider_name}'.",
                provider=self.provider_name,
                model=self.model_name,
            )

        if self.simulate_auth_error:
            raise LLMAuthenticationError(
                f"Authentication failed for provider '{self.provider_name}': invalid API key.",
                provider=self.provider_name,
                model=self.model_name,
            )

        if self.simulate_timeout:
            raise LLMTimeoutError(
                f"Request to provider '{self.provider_name}' exceeded caller timeout (15.0s).",
                provider=self.provider_name,
                model=self.model_name,
            )

        if self.simulate_rate_limit:
            raise LLMRateLimitError(
                f"Provider '{self.provider_name}' returned 429 Too Many Requests (rate limit exceeded).",
                provider=self.provider_name,
                model=self.model_name,
            )

        if self.simulate_unavailable:
            raise LLMUnavailableError(
                f"Provider '{self.provider_name}' is currently unavailable (503 Service Unavailable).",
                provider=self.provider_name,
                model=self.model_name,
            )

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2000,
    ) -> T:
        result, _ = await self.generate_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=response_model,
            temperature=temperature,
            max_tokens=max_tokens,
        )
        return result

    async def generate_structured_with_meta(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2000,
        task_name: Optional[str] = None,
    ) -> Tuple[T, LLMResponseMetadata]:
        self.call_history.append({
            "method": "generate_structured",
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "response_model": response_model,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "task_name": task_name,
        })

        self._check_simulated_faults()

        if self.simulate_malformed:
            # Trigger malformed output extraction
            extract_json_from_text(self.canned_raw_malformed)
            # If for some reason that didn't raise, raise directly
            raise LLMMalformedOutputError(
                f"Simulated malformed output from model '{self.model_name}'.",
                raw_output=self.canned_raw_malformed,
                provider=self.provider_name,
                model=self.model_name,
            )

        metadata = LLMResponseMetadata(
            provider=self.provider_name,
            model=self.model_name,
            task_name=task_name,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.prompt_tokens + self.completion_tokens,
            latency_ms=self.latency_ms,
            fallback_triggered=False,
            secondary_used=False,
        )

        # Resolve canned response
        if callable(self.canned_structured):
            instance = self.canned_structured(response_model)
        elif isinstance(self.canned_structured, dict) and response_model in self.canned_structured:
            instance = self.canned_structured[response_model]
        elif isinstance(self.canned_structured, response_model):
            instance = self.canned_structured
        else:
            # Construct a minimal valid instance of response_model if fields allow
            instance = self._create_default_instance(response_model)

        log_llm_execution(
            metadata=metadata,
            system_prompt_len=len(system_prompt),
            user_prompt_len=len(user_prompt),
            success=True,
        )
        return instance, metadata

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        task_name: Optional[str] = None,
    ) -> str:
        self.call_history.append({
            "method": "generate_text",
            "system_prompt": system_prompt,
            "user_prompt": user_prompt,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "task_name": task_name,
        })

        self._check_simulated_faults()

        metadata = LLMResponseMetadata(
            provider=self.provider_name,
            model=self.model_name,
            task_name=task_name,
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.prompt_tokens + self.completion_tokens,
            latency_ms=self.latency_ms,
        )

        log_llm_execution(
            metadata=metadata,
            system_prompt_len=len(system_prompt),
            user_prompt_len=len(user_prompt),
            success=True,
        )
        return self.canned_text

    def _create_default_instance(self, model_cls: Type[T]) -> T:
        """Create a valid baseline instance for known schema classes."""
        try:
            return model_cls()  # If all fields have defaults
        except ValidationError:
            # Construct dictionary with plausible default values based on field definitions
            sample_data: Dict[str, Any] = {}
            for field_name, field_info in model_cls.model_fields.items():
                if field_info.is_required():
                    annotation = field_info.annotation
                    if annotation == str or getattr(annotation, "__name__", "") == "str":
                        sample_data[field_name] = f"Mock {field_name}"
                    elif annotation == int:
                        sample_data[field_name] = 1
                    elif annotation == float:
                        sample_data[field_name] = 0.95
                    elif annotation == bool:
                        sample_data[field_name] = True
                    elif getattr(annotation, "__origin__", None) == list:
                        sample_data[field_name] = []
                    elif getattr(annotation, "__origin__", None) == dict:
                        sample_data[field_name] = {}
                    else:
                        sample_data[field_name] = f"Sample {field_name}"
            return model_cls.model_validate(sample_data)
