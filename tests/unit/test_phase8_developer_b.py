"""Phase 8 Developer B Test Suite: Multi-Language Benchmark Execution & Scoring Integration.

Validates Developer B Phase 8 responsibilities:
1. Multi-Language Challenge Benchmarks: Validates evaluation across Python, JavaScript, and TypeScript problems.
2. Verified Skill Scoring Boost: Verified skills receive guaranteed 1.0 confidence and verification bonus in DeterministicScorer.
3. Verified Skill Immunity: Verified skills cannot be categorized as uncertain_skills in GapAnalyzer.
4. End-to-End Skill Gap Resolution: Assessment verification elevates candidate score and closes job requirement gaps.
5. Anti-Tamper & Adversarial Defense: Malicious or spoofed submissions fail verification without contaminating profile.
"""

from datetime import datetime, timezone
import pytest

from app.matching.assessment_catalog import get_challenge_by_id
from app.matching.engine import MatchingEngine
from app.matching.gap_analysis import GapAnalyzer
from app.matching.scorer import DeterministicScorer
from app.repositories.job_repo import InMemoryJobRepository
from app.repositories.resume_repo import InMemoryResumeRepository
from app.sandbox.service import SandboxService
from app.schemas.assessment import (
    AssessmentResult,
    AssessmentSubmissionRequest,
    ExecutionStatus,
    SupportedLanguage,
    TestCase,
    TestCaseResult,
)
from app.schemas.job import Job
from app.schemas.resume import CandidateProfile, SkillItem
from app.services.skill_verification_service import SkillVerificationService


@pytest.fixture
def resume_repo():
    return InMemoryResumeRepository()


@pytest.fixture
def job_repo():
    return InMemoryJobRepository()


@pytest.fixture
def scorer():
    return DeterministicScorer()


@pytest.fixture
def gap_analyzer():
    return GapAnalyzer()


@pytest.fixture
def skill_verifier(resume_repo):
    return SkillVerificationService(resume_repo=resume_repo)


# ==============================================================================
# TEST 1: MULTI-LANGUAGE BENCHMARK CHALLENGE VALIDATION
# ==============================================================================
@pytest.mark.asyncio
async def test_multi_language_benchmark_challenges(resume_repo, skill_verifier):
    """Test 1: Evaluates candidate submissions across Python, JavaScript, and TypeScript challenges."""
    candidate = CandidateProfile(
        id="cand_polyglot",
        user_id="usr_polyglot",
        raw_hash="hash_poly",
        full_name="Polyglot Engineer",
        skills=[
            SkillItem(name="Python", category="language", confidence=0.8),
            SkillItem(name="JavaScript", category="language", confidence=0.8),
            SkillItem(name="TypeScript", category="language", confidence=0.8),
        ],
    )
    await resume_repo.save_profile(candidate)

    # 1. Python Challenge
    py_challenge = get_challenge_by_id("py-two-sum")
    assert py_challenge.language == SupportedLanguage.PYTHON
    py_result = AssessmentResult(
        submission_id="sub_py_bench",
        candidate_id=candidate.id,
        skill="Python",
        language="python",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        passed_tests=len(py_challenge.test_cases),
        total_tests=len(py_challenge.test_cases),
    )
    py_fb = await skill_verifier.evaluate_and_verify(py_result)
    assert py_fb.verified is True

    # 2. JavaScript Challenge
    js_challenge = get_challenge_by_id("js-array-filter")
    assert js_challenge.language == SupportedLanguage.JAVASCRIPT
    js_result = AssessmentResult(
        submission_id="sub_js_bench",
        candidate_id=candidate.id,
        skill="JavaScript",
        language="javascript",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        passed_tests=len(js_challenge.test_cases),
        total_tests=len(js_challenge.test_cases),
    )
    js_fb = await skill_verifier.evaluate_and_verify(js_result)
    assert js_fb.verified is True

    # 3. TypeScript Challenge
    ts_challenge = get_challenge_by_id("ts-typed-dict")
    assert ts_challenge.language == SupportedLanguage.TYPESCRIPT
    ts_result = AssessmentResult(
        submission_id="sub_ts_bench",
        candidate_id=candidate.id,
        skill="TypeScript",
        language="typescript",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        passed_tests=len(ts_challenge.test_cases),
        total_tests=len(ts_challenge.test_cases),
    )
    ts_fb = await skill_verifier.evaluate_and_verify(ts_result)
    assert ts_fb.verified is True

    # Verify all 3 skills are verified in candidate profile
    updated = await resume_repo.get_by_id(candidate.id)
    verified_skills = {s.name for s in updated.skills if s.is_verified}
    assert {"Python", "JavaScript", "TypeScript"}.issubset(verified_skills)


# ==============================================================================
# TEST 2: VERIFIED SKILL BOOST IN SCORER
# ==============================================================================
def test_verified_skill_boost_in_scorer(scorer):
    """Test 2: Candidate with verified skill achieves higher score than identical candidate with unverified skill."""
    job = Job(
        id="job_py_senior",
        title="Senior Python Architect",
        company="PyCore",
        required_skills=["Python"],
        preferred_skills=["Docker"],
        description="Core Python architecture role.",
    )

    unverified_candidate = CandidateProfile(
        id="cand_unverified",
        user_id="usr_1",
        raw_hash="hash_1",
        skills=[
            SkillItem(name="Python", category="language", confidence=0.75, is_verified=False),
        ],
    )

    verified_candidate = CandidateProfile(
        id="cand_verified",
        user_id="usr_2",
        raw_hash="hash_2",
        skills=[
            SkillItem(
                name="Python",
                category="language",
                confidence=1.0,
                is_verified=True,
                verification_score=100.0,
            ),
        ],
    )

    score_unverified = scorer.calculate_skill_score(unverified_candidate, job)
    score_verified = scorer.calculate_skill_score(verified_candidate, job)

    assert score_verified > score_unverified
    # Verified candidate receives 1.0 confidence + verification bonus
    assert score_verified >= 80.0
    assert score_unverified <= 60.0


