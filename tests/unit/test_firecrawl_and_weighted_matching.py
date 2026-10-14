"""Automated Verification Suite: Firecrawl Job Scraping & 5-Factor Weighted AI Matching.

Covers all 10 mandated requirements:
1. Firecrawl configuration test: Missing or invalid API credentials produce a clear, safe error.
2. Scraping success test: Mocked successful Firecrawl response parses into normalized job structure.
3. Scraping failure test: Timeouts, rate limits, or failed responses handled gracefully without crashing.
4. Matching calculation test: Given known factor scores and weights, final percentage matches exact math:
   (80 * 0.40) + (90 * 0.25) + (100 * 0.10) + (70 * 0.15) + (60 * 0.10) = 81.0%.
5. Ranking test: Jobs with scores of 81%, 92%, and 65% are ranked in order 92% (Rank 1), 81% (Rank 2), 65% (Rank 3).
6. Missing skills test: Correctly identifies required skills absent from candidate profile.
7. Missing information test: Unavailable factors handled via weight renormalization and reduced confidence without unearned 100%.
8. Duplicate jobs test: Duplicate postings are deduplicated rather than counted as separate opportunities.
9. API integration test: Upload-and-match endpoint returns expected factor scores, overall percentage, and ranks.
10. Frontend contract & integration check: Verifies frontend template, assets, and response schemas align with backend.
"""

from datetime import datetime, timezone
import hashlib
from unittest.mock import AsyncMock
import pytest
from httpx import AsyncClient, ASGITransport

from app.core.config import get_settings
from app.integrations.firecrawl.client import (
    FirecrawlAuthenticationError,
    FirecrawlClient,
    FirecrawlRateLimitError,
    FirecrawlTimeoutError,
)
from app.integrations.firecrawl.source_adapter import (
    FirecrawlSourceAdapter,
    SourceMalformedError,
    SourceRateLimitError,
    SourceTimeoutError,
)
from app.matching.engine import MatchingEngine
from app.matching.scorer import DeterministicScorer
from app.schemas.job import Job
from app.schemas.matching import FactorScoreDetail, MatchingWeights, ScoreBreakdown
from app.schemas.resume import (
    CandidateProfile,
    EducationItem,
    ExperienceItem,
    SkillItem,
)
from app.main import app


# ==============================================================================
# TEST 1: FIRECRAWL CONFIGURATION TEST
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_1_firecrawl_missing_or_invalid_credentials_produces_safe_error():
    """Scenario 1: Missing or blank Firecrawl API credentials raise clear, safe error without leaking secrets."""
    # Instantiating client with explicitly empty key raises upon dispatch
    client_empty = FirecrawlClient(api_key="")
    with pytest.raises(FirecrawlAuthenticationError) as exc_info:
        await client_empty.scrape_url("https://careers.techcompany.com/jobs/123")

    assert "missing or not configured" in str(exc_info.value)
    # Verify no secret or internal tokens are leaked in exception
    assert "Bearer" not in str(exc_info.value)

    # Whitespace key must also be safely rejected
    client_spaces = FirecrawlClient(api_key="    ")
    with pytest.raises(FirecrawlAuthenticationError):
        await client_spaces.scrape_url("https://careers.techcompany.com/jobs/123")

    # Adapter wrapping must translate authentication failure into safe SourceMalformedError
    adapter = FirecrawlSourceAdapter(client=client_empty)
    with pytest.raises(SourceMalformedError) as exc_adapter:
        await adapter.scrape_job("https://careers.techcompany.com/jobs/123")
    assert "Authentication failure" in str(exc_adapter.value)


