"""AI Agent package implementing bounded orchestration, allowlisted tools, and policy gates."""

from app.agents.budgets import AgentExecutionBudget, BudgetLimitExceededError
from app.agents.orchestrator import AgentOrchestrator
from app.agents.policy_gate import (
    MalformedToolArgumentsError,
    PolicyGate,
    PolicyViolationError,
    ToolNotAllowedError,
)
from app.agents.states import AgentState, AgentStateMachine, StateTransition
from app.agents.tools.registry import ToolDefinition, ToolRegistry, get_tool_registry
from app.agents.validator import AgentResponseValidator

__all__ = [
    "AgentExecutionBudget",
    "AgentOrchestrator",
    "AgentResponseValidator",
    "AgentState",
    "AgentStateMachine",
    "BudgetLimitExceededError",
    "MalformedToolArgumentsError",
    "PolicyGate",
    "PolicyViolationError",
    "StateTransition",
    "ToolDefinition",
    "ToolNotAllowedError",
    "ToolRegistry",
    "get_tool_registry",
]
