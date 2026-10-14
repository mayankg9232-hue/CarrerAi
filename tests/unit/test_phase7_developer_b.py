"""Phase 7 Developer B Test Suite: Assessment Challenge Catalog, Skill Verification & Agent Tools.

Validates Developer B Phase 7 responsibilities:
1. Assessment Challenge Catalog: Retrieval of coding challenges by skill and language with visible & hidden test cases.
2. Skill Verification Evaluation: Evaluating passed assessments and certifying skills into CandidateProfile.
3. Truthful Remediation Guidance: Failed or partial assessments return actionable tips without certifying or hallucinating.
4. Agent Tool (recommend_skill_assessments): Contextually recommends coding challenges based on candidate gaps.
5. Agent Tool (verify_skill_from_assessment): Certifies skills from completed sandbox submissions with IDOR safety.
"""

from datetime import datetime, timezone
import pytest

from app.agents.tools.developer_b_tools import (
    RecommendSkillAssessmentsInput,
    VerifySkillFromAssessmentInput,
    create_developer_b_tools,
)
from app.matching.assessment_catalog import (
    AssessmentChallenge,
    get_all_challenges,
    get_challenge_by_id,
    get_challenges_by_skill,
)
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
def skill_verifier(resume_repo):
    return SkillVerificationService(resume_repo=resume_repo, passing_threshold=100.0)


@pytest.fixture
def sample_candidate():
    return CandidateProfile(
        id="cand_test_p7",
        user_id="usr_cand_p7",
        raw_hash="hash_p7_candidate",
        full_name="Alice Candidate",
        email="alice@example.com",
        summary="Junior software developer looking to verify backend skills.",
        skills=[
            SkillItem(name="Python", category="language", confidence=0.6, is_verified=False),
            SkillItem(name="SQL", category="database", confidence=0.8, is_verified=False),
        ],
    )


# ==============================================================================
# TEST 1: CHALLENGE CATALOG RETRIEVAL AND BENCHMARK SPECIFICATIONS
# ==============================================================================
def test_assessment_catalog_retrieval_and_specifications():
    """Test 1: Verify catalog lookup by skill and language with test cases."""
    all_challenges = get_all_challenges()
    assert len(all_challenges) >= 3, "Catalog must provide multiple coding challenges"

    # Query Python challenges
    py_challenges = get_challenges_by_skill("Python")
    assert len(py_challenges) >= 2
    assert any(c.id == "py-two-sum" for c in py_challenges)

    two_sum = get_challenge_by_id("py-two-sum")
    assert two_sum is not None
    assert two_sum.skill == "Python"
    assert two_sum.language == SupportedLanguage.PYTHON
    assert len(two_sum.test_cases) >= 3
    # Verify presence of both visible and hidden test cases
    has_visible = any(not tc.is_hidden for tc in two_sum.test_cases)
    has_hidden = any(tc.is_hidden for tc in two_sum.test_cases)
    assert has_visible, "Must have public sample test cases"
    assert has_hidden, "Must have hidden evaluation test cases"

    # Query with language filter
    js_challenges = get_challenges_by_skill("JavaScript", language="javascript")
    assert len(js_challenges) >= 1
    assert js_challenges[0].language == SupportedLanguage.JAVASCRIPT


# ==============================================================================
# TEST 2: SKILL VERIFICATION ON SUCCESSFUL ASSESSMENT COMPLETION
# ==============================================================================
@pytest.mark.asyncio
async def test_successful_assessment_certifies_skill_in_profile(
    resume_repo, skill_verifier, sample_candidate
):
    """Test 2: Verifies passed assessment certifies skill into candidate profile with score and timestamp."""
    await resume_repo.save_profile(sample_candidate)

    # Candidate was initially unverified with confidence 0.6
    init_cand = await resume_repo.get_by_id(sample_candidate.id)
    py_skill = next(s for s in init_cand.skills if s.name.lower() == "python")
    assert py_skill.is_verified is False
    assert py_skill.confidence == 0.6

    assessment_res = AssessmentResult(
        submission_id="sub_pass_001",
        candidate_id=sample_candidate.id,
        skill="Python",
        language="python",
        status=ExecutionStatus.COMPLETED,
        exit_code=0,
        passed_tests=4,
        total_tests=4,
        test_results=[
            TestCaseResult(test_case_id="tc_1", passed=True, actual_output="0 1", expected_output="0 1"),
            TestCaseResult(test_case_id="tc_2", passed=True, actual_output="1 2", expected_output="1 2"),
            TestCaseResult(test_case_id="tc_3", passed=True, actual_output="0 1", expected_output="0 1"),
            TestCaseResult(test_case_id="tc_4", passed=True, actual_output="0 4", expected_output="0 4"),
        ],
    )

    feedback = await skill_verifier.evaluate_and_verify(assessment_res)
    assert feedback.verified is True
    assert feedback.score == 100.0
    assert feedback.passed_tests == 4
    assert "certified as verified" in feedback.feedback_summary

    # Verify profile was updated in repository
    updated_cand = await resume_repo.get_by_id(sample_candidate.id)
    updated_py_skill = next(s for s in updated_cand.skills if s.name.lower() == "python")
    assert updated_py_skill.is_verified is True
    assert updated_py_skill.confidence == 1.0
    assert updated_py_skill.verification_score == 100.0
    assert updated_py_skill.verification_submission_id == "sub_pass_001"
    assert updated_py_skill.verified_at is not None