# ==============================================================================
# TEST 2: SCRAPING SUCCESS TEST
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_2_scraping_success_normalized_job_structure():
    """Scenario 2: Mocked successful Firecrawl response parses into normalized Job structure with all required fields."""
    mock_payload = {
        "metadata": {
            "title": "Senior Python Backend Engineer at TechCorp",
            "ogTitle": "Senior Python Backend Engineer",
            "location": "San Francisco, CA (Remote)",
        },
        "markdown": (
            "# Senior Python Backend Engineer\n\n"
            "**Company**: TechCorp Global\n"
            "**Location**: San Francisco, CA (Remote)\n"
            "**Salary**: $150,000 - $185,000 / year\n\n"
            "## Requirements:\n"
            "- 5+ years of software engineering experience\n"
            "- Bachelor's or Master's degree in Computer Science or related field\n"
            "- Strong proficiency in Python, FastAPI, PostgreSQL, and Docker\n"
            "- Experience with Redis, Kubernetes, and AWS\n\n"
            "## Responsibilities:\n"
            "- Design and scale high-throughput REST APIs and microservices\n"
            "- Collaborate with frontend and ML engineers\n"
        ),
    }

    mock_client = AsyncMock(spec=FirecrawlClient)
    mock_client.scrape_url.return_value = mock_payload

    adapter = FirecrawlSourceAdapter(client=mock_client)
    job_dict = await adapter.scrape_job("https://careers.techcompany.com/jobs/senior-backend")

    # Normalize into Job schema
    job = Job(id="job_scraped_001", **job_dict)

    assert isinstance(job, Job)
    assert job.title == "Senior Python Backend Engineer at TechCorp"
    assert job.company == "TechCorp Global"
    assert "Remote" in job.location or "San Francisco" in job.location
    assert len(job.required_skills) > 0
    assert "Python" in job.required_skills
    assert "FastAPI" in job.required_skills
    assert "$150,000" in str(job.salary) and "$185,000" in str(job.salary)
    assert job.experience_years_required == 5.0
    assert "Bachelor" in str(job.education_required) or "Master" in str(job.education_required)
    assert job.source_url == "https://careers.techcompany.com/jobs/senior-backend"
    assert job.domain in ("Backend Engineering", "Cloud & DevOps")
    assert job.is_active is True


# ==============================================================================
# TEST 3: SCRAPING FAILURE TEST (TIMEOUTS & RATE LIMITS)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_3_scraping_failure_handled_gracefully():
    """Scenario 3: Timeouts and rate limits are handled cleanly with typed domain exceptions without crashing."""
    mock_client = AsyncMock(spec=FirecrawlClient)

    # 1. Timeout handling
    mock_client.scrape_url.side_effect = FirecrawlTimeoutError("Scrape timed out after 15.0s")
    adapter = FirecrawlSourceAdapter(client=mock_client)
    with pytest.raises(SourceTimeoutError) as exc_timeout:
        await adapter.scrape_job("https://careers.techcompany.com/jobs/timeout-job")
    assert "timed out" in str(exc_timeout.value).lower()

    # 2. Rate limit handling
    mock_client.scrape_url.side_effect = FirecrawlRateLimitError("Firecrawl rate limit exceeded: 429 Too Many Requests")
    adapter_rl = FirecrawlSourceAdapter(client=mock_client)
    with pytest.raises(SourceRateLimitError) as exc_rl:
        await adapter_rl.scrape_job("https://careers.techcompany.com/jobs/rate-limit-job")
    assert "rate limit" in str(exc_rl.value).lower()


# ==============================================================================
# TEST 4: MATCHING CALCULATION TEST (EXACT MATHEMATICAL VERIFICATION)
# ==============================================================================
def test_scenario_4_matching_calculation_exact_weighted_formula():
    """Scenario 4: Given factor scores:
       - Technical skills: 80% (Weight 40% -> 32.0)
       - Experience: 90% (Weight 25% -> 22.5)
       - Education: 100% (Weight 10% -> 10.0)
       - Role alignment: 70% (Weight 15% -> 10.5)
       - Project relevance: 60% (Weight 10% -> 6.0)
       Total = 81.0%
    """
    weights = MatchingWeights(
        technical_skills=0.40,
        relevant_experience=0.25,
        education=0.10,
        role_alignment=0.15,
        projects=0.10,
    )
    scorer = DeterministicScorer(weights=weights)

    tech = FactorScoreDetail(
        name="Technical skills",
        score=80.0,
        weight=0.40,
        effective_weight=0.40,
        weighted_score=32.0,
        available=True,
    )
    exp = FactorScoreDetail(
        name="Relevant experience",
        score=90.0,
        weight=0.25,
        effective_weight=0.25,
        weighted_score=22.5,
        available=True,
    )
    edu = FactorScoreDetail(
        name="Education and qualifications",
        score=100.0,
        weight=0.10,
        effective_weight=0.10,
        weighted_score=10.0,
        available=True,
    )
    role = FactorScoreDetail(
        name="Role and responsibility alignment",
        score=70.0,
        weight=0.15,
        effective_weight=0.15,
        weighted_score=10.5,
        available=True,
    )
    proj = FactorScoreDetail(
        name="Projects and domain relevance",
        score=60.0,
        weight=0.10,
        effective_weight=0.10,
        weighted_score=6.0,
        available=True,
    )

    breakdown = ScoreBreakdown(
        skill_score=80.0,
        experience_score=90.0,
        education_score=100.0,
        role_alignment_score=70.0,
        projects_score=60.0,
        technical_skills=tech,
        relevant_experience=exp,
        education=edu,
        role_alignment=role,
        projects=proj,
        factors=[tech, exp, edu, role, proj],
        reduced_confidence=False,
        is_provisional=False,
    )

    final_score = scorer.compute_final_weighted_percentage(breakdown)
    assert final_score == 81.0, f"Expected 81.0%, got {final_score}%"

    # Mathematical identity assertion
    expected_math = round(
        (80.0 * 0.40) + (90.0 * 0.25) + (100.0 * 0.10) + (70.0 * 0.15) + (60.0 * 0.10), 1
    )
    assert final_score == expected_math == 81.0


