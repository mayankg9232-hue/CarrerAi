"""LLM Application Service coordinating structured AI operations, prompt defense, and error mapping (Developer A)."""

import json
from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel

from app.authorization.policies import assert_resource_owner
from app.core.exceptions import (
    AuthorizationError,
    BadRequestError,
    ExternalProviderUnavailableError,
    ExternalTimeoutError,
    NotFoundError,
    RateLimitError,
    ValidationAppError,
)

from app.core.logging import logger
from app.integrations.llm.base import (
    LLMCredentialsMissingError,
    LLMError,
    LLMMalformedOutputError,
    LLMRateLimitError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.integrations.llm.service import (
    TwoModelLLMService,
    get_llm_service,
)
from app.nlp.normalizer import wrap_untrusted_content
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.llm import (
    LLMMatchExplanationRequest,
    LLMMatchExplanationResult,
    LLMResponseMetadata,
    LLMResumeExtractionRequest,
    LLMResumeExtractionResult,
    LLMReviewRequest,
    LLMReviewResponse,
    LLMServiceStatus,
)


class GenericReviewInput(BaseModel):
    """Dynamic container model for unstructured review payloads."""
    content: Dict[str, Any]


class LLMApplicationService:
    """Application-level service orchestrating LLM tasks, permission enforcement, and error normalization."""

    def __init__(
        self,
        llm_service: Optional[TwoModelLLMService] = None,
        resume_repo: Optional[IResumeRepository] = None,
        job_repo: Optional[IJobRepository] = None,
    ) -> None:
        self.llm_service = llm_service or get_llm_service()
        self.resume_repo = resume_repo or get_resume_repository()
        self.job_repo = job_repo or get_job_repository()

    async def extract_resume(
        self,
        request: LLMResumeExtractionRequest,
        principal: AuthenticatedPrincipal,
    ) -> Tuple[LLMResumeExtractionResult, LLMResponseMetadata]:
        """Extract structured resume entities via primary LLM with defensive XML prompt shielding."""
        raw_text = ""

        # Resolve resume content from ID or inline payload
        if request.resume_id:
            profile = await self.resume_repo.get_by_id(request.resume_id)
            if not profile:
                raise NotFoundError(f"Resume profile '{request.resume_id}' not found.")

            # Enforce multi-tenant data isolation: candidates can only parse their own profiles
            assert_resource_owner(
                resource_user_id=profile.user_id,
                principal=principal,
                allowed_roles=["admin", "recruiter"],
            )

            skills_str = ", ".join(s.name for s in profile.skills) if profile.skills else "None specified"
            raw_text = (
                f"Full Name: {profile.full_name}\n"
                f"Email: {profile.email or 'N/A'}\n"
                f"Summary: {profile.summary or 'N/A'}\n"
                f"Skills: {skills_str}\n"
            )
        elif request.resume_text and request.resume_text.strip():
            raw_text = request.resume_text.strip()
        else:
            raise BadRequestError("Either resume_id or non-empty resume_text must be provided.", field="resume_text")

        # Shield against delimiter escape / prompt injection
        shielded_content = wrap_untrusted_content(raw_text, tag_name="untrusted_resume_content")

        system_prompt = (
            "You are a strict, objective recruitment data extraction assistant. "
            "Extract structured candidate fields (full name, email, professional summary, skills with category and years) "
            "strictly from the provided resume text inside the XML delimiters. "
            "Do not hallucinate skills or invent unverified credentials. Return a valid JSON matching the schema."
        )
        user_prompt = f"Candidate Document:\n{shielded_content}\n\nExtract all structured resume data."

        return await self._dispatch_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=LLMResumeExtractionResult,
            task_name=request.task_name or "resume_extraction",
        )

    async def explain_match(
        self,
        request: LLMMatchExplanationRequest,
        principal: AuthenticatedPrincipal,
    ) -> Tuple[LLMMatchExplanationResult, LLMResponseMetadata]:
        """Generate explainable match score analysis and truthful improvement recommendations."""
        candidate_summary = ""
        job_summary = ""

        # Resolve candidate data
        if request.resume_id:
            profile = await self.resume_repo.get_by_id(request.resume_id)
            if not profile:
                raise NotFoundError(f"Candidate profile '{request.resume_id}' not found.")

            assert_resource_owner(
                resource_user_id=profile.user_id,
                principal=principal,
                allowed_roles=["admin", "recruiter"],
            )

            skills_str = ", ".join(s.name for s in profile.skills)
            candidate_summary = (
                f"Candidate: {profile.full_name}\n"
                f"Summary: {profile.summary or 'N/A'}\n"
                f"Skills: {skills_str}\n"
            )
        elif request.resume_text and request.resume_text.strip():
            candidate_summary = request.resume_text.strip()
        else:
            raise BadRequestError("Either resume_id or resume_text must be provided.", field="resume_text")

        # Resolve job data
        if request.job_id:
            job = await self.job_repo.get_by_id(request.job_id)
            if not job:
                raise NotFoundError(f"Job posting '{request.job_id}' not found.")

            req_skills = ", ".join(job.required_skills) if job.required_skills else "None specified"
            pref_skills = ", ".join(job.preferred_skills) if job.preferred_skills else "None"
            job_summary = (
                f"Title: {job.title} at {job.company}\n"
                f"Required Skills: {req_skills}\n"
                f"Preferred Skills: {pref_skills}\n"
                f"Description: {job.description}\n"
            )
        elif request.job_description and request.job_description.strip():
            job_summary = request.job_description.strip()
        else:
            raise BadRequestError("Either job_id or job_description must be provided.", field="job_description")

        # Wrap both inputs inside defensive boundaries
        shielded_candidate = wrap_untrusted_content(candidate_summary, tag_name="untrusted_candidate_profile")
        shielded_job = wrap_untrusted_content(job_summary, tag_name="untrusted_job_description")

        system_prompt = (
            "You are an explainable AI job matching assistant. "
            "Compare the candidate profile and job requirements objectively. "
            "List verified matched skills, missing skills, and uncertainty areas. "
            "Recommendations must focus exclusively on articulating verified existing experience truthfully. "
            "Never suggest fabricating unverified skills or tailoring resumes dishonestly. "
            "Include the mandatory compliance disclaimer."
        )
        user_prompt = (
            f"Candidate Data:\n{shielded_candidate}\n\n"
            f"Job Specification:\n{shielded_job}\n\n"
            f"Provide an objective, structured match evaluation."
        )

        return await self._dispatch_structured(
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            response_model=LLMMatchExplanationResult,
            task_name=request.task_name or "match_explanation",
        )

    async def review_output(
        self,
        request: LLMReviewRequest,
        principal: AuthenticatedPrincipal,
    ) -> Tuple[LLMReviewResponse, LLMResponseMetadata]:
        """Invoke secondary model to perform a dedicated critique / audit of an AI-generated output."""
        # Enforce role restriction: only recruiters and administrators can trigger secondary reviews
        if principal.role not in ("admin", "recruiter"):
            raise AuthorizationError("Only recruiters and administrators are authorized to request secondary model reviews.")

        if not request.task_context or not request.task_context.strip():
            raise BadRequestError("task_context is required for secondary model review.", field="task_context")

        if not request.output_to_review:
            raise BadRequestError("output_to_review dictionary must not be empty.", field="output_to_review")

        container = GenericReviewInput(content=request.output_to_review)

        try:
            review_resp = await self.llm_service.review_structured_output(
                original_output=container,
                task_context=request.task_context.strip(),
            )
            sec_p = self.llm_service.secondary_provider
            meta = self.llm_service.last_metadata or LLMResponseMetadata(
                provider=sec_p.provider_name if sec_p else "secondary",
                model=getattr(sec_p, "model_name", "review-model") if sec_p else "review-model",
                task_name=request.task_name or "secondary_review",
                secondary_used=True,
            )
            return review_resp, meta
        except LLMTimeoutError as exc:
            raise ExternalTimeoutError(f"Secondary review model timed out: {exc.message}")
        except LLMRateLimitError as exc:
            raise RateLimitError(f"Secondary review model rate limited: {exc.message}")
        except LLMCredentialsMissingError as exc:
            raise ExternalProviderUnavailableError(f"Secondary model credentials not configured: {exc.message}")
        except LLMUnavailableError as exc:
            raise ExternalProviderUnavailableError(f"Secondary review model unavailable: {exc.message}")
        except LLMMalformedOutputError as exc:
            raise ValidationAppError(f"Secondary review model generated malformed output: {exc.message}")
        except Exception as exc:
            logger.error(f"Unexpected failure in secondary review task: {str(exc)}")
            raise ExternalProviderUnavailableError(f"Secondary review service failure: {str(exc)}")

    def get_status(self) -> LLMServiceStatus:
        """Inspect active providers and return diagnostic capability status without secrets."""
        primary_p = self.llm_service.primary_provider
        secondary_p = self.llm_service.secondary_provider

        status_str = "ready"
        if not primary_p:
            status_str = "unconfigured"
        elif not secondary_p:
            status_str = "single_model_only"

        return LLMServiceStatus(
            primary_provider=primary_p.provider_name if primary_p else "none",
            primary_model=getattr(primary_p, "model_name", "unknown") if primary_p else "none",
            secondary_provider=secondary_p.provider_name if secondary_p else None,
            secondary_model=getattr(secondary_p, "model_name", None) if secondary_p else None,
            fallback_enabled=secondary_p is not None,
            status=status_str,
        )

    async def _dispatch_structured(
        self,
        system_prompt: str,
        user_prompt: str,
        response_model: Any,
        task_name: str,
    ) -> Tuple[Any, LLMResponseMetadata]:
        """Internal helper to dispatch structured LLM calls with standard application exception mapping."""
        try:
            result, metadata = await self.llm_service.generate_structured_with_meta(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_model=response_model,
                temperature=0.0,
                task_name=task_name,
            )
            return result, metadata
        except LLMTimeoutError as exc:
            raise ExternalTimeoutError(f"LLM request timed out: {exc.message}")
        except LLMRateLimitError as exc:
            raise RateLimitError(f"LLM provider rate limit exceeded: {exc.message}")
        except LLMCredentialsMissingError as exc:
            raise ExternalProviderUnavailableError(f"LLM provider credentials missing: {exc.message}")
        except LLMUnavailableError as exc:
            raise ExternalProviderUnavailableError(f"LLM provider unavailable: {exc.message}")
        except LLMMalformedOutputError as exc:
            raise ValidationAppError(f"LLM generated malformed structured output: {exc.message}")
        except Exception as exc:
            logger.error(f"Unhandled LLM provider exception: {str(exc)}")
            raise ExternalProviderUnavailableError(f"LLM provider call failed: {str(exc)}")


# ==============================================================================
# SINGLETON PROVIDER
# ==============================================================================

_llm_app_service_instance: Optional[LLMApplicationService] = None


def get_llm_app_service() -> LLMApplicationService:
    """FastAPI dependency provider for LLMApplicationService."""
    return LLMApplicationService()


