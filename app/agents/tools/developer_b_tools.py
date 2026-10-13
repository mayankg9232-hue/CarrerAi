"""Developer B Agent Capability Tools: NLP, Firecrawl, RAG Retrieval, Matching & Truthful Advice.

Implements Developer B responsibilities from docs/AI_Job_Matcher_Phases_and_Structure.md Phase 6:
- Safe, bounded tools exposing Developer B's approved domain capabilities to the Agent Orchestrator.
- Strict Pydantic input schemas with field length and type validation (Rulebook Section 5).
- Strict Pydantic output schemas (Rulebook Section 5).
- Explicit permission declarations (Rulebook Section 5).
- Untrusted content handling with prompt-injection defense (Rulebook Section 7).
- Anti-hallucination and truthfulness enforcement (Rulebook Section 14).
- Safe error handling and provider outage resilience (Rulebook Section 11 & 16).
"""

import hashlib
import re
import uuid
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.core.config import get_settings
from app.core.logging import logger
from app.integrations.firecrawl.source_adapter import (
    DomainNotApprovedError,
    FirecrawlSourceAdapter,
    IFirecrawlAdapter,
    MockFirecrawlAdapter,
    SSRFBlockedError,
    SourceMalformedError,
    SourceRateLimitError,
    SourceTimeoutError,
    validate_source_url,
)
from app.integrations.llm.base import (
    LLMError,
    LLMTimeoutError,
    LLMUnavailableError,
)
from app.integrations.llm.service import TwoModelLLMService, get_llm_service
from app.matching.engine import MatchingEngine
from app.matching.explain import MatchExplainer
from app.matching.gap_analysis import GapAnalyzer, GapAnalysisResult
from app.nlp.normalizer import sanitize_text, wrap_untrusted_content
from app.nlp.skill_extractor import SkillExtractor
from app.repositories.job_repo import IJobRepository, get_job_repository
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.retrieval.embeddings import (
    IEmbeddingProvider,
    cosine_similarity,
    get_embedding_provider,
)
from app.retrieval.retriever import JobRetriever
from app.schemas.job import Job
from app.schemas.matching import MatchResult
from app.schemas.resume import CandidateProfile, SkillItem


# ==============================================================================
# TOOL 1: EXTRACT SKILLS FROM TEXT (NLP)
# ==============================================================================

class ExtractSkillsInput(BaseModel):
    """Input payload to extract technical skills from candidate or job text."""

    text: str = Field(
        ...,
        min_length=1,
        max_length=10000,
        description="Raw or sanitized text to extract skills from",
    )
    section_context: Optional[str] = Field(
        None,
        max_length=100,
        description="Optional contextual section identifier (e.g. 'skills', 'experience', 'summary')",
    )
    min_confidence: float = Field(
        default=0.7,
        ge=0.0,
        le=1.0,
        description="Minimum confidence threshold cutoff (0.0 to 1.0)",
    )


class ExtractedSkillsOutput(BaseModel):
    """Output envelope containing extracted canonical skills."""

    skills: List[SkillItem] = Field(
        default_factory=list,
        description="Extracted canonical skills with confidence and categories",
    )
    total_extracted: int = Field(ge=0, description="Total count of unique extracted skills")


# ==============================================================================
# TOOL 2: PARSE RESUME FACTS (NLP with Prompt Injection Defense)
# ==============================================================================

class ParseResumeFactsInput(BaseModel):
    """Input payload to parse structured profile facts from untrusted CV text."""

    raw_text: str = Field(
        ...,
        min_length=10,
        max_length=20000,
        description="Raw untrusted resume text to safely extract profile facts from",
    )


class ParsedResumeFactsOutput(BaseModel):
    """Output envelope for extracted biographical and professional resume facts."""

    skills: List[SkillItem] = Field(
        default_factory=list,
        description="Validated skills extracted from text",
    )
    summary: str = Field(default="", description="Sanitized summary excerpt")
    detected_years_experience: float = Field(
        ge=0.0,
        description="Estimated total professional experience in years",
    )
    top_categories: List[str] = Field(
        default_factory=list,
        description="Dominant technical skill categories identified",
    )


# ==============================================================================
# TOOL 3: SEMANTIC JOB SEARCH (Vector Retrieval & RAG)
# ==============================================================================

