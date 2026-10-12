"""Unit test suite for Developer B Phase 1 data contracts and Pydantic schemas.

Verifies schemas defined in docs/api-contracts.md:
1. Authentication & Principal schemas (valid inputs, invalid email, short password)
2. Resume & CandidateProfile schemas (skills, confidence ranges, experience items)
3. Job listing schemas (provenance, validation constraints, ingestion payloads)
4. Matching & Gap Analysis schemas (scoring ranges [0-100], mandatory caveat)
5. Envelope integration (serialization within StandardResponse[T])
6. Pydantic validation error handling on malformed schemas
"""

import pytest
from pydantic import ValidationError

from app.schemas.auth import (
    AuthenticatedPrincipal,
    TokenResponse,
    UserLoginRequest,
    UserRegisterRequest,
    UserResponse,
)
from app.schemas.common import StandardResponse
from app.schemas.job import Job, JobIngestRequest, JobIngestResponse, JobSourceProvenance
from app.schemas.matching import (
    MatchRequest,
    MatchResult,
    RecommendedMatchesResponse,
    ScoreBreakdown,
)
from app.schemas.resume import (
    CandidateProfile,
    EducationItem,
    ExperienceItem,
    SkillItem,
)


def test_auth_registration_and_principal_schemas() -> None:
    """Test 1: Auth schemas validate correct inputs and reject invalid email/password."""
    # Valid registration
    valid_data = {
        "email": "candidate@example.com",
        "password": "SecurePassword123!",
        "full_name": "Alex Mercer",
        "role": "candidate",
    }
    req = UserRegisterRequest(**valid_data)
    assert req.email == "candidate@example.com"
    assert req.role == "candidate"

    # Invalid email rejected
    with pytest.raises(ValidationError) as exc_info:
        UserRegisterRequest(
            email="not-an-email",
            password="SecurePassword123!",
            full_name="Alex Mercer",
        )
    assert "email" in str(exc_info.value)

    # Password too short (<8 chars) rejected
    with pytest.raises(ValidationError) as exc_info:
        UserRegisterRequest(
            email="valid@example.com",
            password="short",
            full_name="Alex Mercer",
        )
    assert "password" in str(exc_info.value)

    # Principal schema
    principal = AuthenticatedPrincipal(
        id="usr_01",
        email="candidate@example.com",
        full_name="Alex Mercer",
        role="candidate",
    )
    assert principal.is_active is True
    assert principal.role == "candidate"


def test_resume_candidate_profile_schemas() -> None:
    """Test 2: CandidateProfile schemas validate skills and enforce confidence bounds."""
    # Valid skill
    skill = SkillItem(name="Python", category="language", confidence=0.95, years_experience=4.0)
    assert skill.confidence == 0.95

    # Skill with invalid confidence (> 1.0) rejected
    with pytest.raises(ValidationError):
        SkillItem(name="Python", confidence=1.5)

    # Skill with negative confidence (< 0.0) rejected
    with pytest.raises(ValidationError):
        SkillItem(name="Python", confidence=-0.1)

    # Full profile
    profile = CandidateProfile(
        id="prof_01",
        user_id="usr_01",
        raw_hash="hash123",
        full_name="Alex Mercer",
        email="candidate@example.com",
        summary="Experienced Python engineer",
        skills=[skill],
        experience=[
            ExperienceItem(
                title="Software Engineer",
                company="Tech Solutions",
                start_date="2023-01",
                end_date="2025-06",
                description="Built FastAPI services.",
            )
        ],
        education=[
            EducationItem(
                institution="State University",
                degree="B.S. CS",
                graduation_year=2022,
            )
        ],
    )
    assert len(profile.skills) == 1
    assert profile.skills[0].name == "Python"
    assert len(profile.experience) == 1
    assert profile.raw_hash == "hash123"


def test_job_listing_and_ingestion_schemas() -> None:
    """Test 3: Job schemas enforce description length and provenance tracking."""
    job = Job(
        id="job_01",
        title="Senior Python Developer",
        company="Enterprise Tech",
        location="Remote",
        description="Looking for an experienced Python developer to build robust microservices.",
        required_skills=["Python", "FastAPI", "SQL"],
        preferred_skills=["Docker"],
        source="firecrawl",
        source_url="https://approved-board.example.com/job/1",
    )
    assert job.is_active is True
    assert "FastAPI" in job.required_skills

    # Job description too short (<10 chars) rejected
    with pytest.raises(ValidationError):
        Job(
            id="job_02",
            title="Dev",
            company="Co",
            description="Short",
        )

    # Ingestion request requires at least one URL
    with pytest.raises(ValidationError):
        JobIngestRequest(source_urls=[])

    # Valid ingestion request
    ingest_req = JobIngestRequest(source_urls=["https://approved.example.com/job"])
    assert ingest_req.max_pages == 1


def test_matching_and_gap_analysis_schemas() -> None:
    """Test 4: MatchResult enforces 0-100 score bounds and includes mandatory caveat."""
    breakdown = ScoreBreakdown(skill_score=80.0, experience_score=85.0, education_score=90.0)
    match = MatchResult(
        candidate_id="prof_01",
        job_id="job_01",
        overall_score=83.5,
        breakdown=breakdown,
        matched_skills=["Python", "FastAPI"],
        missing_skills=["SQL"],
        explanation="Candidate meets core Python and FastAPI requirements; missing SQL.",
        improvement_suggestions=["Highlight relational database projects."],
    )
    assert match.overall_score == 83.5
    assert "not guarantee an interview" in match.caveat

    # Overall score > 100 rejected
    with pytest.raises(ValidationError):
        MatchResult(
            candidate_id="prof_01",
            job_id="job_01",
            overall_score=105.0,
            breakdown=breakdown,
            explanation="Invalid score",
        )

    # Sub-score < 0 rejected
    with pytest.raises(ValidationError):
        ScoreBreakdown(skill_score=-5.0, experience_score=80.0, education_score=80.0)


def test_envelope_integration_with_domain_models() -> None:
    """Test 5: Developer B domain models serialize properly inside StandardResponse envelope."""
    breakdown = ScoreBreakdown(skill_score=90.0, experience_score=90.0, education_score=90.0)
    match = MatchResult(
        candidate_id="prof_01",
        job_id="job_01",
        overall_score=90.0,
        breakdown=breakdown,
        matched_skills=["Python"],
        missing_skills=[],
        explanation="Excellent match",
    )

    envelope = StandardResponse[MatchResult](data=match, message="Match calculated successfully")
    serialized = envelope.model_dump()

    assert serialized["success"] is True
    assert serialized["data"]["overall_score"] == 90.0
    assert serialized["data"]["candidate_id"] == "prof_01"
    assert serialized["message"] == "Match calculated successfully"
    assert "timestamp" in serialized
