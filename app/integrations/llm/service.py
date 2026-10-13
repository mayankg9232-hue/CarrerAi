"""Two-model service orchestration implementing primary task execution, graceful fallback, and secondary review."""

from typing import Any, Dict, Optional, Tuple, Type, TypeVar
from pydantic import BaseModel

from app.core.config import Settings, get_settings
from app.core.logging import logger
from app.integrations.llm.base import (
    LLMCredentialsMissingError,
    LLMError,
    LLMMalformedOutputError,
    LLMProviderInterface,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.integrations.llm.mock_provider import MockLLMAdapter
from app.integrations.llm.primary_provider import PrimaryLLMAdapter
from app.integrations.llm.secondary_provider import SecondaryLLMAdapter
from app.schemas.llm import (
    LLMResponseMetadata,
    LLMReviewResponse,
    LLMReviewVerdict,
)

T = TypeVar("T", bound=BaseModel)


class TwoModelLLMService:
    """Orchestrates primary task execution, outage fallback, and secondary review workflows.

    Crucial Rule:
    The two-model pattern uses a primary model for defined tasks and a secondary model
    for limited review or outage fallback. Both models are NOT called automatically on
    routine successful requests.
    """

    def __init__(
        self,
        primary_provider: LLMProviderInterface,
        secondary_provider: Optional[LLMProviderInterface] = None,
    ) -> None:
        self.primary_provider = primary_provider
        self.secondary_provider = secondary_provider
        self.last_metadata: Optional[LLMResponseMetadata] = None

    async def generate_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2000,
        allow_fallback: bool = True,
        task_name: Optional[str] = None,
    ) -> T:
        """Execute structured generation using primary provider, falling back to secondary only on provider outage."""
        result, _ = await self.generate_structured_with_meta(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=response_model,
            temperature=temperature,
            max_tokens=max_tokens,
            allow_fallback=allow_fallback,
            task_name=task_name,
        )
        return result

    async def generate_structured_with_meta(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Type[T],
        temperature: float = 0.0,
        max_tokens: int = 2000,
        allow_fallback: bool = True,
        task_name: Optional[str] = None,
    ) -> Tuple[T, LLMResponseMetadata]:
        """Execute structured generation with metadata capture and fallback support."""
        try:
            result, metadata = await self.primary_provider.generate_structured_with_meta(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=temperature,
                max_tokens=max_tokens,
                task_name=task_name,
            )
            self.last_metadata = metadata
            return result, metadata

        except (LLMUnavailableError, LLMTimeoutError, LLMCredentialsMissingError) as exc:
            if not allow_fallback or self.secondary_provider is None:
                logger.warning(
                    f"Primary provider '{self.primary_provider.provider_name}' failed ({type(exc).__name__}), "
                    f"fallback disabled or secondary provider unavailable."
                )
                raise

            logger.warning(
                f"Primary provider '{self.primary_provider.provider_name}' experienced outage ({str(exc)}). "
                f"Initiating bounded fallback to secondary provider '{self.secondary_provider.provider_name}'."
            )

            try:
                result, sec_metadata = await self.secondary_provider.generate_structured_with_meta(
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    response_model=response_model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    task_name=f"{task_name or 'unnamed'}_fallback",
                )
                sec_metadata.fallback_triggered = True
                sec_metadata.secondary_used = True
                self.last_metadata = sec_metadata
                return result, sec_metadata
            except Exception as sec_exc:
                logger.error(
                    f"Secondary provider '{self.secondary_provider.provider_name}' also failed during fallback: {str(sec_exc)}"
                )
                raise LLMUnavailableError(
                    f"Both primary ({self.primary_provider.provider_name}) and secondary "
                    f"({self.secondary_provider.provider_name}) providers failed: {str(sec_exc)}"
                ) from sec_exc

    async def generate_text(
        self,
        system_prompt: str,
        user_prompt: str,
        temperature: float = 0.2,
        max_tokens: int = 1000,
        allow_fallback: bool = True,
        task_name: Optional[str] = None,
    ) -> str:
        """Generate unstructured text with optional fallback on provider outage."""
        try:
            return await self.primary_provider.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                task_name=task_name,
            )
        except (LLMUnavailableError, LLMTimeoutError, LLMCredentialsMissingError) as exc:
            if not allow_fallback or self.secondary_provider is None:
                raise

            logger.warning(
                f"Primary text generation failed ({str(exc)}). Falling back to secondary '{self.secondary_provider.provider_name}'."
            )
            return await self.secondary_provider.generate_text(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=temperature,
                max_tokens=max_tokens,
                task_name=f"{task_name or 'unnamed'}_fallback",
            )

    async def review_structured_output(
        self,
        original_output: BaseModel,
        task_context: str,
        review_model: Type[LLMReviewResponse] = LLMReviewResponse,
    ) -> LLMReviewResponse:
        """Invoke secondary provider to conduct an independent verification/critique of primary output.

        This is a dedicated review task, not called automatically on every request.
        """
        if self.secondary_provider is None:
            raise LLMUnavailableError("Secondary model provider is required for review tasks but is not configured.")

        system_prompt = (
            "You are an impartial secondary AI reviewer and quality gatekeeper. "
            "Analyze the candidate output against the provided source context. "
            "Detect hallucinations, fabricated credentials, inflated scores, or formatting errors. "
            "Return a structured evaluation."
        )
        user_prompt = (
            f"Context / Task:\n{task_context}\n\n"
            f"Output Under Review (JSON):\n{original_output.model_dump_json(indent=2)}"
        )

        return await self.secondary_provider.generate_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=review_model,
            temperature=0.0,
            max_tokens=1500,
        )


