from enum import Enum
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class AgentState(str, Enum):
    """Execution lifecycle states conforming to AI Agent Rulebook."""

    RECEIVED = "RECEIVED"
    AUTHORIZED = "AUTHORIZED"
    PLANNED = "PLANNED"
    TOOL_CALL_VALIDATED = "TOOL_CALL_VALIDATED"
    EXECUTING = "EXECUTING"
    RESULT_VALIDATED = "RESULT_VALIDATED"
    NEXT_STEP = "NEXT_STEP"

    # Terminal states
    COMPLETE = "COMPLETE"
    DENIED = "DENIED"
    NEEDS_USER_INPUT = "NEEDS_USER_INPUT"
    FAILED = "FAILED"
    BUDGET_EXCEEDED = "BUDGET_EXCEEDED"


class AgentTaskRequest(BaseModel):
    """Request payload to initiate a bounded agent task."""

    task: str = Field(..., min_length=3, max_length=2000, description="User instruction or goal for agent")
    candidate_id: Optional[str] = Field(None, description="Candidate profile ID if task is candidate-scoped")
    job_id: Optional[str] = Field(None, description="Job ID if task is job-scoped")
    max_steps: Optional[int] = Field(None, ge=1, le=16, description="Optional override for maximum orchestration steps")


class AgentStepEvent(BaseModel):
    """Telemetry record for an individual step executed by the agent."""

    step_number: int = Field(..., description="Orchestration step index (1-based)")
    state: AgentState = Field(..., description="State reached during step")
    tool_name: Optional[str] = Field(None, description="Name of tool invoked, if any")
    tool_arguments: Optional[Dict[str, Any]] = Field(None, description="Sanitized arguments passed to tool")
    tool_result: Optional[Any] = Field(None, description="Summary or serialized result of tool")
    error: Optional[str] = Field(None, description="Error message if step or tool failed")
    duration_ms: float = Field(default=0.0, description="Execution duration of step in milliseconds")


class AgentTaskResponse(BaseModel):
    """Complete structured response returned by Agent Orchestrator."""

    task_id: str = Field(..., description="Unique agent run identifier")
    status: str = Field(..., description="High-level completion status (completed, budget_exceeded, denied, failed)")
    final_state: AgentState = Field(..., description="Final state machine state")
    final_answer: str = Field(..., description="Transparent, truthful answer or partial completion summary")
    steps: List[AgentStepEvent] = Field(default_factory=list, description="Telemetry sequence of execution steps")
    total_steps: int = Field(ge=0, description="Total steps executed")
    total_tool_calls: int = Field(ge=0, description="Total tool invocations made")
    duration_ms: float = Field(ge=0.0, description="Total wall-clock duration in milliseconds")
    disclaimer: str = Field(
        default="This match score is an automated estimate and does not guarantee an interview or employment offer.",
        description="Mandatory legal and truthfulness disclaimer required by rulebook",
    )


class ToolMetadata(BaseModel):
    """Descriptive metadata for registered allowlisted tools."""

    name: str = Field(..., description="Unique tool identifier")
    description: str = Field(..., description="Clear human-readable description of tool capabilities")
    required_permission: str = Field(..., description="Permission string required to execute this tool")
    input_schema: Dict[str, Any] = Field(default_factory=dict, description="JSON schema for tool arguments")
