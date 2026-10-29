"""Code-enforced Policy Gate for AI Agent Tool Execution (Developer A).

Enforces docs/AI_Agent_Rulebook.md Sections 1, 5, 6, and 7:
- The policy gate—not the model—decides whether tool calls are allowed.
- Unknown tools denied by default.
- Authorization and IDOR ownership validated outside the model.
- Strict Pydantic input argument validation before execution.
- Budget constraints enforced prior to invocation.
- Redacted audit event recorded for every execution.
"""

import asyncio
from typing import Any, Dict, Optional, Tuple
from pydantic import BaseModel, ValidationError

from app.agents.budgets import AgentExecutionBudget, BudgetLimitExceededError
from app.agents.tools.registry import ToolDefinition, ToolRegistry, get_tool_registry
from app.authorization.policies import assert_resource_owner
from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.logging import logger
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.auth import AuthenticatedPrincipal


class PolicyViolationError(Exception):
    """Raised when an agent tool call violates security policies or allowlist."""

    def __init__(self, message: str, code: str = "POLICY_VIOLATION") -> None:
        super().__init__(message)
        self.message = message
        self.code = code


class ToolNotAllowedError(PolicyViolationError):
    """Raised when an agent attempts to invoke an unregistered or forbidden tool."""

    def __init__(self, tool_name: str) -> None:
        super().__init__(
            message=f"Tool '{tool_name}' is not in the approved tool allowlist. Action denied.",
            code="UNKNOWN_TOOL_DENIED",
        )
        self.tool_name = tool_name


class MalformedToolArgumentsError(PolicyViolationError):
    """Raised when proposed tool arguments fail strict Pydantic schema validation."""

    def __init__(self, tool_name: str, errors: Any) -> None:
        super().__init__(
            message=f"Malformed arguments for tool '{tool_name}': {str(errors)}",
            code="MALFORMED_TOOL_ARGUMENTS",
        )
        self.tool_name = tool_name
        self.errors = errors


class PolicyGate:
    """Security policy gate intercepting and validating all agent tool calls."""

    def __init__(
        self,
        tool_registry: Optional[ToolRegistry] = None,
        resume_repo: Optional[IResumeRepository] = None,
    ) -> None:
        self._registry = tool_registry
        self._resume_repo = resume_repo

    @property
    def registry(self) -> ToolRegistry:
        return self._registry or get_tool_registry()

    @registry.setter
    def registry(self, value: Optional[ToolRegistry]) -> None:
        self._registry = value

    @property
    def resume_repo(self) -> IResumeRepository:
        return self._resume_repo or get_resume_repository()

    @resume_repo.setter
    def resume_repo(self, value: Optional[IResumeRepository]) -> None:
        self._resume_repo = value

    async def authorize_and_validate(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        principal: AuthenticatedPrincipal,
        budget: AgentExecutionBudget,
    ) -> Tuple[ToolDefinition, BaseModel]:
        """Authorize caller and validate arguments before executing any tool.

        Raises:
            ToolNotAllowedError: If tool is not registered in the allowlist.
            AuthorizationError / PolicyViolationError: If caller lacks permissions or violates IDOR boundaries.
            BudgetLimitExceededError: If execution budget is exhausted.
            MalformedToolArgumentsError: If arguments fail schema validation.
        """
        # 1. Budget Gate
        budget.check_budget()

        # 2. Allowlist Gate (Unknown tools denied by default)
        tool = self.registry.get_tool(tool_name)
        if tool is None:
            logger.warning(
                "Policy Gate: Unknown tool invocation blocked: tool=%s caller=%s",
                tool_name,
                principal.id,
            )
            raise ToolNotAllowedError(tool_name)

        # 3. Caller Active Account Gate
        if not principal.is_active:
            raise AuthorizationError("Account is inactive or disabled")

        # 3b. Role Permission Gate (Rulebook Section 5 & 16: Tool call without required permission is denied)
        if tool.required_permission == "jobs:ingest" and principal.role not in ("recruiter", "admin"):
            raise AuthorizationError(
                f"Permission denied: role '{principal.role}' is not authorized to invoke tool '{tool.name}' requiring '{tool.required_permission}'."
            )
        if tool.required_permission.startswith("admin:") and principal.role != "admin":
            raise AuthorizationError(
                f"Permission denied: role '{principal.role}' is not authorized to invoke tool '{tool.name}' requiring '{tool.required_permission}'."
            )


        # 4. Strict Pydantic Argument Validation Gate
        try:
            validated_args = tool.input_schema.model_validate(arguments)
        except ValidationError as exc:
            logger.warning(
                "Policy Gate: Malformed tool arguments rejected: tool=%s caller=%s errors=%s",
                tool_name,
                principal.id,
                exc.errors(),
            )
            raise MalformedToolArgumentsError(tool_name, exc.errors())

        # 5. Resource Ownership & IDOR Gate
        # If arguments contain candidate_id, verify that candidate profile belongs to caller
        candidate_id = getattr(validated_args, "candidate_id", None)
        if candidate_id:
            candidate = await self.resume_repo.get_by_id(candidate_id)
            if not candidate:
                candidate = await self.resume_repo.resolve_profile(
                    identifier=candidate_id,
                    user_id=principal.id if principal.role not in ("admin", "recruiter") else None,
                )
            if not candidate:
                raise NotFoundError(f"Candidate profile '{candidate_id}' not found.")

            # Enforce candidate isolation: Candidate can only inspect their own profile
            assert_resource_owner(
                resource_user_id=candidate.user_id,
                principal=principal,
                allow_admin=True,
                allowed_roles=("recruiter", "admin"),
            )

        return tool, validated_args

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        principal: AuthenticatedPrincipal,
        budget: AgentExecutionBudget,
    ) -> Any:
        """Run policy authorization, validate inputs, execute tool, and validate output schema."""
        # Authorize and validate arguments
        tool, validated_args = await self.authorize_and_validate(
            tool_name=tool_name,
            arguments=arguments,
            principal=principal,
            budget=budget,
        )

        # Increment tool invocation counter in budget
        budget.increment_tool_call()

        # Execute tool handler with timeout
        try:
            result = await asyncio.wait_for(
                tool.handler(validated_args),
                timeout=tool.timeout_seconds,
            )
        except asyncio.TimeoutError:
            logger.error("Tool execution timed out: tool=%s timeout=%.1fs", tool_name, tool.timeout_seconds)
            raise TimeoutError(f"Tool '{tool_name}' timed out after {tool.timeout_seconds} seconds.")

        # Validate result conforms to output schema
        if isinstance(result, BaseModel):
            validated_result = result
        elif isinstance(result, dict):
            validated_result = tool.output_schema.model_validate(result)
        else:
            validated_result = result

        # Redacted audit logging (No secrets or raw CV text logged)
        logger.info(
            "Agent Tool Executed: tool=%s caller=%s role=%s calls_count=%d",
            tool_name,
            principal.id,
            principal.role,
            budget.current_tool_calls,
        )

        return validated_result
