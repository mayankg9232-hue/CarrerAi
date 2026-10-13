"""Agent Application Service enforcing IDOR boundaries, audit logging, and orchestration (Developer A)."""

from typing import List, Optional

from app.agents.orchestrator import AgentOrchestrator
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
        orchestrator: Optional[AgentOrchestrator] = None,
        tool_registry: Optional[ToolRegistry] = None,
        resume_repo: Optional[IResumeRepository] = None,
    ) -> None:
        self.orchestrator = orchestrator or AgentOrchestrator()
        self.tool_registry = tool_registry or get_tool_registry()
        self.resume_repo = resume_repo or get_resume_repository()

    async def run_task(
        self,
        request: AgentTaskRequest,
        principal: AuthenticatedPrincipal,
    ) -> AgentTaskResponse:
        """Authorize and run bounded agent workflow with candidate isolation."""
        # Enforce IDOR protection if task is candidate-scoped
        if request.candidate_id:
            candidate = await self.resume_repo.get_by_id(request.candidate_id)
            if not candidate:
                raise NotFoundError(f"Candidate profile '{request.candidate_id}' not found.")

            assert_resource_owner(
                resource_user_id=candidate.user_id,
                principal=principal,
                allow_admin=True,
                allowed_roles=("recruiter", "admin"),
            )

        # Execute bounded workflow through state-machine orchestrator
        response = await self.orchestrator.execute_task(request=request, principal=principal)

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


def get_agent_app_service() -> AgentApplicationService:
    """Dependency provider for AgentApplicationService."""
    return AgentApplicationService()