class SemanticJobSearchInput(BaseModel):
    """Input payload for vector cosine similarity search over active job listings."""

    query_text: Optional[str] = Field(
        None,
        max_length=500,
        description="Natural language search query or desired skill keywords",
    )
    candidate_id: Optional[str] = Field(
        None,
        max_length=100,
        description="Optional candidate profile ID to match semantically against active jobs",
    )
    limit: int = Field(
        default=5,
        ge=1,
        le=20,
        description="Maximum number of results to return",
    )
    min_similarity: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Minimum cosine similarity cutoff",
    )


class SemanticJobMatchItem(BaseModel):
    """Ranked job item from vector semantic search."""

    job_id: str
    title: str
    company: str
    location: str
    required_skills: List[str]
    similarity_score: float
    description_preview: str


class SemanticJobSearchOutput(BaseModel):
    """Output envelope for semantic vector job search."""

    query: str
    matches: List[SemanticJobMatchItem] = Field(default_factory=list)
    total_found: int = Field(ge=0)


# ==============================================================================
# TOOL 4: INGEST JOB FROM PERMITTED SOURCE (Firecrawl with SSRF Guard)
# ==============================================================================

class IngestJobFromSourceInput(BaseModel):
    """Input payload to ingest job listing from an approved source URL."""

    source_url: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Full HTTP/HTTPS URL from an approved job source domain",
    )


class IngestJobOutput(BaseModel):
    """Output envelope for scraped and ingested job listing."""

    job_id: str
    title: str
    company: str
    location: str
    required_skills: List[str]
    source_url: str
    source: str
    is_new: bool
    message: str


# ==============================================================================
# TOOL 5: TRUTHFUL RESUME SUGGESTIONS (Anti-Hallucination Advice)
# ==============================================================================

class GenerateTruthfulSuggestionsInput(BaseModel):
    """Input payload to generate truthful skill gap improvement suggestions."""

    candidate_id: str = Field(..., min_length=1, max_length=100, description="Candidate profile ID")
    job_id: str = Field(..., min_length=1, max_length=100, description="Target job ID")


class TruthfulResumeSuggestionsOutput(BaseModel):
    """Output envelope containing truthful, anti-hallucinatory resume suggestions."""

    candidate_id: str
    job_id: str
    matched_skills: List[str]
    missing_skills: List[str]
    suggestions: List[str]
    anti_hallucination_note: str
    disclaimer: str


# ==============================================================================
# TOOL 6: SYNTHESIZE GROUNDED MATCH EXPLANATION (LLM & Matching)
# ==============================================================================

class SynthesizeMatchExplanationInput(BaseModel):
    """Input payload to synthesize an explainable natural language match breakdown."""

    candidate_id: str = Field(..., min_length=1, max_length=100, description="Candidate profile ID")
    job_id: str = Field(..., min_length=1, max_length=100, description="Job listing ID")


class SynthesizedMatchExplanationOutput(BaseModel):
    """Output envelope for grounded natural language match explanation."""

    candidate_id: str
    job_id: str
    overall_score: float
    grounded_summary: str
    matched_skills: List[str]
    missing_skills: List[str]
    caveats: List[str]
    disclaimer: str


# ==============================================================================
# TOOL FACTORY FOR DEVELOPER B CAPABILITIES
# ==============================================================================