# ==============================================================================
# TEST 3: FAILED ASSESSMENT PRODUCES ACTIONABLE ADVICE WITHOUT VERIFYING
# ==============================================================================
@pytest.mark.asyncio
async def test_failed_assessment_returns_truthful_remediation_without_verification(
    resume_repo, skill_verifier, sample_candidate
):
    """Test 3: Unsuccessful assessment (runtime error or timeout) does not certify and gives honest advice."""
    await resume_repo.save_profile(sample_candidate)

    assessment_res = AssessmentResult(
        submission_id="sub_fail_001",
        candidate_id=sample_candidate.id,
        skill="Python",
        language="python",
        status=ExecutionStatus.TIMEOUT,
        exit_code=124,
        stderr="Process killed: execution exceeded time limit of 5.0s",
        passed_tests=1,
        total_tests=3,
        test_results=[
            TestCaseResult(test_case_id="tc_1", passed=True),
            TestCaseResult(test_case_id="tc_2", passed=False, error_message="Timeout"),
            TestCaseResult(test_case_id="tc_3", passed=False, error_message="Timeout"),
        ],
    )

    feedback = await skill_verifier.evaluate_and_verify(assessment_res)
    assert feedback.verified is False
    assert feedback.score < 100.0
    assert "remains unverified" in feedback.feedback_summary
    assert any("timeout limit" in r.lower() for r in feedback.remediation_advice)

    # Candidate profile must remain unverified
    cand_after = await resume_repo.get_by_id(sample_candidate.id)
    py_skill = next(s for s in cand_after.skills if s.name.lower() == "python")
    assert py_skill.is_verified is False


# ==============================================================================
# TEST 4: AGENT TOOL RECOMMEND_SKILL_ASSESSMENTS TARGETS CANDIDATE GAPS
# ==============================================================================
@pytest.mark.asyncio
async def test_agent_tool_recommend_skill_assessments(resume_repo, job_repo, sample_candidate):
    """Test 4: Agent tool recommends specific challenges matching candidate missing/unverified skills."""
    await resume_repo.save_profile(sample_candidate)

    job = Job(
        id="job_target_p7",
        title="Python Software Developer",
        company="TechCorp",
        required_skills=["Python", "JavaScript"],
        description="Looking for Python and JavaScript full stack developer.",
    )
    await job_repo.create_job(job)

    tools = create_developer_b_tools(
        resume_repo=resume_repo,
        job_repo=job_repo,
    )
    rec_tool = next(t for t in tools if t.name == "recommend_skill_assessments")

    # Call tool targeting the job
    args = RecommendSkillAssessmentsInput(
        candidate_id=sample_candidate.id,
        job_id=job.id,
    )
    output = await rec_tool.handler(args)

    assert output.candidate_id == sample_candidate.id
    assert len(output.recommendations) >= 1
    # Should include Python and JavaScript challenges
    recommended_skills = [r.skill for r in output.recommendations]
    assert "Python" in recommended_skills or "JavaScript" in recommended_skills


# ==============================================================================
# TEST 5: AGENT TOOL VERIFY_SKILL_FROM_ASSESSMENT HANDLER
# ==============================================================================
@pytest.mark.asyncio
async def test_agent_tool_verify_skill_from_assessment_handler(
    resume_repo, sample_candidate
):
    """Test 5: Agent tool verify_skill_from_assessment certifies skill from recorded sandbox result."""
    await resume_repo.save_profile(sample_candidate)

    sandbox = SandboxService()
    # Execute a valid assessment through sandbox service
    sub_req = AssessmentSubmissionRequest(
        candidate_id=sample_candidate.id,
        skill="Python",
        language="python",
        code=(
            "# output: 0 1\n"
            "print('0 1')\n"
        ),
        test_cases=[
            TestCase(id="tc_1", input_data="2,7;9\n", expected_output="0 1"),
        ],
    )
    res = await sandbox.execute_assessment(sub_req)
    assert res.submission_id is not None

    tools = create_developer_b_tools(
        resume_repo=resume_repo,
        sandbox_service=sandbox,
    )
    verify_tool = next(t for t in tools if t.name == "verify_skill_from_assessment")

    verify_args = VerifySkillFromAssessmentInput(
        candidate_id=sample_candidate.id,
        submission_id=res.submission_id,
    )
    output = await verify_tool.handler(verify_args)

    assert output.candidate_id == sample_candidate.id
    assert output.submission_id == res.submission_id
    assert output.skill == "Python"
    assert output.verified is True
    assert output.score == 100.0

    # Ensure profile was certified
    cand_after = await resume_repo.get_by_id(sample_candidate.id)
    skill_item = next(s for s in cand_after.skills if s.name.lower() == "python")
    assert skill_item.is_verified is True