# ==============================================================================
# SINGLETON FACTORY AND LIFECYCLE MANAGEMENT
# ==============================================================================

_llm_service_instance: Optional[TwoModelLLMService] = None


def get_llm_service(settings: Optional[Settings] = None) -> TwoModelLLMService:
    """Return configured TwoModelLLMService singleton based on application settings."""
    global _llm_service_instance
    if _llm_service_instance is not None:
        return _llm_service_instance

    cfg = settings or get_settings()

    # Determine primary adapter
    primary_name = (cfg.PRIMARY_LLM_PROVIDER or "").lower()
    if primary_name == "mock" or cfg.APP_ENV == "testing":
        primary = MockLLMAdapter(provider_name="mock-primary", model_name=cfg.PRIMARY_LLM_MODEL or "mock-model")
    elif primary_name == "gemini":
        primary = SecondaryLLMAdapter(
            api_key=cfg.PRIMARY_LLM_API_KEY,
            model=cfg.PRIMARY_LLM_MODEL or "gemini-1.5-flash",
            provider_name="gemini-primary",
        )
    else:
        primary = PrimaryLLMAdapter(
            api_key=cfg.PRIMARY_LLM_API_KEY,
            model=cfg.PRIMARY_LLM_MODEL,
            provider_name=primary_name or "openai",
        )

    # Determine secondary adapter
    secondary_name = (cfg.SECONDARY_LLM_PROVIDER or "").lower()
    secondary: Optional[LLMProviderInterface] = None
    if secondary_name == "mock" or cfg.APP_ENV == "testing":
        secondary = MockLLMAdapter(provider_name="mock-secondary", model_name=cfg.SECONDARY_LLM_MODEL or "mock-secondary")
    elif secondary_name == "gemini":
        secondary = SecondaryLLMAdapter(
            api_key=cfg.SECONDARY_LLM_API_KEY,
            model=cfg.SECONDARY_LLM_MODEL or "gemini-1.5-flash",
            provider_name="gemini-secondary",
        )
    elif secondary_name:
        secondary = PrimaryLLMAdapter(
            api_key=cfg.SECONDARY_LLM_API_KEY,
            model=cfg.SECONDARY_LLM_MODEL,
            provider_name=secondary_name,
        )

    _llm_service_instance = TwoModelLLMService(primary_provider=primary, secondary_provider=secondary)
    return _llm_service_instance


def set_llm_service(service: Optional[TwoModelLLMService]) -> None:
    """Override LLM service singleton (used in test suites)."""
    global _llm_service_instance
    _llm_service_instance = service


def reset_llm_service() -> None:
    """Reset LLM service singleton to None."""
    global _llm_service_instance
    _llm_service_instance = None
