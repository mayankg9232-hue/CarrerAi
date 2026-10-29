"""Agent Application Service enforcing IDOR boundaries, audit logging, and orchestration (Developer A)."""

import uuid
from typing import Any, List, Optional
from fastapi import Depends

from app.agents.tools.registry import ToolDefinition, ToolRegistry, get_tool_registry
from app.authorization.policies import assert_resource_owner
from app.core.exceptions import NotFoundError
from app.core.logging import logger
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.agent import (
    AgentTaskRequest,
    AgentTaskResponse,
    ToolMetadata,
)
from app.schemas.auth import AuthenticatedPrincipal


class AgentApplicationService:
    """Application-level service orchestrating agent execution, caller authorization, and IDOR isolation."""

    def __init__(
        self,
        orchestrator: Optional[Any] = None,
        tool_registry: Optional[ToolRegistry] = None,
        resume_repo: Optional[IResumeRepository] = None,
    ) -> None:
        if orchestrator is None:
            from app.agents.orchestrator import AgentOrchestrator
            orchestrator = AgentOrchestrator()
        self.orchestrator = orchestrator
        self.tool_registry = tool_registry or get_tool_registry()
        self.resume_repo = resume_repo or get_resume_repository()

    async def run_task(
        self,
        request: AgentTaskRequest,
        principal: AuthenticatedPrincipal,
    ) -> AgentTaskResponse:
        """Authorize and run bounded agent workflow with candidate isolation."""
        candidate = None

        # 1. Authoritative active resume resolution for candidates (prevents stale profile data)
        if principal.role == "candidate":
            active_resume = await self.resume_repo.get_active_resume(principal.id)
            if active_resume:
                if active_resume.status == "processing":
                    return AgentTaskResponse(
                        task_id=f"task_{uuid.uuid4().hex[:12]}",
                        status="completed",
                        final_state="COMPLETE",
                        final_answer=(
                            f"Your resume ('{active_resume.filename}') is currently being processed and indexed. "
                            "Please allow a few moments for skill extraction to finish, then ask again or refresh your workspace."
                        ),
                        steps=[],
                        total_steps=0,
                        total_tool_calls=0,
                        duration_ms=10.0,
                    )
                if active_resume.status == "completed" and active_resume.profile_id:
                    active_profile = await self.resume_repo.get_by_id(active_resume.profile_id)
                    if active_profile:
                        candidate = active_profile
                        request.candidate_id = active_profile.id

        # 2. Resolve candidate profile if candidate_id provided or for recruiters/admins
        if not candidate and request.candidate_id:
            candidate = await self.resume_repo.resolve_profile(
                identifier=request.candidate_id,
                user_id=principal.id if principal.role not in ("admin", "recruiter") else None,
            )
            if candidate:
                assert_resource_owner(
                    resource_user_id=candidate.user_id,
                    principal=principal,
                    allow_admin=True,
                    allowed_roles=("recruiter", "admin"),
                )
                request.candidate_id = candidate.id
            else:
                # Check for explicit IDOR access to another candidate's profile
                direct_prof = await self.resume_repo.get_by_id(request.candidate_id)
                if direct_prof and direct_prof.user_id != principal.id:
                    assert_resource_owner(
                        resource_user_id=direct_prof.user_id,
                        principal=principal,
                        allow_admin=True,
                        allowed_roles=("recruiter", "admin"),
                    )

        # Contextual resolution if caller is candidate and profile wasn't found by explicit id
        if not candidate and principal.role == "candidate":
            active_resume = await self.resume_repo.get_active_resume(principal.id) if self.resume_repo else None
            if active_resume and active_resume.status == "processing":
                return AgentTaskResponse(
                    task_id=f"task_{uuid.uuid4().hex[:12]}",
                    status="completed",
                    final_state="COMPLETE",
                    final_answer=(
                        f"Your resume ('{active_resume.filename}') is currently being processed. "
                        "Please allow a few moments for skill extraction to finish, then ask again or refresh your workspace."
                    ),
                    steps=[],
                    total_steps=0,
                    total_tool_calls=0,
                    duration_ms=0.0,
                )
            candidate = await self.resume_repo.resolve_profile(user_id=principal.id)
            if candidate:
                request.candidate_id = candidate.id

        # 2. Determine if the task strictly requires an extracted candidate profile
        task_lower = request.task.lower().strip()
        is_explicit_match_intent = any(
            kw in task_lower for kw in [
                "compare my profile", "match my profile", "evaluate match", "run match",
                "match candidate to job", "calculate match", "calculate alignment", "check fit", "match to job"
            ]
        )
        is_resume_tool_intent = any(
            phrase in task_lower for phrase in [
                "resume improvement", "improve resume", "tailor resume", "resume suggestion",
                "truthful resume", "truthful suggestion", "resume feedback", "resume advice"
            ]
        )
        requires_candidate_profile = (request.job_id is not None and (is_explicit_match_intent or is_resume_tool_intent)) or (
            any(w in task_lower for w in ["match", "compare"]) and any(w in task_lower for w in ["jobs", "openings", "roles"])
        )

        if not candidate and requires_candidate_profile:
            active_resume = await self.resume_repo.get_active_resume(principal.id)
            if active_resume and active_resume.status == "processing":
                return AgentTaskResponse(
                    task_id=f"task_{uuid.uuid4().hex[:12]}",
                    status="completed",
                    final_state="COMPLETE",
                    final_answer=(
                        f"Your resume ('{active_resume.filename}') is currently being processed. "
                        "Please allow a few moments for skill extraction to finish, then ask again or refresh your workspace."
                    ),
                    steps=[],
                    total_steps=0,
                    total_tool_calls=0,
                    duration_ms=0.0,
                    budget_used={"steps": 0, "tool_calls": 0},
                )
            elif active_resume and active_resume.status == "failed":
                err_info = f": {active_resume.error_message}" if active_resume.error_message else ""
                return AgentTaskResponse(
                    task_id=f"task_{uuid.uuid4().hex[:12]}",
                    status="completed",
                    final_state="COMPLETE",
                    final_answer=(
                        f"Resume processing failed for '{active_resume.filename}'{err_info}. "
                        "Please retry processing or upload a new PDF resume in the Resume section."
                    ),
                    steps=[],
                    total_steps=0,
                    total_tool_calls=0,
                    duration_ms=0.0,
                    budget_used={"steps": 0, "tool_calls": 0},
                )
            else:
                return AgentTaskResponse(
                    task_id=f"task_{uuid.uuid4().hex[:12]}",
                    status="completed",
                    final_state="COMPLETE",
                    final_answer=(
                        "No candidate profile found. Please upload your resume in the Resume section first "
                        "so I can analyze your skills, suggest tailored improvements, and calculate job match scores."
                    ),
                    steps=[],
                    total_steps=0,
                    total_tool_calls=0,
                    duration_ms=0.0,
                    budget_used={"steps": 0, "tool_calls": 0},
                )

        if not candidate:
            # Task is general career advice/greeting - clear candidate_id to allow conversational response
            request.candidate_id = None

        # Execute bounded workflow through state-machine orchestrator with verified candidate profile
        response = await self.orchestrator.execute_task(
            request=request,
            principal=principal,
            candidate_profile=candidate,
        )

        # Redacted audit logging
        logger.info(
            "Agent Workflow Completed: task_id=%s status=%s steps=%d calls=%d duration_ms=%.1f caller=%s role=%s",
            response.task_id,
            response.status,
            response.total_steps,
            response.total_tool_calls,
            response.duration_ms,
            principal.id,
            principal.role,
        )

        return response

    async def run_public_chat(
        self,
        request: AgentTaskRequest,
    ) -> AgentTaskResponse:
        """Execute unauthenticated public career advice workflow without access to candidate tools."""
        from app.core.exceptions import AuthorizationError
        if request.candidate_id:
            raise AuthorizationError("Candidate profile access requires authentication.")

        anonymous_principal = AuthenticatedPrincipal(
            id="usr_public_guest",
            email="guest@public.career",
            role="candidate",
            is_active=True,
        )
        return await self.orchestrator.execute_task(request=request, principal=anonymous_principal)

    def list_allowlisted_tools(self) -> List[ToolMetadata]:
        """Return metadata and schemas for all allowlisted tools."""
        tools = self.tool_registry.list_tools()
        result: List[ToolMetadata] = []
        for t in tools:
            schema_json = t.input_schema.model_json_schema() if hasattr(t.input_schema, "model_json_schema") else {}
            result.append(
                ToolMetadata(
                    name=t.name,
                    description=t.description,
                    required_permission=t.required_permission,
                    input_schema=schema_json,
                )
            )
        return result


def get_agent_app_service(
    resume_repo: IResumeRepository = Depends(get_resume_repository),
) -> AgentApplicationService:
    """Dependency provider for AgentApplicationService."""
    return AgentApplicationService(resume_repo=resume_repo)