# ==============================================================================
# TEST 5: RANKING TEST
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_5_ranking_descending_order_and_rank_numbers():
    """Scenario 5: Jobs with different match percentages are ranked strictly descending:
       Rank 1 -> Highest %, Rank 2 -> Second %, Rank 3 -> Lowest %.
    """
    engine = MatchingEngine()

    candidate = CandidateProfile(
        id="cand_test_ranking",
        user_id="usr_001",
        raw_hash=hashlib.sha256(b"Alex Engineer CV").hexdigest(),
        full_name="Alex Engineer",
        email="alex@example.com",
        skills=[
            SkillItem(name="Python", category="language", years=4.0),
            SkillItem(name="FastAPI", category="framework", years=3.0),
            SkillItem(name="PostgreSQL", category="database", years=3.0),
        ],
        experience=[
            ExperienceItem(
                company="Tech Co",
                title="Software Engineer",
                duration="3 years",
                description="Built REST APIs with Python and FastAPI for 3 years",
            )
        ],
        education=[EducationItem(institution="State Univ", degree="Bachelor of Science in CS", graduation_year=2021)],
    )

    # Job A: High match
    job_a = Job(
        id="job_a_high",
        title="Software Engineer - Python",
        company="Alpha Corp",
        location="Remote",
        description="Looking for a Python software engineer with FastAPI experience. BS required. 3 years exp.",
        required_skills=["Python", "FastAPI"],
        preferred_skills=["PostgreSQL"],
        experience_years_required=3.0,
        education_required="Bachelor's Degree",
        responsibilities=["Build REST APIs with Python and FastAPI"],
        domain="Backend Engineering",
        source_url="https://careers.techcompany.com/alpha/job-a",
        is_active=True,
    )

    # Job B: Moderate match
    job_b = Job(
        id="job_b_med",
        title="Full Stack Engineer",
        company="Beta Corp",
        location="Remote",
        description="Seeking Full Stack Engineer with Python and React experience. 4 years required.",
        required_skills=["Python", "React", "Docker"],
        preferred_skills=["FastAPI"],
        experience_years_required=4.0,
        education_required="Bachelor's Degree",
        responsibilities=["Develop full stack web applications"],
        domain="Full Stack Web",
        source_url="https://careers.techcompany.com/beta/job-b",
        is_active=True,
    )

    # Job C: Lower match
    job_c = Job(
        id="job_c_low",
        title="Senior Site Reliability Engineer",
        company="Gamma Corp",
        location="New York, NY",
        description="Senior SRE to manage Kubernetes clusters and Terraform. 7+ years required.",
        required_skills=["Kubernetes", "Terraform", "Go", "AWS"],
        preferred_skills=["Python"],
        experience_years_required=7.0,
        education_required="Master's Degree",
        responsibilities=["Maintain 99.99% uptime and automate CI/CD"],
        domain="DevOps & Infrastructure",
        source_url="https://careers.techcompany.com/gamma/job-c",
        is_active=True,
    )

    ranked_results = await engine.match_and_rank_jobs(candidate, [job_b, job_c, job_a])

    assert len(ranked_results) == 3
    # Ranks must be sequentially 1, 2, 3
    assert [r.rank for r in ranked_results] == [1, 2, 3]

    # Scores must be strictly non-ascending (descending)
    scores = [r.overall_match_percentage for r in ranked_results]
    assert scores == sorted(scores, reverse=True)

    # Job A should rank first because of near-complete skill and role alignment
    assert ranked_results[0].job_id == "job_a_high"
    assert ranked_results[2].job_id == "job_c_low"


