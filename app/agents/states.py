"""Explicit finite state machine and transitions for AI agent orchestration (Developer A).

Follows docs/AI_Agent_Rulebook.md Section 9:
RECEIVED -> AUTHORIZED -> PLANNED -> TOOL_CALL_VALIDATED -> EXECUTING -> RESULT_VALIDATED -> (NEXT_STEP or COMPLETE)
Terminal states: COMPLETE, DENIED, NEEDS_USER_INPUT, FAILED, BUDGET_EXCEEDED.
"""

from typing import List, Optional, Set
from pydantic import BaseModel, Field

from app.schemas.agent import AgentState


class StateTransition(BaseModel):
    """Audit record capturing an explicit state transition in the agent lifecycle."""

    from_state: AgentState
    to_state: AgentState
    reason: Optional[str] = None
    timestamp: float = Field(default_factory=lambda: 0.0)


# Valid allowed state transitions (Policy-enforced state machine)
VALID_TRANSITIONS: dict[AgentState, Set[AgentState]] = {
    AgentState.RECEIVED: {AgentState.AUTHORIZED, AgentState.DENIED, AgentState.FAILED},
    AgentState.AUTHORIZED: {AgentState.PLANNED, AgentState.DENIED, AgentState.FAILED, AgentState.BUDGET_EXCEEDED},
    AgentState.PLANNED: {
        AgentState.TOOL_CALL_VALIDATED,
        AgentState.COMPLETE,
        AgentState.NEEDS_USER_INPUT,
        AgentState.FAILED,
        AgentState.BUDGET_EXCEEDED,
    },
    AgentState.TOOL_CALL_VALIDATED: {
        AgentState.EXECUTING,
        AgentState.DENIED,
        AgentState.FAILED,
        AgentState.BUDGET_EXCEEDED,
    },
    AgentState.EXECUTING: {
        AgentState.RESULT_VALIDATED,
        AgentState.FAILED,
        AgentState.BUDGET_EXCEEDED,
    },
    AgentState.RESULT_VALIDATED: {
        AgentState.NEXT_STEP,
        AgentState.COMPLETE,
        AgentState.NEEDS_USER_INPUT,
        AgentState.FAILED,
        AgentState.BUDGET_EXCEEDED,
    },
    AgentState.NEXT_STEP: {
        AgentState.PLANNED,
        AgentState.TOOL_CALL_VALIDATED,
        AgentState.BUDGET_EXCEEDED,
        AgentState.COMPLETE,
        AgentState.FAILED,
    },
    # Terminal states have no outbound transitions
    AgentState.COMPLETE: set(),
    AgentState.DENIED: set(),
    AgentState.NEEDS_USER_INPUT: set(),
    AgentState.FAILED: set(),
    AgentState.BUDGET_EXCEEDED: set(),
}


class AgentStateMachine:
    """Manages predictable agent state transitions and prevents illegal state bypasses."""

    def __init__(self, initial_state: AgentState = AgentState.RECEIVED) -> None:
        self.current_state: AgentState = initial_state
        self.history: List[StateTransition] = []

    @property
    def is_terminal(self) -> bool:
        """Return True if the current state is a terminal stopping state."""
        return self.current_state in {
            AgentState.COMPLETE,
            AgentState.DENIED,
            AgentState.NEEDS_USER_INPUT,
            AgentState.FAILED,
            AgentState.BUDGET_EXCEEDED,
        }

    def transition_to(self, target_state: AgentState, reason: Optional[str] = None, timestamp: float = 0.0) -> None:
        """Transition to new state if permitted by the state machine policy."""
        if self.is_terminal:
            return

        allowed = VALID_TRANSITIONS.get(self.current_state, set())
        if target_state not in allowed:
            # Force transition to FAILED if illegal transition attempted
            transition = StateTransition(
                from_state=self.current_state,
                to_state=AgentState.FAILED,
                reason=f"Illegal state transition attempted: {self.current_state} -> {target_state}",
                timestamp=timestamp,
            )
            self.history.append(transition)
            prev_state = self.current_state
            self.current_state = AgentState.FAILED
            raise ValueError(f"Illegal transition: {prev_state} -> {target_state}")

        transition = StateTransition(
            from_state=self.current_state,
            to_state=target_state,
            reason=reason,
            timestamp=timestamp,
        )
        self.history.append(transition)
        self.current_state = target_state
