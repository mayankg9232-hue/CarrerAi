"""Execution budgets and resource counters for AI agents (Developer A).

Enforces strict code-level execution budgets conforming to docs/AI_Agent_Rulebook.md Section 8:
- Maximum orchestration steps
- Maximum tool invocations
- Maximum wall-clock execution time
- Maximum re-planning attempts
"""

import time
from typing import Optional, Tuple
from pydantic import BaseModel, Field

from app.core.config import get_settings


class BudgetLimitExceededError(Exception):
    """Raised when an agent execution limit is exhausted."""

    def __init__(self, resource: str, limit: float, current: float) -> None:
        super().__init__(f"Agent budget exhausted for '{resource}': limit={limit}, current={current}")
        self.resource = resource
        self.limit = limit
        self.current = current


class AgentExecutionBudget(BaseModel):
    """Tracks and enforces step, call, and time budgets for an individual agent run."""

    max_steps: int = Field(default=8, description="Maximum orchestration steps allowed")
    max_tool_calls: int = Field(default=12, description="Maximum total tool calls allowed")
    timeout_seconds: float = Field(default=90.0, description="Maximum wall-clock execution time")
    max_replanning_attempts: int = Field(default=2, description="Maximum re-planning attempts after error")

    current_steps: int = 0
    current_tool_calls: int = 0
    current_replanning_attempts: int = 0
    start_time: float = Field(default_factory=time.perf_counter)

    @classmethod
    def from_settings(
        cls,
        max_steps: Optional[int] = None,
        max_tool_calls: Optional[int] = None,
        timeout_seconds: Optional[float] = None,
    ) -> "AgentExecutionBudget":
        """Instantiate budget using application settings or explicit overrides."""
        settings = get_settings()
        return cls(
            max_steps=max_steps or settings.AGENT_MAX_STEPS,
            max_tool_calls=max_tool_calls or settings.AGENT_MAX_TOOL_CALLS,
            timeout_seconds=timeout_seconds or float(settings.AGENT_TIMEOUT_SECONDS),
        )

    @property
    def elapsed_seconds(self) -> float:
        """Return elapsed wall-clock seconds since budget initialization."""
        return time.perf_counter() - self.start_time

    def check_budget(self) -> None:
        """Validate that all budgets are within permitted thresholds. Raises BudgetLimitExceededError if exhausted."""
        if self.elapsed_seconds >= self.timeout_seconds:
            raise BudgetLimitExceededError("time_seconds", self.timeout_seconds, round(self.elapsed_seconds, 2))

        if self.current_steps >= self.max_steps:
            raise BudgetLimitExceededError("steps", self.max_steps, self.current_steps)

        if self.current_tool_calls >= self.max_tool_calls:
            raise BudgetLimitExceededError("tool_calls", self.max_tool_calls, self.current_tool_calls)

    def is_exhausted(self) -> Tuple[bool, Optional[str]]:
        """Return (True, reason) if any budget threshold has been hit without raising exception."""
        if self.elapsed_seconds >= self.timeout_seconds:
            return True, f"Time budget exceeded ({round(self.elapsed_seconds, 1)}s >= {self.timeout_seconds}s)"
        if self.current_steps >= self.max_steps:
            return True, f"Step budget exceeded ({self.current_steps} >= {self.max_steps})"
        if self.current_tool_calls >= self.max_tool_calls:
            return True, f"Tool call budget exceeded ({self.current_tool_calls} >= {self.max_tool_calls})"
        return False, None

    def increment_step(self) -> None:
        """Record an orchestration step."""
        self.check_budget()
        self.current_steps += 1

    def increment_tool_call(self) -> None:
        """Record a tool invocation."""
        self.check_budget()
        self.current_tool_calls += 1

    def increment_replanning(self) -> None:
        """Record a re-planning attempt."""
        if self.current_replanning_attempts >= self.max_replanning_attempts:
            raise BudgetLimitExceededError("replanning_attempts", self.max_replanning_attempts, self.current_replanning_attempts)
        self.current_replanning_attempts += 1
