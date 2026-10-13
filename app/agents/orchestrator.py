"""Agent Orchestrator with explicit state machine, policy gate, and budget enforcement (Developer A).

Implements docs/AI_Job_Matcher_Phases_and_Structure.md Phase 6:
- Explicit finite state machine:
  RECEIVED -> AUTHORIZED -> PLANNED -> TOOL_CALL_VALIDATED -> EXECUTING -> RESULT_VALIDATED -> (NEXT_STEP or COMPLETE)
- Allowlisted tool registry
- Strict policy gate outside model control
- Step/tool/time budget tracker
- Prompt-injection defense on untrusted CV/job data
- Response truthfulness and disclaimer validator
"""

import time
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel

from app.agents.budgets import AgentExecutionBudget, BudgetLimitExceededError
from app.agents.policy_gate import (
    MalformedToolArgumentsError,
    PolicyGate,
    PolicyViolationError,
    ToolNotAllowedError,
)
from app.agents.states import AgentState, AgentStateMachine
from app.agents.tools.registry import ToolRegistry, get_tool_registry
from app.agents.validator import AgentResponseValidator
from app.core.exceptions import AuthorizationError, NotFoundError
from app.core.logging import logger
from app.integrations.llm.service import TwoModelLLMService, get_llm_service
from app.nlp.normalizer import wrap_untrusted_content
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.agent import (
    AgentStepEvent,
    AgentTaskRequest,
    AgentTaskResponse,
)
from app.schemas.auth import AuthenticatedPrincipal
from app.schemas.matching import MatchResult, RecommendedMatchesResponse
from app.schemas.resume import CandidateProfile