# ==============================================================================
# TEST 6: MISSING SKILLS TEST
# ==============================================================================
def test_scenario_6_missing_skills_correctly_identified():
    """Scenario 6: Missing required technical skills are explicitly identified without hallucination."""
    candidate = CandidateProfile(
        id="cand_skills_test",
        user_id="usr_002",
        raw_hash=hashlib.sha256(b"Sam Coder CV").hexdigest(),
        full_name="Sam Coder",
        email="sam@example.com",
        skills=[
            SkillItem(name="Python", category="language"),
            SkillItem(name="Docker", category="tool"),
        ],
    )

    job = Job(
        id="job_missing_skills",
        title="Backend Developer",
        company="CloudTech",
        description="Backend developer needed.",
        required_skills=["Python", "FastAPI", "Kubernetes", "PostgreSQL"],
        preferred_skills=["Docker", "AWS"],
        is_active=True,
    )

    engine = MatchingEngine()
    gaps = engine.gap_analyzer.analyze(candidate, job)

    assert "Python" in gaps.matched_skills
    assert "FastAPI" in gaps.missing_skills
    assert "Kubernetes" in gaps.missing_skills
    assert "PostgreSQL" in gaps.missing_skills
    # Docker is matched from preferred skills
    assert "Docker" in gaps.matched_skills


# ==============================================================================
# TEST 7: MISSING INFORMATION TEST (WEIGHT RENORMALIZATION)
# ==============================================================================
def test_scenario_7_missing_information_renormalization_without_unearned_scores():
    """Scenario 7: When a job omits education or domain requirements, the factor is marked unavailable,
       the remaining factor weights are renormalized to 100%, and reduced_confidence is set to True.
       Never awards an unearned 100% score.
    """
    scorer = DeterministicScorer()

    candidate = CandidateProfile(
        id="cand_003",
        user_id="usr_003",
        raw_hash=hashlib.sha256(b"Morgan Tech CV").hexdigest(),
        full_name="Morgan Tech",
        email="morgan@example.com",
        skills=[SkillItem(name="Python", category="language")],
    )

    # Job with no education specified in schema or description
    job_sparse = Job(
        id="job_sparse_info",
        title="Python Developer",
        company="StartupX",
        description="Python developer wanted. Seeking 3 years exp with Python.",
        required_skills=["Python"],
        experience_years_required=3.0,
        education_required=None,  # Unavailable
        domain=None,
        is_active=True,
    )

    overall_score, breakdown = scorer.compute_overall_score(candidate, job_sparse)

    # Education must be marked unavailable
    assert breakdown.education.available is False
    assert breakdown.education.score == 0.0  # NOT awarded 100%

    # Renormalization flag should be set
    assert breakdown.reduced_confidence is True

    # The effective weights of available factors must sum to 1.0 (100%)
    available_factors = [f for f in breakdown.factors if f.available]
    effective_weight_sum = sum(f.effective_weight for f in available_factors)
    assert pytest.approx(effective_weight_sum, 0.01) == 1.0


# ==============================================================================
# TEST 8: DUPLICATE JOBS DEDUPLICATION TEST
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_8_duplicate_jobs_deduplicated():
    """Scenario 8: Duplicate job postings with identical content or source URLs are merged/deduplicated."""
    engine = MatchingEngine()

    candidate = CandidateProfile(
        id="cand_dedup",
        user_id="usr_004",
        raw_hash=hashlib.sha256(b"Taylor Dev CV").hexdigest(),
        full_name="Taylor Dev",
        email="taylor@example.com",
        skills=[SkillItem(name="Python", category="language")],
    )

    job1 = Job(
        id="job_dup_1",
        title="Python Engineer",
        company="DupeCorp",
        description="Python engineer role at DupeCorp.",
        source_url="https://careers.techcompany.com/dupe/1",
        required_skills=["Python"],
        content_hash="hash_abc123",
        is_active=True,
    )

    job2 = Job(
        id="job_dup_2",
        title="Python Engineer",
        company="DupeCorp",
        description="Python engineer role at DupeCorp.",
        source_url="https://careers.techcompany.com/dupe/1",  # Same URL and content hash
        required_skills=["Python"],
        content_hash="hash_abc123",
        is_active=True,
    )

    job3 = Job(
        id="job_unique",
        title="Data Engineer",
        company="DataCorp",
        description="Data Engineer role.",
        source_url="https://careers.techcompany.com/datacorp/2",
        required_skills=["Python"],
        content_hash="hash_xyz999",
        is_active=True,
    )

    results = await engine.match_and_rank_jobs(candidate, [job1, job2, job3])

    # Exactly 2 unique jobs should be returned, not 3
    assert len(results) == 2
    returned_urls = [str(r.job.source_url) for r in results]
    assert len(set(returned_urls)) == 2


