"""Explainability engine producing transparent, grounded rationale for match scores."""

from typing import List, Optional
from app.matching.gap_analysis import GapAnalysisResult
from app.schemas.job import Job
from app.schemas.matching import ScoreBreakdown
from app.schemas.resume import CandidateProfile


class MatchExplainer:
    """Generates transparent, grounded explanations directly reflecting score inputs."""

    MANDATORY_DISCLAIMER = (
        "This match score is an automated estimate and does not guarantee an interview or employment offer."
    )

    def generate_explanation(
        self,
        candidate: CandidateProfile,
        job: Job,
        overall_score: float,
        breakdown: ScoreBreakdown,
        gap_analysis: GapAnalysisResult,
    ) -> str:
        """Construct a transparent explanation strictly consistent with score inputs."""
        # 1. High-level assessment
        if overall_score >= 80.0:
            assessment = "Strong match"
            fit_summary = (
                f"Strong match: Candidate exhibits a strong overall alignment ({overall_score:.1f}/100) "
                f"with the requirements for {job.title} at {job.company}."
            )
        elif overall_score >= 50.0:
            assessment = "Moderate match"
            fit_summary = (
                f"Moderate match: Candidate presents a moderate alignment ({overall_score:.1f}/100) "
                f"with partial skill overlap for {job.title} at {job.company}."
            )
        else:
            assessment = "Weak match"
            fit_summary = (
                f"Weak match: Candidate demonstrates limited alignment ({overall_score:.1f}/100) "
                f"with substantial skill or experience gaps for {job.title} at {job.company}."
            )

        # 2. Score breakdown details
        breakdown_text = (
            f"Component Breakdown: Technical Skills: {breakdown.skill_score:.1f}/100 | "
            f"Experience: {breakdown.experience_score:.1f}/100 | "
            f"Education: {breakdown.education_score:.1f}/100."
        )

        # 3. Strengths and verified overlap
        if gap_analysis.matched_skills:
            strengths_text = f"Verified Strengths: Possesses matching skills including {', '.join(gap_analysis.matched_skills)}."
        else:
            strengths_text = "Verified Strengths: No mandatory technical skills matched in the candidate's profile."

        # 4. Gaps and missing requirements
        if gap_analysis.missing_skills:
            gaps_text = f"Key Gaps: Missing required skills in {', '.join(gap_analysis.missing_skills)}."
        else:
            gaps_text = "Key Gaps: All primary required skills are satisfied by the candidate profile."

        # 5. Uncertainties / missing information
        uncertain_text = ""
        if gap_analysis.uncertain_skills:
            uncertain_text = (
                f"Uncertainties: Mentions of {', '.join(gap_analysis.uncertain_skills)} "
                f"require verification or proficiency clarification."
            )

        if not candidate.experience:
            uncertain_text += " Note: Candidate profile lacks formal work experience history."

        # Assemble full explanation
        sections = [fit_summary, breakdown_text, strengths_text, gaps_text]
        if uncertain_text:
            sections.append(uncertain_text.strip())

        return "\n\n".join(sections)