def create_developer_b_tools(
    resume_repo: Optional[IResumeRepository] = None,
    job_repo: Optional[IJobRepository] = None,
    matching_engine: Optional[MatchingEngine] = None,
    llm_service: Optional[TwoModelLLMService] = None,
    job_retriever: Optional[JobRetriever] = None,
    firecrawl_adapter: Optional[IFirecrawlAdapter] = None,
    skill_extractor: Optional[SkillExtractor] = None,
) -> List[Any]:
    """Instantiate and configure Developer B's allowlisted capability tools."""
    # Resolve dependencies with safe singleton or mock fallbacks
    _resume_repo = resume_repo or get_resume_repository()
    _job_repo = job_repo or get_job_repository()
    _matching_engine = matching_engine or MatchingEngine(job_repository=_job_repo)
    _llm_service = llm_service or get_llm_service()
    _job_retriever = job_retriever or JobRetriever(job_repository=_job_repo)
    _skill_extractor = skill_extractor or SkillExtractor()

    # If no Firecrawl adapter provided, determine from environment configuration
    if firecrawl_adapter is None:
        settings = get_settings()
        if settings.FIRECRAWL_API_KEY:
            _firecrawl_adapter: IFirecrawlAdapter = FirecrawlSourceAdapter(
                skill_extractor=_skill_extractor,
                settings=settings,
            )
        else:
            _firecrawl_adapter = MockFirecrawlAdapter()
    else:
        _firecrawl_adapter = firecrawl_adapter

    # Deferred import to prevent circular dependency
    from app.agents.tools.registry import ToolDefinition

    tools: List[ToolDefinition] = []

    # --------------------------------------------------------------------------
    # 1. extract_skills_from_text
    # --------------------------------------------------------------------------
    async def _handle_extract_skills(args: ExtractSkillsInput) -> ExtractedSkillsOutput:
        extracted = _skill_extractor.extract_skills(
            text=args.text,
            section_context=args.section_context,
        )
        filtered_items = [
            SkillItem(**item)
            for item in extracted
            if item.get("confidence", 0.0) >= args.min_confidence
        ]
        return ExtractedSkillsOutput(
            skills=filtered_items,
            total_extracted=len(filtered_items),
        )

    tools.append(
        ToolDefinition(
            name="extract_skills_from_text",
            description="Extract normalized technical skills, confidence scores, and categories from text using taxonomy matching.",
            input_schema=ExtractSkillsInput,
            output_schema=ExtractedSkillsOutput,
            required_permission="nlp:extract",
            timeout_seconds=5.0,
            max_retries=1,
            handler=_handle_extract_skills,
        )
    )

    # --------------------------------------------------------------------------
    # 2. parse_resume_facts
    # --------------------------------------------------------------------------
    async def _handle_parse_resume_facts(args: ParseResumeFactsInput) -> ParsedResumeFactsOutput:
        # Treat raw input as untrusted content; sanitize and ignore adversarial instructions
        clean_text = sanitize_text(args.raw_text)

        # Extract structured skills from sanitized text
        extracted = _skill_extractor.extract_skills(clean_text)
        skill_items = [SkillItem(**item) for item in extracted]

        # Estimate years of experience from text heuristics
        year_matches = [
            float(m)
            for m in re.findall(
                r"\b(\d+(?:\.\d+)?)\+?\s*years?(?:\s+of\s+experience)?\b",
                clean_text,
                flags=re.IGNORECASE,
            )
        ]
        detected_years = max(year_matches) if year_matches else 0.0

        # Unique dominant categories
        categories = list(dict.fromkeys([s.category for s in skill_items if s.category]))

        # First 250 characters as clean summary excerpt
        summary_excerpt = clean_text[:250].strip()

        return ParsedResumeFactsOutput(
            skills=skill_items,
            summary=summary_excerpt,
            detected_years_experience=detected_years,
            top_categories=categories[:5],
        )

    tools.append(
        ToolDefinition(
            name="parse_resume_facts",
            description="Safely extract structured candidate facts (skills, experience years) from untrusted resume text while ignoring prompt injections.",
            input_schema=ParseResumeFactsInput,
            output_schema=ParsedResumeFactsOutput,
            required_permission="nlp:extract",
            timeout_seconds=5.0,
            max_retries=1,
            handler=_handle_parse_resume_facts,
        )
    )

    # --------------------------------------------------------------------------
    # 3. semantic_search_jobs
    # --------------------------------------------------------------------------
    async def _handle_semantic_search(args: SemanticJobSearchInput) -> SemanticJobSearchOutput:
        query_label = args.query_text or ""

        if args.candidate_id:
            cand = await _resume_repo.get_by_id(args.candidate_id)
            if not cand:
                raise ValueError(f"Candidate profile '{args.candidate_id}' not found.")
            ranked_tuples = await _job_retriever.search_relevant_jobs(
                candidate=cand,
                top_k=args.limit,
                min_similarity=args.min_similarity,
            )
            query_label = f"Candidate Profile: {cand.full_name}"
        else:
            if not query_label:
                raise ValueError("Either 'query_text' or 'candidate_id' must be provided.")
            active_jobs = await _job_repo.query_jobs(only_active=True, limit=100)
            query_vec = await _job_retriever.embedder.embed_text(query_label)
            ranked: List[tuple[Job, float]] = []
            for j in active_jobs:
                j_vec = await _job_retriever.get_or_create_job_embedding(j)
                sim = cosine_similarity(query_vec, j_vec)
                if sim >= args.min_similarity:
                    ranked.append((j, sim))
            ranked.sort(key=lambda item: item[1], reverse=True)
            ranked_tuples = ranked[: args.limit]

        matches = [
            SemanticJobMatchItem(
                job_id=job.id,
                title=job.title,
                company=job.company,
                location=job.location,
                required_skills=job.required_skills,
                similarity_score=round(float(sim), 4),
                description_preview=job.description[:150] + ("..." if len(job.description) > 150 else ""),
            )
            for job, sim in ranked_tuples
        ]

        return SemanticJobSearchOutput(
            query=query_label,
            matches=matches,
            total_found=len(matches),
        )

    tools.append(
        ToolDefinition(
            name="semantic_search_jobs",
            description="Perform vector cosine similarity search over active job listings matching query text or candidate profile.",
            input_schema=SemanticJobSearchInput,
            output_schema=SemanticJobSearchOutput,
            required_permission="retrieval:read",
            timeout_seconds=8.0,
            max_retries=1,
            handler=_handle_semantic_search,
        )
    )

    # --------------------------------------------------------------------------
    # 4. ingest_job_from_source
    # --------------------------------------------------------------------------
    async def _handle_ingest_job(args: IngestJobFromSourceInput) -> IngestJobOutput:
        settings = get_settings()
        # SSRF and domain allowlist guard
        validated_url = validate_source_url(args.source_url, settings.APPROVED_JOB_DOMAINS)

        try:
            scraped = await _firecrawl_adapter.scrape_job(validated_url)
        except (SourceTimeoutError, SourceRateLimitError, SourceMalformedError) as exc:
            logger.error("Firecrawl ingestion failed for '%s': %s", validated_url, str(exc))
            raise

        content_raw = f"{scraped['title']}|{scraped['company']}|{scraped['description']}"
        raw_hash = hashlib.sha256(content_raw.encode("utf-8")).hexdigest()

        # Deduplicate by URL or content hash
        existing = await _job_repo.get_by_source_url(validated_url)
        if not existing:
            existing = await _job_repo.get_by_content_hash(raw_hash)

        if existing:
            existing.last_seen_at = scraped.get("collected_at")
            await _job_repo.update_job(existing.id, last_seen_at=existing.last_seen_at)
            return IngestJobOutput(
                job_id=existing.id,
                title=existing.title,
                company=existing.company,
                location=existing.location,
                required_skills=existing.required_skills,
                source_url=validated_url,
                source="firecrawl",
                is_new=False,
                message="Existing job listing recognized and updated with fresh timestamp.",
            )

        new_job = Job(
            id=f"job_fc_{uuid.uuid4().hex[:8]}",
            title=scraped["title"],
            company=scraped["company"],
            location=scraped.get("location", "Remote"),
            description=scraped["description"],
            required_skills=scraped.get("required_skills", []),
            preferred_skills=scraped.get("preferred_skills", []),
            experience_level=scraped.get("experience_level"),
            source="firecrawl",
            source_url=validated_url,
            content_hash=raw_hash,
            is_active=True,
        )
        await _job_repo.create_job(new_job)

        return IngestJobOutput(
            job_id=new_job.id,
            title=new_job.title,
            company=new_job.company,
            location=new_job.location,
            required_skills=new_job.required_skills,
            source_url=validated_url,
            source="firecrawl",
            is_new=True,
            message="Successfully scraped and ingested new job listing.",
        )

    tools.append(
        ToolDefinition(
            name="ingest_job_from_source",
            description="Scrape and ingest a job listing from an approved domain via Firecrawl, with SSRF protection, provenance, and deduplication.",
            input_schema=IngestJobFromSourceInput,
            output_schema=IngestJobOutput,
            required_permission="jobs:ingest",
            timeout_seconds=10.0,
            max_retries=1,
            handler=_handle_ingest_job,
        )
    )

    # --------------------------------------------------------------------------
    # 5. generate_truthful_resume_suggestions
    # --------------------------------------------------------------------------
    async def _handle_truthful_suggestions(args: GenerateTruthfulSuggestionsInput) -> TruthfulResumeSuggestionsOutput:
        cand = await _resume_repo.get_by_id(args.candidate_id)
        if not cand:
            raise ValueError(f"Candidate profile '{args.candidate_id}' not found.")
        job = await _job_repo.get_by_id(args.job_id)
        if not job or not job.is_active:
            raise ValueError(f"Job listing '{args.job_id}' not found or inactive.")

        gap_result = _matching_engine.gap_analyzer.analyze(cand, job)

        # Generate truthful, non-hallucinatory suggestions based strictly on missing skills
        suggestions: List[str] = []
        for missing in gap_result.missing_skills:
            suggestions.append(
                f"If you have hands-on academic or personal project experience with {missing}, articulate it clearly under your project section."
            )
        for uncertain in gap_result.uncertain_skills:
            suggestions.append(
                f"Clarify your role and duration working with {uncertain} in your work history to substantiate proficiency."
            )

        if not suggestions:
            suggestions.append(
                "Your verified profile strongly aligns with all mandatory technical requirements for this role."
            )

        return TruthfulResumeSuggestionsOutput(
            candidate_id=cand.id,
            job_id=job.id,
            matched_skills=gap_result.matched_skills,
            missing_skills=gap_result.missing_skills,
            suggestions=suggestions,
            anti_hallucination_note="Suggestions are strictly limited to verified candidate history and honest skill articulation; no unearned credentials were fabricated.",
            disclaimer=MatchExplainer.MANDATORY_DISCLAIMER,
        )

    tools.append(
        ToolDefinition(
            name="generate_truthful_resume_suggestions",
            description="Generate grounded, truthful suggestions for addressing missing skill gaps without inventing experience or unearned credentials.",
            input_schema=GenerateTruthfulSuggestionsInput,
            output_schema=TruthfulResumeSuggestionsOutput,
            required_permission="matching:execute",
            timeout_seconds=8.0,
            max_retries=1,
            handler=_handle_truthful_suggestions,
        )
    )

    # --------------------------------------------------------------------------
    # 6. synthesize_grounded_match_explanation
    # --------------------------------------------------------------------------
    async def _handle_synthesize_explanation(args: SynthesizeMatchExplanationInput) -> SynthesizedMatchExplanationOutput:
        cand = await _resume_repo.get_by_id(args.candidate_id)
        if not cand:
            raise ValueError(f"Candidate profile '{args.candidate_id}' not found.")
        job = await _job_repo.get_by_id(args.job_id)
        if not job or not job.is_active:
            raise ValueError(f"Job listing '{args.job_id}' not found or inactive.")

        match_res = await _matching_engine.match_candidate_to_job(candidate=cand, job=job)

        caveats: List[str] = []
        if match_res.missing_skills:
            caveats.append(f"Missing required skills: {', '.join(match_res.missing_skills)}")
        if match_res.breakdown.skill_score < 70.0:
            caveats.append("Technical skill alignment is moderate or partial.")

        grounded_summary = (
            f"Evaluated candidate {cand.full_name} for role '{job.title}' at {job.company}. "
            f"Calculated overall alignment of {match_res.overall_score:.1f}/100. "
            f"Verified skills match: {', '.join(match_res.matched_skills) if match_res.matched_skills else 'None'}."
        )

        return SynthesizedMatchExplanationOutput(
            candidate_id=cand.id,
            job_id=job.id,
            overall_score=match_res.overall_score,
            grounded_summary=grounded_summary,
            matched_skills=match_res.matched_skills,
            missing_skills=match_res.missing_skills,
            caveats=caveats,
            disclaimer=MatchExplainer.MANDATORY_DISCLAIMER,
        )

    tools.append(
        ToolDefinition(
            name="synthesize_grounded_match_explanation",
            description="Generate an explainable natural-language match summary grounded strictly in validated candidate facts and job requirements.",
            input_schema=SynthesizeMatchExplanationInput,
            output_schema=SynthesizedMatchExplanationOutput,
            required_permission="matching:execute",
            timeout_seconds=8.0,
            max_retries=1,
            handler=_handle_synthesize_explanation,
        )
    )

    return tools