# ==============================================================================
# TEST 9: API INTEGRATION TEST (/upload-and-match)
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_9_api_upload_and_match_endpoint():
    """Scenario 9: POST /api/v1/resumes/upload-and-match returns 5-factor breakdown, weights, ranks, and metadata."""
    sample_cv = """
    Jane Smith
    Senior Software Engineer
    jane.smith@example.com

    Summary:
    5 years of backend engineering experience building scalable microservices with Python, FastAPI, and PostgreSQL.

    Experience:
    Senior Backend Engineer - FinTech Innovations (2021 - Present)
    - Architected REST and GraphQL APIs using FastAPI and PostgreSQL.
    - Containerized microservices using Docker and orchestrated deployments on Kubernetes.

    Skills:
    Python, FastAPI, PostgreSQL, Docker, Redis, Git, Microservices

    Education:
    Bachelor of Science in Computer Science - University of California (2020)
    """

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post(
            "/api/v1/resumes/upload-and-match",
            files={"file": ("jane_smith_resume.txt", sample_cv.encode("utf-8"), "text/plain")},
        )

        assert response.status_code in (200, 201), f"Response failed: {response.text}"
        payload = response.json()
        assert payload.get("success") is True
        data = payload.get("data", {})

        assert "profile" in data
        assert "ranked_matches" in data
        assert "scraping_meta" in data
        assert "embedding_meta" in data

        matches = data["ranked_matches"]
        assert len(matches) > 0

        first_item = matches[0]
        assert "match" in first_item
        assert "job" in first_item

        first_match = first_item["match"]
        assert first_match["rank"] == 1
        assert "overall_score" in first_match
        assert "breakdown" in first_match

        # Verify all 5 factors are present in the breakdown
        breakdown = first_match["breakdown"]
        for factor_key in [
            "technical_skills",
            "relevant_experience",
            "education",
            "role_alignment",
            "projects",
        ]:
            assert factor_key in breakdown
            factor_detail = breakdown[factor_key]
            assert "score" in factor_detail
            assert "weight" in factor_detail
            assert "available" in factor_detail

        assert "matched_skills" in first_match
        assert "experience_comparison" in first_match
        assert "strong_matching_points" in first_match
        assert "missing_qualifications" in first_match


# ==============================================================================
# TEST 10: FRONTEND INTEGRATION & CONTRACT TEST
# ==============================================================================
@pytest.mark.asyncio
async def test_scenario_10_frontend_contract_and_assets():
    """Scenario 10: Validates that frontend HTML, CSS, and JS include all required 5-factor elements and rendering logic."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Frontend entry point serves successfully
        resp_index = await client.get("/")
        assert resp_index.status_code == 200
        html_content = resp_index.text

        # Verify key pipeline UI components exist in HTML
        assert 'id="dropzoneCv"' in html_content
        assert 'id="inputCvFile"' in html_content
        assert 'id="pipelineProgressCard"' in html_content
        assert 'id="candidateSummaryBanner"' in html_content
        assert 'id="rankedMatchesSection"' in html_content
        assert 'id="overviewMatchesList"' in html_content

        # 2. Frontend app.js has rendering functions implemented
        resp_js = await client.get("/frontend-assets/app.js")
        assert resp_js.status_code == 200
        js_content = resp_js.text

        assert "renderRankedMatchesList" in js_content
        assert "renderCandidateSummaryBanner" in js_content
        assert "factor-breakdown-grid" in js_content
        assert "experience_comparison" in js_content
        assert "strong_matching_points" in js_content
        assert "missing_qualifications" in js_content
        assert "escapeHtml" in js_content

        # 3. Frontend styles.css has responsive factor styles
        resp_css = await client.get("/frontend-assets/styles.css")
        assert resp_css.status_code == 200
        css_content = resp_css.text

        assert ".factor-breakdown-grid" in css_content
        assert ".factor-card" in css_content
        assert ".rank-badge-pill" in css_content
        assert ".experience-comparison-box" in css_content
