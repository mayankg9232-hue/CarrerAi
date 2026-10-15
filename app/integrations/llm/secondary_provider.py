"""Secondary LLM provider adapter supporting Google Gemini and secondary fallback models."""

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


class SecondaryLLMAdapter(LLMProviderInterface):
    """Secondary LLM adapter supporting Google Gemini API via httpx for review and fallback."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        model: str = "gemini-1.5-flash",
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout: float = 15.0,
        max_retries: int = 2,
        provider_name: str = "gemini",
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
        """Ensure secondary API credentials exist before making external requests."""
        if not self.api_key or not self.api_key.strip():
            raise LLMCredentialsMissingError(
                f"Missing API key for secondary LLM provider '{self.provider_name}'. "
                f"Configure SECONDARY_LLM_API_KEY in the environment.",
                provider=self.provider_name,
                model=self.model_name,
            )

    async def _send_gemini_request(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        require_json: bool = False,
    ) -> Tuple[str, Dict[str, Any], float]:
        """Send content generation request to Gemini API endpoint."""
        self._ensure_credentials()

        endpoint = f"{self.base_url}/models/{self._model}:generateContent"
        params = {"key": self.api_key}
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AIJobMatcher-SecondaryLLM/1.0",
        }

        combined_prompt = f"{system_prompt}\n\nUser Input:\n{user_prompt}"
        generation_config: Dict[str, Any] = {
            "temperature": temperature,
            "maxOutputTokens": max_tokens,
        }
        if require_json:
            generation_config["responseMimeType"] = "application/json"

        payload = {
            "contents": [
                {
                    "role": "user",
                    "parts": [{"text": combined_prompt}],
                }
            ],
            "generationConfig": generation_config,
        }

        start_time = time.perf_counter()

        for attempt in range(self.max_retries + 1):
            try:
                client_kwargs: Dict[str, Any] = {"timeout": self.timeout}
                if self.transport is not None:
                    client_kwargs["transport"] = self.transport
                async with httpx.AsyncClient(**client_kwargs) as client:
                    response = await client.post(endpoint, params=params, json=payload, headers=headers)

                    if response.status_code == 429:
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise LLMRateLimitError(
                            f"Secondary provider '{self.provider_name}' rate limit exceeded (HTTP 429).",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    if response.status_code in (401, 403):
                        raise LLMAuthenticationError(
                            f"Secondary provider '{self.provider_name}' authentication failed: invalid or unauthorized API key.",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    if response.status_code in (408, 504):
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise LLMTimeoutError(
                            f"Secondary provider '{self.provider_name}' timed out (HTTP {response.status_code}).",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    if response.status_code >= 500:
                        if attempt < self.max_retries:
                            await asyncio.sleep(0.5 * (2 ** attempt))
                            continue
                        raise LLMUnavailableError(
                            f"Secondary provider '{self.provider_name}' service unavailable (HTTP {response.status_code}).",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    if response.status_code != 200:
                        raise LLMError(
                            f"Secondary provider '{self.provider_name}' returned status {response.status_code}.",
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    data = response.json()
                    candidates = data.get("candidates", [])
                    if not candidates:
                        raise LLMMalformedOutputError(
                            "Secondary provider returned zero candidates.",
                            raw_output=response.text[:200],
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    parts = candidates[0].get("content", {}).get("parts", [])
                    if not parts:
                        raise LLMMalformedOutputError(
                            "Secondary provider returned candidate with empty parts list.",
                            raw_output=response.text[:200],
                            provider=self.provider_name,
                            model=self.model_name,
                        )

                    content = parts[0].get("text", "")
                    usage = data.get("usageMetadata", {})
                    latency_ms = (time.perf_counter() - start_time) * 1000
                    return content, usage, latency_ms

            except httpx.TimeoutException as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise LLMTimeoutError(
                    f"Secondary provider '{self.provider_name}' request exceeded timeout ({self.timeout}s): {str(exc)}",
                    provider=self.provider_name,
                    model=self.model_name,
                ) from exc
            except (LLMError, LLMCredentialsMissingError):
                raise
            except Exception as exc:
                if attempt < self.max_retries:
                    await asyncio.sleep(0.5 * (2 ** attempt))
                    continue
                raise LLMUnavailableError(
                    f"Connection to secondary provider '{self.provider_name}' failed: {str(exc)}",
                    provider=self.provider_name,
                    model=self.model_name,
                ) from exc

        raise LLMUnavailableError(
            f"Failed communicating with secondary provider '{self.provider_name}' after {self.max_retries + 1} attempts.",
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
        schema_instruction = (
            f"{system_prompt}\n\n"
            f"You MUST output valid JSON conforming strictly to this schema:\n"
            f"{response_model.model_json_schema()}"
        )

        content, usage, latency_ms = await self._send_gemini_request(
            system_prompt=schema_instruction,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            require_json=True,
        )

        prompt_tokens = usage.get("promptTokenCount")
        comp_tokens = usage.get("candidatesTokenCount")
        total_tokens = usage.get("totalTokenCount", (prompt_tokens or 0) + (comp_tokens or 0) if prompt_tokens is not None else None)

        metadata = LLMResponseMetadata(
            provider=self.provider_name,
            model=self.model_name,
            task_name=task_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=comp_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            fallback_triggered=False,
            secondary_used=True,
        )

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
                f"Secondary model response failed Pydantic validation: {str(exc)}",
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
        content, usage, latency_ms = await self._send_gemini_request(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            temperature=temperature,
            max_tokens=max_tokens,
            require_json=False,
        )

        prompt_tokens = usage.get("promptTokenCount")
        comp_tokens = usage.get("candidatesTokenCount")
        total_tokens = usage.get("totalTokenCount")

        metadata = LLMResponseMetadata(
            provider=self.provider_name,
            model=self.model_name,
            task_name=task_name,
            prompt_tokens=prompt_tokens,
            completion_tokens=comp_tokens,
            total_tokens=total_tokens,
            latency_ms=latency_ms,
            secondary_used=True,
        )

        log_llm_execution(
            metadata=metadata,
            system_prompt_len=len(system_prompt),
            user_prompt_len=len(user_prompt),
            success=True,
        )
        return content