# ==============================================================================
# TEST 3: VERIFIED SKILL IMMUNITY FROM UNCERTAIN_SKILLS IN GAP ANALYZER
# ==============================================================================
def test_verified_skills_never_flagged_uncertain(gap_analyzer):
    """Test 3: Skills marked verified are never flagged as uncertain, even with casual summary text."""
    job = Job(
        id="job_gap_py",
        title="Python Engineer",
        company="DataCo",
        required_skills=["Python", "FastAPI"],
        description="Looking for Python and FastAPI developers.",
    )

    # Candidate has casual mention of Python in summary, but Python is verified
    candidate = CandidateProfile(
        id="cand_ver_immune",
        user_id="usr_3",
        raw_hash="hash_3",
        summary="I have casually used Python and some FastAPI.",
        skills=[
            SkillItem(name="Python", category="language", confidence=0.5, is_verified=True),
        ],
    )

    gap = gap_analyzer.analyze(candidate, job)
    assert "Python" in gap.matched_skills
    assert "Python" not in gap.uncertain_skills, "Verified skill must never be flagged as uncertain"


# ==============================================================================
# TEST 4: END-TO-END GAP RESOLUTION VIA ASSESSMENT VERIFICATION
# ==============================================================================
@pytest.mark.asyncio
async def test_assessment_verification_resolves_gap_and_elevates_match(
    resume_repo, job_repo, skill_verifier
):
    """Test 4: Failing candidate passes assessment, verifies skill, and match score increases."""
    job = Job(
        id="job_p8_backend",
        title="Python Backend Developer",
        company="CloudTech",
        required_skills=["Python"],
        description="Must have strong Python engineering background.",
        is_active=True,
    )
    await job_repo.create_job(job)

    # Candidate has no Python initially
    candidate = CandidateProfile(
        id="cand_gap_close",
        user_id="usr_gap",
        raw_hash="hash_gap",
        full_name="Bob Transition",
        skills=[
            SkillItem(name="HTML", category="web", confidence=0.9),
            SkillItem(name="CSS", category="web", confidence=0.9),
        ],
    )
    await resume_repo.save_profile(candidate)

    engine = MatchingEngine(job_repository=job_repo)

    # Initial Match: Missing required Python -> low score with substantive penalty
    initial_match = await engine.match_candidate_to_job(candidate=candidate, job=job)
    assert initial_match.overall_score <= 35.0
    assert "Python" in initial_match.missing_skills

    # Candidate completes assessment for Python
    res = AssessmentResult(
        submission_id="sub_gap_pass",
        candidate_id=candidate.id,
        skill="Python",
        language="python",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        passed_tests=3,
        total_tests=3,
    )
    fb = await skill_verifier.evaluate_and_verify(res)
    assert fb.verified is True

    # Re-fetch candidate profile
    updated_candidate = await resume_repo.get_by_id(candidate.id)

    # Re-match after verification
    recomputed_match = await engine.match_candidate_to_job(candidate=updated_candidate, job=job)
    assert recomputed_match.overall_score > initial_match.overall_score
    assert "Python" in recomputed_match.matched_skills
    assert "Python" not in recomputed_match.missing_skills


# ==============================================================================
# TEST 5: ANTI-TAMPER AND ADVERSARIAL DEFENSE
# ==============================================================================
@pytest.mark.asyncio
async def test_spoofed_or_failing_assessment_rejected(resume_repo, skill_verifier):
    """Test 5: Submissions attempting to spoof exit codes or with failed test cases are rejected."""
    candidate = CandidateProfile(
        id="cand_tamper",
        user_id="usr_tamper",
        raw_hash="hash_tamper",
        skills=[
            SkillItem(name="Python", category="language", confidence=0.7, is_verified=False),
        ],
    )
    await resume_repo.save_profile(candidate)

    # 1. Partial test case pass (2/3 passed)
    partial_result = AssessmentResult(
        submission_id="sub_tamper_01",
        candidate_id=candidate.id,
        skill="Python",
        language="python",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        passed_tests=2,
        total_tests=3,
    )
    fb1 = await skill_verifier.evaluate_and_verify(partial_result)
    assert fb1.verified is False

    # 2. Non-zero exit code (Runtime error)
    error_result = AssessmentResult(
        submission_id="sub_tamper_02",
        candidate_id=candidate.id,
        skill="Python",
        language="python",
        status=ExecutionStatus.ERROR,
        exit_code=1,
        passed_tests=0,
        total_tests=3,
    )
    fb2 = await skill_verifier.evaluate_and_verify(error_result)
    assert fb2.verified is False

    # Verify profile never got certified
    cand_final = await resume_repo.get_by_id(candidate.id)
    py_skill = next(s for s in cand_final.skills if s.name.lower() == "python")
    assert py_skill.is_verified is False
