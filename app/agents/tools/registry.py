"""Allowlisted Tool Registry for AI Agents (Developer A).

Enforces docs/AI_Agent_Rulebook.md Section 5:
- Stable name and documented purpose
- Narrow Pydantic input schema with length, range, and type validation
- Narrow output schema
- Declared permission requirement
- Timeout and retry limit
- Unknown tools denied by default
"""

from typing import Any, Callable, Dict, List, Optional, Type
from pydantic import BaseModel, Field

from app.matching.engine import MatchingEngine
from app.matching.gap_analysis import GapAnalysisResult
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.retrieval.rag_service import RAGContextAssembler
from app.schemas.job import Job
from app.schemas.matching import MatchResult, RecommendedMatchesResponse
from app.schemas.resume import CandidateProfile


# ==============================================================================
# TOOL INPUT SCHEMAS (Strict type and range validation)
# ==============================================================================

class GetCandidateProfileInput(BaseModel):
    """Input payload to retrieve candidate profile facts."""
    candidate_id: str = Field(..., min_length=1, max_length=100, description="Unique candidate profile identifier")


class QueryJobsInput(BaseModel):
    """Input payload to query active job listings."""
    q: Optional[str] = Field(None, max_length=200, description="Keyword or title search term")
    skills: Optional[List[str]] = Field(None, max_length=20, description="List of required skill filters")
    location: Optional[str] = Field(None, max_length=100, description="Geographic or remote location")
    limit: int = Field(default=10, ge=1, le=50, description="Maximum listings to return")


class GetJobDetailsInput(BaseModel):
    """Input payload to retrieve specific job details."""
    job_id: str = Field(..., min_length=1, max_length=100, description="Unique job listing identifier")


class MatchCandidateToJobInput(BaseModel):
    """Input payload to calculate candidate-job match score and gap analysis."""
    candidate_id: str = Field(..., min_length=1, max_length=100, description="Candidate profile ID")
    job_id: str = Field(..., min_length=1, max_length=100, description="Target job listing ID")


class RecommendJobsForCandidateInput(BaseModel):
    """Input payload to generate ranked job recommendations."""
    candidate_id: str = Field(..., min_length=1, max_length=100, description="Candidate profile ID")
    min_score: float = Field(default=50.0, ge=0.0, le=100.0, description="Minimum overall match score (0-100)")
    limit: int = Field(default=5, ge=1, le=20, description="Maximum number of recommendations to return")


class AnalyzeSkillGapsInput(BaseModel):
    """Input payload to evaluate skill gaps between candidate and job."""
    candidate_id: str = Field(..., min_length=1, max_length=100, description="Candidate profile ID")
    job_id: str = Field(..., min_length=1, max_length=100, description="Job listing ID")


class AssembleRAGContextInput(BaseModel):
    """Input payload to assemble prompt-injection defended RAG context."""
    candidate_id: str = Field(..., min_length=1, max_length=100, description="Candidate profile ID")
    job_id: str = Field(..., min_length=1, max_length=100, description="Job listing ID")


# ==============================================================================
# TOOL OUTPUT SCHEMAS
# ==============================================================================

class QueryJobsOutput(BaseModel):
    """Output envelope for job queries."""
    jobs: List[Job] = Field(default_factory=list)
    total_found: int = Field(ge=0)


class RAGContextOutput(BaseModel):
    """Output envelope for prompt-shielded RAG context."""
    candidate_xml: str
    job_xml: str
    grounded_prompt: str


# ==============================================================================
# TOOL DEFINITION STRUCTURE
# ==============================================================================

class ToolDefinition(BaseModel):
    """Metadata and execution specification for an allowlisted agent tool."""

    name: str = Field(..., description="Stable tool name")
    description: str = Field(..., description="Purpose and usage instructions")
    input_schema: Type[BaseModel] = Field(..., description="Pydantic model validating arguments")
    output_schema: Type[BaseModel] = Field(..., description="Pydantic model validating results")
    required_permission: str = Field(..., description="Security permission required to invoke tool")
    timeout_seconds: float = Field(default=10.0, description="Individual tool timeout in seconds")
    max_retries: int = Field(default=1, description="Maximum idempotent retries on failure")
    handler: Any = Field(..., description="Async execution callable")


# ==============================================================================
# ALLOWLISTED TOOL REGISTRY
# ==============================================================================