class AgentOrchestrator:
    """Bounded, state-machine driven orchestrator coordinating multi-step agent tasks."""

    def __init__(
        self,
        policy_gate: Optional[PolicyGate] = None,
        tool_registry: Optional[ToolRegistry] = None,
        llm_service: Optional[TwoModelLLMService] = None,
        resume_repo: Optional[IResumeRepository] = None,
        job_repo: Optional[IJobRepository] = None,
        validator: Optional[AgentResponseValidator] = None,
    ) -> None:
        self.tool_registry = tool_registry or get_tool_registry()
        self.resume_repo = resume_repo or get_resume_repository()
        self.job_repo = job_repo or get_job_repository()
        self.policy_gate = policy_gate or PolicyGate(
            tool_registry=self.tool_registry,
            resume_repo=self.resume_repo,
        )
        self.llm_service = llm_service or get_llm_service()
        self.validator = validator or AgentResponseValidator()

    async def execute_task(
        self,
        request: AgentTaskRequest,
        principal: AuthenticatedPrincipal,
    ) -> AgentTaskResponse:
        """Execute agent task through explicit state transitions and policy-enforced tool execution."""
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        start_time = time.perf_counter()
        state_machine = AgentStateMachine(initial_state=AgentState.RECEIVED)
        budget = AgentExecutionBudget.from_settings(max_steps=request.max_steps)
        step_events: List[AgentStepEvent] = []
        collected_results: List[Any] = []
        final_answer = ""

        try:
            # 1. State: RECEIVED -> AUTHORIZED
            state_machine.transition_to(
                AgentState.AUTHORIZED,
                reason="Task received and caller authenticated",
                timestamp=time.perf_counter() - start_time,
            )

            # If caller is inactive, deny immediately
            if not principal.is_active:
                state_machine.transition_to(AgentState.DENIED, reason="Inactive account")
                return self._build_response(
                    task_id=task_id,
                    status="denied",
                    final_state=AgentState.DENIED,
                    final_answer="Access denied: account is inactive.",
                    steps=step_events,
                    budget=budget,
                    start_time=start_time,
                )

            # Check budget before planning
            budget.check_budget()

            # 2. State: AUTHORIZED -> PLANNED
            planned_steps = self._plan_task(request, principal)
            state_machine.transition_to(
                AgentState.PLANNED,
                reason=f"Generated plan with {len(planned_steps)} steps",
                timestamp=time.perf_counter() - start_time,
            )

            # 3. Step Execution Loop
            for step_idx, (planned_tool, planned_args) in enumerate(planned_steps):
                # Check budget prior to each step
                exhausted, reason = budget.is_exhausted()
                if exhausted:
                    state_machine.transition_to(AgentState.BUDGET_EXCEEDED, reason=reason)
                    final_answer = f"Task stopped: {reason}. Partial results delivered."
                    break

                step_start = time.perf_counter()
                budget.increment_step()

                # State: PLANNED (or NEXT_STEP) -> TOOL_CALL_VALIDATED
                state_machine.transition_to(
                    AgentState.TOOL_CALL_VALIDATED,
                    reason=f"Validating arguments for tool '{planned_tool}'",
                    timestamp=time.perf_counter() - start_time,
                )

                # State: TOOL_CALL_VALIDATED -> EXECUTING
                state_machine.transition_to(
                    AgentState.EXECUTING,
                    reason=f"Executing tool '{planned_tool}' via PolicyGate",
                    timestamp=time.perf_counter() - start_time,
                )

                try:
                    tool_output = await self.policy_gate.execute_tool(
                        tool_name=planned_tool,
                        arguments=planned_args,
                        principal=principal,
                        budget=budget,
                    )
                    collected_results.append(tool_output)

                    # State: EXECUTING -> RESULT_VALIDATED
                    state_machine.transition_to(
                        AgentState.RESULT_VALIDATED,
                        reason=f"Tool '{planned_tool}' output validated against schema",
                        timestamp=time.perf_counter() - start_time,
                    )

                    duration_ms = (time.perf_counter() - step_start) * 1000
                    step_events.append(
                        AgentStepEvent(
                            step_number=budget.current_steps,
                            state=AgentState.RESULT_VALIDATED,
                            tool_name=planned_tool,
                            tool_arguments=planned_args,
                            tool_result=self._serialize_tool_result(tool_output),
                            duration_ms=round(duration_ms, 2),
                        )
                    )

                    # Check if more planned steps remain
                    if step_idx + 1 < len(planned_steps):
                        if budget.current_steps >= budget.max_steps:
                            state_machine.transition_to(
                                AgentState.BUDGET_EXCEEDED,
                                reason=f"Step quota reached ({budget.current_steps}/{budget.max_steps})",
                                timestamp=time.perf_counter() - start_time,
                            )
                            final_answer = f"Agent budget exhausted: Step limit reached ({budget.current_steps}/{budget.max_steps}). Safe partial results returned."
                            break

                        state_machine.transition_to(
                            AgentState.NEXT_STEP,
                            reason="Proceeding to next planned step",
                            timestamp=time.perf_counter() - start_time,
                        )

                except ToolNotAllowedError as exc:
                    state_machine.transition_to(AgentState.DENIED, reason=exc.message)
                    step_events.append(
                        AgentStepEvent(
                            step_number=budget.current_steps,
                            state=AgentState.DENIED,
                            tool_name=planned_tool,
                            tool_arguments=planned_args,
                            error=exc.message,
                        )
                    )
                    final_answer = f"Execution blocked by Policy Gate: {exc.message}"
                    break

                except MalformedToolArgumentsError as exc:
                    state_machine.transition_to(AgentState.FAILED, reason=exc.message)
                    step_events.append(
                        AgentStepEvent(
                            step_number=budget.current_steps,
                            state=AgentState.FAILED,
                            tool_name=planned_tool,
                            tool_arguments=planned_args,
                            error=exc.message,
                        )
                    )
                    final_answer = f"Execution failed: {exc.message}"
                    break

                except AuthorizationError as exc:
                    state_machine.transition_to(AgentState.DENIED, reason=str(exc))
                    step_events.append(
                        AgentStepEvent(
                            step_number=budget.current_steps,
                            state=AgentState.DENIED,
                            tool_name=planned_tool,
                            tool_arguments=planned_args,
                            error=str(exc),
                        )
                    )
                    final_answer = f"Access denied: {str(exc)}"
                    break

                except NotFoundError as exc:
                    state_machine.transition_to(AgentState.FAILED, reason=str(exc))
                    step_events.append(
                        AgentStepEvent(
                            step_number=budget.current_steps,
                            state=AgentState.FAILED,
                            tool_name=planned_tool,
                            tool_arguments=planned_args,
                            error=str(exc),
                        )
                    )
                    final_answer = f"Resource not found: {str(exc)}"
                    break

            # 4. Final Answer Synthesis & Validation
            if not final_answer:
                final_answer = self._synthesize_final_answer(request, collected_results)

            if not state_machine.is_terminal:
                state_machine.transition_to(
                    AgentState.COMPLETE,
                    reason="All planned steps executed successfully",
                    timestamp=time.perf_counter() - start_time,
                )

            # Sanitize and enforce truthfulness/disclaimer
            sanitized_answer = self.validator.validate_and_sanitize(final_answer, collected_results)

            status = "completed" if state_machine.current_state == AgentState.COMPLETE else state_machine.current_state.value.lower()

            return self._build_response(
                task_id=task_id,
                status=status,
                final_state=state_machine.current_state,
                final_answer=sanitized_answer,
                steps=step_events,
                budget=budget,
                start_time=start_time,
            )

        except BudgetLimitExceededError as exc:
            if not state_machine.is_terminal:
                state_machine.transition_to(AgentState.BUDGET_EXCEEDED, reason=str(exc))
            return self._build_response(
                task_id=task_id,
                status="budget_exceeded",
                final_state=state_machine.current_state,
                final_answer=f"Agent budget exhausted: {str(exc)}. Safe partial result returned.",
                steps=step_events,
                budget=budget,
                start_time=start_time,
            )

        except Exception as exc:
            logger.error("Agent unhandled failure: %s", str(exc), exc_info=True)
            if not state_machine.is_terminal:
                state_machine.transition_to(AgentState.FAILED, reason=str(exc))
            return self._build_response(
                task_id=task_id,
                status="failed",
                final_state=AgentState.FAILED,
                final_answer=f"Agent workflow encountered an unexpected error: {str(exc)}",
                steps=step_events,
                budget=budget,
                start_time=start_time,
            )

    def _plan_task(self, request: AgentTaskRequest, principal: AuthenticatedPrincipal) -> List[tuple[str, dict]]:
        """Formulate bounded tool execution plan based on task instructions and context."""
        task_lower = request.task.lower()
        planned: List[tuple[str, dict]] = []

        # 1. Candidate-Job Match Workflow
        if request.candidate_id and request.job_id:
            planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
            planned.append(("get_job_details", {"job_id": request.job_id}))
            planned.append(("match_candidate_to_job", {"candidate_id": request.candidate_id, "job_id": request.job_id}))
            return planned

        # 2. Recommendations Workflow
        if request.candidate_id and ("recommend" in task_lower or "find" in task_lower or "jobs" in task_lower):
            planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
            planned.append(("recommend_jobs_for_candidate", {"candidate_id": request.candidate_id, "min_score": 50.0, "limit": 5}))
            return planned

        # 3. Skill Gap Analysis
        if request.candidate_id and "gap" in task_lower:
            planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
            if request.job_id:
                planned.append(("analyze_skill_gaps", {"candidate_id": request.candidate_id, "job_id": request.job_id}))
            return planned

        # 4. Profile Facts Retrieval
        if request.candidate_id:
            planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
            return planned

        # 5. Job Search
        if "search" in task_lower or "job" in task_lower:
            planned.append(("query_jobs", {"q": request.task[:50], "limit": 5}))
            return planned

        # Default: Search active jobs
        planned.append(("query_jobs", {"limit": 5}))
        return planned

    def _serialize_tool_result(self, result: Any) -> Any:
        """Sanitize and serialize tool output for telemetry reporting."""
        if isinstance(result, MatchResult):
            return {
                "candidate_id": result.candidate_id,
                "job_id": result.job_id,
                "overall_score": result.overall_score,
                "matched_skills": result.matched_skills,
                "missing_skills": result.missing_skills,
                "explanation_excerpt": result.explanation[:120],
            }
        if isinstance(result, CandidateProfile):
            return {
                "id": result.id,
                "full_name": result.full_name,
                "skills_count": len(result.skills),
                "skills": [s.name for s in result.skills[:5]],
            }
        if isinstance(result, RecommendedMatchesResponse):
            return {
                "candidate_id": result.candidate_id,
                "total_evaluated": result.total_evaluated,
                "matches_count": len(result.matches),
                "top_score": result.matches[0].overall_score if result.matches else 0.0,
            }
        if hasattr(result, "model_dump"):
            return result.model_dump()
        return str(result)[:200]

    def _synthesize_final_answer(self, request: AgentTaskRequest, results: List[Any]) -> str:
        """Construct grounded, factual summary of executed tool results."""
        if not results:
            return "Task completed. No matching records or results were found."

        parts: List[str] = []
        for r in results:
            if isinstance(r, MatchResult):
                parts.append(
                    f"Match Evaluation for Candidate {r.candidate_id} and Job {r.job_id}:\n"
                    f"- Overall Alignment Score: {r.overall_score:.1f}/100\n"
                    f"- Matched Skills: {', '.join(r.matched_skills) if r.matched_skills else 'None'}\n"
                    f"- Missing Skills: {', '.join(r.missing_skills) if r.missing_skills else 'None'}\n"
                    f"- Rationale: {r.explanation}"
                )
            elif isinstance(r, RecommendedMatchesResponse):
                top_jobs = [f"{m.job_id} ({m.overall_score:.1f}%)" for m in r.matches[:3]]
                parts.append(
                    f"Recommended Jobs for Candidate {r.candidate_id}:\n"
                    f"- Top Matches: {', '.join(top_jobs) if top_jobs else 'No jobs exceeded threshold'}\n"
                    f"- Total Evaluated: {r.total_evaluated} active listings."
                )
            elif isinstance(r, CandidateProfile):
                skills_list = ", ".join([s.name for s in r.skills[:5]])
                parts.append(f"Candidate Profile facts retrieved for {r.full_name}: Skills include {skills_list}.")

        return "\n\n".join(parts) if parts else "Task execution completed successfully."

    def _build_response(
        self,
        task_id: str,
        status: str,
        final_state: AgentState,
        final_answer: str,
        steps: List[AgentStepEvent],
        budget: AgentExecutionBudget,
        start_time: float,
    ) -> AgentTaskResponse:
        """Construct final structured response envelope."""
        duration_ms = (time.perf_counter() - start_time) * 1000
        return AgentTaskResponse(
            task_id=task_id,
            status=status,
            final_state=final_state,
            final_answer=final_answer,
            steps=steps,
            total_steps=budget.current_steps,
            total_tool_calls=budget.current_tool_calls,
            duration_ms=round(duration_ms, 2),
            disclaimer=AgentResponseValidator.MANDATORY_DISCLAIMER,
        )
