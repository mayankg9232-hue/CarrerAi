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
        self._policy_gate = policy_gate
        self._tool_registry = tool_registry
        self._resume_repo = resume_repo
        self._job_repo = job_repo
        self.llm_service = llm_service or get_llm_service()
        self.validator = validator or AgentResponseValidator()

    @property
    def tool_registry(self) -> ToolRegistry:
        return self._tool_registry or get_tool_registry()

    @tool_registry.setter
    def tool_registry(self, value: Optional[ToolRegistry]) -> None:
        self._tool_registry = value

    @property
    def resume_repo(self) -> IResumeRepository:
        return self._resume_repo or get_resume_repository()

    @resume_repo.setter
    def resume_repo(self, value: Optional[IResumeRepository]) -> None:
        self._resume_repo = value

    @property
    def job_repo(self) -> IJobRepository:
        return self._job_repo or get_job_repository()

    @job_repo.setter
    def job_repo(self, value: Optional[IJobRepository]) -> None:
        self._job_repo = value

    @property
    def policy_gate(self) -> PolicyGate:
        if self._policy_gate is not None:
            return self._policy_gate
        return PolicyGate(
            tool_registry=self.tool_registry,
            resume_repo=self.resume_repo,
        )

    @policy_gate.setter
    def policy_gate(self, value: Optional[PolicyGate]) -> None:
        self._policy_gate = value

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
            cand_ctx = None
            if request.candidate_id:
                try:
                    c_rec = await self.resume_repo.get_by_id(request.candidate_id)
                    if c_rec:
                        cand_ctx = {
                            "full_name": principal.full_name or c_rec.full_name,
                            "headline": c_rec.headline or c_rec.current_role,
                            "skills": [s.name for s in c_rec.skills],
                            "years_of_experience": c_rec.years_of_experience,
                        }
                except Exception as exc:
                    logger.debug("Candidate context lookup non-critical notice: %s", exc)

            job_ctx = None
            if request.job_id:
                try:
                    j_rec = await self.job_repo.get_by_id(request.job_id)
                    if j_rec:
                        job_ctx = {
                            "title": j_rec.title,
                            "company": j_rec.company,
                            "required_skills": j_rec.required_skills,
                        }
                except Exception as exc:
                    logger.debug("Job context lookup non-critical notice: %s", exc)

            grounded_facts = ""
            if collected_results:
                grounded_facts = self._synthesize_final_answer(request, collected_results)

            if not final_answer:
                from app.agents.prompts import CAREER_AGENT_SYSTEM_PROMPT, build_conversational_prompt

                conversational_prompt = build_conversational_prompt(
                    task=request.task,
                    history=request.history,
                    candidate_context=cand_ctx,
                    job_context=job_ctx,
                    tool_evidence=grounded_facts,
                )

                try:
                    llm_generated = await self.llm_service.generate_text(
                        system_prompt=CAREER_AGENT_SYSTEM_PROMPT,
                        user_prompt=conversational_prompt,
                        temperature=0.3,
                        max_tokens=1500,
                        task_name="agent_conversational_response",
                    )
                    is_match_query = any(w in request.task.lower() for w in ["match", "alignment", "score", "fit", "compare"])
                    is_resume_query = any(w in request.task.lower() for w in ["resume", "cv", "suggestion", "improve"])
                    if collected_results and (is_match_query or is_resume_query):
                        if grounded_facts and ("Overall Alignment Score" in grounded_facts or "Truthful Resume Improvement" in grounded_facts):
                            final_answer = f"{grounded_facts}\n\n{llm_generated}"
                        else:
                            final_answer = llm_generated or grounded_facts
                    else:
                        final_answer = llm_generated or "I am here to help you navigate your job search and technical career goals."
                except Exception as exc:
                    logger.warning("Agent LLM conversational generation failed: %s", str(exc))
                    is_match_query = any(w in request.task.lower() for w in ["match", "alignment", "score", "fit", "compare"])
                    is_resume_query = any(w in request.task.lower() for w in ["resume", "cv", "suggestion", "improve"])
                    if collected_results and (is_match_query or is_resume_query):
                        final_answer = grounded_facts
                    else:
                        final_answer = "The career assistant service is temporarily unavailable. Please try again shortly."

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
        task_lower = request.task.lower().strip()
        planned: List[tuple[str, dict]] = []

        # 0. Conversational pleasantries and greetings MUST NEVER trigger tools (Case A)
        greetings = {"hi", "hii", "hiii", "hello", "hey", "heyy", "greetings", "good morning", "good afternoon", "good evening", "howdy", "sup", "yo"}
        tokens = task_lower.split()
        first_token = tokens[0] if tokens else ""
        if task_lower in greetings or (len(tokens) <= 3 and first_token in greetings):
            return []

        # 0b. Open-ended career advice questions (Case B) MUST NOT trigger automated matching tools
        is_open_ended_career_question = any(
            phrase in task_lower for phrase in [
                "what to do", "what should i do", "how can i get", "how to get", "how to prepare",
                "what should i learn", "learning roadmap", "career roadmap", "career transition",
                "tips for", "advice for", "how do i start", "how do i become", "guide me"
            ]
        )
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
        if is_open_ended_career_question and not (is_explicit_match_intent or is_resume_tool_intent):
            return []

        # 1. Candidate-Job Match Workflow (Explicit request to match candidate to specific job)
        if request.candidate_id and request.job_id:
            if is_explicit_match_intent or any(w in task_lower for w in ["match", "alignment", "score", "fit", "compare"]):
                planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
                planned.append(("get_job_details", {"job_id": request.job_id}))
                planned.append(("match_candidate_to_job", {"candidate_id": request.candidate_id, "job_id": request.job_id}))
                return planned
            if is_resume_tool_intent or any(phrase in task_lower for phrase in ["improve resume", "tailor resume", "resume suggestion", "truthful suggestion", "truthful resume", "resume improvement"]):
                planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
                planned.append(("get_job_details", {"job_id": request.job_id}))
                planned.append(("generate_truthful_resume_suggestions", {"candidate_id": request.candidate_id, "job_id": request.job_id}))
                return planned
            if any(phrase in task_lower for phrase in ["synthesize match", "explain match", "match explanation"]):
                planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
                planned.append(("get_job_details", {"job_id": request.job_id}))
                planned.append(("synthesize_grounded_match_explanation", {"candidate_id": request.candidate_id, "job_id": request.job_id}))
                return planned
            if "gap" in task_lower:
                planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
                planned.append(("analyze_skill_gaps", {"candidate_id": request.candidate_id, "job_id": request.job_id}))
                return planned

        # 2. Case D — Explicit comparison with available jobs / Recommendations
        if request.candidate_id and any(w in task_lower for w in ["compare", "match"]) and any(w in task_lower for w in ["jobs", "openings", "roles", "positions"]):
            planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
            extracted_term = None
            for term in ["machine learning", "ml engineer", "ml", "frontend", "backend", "full stack", "devops", "cloud"]:
                if term in task_lower:
                    extracted_term = term
                    break
            if extracted_term:
                planned.append(("semantic_search_jobs", {"candidate_id": request.candidate_id, "query_text": extracted_term, "limit": 5}))
            else:
                planned.append(("recommend_jobs_for_candidate", {"candidate_id": request.candidate_id, "min_score": 50.0, "limit": 5}))
            return planned

        if request.candidate_id and any(w in task_lower for w in ["recommend", "find jobs for me", "suitable jobs", "matching jobs for me"]):
            planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
            planned.append(("recommend_jobs_for_candidate", {"candidate_id": request.candidate_id, "min_score": 50.0, "limit": 5}))
            return planned

        # 3. Semantic / Vector Search Workflow
        if any(w in task_lower for w in ["semantic", "vector search", "similarity search"]):
            if request.candidate_id:
                planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
                planned.append(("semantic_search_jobs", {"candidate_id": request.candidate_id, "limit": 5}))
                return planned
            planned.append(("semantic_search_jobs", {"query_text": request.task[:100], "limit": 5}))
            return planned

        # 4. Extract Skills from raw text
        if ("extract" in task_lower or "parse" in task_lower) and "skill" in task_lower:
            planned.append(("extract_skills_from_text", {"text": request.task[:500]}))
            return planned

        # 5. Profile Facts Retrieval
        if request.candidate_id and any(w in task_lower for w in ["my profile", "my resume", "my cv", "my skills", "show my skills", "get my profile"]):
            planned.append(("get_candidate_profile", {"candidate_id": request.candidate_id}))
            return planned

        # 6. Explicit Job Search
        if any(phrase in task_lower for phrase in ["search for jobs", "search jobs", "browse jobs", "list jobs", "find openings"]):
            query_text = request.task
            for prefix in ["search for jobs", "search jobs", "browse jobs", "list jobs", "find openings"]:
                if prefix in task_lower:
                    query_text = task_lower.split(prefix, 1)[-1].strip(" :,-")
                    break
            planned.append(("query_jobs", {"q": query_text[:50] or None, "limit": 5}))
            return planned

        # Conversational turns (greetings, career transitions, explanations, roadmaps, follow-ups):
        # Do not force unneeded tool calls — proceed cleanly to conversational synthesis
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

        from app.agents.tools.developer_b_tools import (
            ExtractedSkillsOutput,
            ParsedResumeFactsOutput,
            SemanticJobSearchOutput,
            IngestJobOutput,
            TruthfulResumeSuggestionsOutput,
            SynthesizedMatchExplanationOutput,
        )

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
            elif isinstance(r, TruthfulResumeSuggestionsOutput):
                sugg_text = "\n".join([f"- {s}" for s in r.suggestions])
                parts.append(
                    f"Truthful Resume Improvement Suggestions for Candidate {r.candidate_id} and Job {r.job_id}:\n"
                    f"{sugg_text}\n"
                    f"Note: {r.anti_hallucination_note}"
                )
            elif isinstance(r, SynthesizedMatchExplanationOutput):
                caveats_str = f"\n- Caveats: {', '.join(r.caveats)}" if r.caveats else ""
                parts.append(
                    f"Grounded Match Synthesis for Candidate {r.candidate_id} and Job {r.job_id}:\n"
                    f"- Overall Alignment Score: {r.overall_score:.1f}/100\n"
                    f"- {r.grounded_summary}"
                    f"{caveats_str}"
                )
            elif isinstance(r, SemanticJobSearchOutput):
                top_items = [f"{m.title} at {m.company} ({m.similarity_score:.2f})" for m in r.matches[:3]]
                parts.append(
                    f"Semantic Job Search Results for '{r.query}':\n"
                    f"- Top Matches: {', '.join(top_items) if top_items else 'No semantic matches found'}\n"
                    f"- Total Found: {r.total_found}"
                )
            elif isinstance(r, ExtractedSkillsOutput):
                skills_str = ", ".join([s.name for s in r.skills])
                parts.append(f"Extracted {r.total_extracted} skills: {skills_str}.")
            elif isinstance(r, ParsedResumeFactsOutput):
                parts.append(
                    f"Parsed Resume Facts: {len(r.skills)} skills extracted, "
                    f"estimated {r.detected_years_experience:.1f} years experience."
                )
            elif isinstance(r, IngestJobOutput):
                status_str = "New listing" if r.is_new else "Updated listing"
                parts.append(
                    f"Job Ingestion: '{r.title}' at {r.company} (ID: {r.job_id}). Status: {status_str}."
                )

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
