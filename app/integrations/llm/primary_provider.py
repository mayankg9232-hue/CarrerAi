"""Primary LLM provider adapter supporting OpenAI and OpenAI-compatible chat endpoints."""

import asyncio
import time
from typing import Any, Dict, Optional, Tuple, Type, TypeVar
import httpx
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


class PrimaryLLMAdapter(LLMProviderInterface):
    """Primary LLM adapter using httpx for OpenAI or OpenAI-compatible REST APIs."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gpt-4o-mini",
        base_url: str = "https://api.openai.com/v1",
        timeout: float = 15.0,
        max_retries: int = 1,
        provider_name: str = "openai",
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self.api_key = api_key
        self._model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = max(1.0, float(timeout))
        self.max_retries = max(0, int(max_retries))
        self._provider_name = provider_name
        self.transport = transport

    @property
    def provider_name(self) -> str:
        return self._provider_name

    @property
    def model_name(self) -> str:
        return self._model

    def _ensure_credentials(self) -> None:
        """Validate that API credentials are provided before making any outbound call."""
        if not self.api_key or not self.api_key.strip():
            raise LLMCredentialsMissingError(
                f"Missing API key for primary LLM provider '{self.provider_name}'. "
                f"Configure PRIMARY_LLM_API_KEY in the environment.",
                provider=self.provider_name,
                model=self.model_name,
            )

    async def _send_chat_completion(
        self,
        messages: list,
        temperature: float,
        max_tokens: int,
        require_json: bool = False,
    ) -> Tuple[str, Dict[str, Any], float]:
        """Send chat completion request with bounded retries and timeout."""
        self._ensure_credentials()

        endpoint = f"{self.base_url}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "User-Agent": "AIJobMatcher-LLM/1.0",
        }
        payload: Dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if require_json:
            payload["response_format"] = {"type": "json_object"}

        start_time = time.perf_counter()
        last_exception: Optional[Exception] = None

        for attempt in range(self.max_retries + 1):
            try:
                client_kwargs: Dict[str, Any] = {"timeout": self.timeout}
                if self.transport is not None:
                    client_kwargs["transport"] = self.transport
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await client.post(endpoint, json=payload, headers=headers)

                    if response.status_code == 429:
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise LLMRateLimitError(
                            f"Primary provider '{self.provider_name}' rate limit exceeded (HTTP 429).",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    if response.status_code in (401, 403):
                        raise LLMAuthenticationError(
                            f"Primary provider '{self.provider_name}' authentication failed: invalid or unauthorized API key.",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    if response.status_code in (408, 504):
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise LLMTimeoutError(
                            f"Primary provider '{self.provider_name}' timed out (HTTP {response.status_code}).",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    if response.status_code >= 500:
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise LLMUnavailableError(
                            f"Primary provider '{self.provider_name}' service unavailable (HTTP {response.status_code}).",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    if response.status_code != 200:
                        raise LLMError(
                            f"Primary provider '{self.provider_name}' returned unexpected status {response.status_code}.",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    data = response.json()
                    choices = data.get("choices", [])
                    if not choices:
                        raise LLMMalformedOutputError(
                            "Primary provider returned empty choices list.",
                            raw_output=response.text[:200],
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    content = choices[0].get("message", {}).get("content", "")
                    usage = data.get("usage", {})
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    return content, usage, latency_ms

            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise LLMTimeoutError(
                    f"Primary provider '{self.provider_name}' request exceeded timeout ({self.timeout}s): {str(exc)}",
                    provider=self.provider_name,
                    model=self.model_name,
                ) from exc
            except (LLMError, LLMCredentialsMissingError):
                raise
            except Exception as exc:
                last_exception = exc
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise LLMUnavailableError(
                    f"Connection to primary provider '{self.provider_name}' failed: {str(exc)}",
                    provider=self.provider_name,
                    model=self.model_name,
                ) from exc

        raise LLMUnavailableError(
            f"Failed communicating with primary provider '{self.provider_name}' after {self.max_retries + 1} attempts.",
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
        # Append explicit schema expectation to system prompt
        schema_prompt = (
            f"{system_prompt}\n\n"
            f"Your output MUST be a valid JSON object matching this schema:\n"
            f"{response_model.model_json_schema()}"
        )
        messages = [
            {"role": "system", "content": schema_prompt},
            {"role": "user", "content": user_prompt},
        ]

        content, usage, latency_ms = await self._send_chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            require_json=True,
        )

        metadata = LLMResponseMetadata(
            provider=self.provider_name,
            model=self.model_name,
            task_name=task_name,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=latency_ms,
            fallback_triggered=False,
            secondary_used=False,
        )

        # Parse and validate structured output
        try:
            parsed_json = extract_json_from_text(content)
            validated_obj = response_model.model_validate(parsed_json)
        except ValidationError as exc:
            log_llm_execution(
                metadata=metadata,
                system_prompt_len=len(system_prompt),
                user_prompt_len=len(user_prompt),
                success=False,
                error_msg=f"Pydantic validation error: {str(exc)}",
            )
            raise LLMMalformedOutputError(
                f"Model response failed Pydantic validation: {str(exc)}",
                raw_output=content[:200],
                provider=self.provider_name,
                model=self.model_name,
            ) from exc
        except LLMMalformedOutputError as exc:
            log_llm_execution(
                metadata=metadata,
                system_prompt_len=len(system_prompt),
                user_prompt_len=len(user_prompt),
                success=False,
                error_msg=f"Malformed JSON: {str(exc)}",
            )
            raise

        log_llm_execution(
            metadata=metadata,
            system_prompt_len=len(system_prompt),
            user_prompt_len=len(user_prompt),
            success=True,
        )
        return validated_obj, metadata

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        task_name: Optional[str] = None,
    ) -> str:
        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        content, usage, latency_ms = await self._send_chat_completion(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            require_json=False,
        )

        metadata = LLMResponseMetadata(
            provider=self.provider_name,
            model=self.model_name,
            task_name=task_name,
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
            total_tokens=usage.get("total_tokens"),
            latency_ms=latency_ms,
        )

        log_llm_execution(
            metadata=metadata,
            system_prompt_len=len(system_prompt),
            user_prompt_len=len(user_prompt),
            success=True,
        )
        return content
