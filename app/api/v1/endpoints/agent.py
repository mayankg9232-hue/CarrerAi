"""Agentic AI workflow and tool introspection endpoints (Phase 6 Developer A)."""

from typing import List
from fastapi import APIRouter, Body, Depends, status

from app.authentication.dependencies import get_current_user, require_verified_user
from app.schemas.agent import (
    AgentTaskRequest,
    AgentTaskResponse,
    ToolMetadata,
)
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.common import StandardResponse
from app.services.agent_service import (
    AgentApplicationService,
    get_agent_app_service,
)

router = APIRouter()


@router.post(
    "/run",
    response_model=StandardResponse[AgentTaskResponse],
    status_code=status.HTTP_200_OK,
    summary="Execute bounded agent task",
    description="Orchestrates multi-step agent workflow with allowlisted tools, strict policy gate, and budget enforcement.",
)
async def run_agent_task(
    payload: AgentTaskRequest = Body(..., embed=False),
    current_user: AuthenticatedPrincipal = Depends(require_verified_user),
    service: AgentApplicationService = Depends(get_agent_app_service),
) -> StandardResponse[AgentTaskResponse]:
    """Execute bounded, state-machine driven agent workflow for candidate or job task."""
    result = await service.run_task(request=payload, principal=current_user)
    return StandardResponse(
        data=result,
        message=f"Agent workflow concluded with status '{result.status}'.",
    )


@router.post(
    "/public-chat",
    response_model=StandardResponse[AgentTaskResponse],
    status_code=status.HTTP_200_OK,
    summary="Public conversational career assistant",
    description="Permits unauthenticated general career questions and learning roadmaps without candidate tools.",
)
async def public_agent_chat(
    payload: AgentTaskRequest = Body(..., embed=False),
    service: AgentApplicationService = Depends(get_agent_app_service),
) -> StandardResponse[AgentTaskResponse]:
    """Execute public career advice and roadmapping without access to private candidate tools."""
    result = await service.run_public_chat(request=payload)
    return StandardResponse(
        data=result,
        message="Public career guidance generated successfully.",
    )


@router.get(
    "/tools",
    response_model=StandardResponse[List[ToolMetadata]],
    status_code=status.HTTP_200_OK,
    summary="List allowlisted agent tools",
    description="Inspect registered allowlisted tools, required permissions, and argument schemas.",
)
async def list_agent_tools(
    current_user: AuthenticatedPrincipal = Depends(get_current_user),
    service: AgentApplicationService = Depends(get_agent_app_service),
) -> StandardResponse[List[ToolMetadata]]:
    """Return all allowlisted tools available to the agent."""
    tools = service.list_allowlisted_tools()
    return StandardResponse(
        data=tools,
        message=f"Retrieved {len(tools)} allowlisted tools.",
    )