class ToolRegistry:
    """Registry maintaining approved tools and rejecting unknown tools by default."""

    def __init__(
        self,
        resume_repo: Optional[IResumeRepository] = None,
        job_repo: Optional[IJobRepository] = None,
        matching_engine: Optional[MatchingEngine] = None,
    ) -> None:
        self.resume_repo = resume_repo or get_resume_repository()
        self.job_repo = job_repo or get_job_repository()
        self.matching_engine = matching_engine or MatchingEngine(job_repository=self.job_repo)
        self._tools: Dict[str, ToolDefinition] = {}
        self._register_default_tools()

    def register_tool(self, tool: ToolDefinition) -> None:
        """Register an approved tool into the allowlist."""
        self._tools[tool.name] = tool

    def get_tool(self, tool_name: str) -> Optional[ToolDefinition]:
        """Look up tool by name. Returns None if unknown (denial by default)."""
        return self._tools.get(tool_name)

    def is_allowed(self, tool_name: str) -> bool:
        """Check if tool name is registered in the allowlist."""
        return tool_name in self._tools

    def list_tools(self) -> List[ToolDefinition]:
        """Return list of all registered allowlisted tools."""
        return list(self._tools.values())

    def _register_default_tools(self) -> None:
        """Register Developer B's approved domain interfaces under strict schemas."""

        # 1. Get Candidate Profile Facts
        async def _handle_get_candidate(args: GetCandidateProfileInput) -> CandidateProfile:
            profile = await self.resume_repo.get_by_id(args.candidate_id)
            if not profile:
                raise ValueError(f"Candidate profile '{args.candidate_id}' not found.")
            return profile

        self.register_tool(
            ToolDefinition(
                name="get_candidate_profile",
                description="Retrieve structured profile facts (skills, experience, education) for a candidate.",
                input_schema=GetCandidateProfileInput,
                output_schema=CandidateProfile,
                required_permission="candidate:read",
                handler=_handle_get_candidate,
            )
        )

        # 2. Query Job Listings
        async def _handle_query_jobs(args: QueryJobsInput) -> QueryJobsOutput:
            jobs = await self.job_repo.query_jobs(
                q=args.q,
                skills=args.skills,
                location=args.location,
                only_active=True,
                limit=args.limit,
            )
            return QueryJobsOutput(jobs=jobs, total_found=len(jobs))

        self.register_tool(
            ToolDefinition(
                name="query_jobs",
                description="Search active job listings by keyword, skill requirements, or location.",
                input_schema=QueryJobsInput,
                output_schema=QueryJobsOutput,
                required_permission="jobs:read",
                handler=_handle_query_jobs,
            )
        )

        # 3. Get Job Details
        async def _handle_get_job(args: GetJobDetailsInput) -> Job:
            job = await self.job_repo.get_by_id(args.job_id)
            if not job or not job.is_active:
                raise ValueError(f"Job listing '{args.job_id}' not found or inactive.")
            return job

        self.register_tool(
            ToolDefinition(
                name="get_job_details",
                description="Retrieve full details for a specific active job listing.",
                input_schema=GetJobDetailsInput,
                output_schema=Job,
                required_permission="jobs:read",
                handler=_handle_get_job,
            )
        )

        # 4. Match Candidate to Job
        async def _handle_match(args: MatchCandidateToJobInput) -> MatchResult:
            cand = await self.resume_repo.get_by_id(args.candidate_id)
            if not cand:
                raise ValueError(f"Candidate profile '{args.candidate_id}' not found.")
            job = await self.job_repo.get_by_id(args.job_id)
            if not job or not job.is_active:
                raise ValueError(f"Job listing '{args.job_id}' not found or inactive.")
            return await self.matching_engine.match_candidate_to_job(candidate=cand, job=job)

        self.register_tool(
            ToolDefinition(
                name="match_candidate_to_job",
                description="Calculate explainable match score, breakdown, and gap analysis between candidate and job.",
                input_schema=MatchCandidateToJobInput,
                output_schema=MatchResult,
                required_permission="matching:execute",
                handler=_handle_match,
            )
        )

        # 5. Recommend Jobs for Candidate
        async def _handle_recommend(args: RecommendJobsForCandidateInput) -> RecommendedMatchesResponse:
            cand = await self.resume_repo.get_by_id(args.candidate_id)
            if not cand:
                raise ValueError(f"Candidate profile '{args.candidate_id}' not found.")
            return await self.matching_engine.recommend_jobs_for_candidate(
                candidate=cand,
                top_k=args.limit,
                min_score=args.min_score,
            )

        self.register_tool(
            ToolDefinition(
                name="recommend_jobs_for_candidate",
                description="Evaluate candidate profile against active jobs and return ranked recommendations.",
                input_schema=RecommendJobsForCandidateInput,
                output_schema=RecommendedMatchesResponse,
                required_permission="matching:execute",
                handler=_handle_recommend,
            )
        )

        # 6. Analyze Skill Gaps
        async def _handle_gap_analysis(args: AnalyzeSkillGapsInput) -> GapAnalysisResult:
            cand = await self.resume_repo.get_by_id(args.candidate_id)
            if not cand:
                raise ValueError(f"Candidate profile '{args.candidate_id}' not found.")
            job = await self.job_repo.get_by_id(args.job_id)
            if not job or not job.is_active:
                raise ValueError(f"Job listing '{args.job_id}' not found or inactive.")
            return self.matching_engine.gap_analyzer.analyze(candidate=cand, job=job)

        self.register_tool(
            ToolDefinition(
                name="analyze_skill_gaps",
                description="Identify matched skills, missing skills, and truthful improvement suggestions.",
                input_schema=AnalyzeSkillGapsInput,
                output_schema=GapAnalysisResult,
                required_permission="matching:execute",
                handler=_handle_gap_analysis,
            )
        )

        # 7. Assemble Grounded RAG Context
        async def _handle_rag_context(args: AssembleRAGContextInput) -> RAGContextOutput:
            cand = await self.resume_repo.get_by_id(args.candidate_id)
            if not cand:
                raise ValueError(f"Candidate profile '{args.candidate_id}' not found.")
            job = await self.job_repo.get_by_id(args.job_id)
            if not job or not job.is_active:
                raise ValueError(f"Job listing '{args.job_id}' not found or inactive.")
            ctx = RAGContextAssembler.assemble_grounded_context(candidate=cand, job=job)
            return RAGContextOutput(
                candidate_xml=ctx["candidate_xml"],
                job_xml=ctx["job_xml"],
                grounded_prompt=ctx["grounded_prompt"],
            )

        self.register_tool(
            ToolDefinition(
                name="assemble_rag_context",
                description="Construct grounded, prompt-injection shielded XML context for candidate and job.",
                input_schema=AssembleRAGContextInput,
                output_schema=RAGContextOutput,
                required_permission="matching:execute",
                handler=_handle_rag_context,
            )
        )


_global_tool_registry: Optional[ToolRegistry] = None


def get_tool_registry() -> ToolRegistry:
    """Return singleton allowlisted ToolRegistry."""
    global _global_tool_registry
    if _global_tool_registry is None:
        _global_tool_registry = ToolRegistry()
    return _global_tool_registry
