"""Deterministic multi-factor scoring engine calculating explainable match scores."""

import re
from typing import Optional, Tuple
from app.nlp.normalizer import canonicalize_skill_name
from app.schemas.job import Job
from app.schemas.matching import ScoreBreakdown
from app.schemas.resume import CandidateProfile


class DeterministicScorer:
    """Multi-factor explainable scoring engine aligning skills, experience, education, and semantics."""

    def __init__(
        self,
        skill_weight: float = 0.45,
        experience_weight: float = 0.25,
        education_weight: float = 0.15,
        semantic_weight: float = 0.15,
    ) -> None:
        self.skill_weight = skill_weight
        self.experience_weight = experience_weight
        self.education_weight = education_weight
        self.semantic_weight = semantic_weight

    def calculate_skill_score(self, candidate: CandidateProfile, job: Job) -> float:
        """Calculate technical skill overlap score (0.0 to 100.0) with alias resolution and confidence."""
        if not job.required_skills and not job.preferred_skills:
            # If no skills specified in job listing, use neutral baseline
            return 60.0

        candidate_skills = {
            canonicalize_skill_name(s.name).lower(): s.confidence
            for s in candidate.skills
        }

        # Check required skills
        req_weights = 1.0
        pref_weights = 0.3

        total_weight = 0.0
        accumulated_score = 0.0

        # Required skills evaluation
        for req in job.required_skills:
            total_weight += req_weights
            canonical = canonicalize_skill_name(req).lower()
            if canonical in candidate_skills:
                conf = candidate_skills[canonical]
                accumulated_score += req_weights * conf

        # Preferred skills evaluation
        for pref in job.preferred_skills:
            total_weight += pref_weights
            canonical = canonicalize_skill_name(pref).lower()
            if canonical in candidate_skills:
                conf = candidate_skills[canonical]
                accumulated_score += pref_weights * conf

        if total_weight == 0.0:
            return 60.0

        raw_score = (accumulated_score / total_weight) * 100.0
        return max(0.0, min(100.0, round(raw_score, 1)))

    def calculate_experience_score(self, candidate: CandidateProfile, job: Job) -> float:
        """Calculate experience alignment based on work history and job seniority tier."""
        # Calculate estimated total candidate years
        total_years = 0.0
        for exp in candidate.experience:
            text = f"{exp.title} {exp.description or ''}"
            # Look for duration patterns in text e.g. "3 years", "5+ years"
            match = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", text, re.IGNORECASE)
            if match:
                try:
                    total_years += float(match.group(1))
                except ValueError:
                    total_years += 1.5
            else:
                total_years += 1.5  # default 1.5 years per distinct job role

        # If candidate has no experience listed at all
        if not candidate.experience:
            return 20.0

        # Determine target years based on job experience_level tier
        level = (job.experience_level or "").lower()
        if any(term in level for term in ["junior", "entry", "intern", "associate"]):
            target_years = 1.0
        elif any(term in level for term in ["mid", "intermediate"]):
            target_years = 3.0
        elif any(term in level for term in ["senior", "sr."]):
            target_years = 5.0
        elif any(term in level for term in ["lead", "principal", "staff", "architect", "director"]):
            target_years = 8.0
        else:
            # Try to infer from job description text
            desc_match = re.search(r"(\d+)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?experience", job.description, re.IGNORECASE)
            if desc_match:
                target_years = float(desc_match.group(1))
            else:
                target_years = 3.0

        if total_years >= target_years:
            # Fully meets or exceeds target
            score = 95.0 + min(5.0, (total_years - target_years) * 1.0)
        else:
            # Proportional attainment
            score = (total_years / target_years) * 90.0

        return max(15.0, min(100.0, round(score, 1)))

    def calculate_education_score(self, candidate: CandidateProfile, job: Job) -> float:
        """Calculate educational credential alignment."""
        if not candidate.education:
            return 50.0  # Neutral baseline when unstated

        # Score highest degree attained
        highest_score = 60.0
        for edu in candidate.education:
            deg = f"{edu.degree} {edu.field_of_study or ''}".lower()
            if any(term in deg for term in ["ph.d", "phd", "doctorate", "doctor"]):
                highest_score = max(highest_score, 100.0)
            elif any(term in deg for term in ["m.s.", "ms", "master", "m.tech", "mba"]):
                highest_score = max(highest_score, 90.0)
            elif any(term in deg for term in ["b.s.", "bs", "bachelor", "b.tech", "b.e."]):
                highest_score = max(highest_score, 85.0)
            elif any(term in deg for term in ["associate", "diploma"]):
                highest_score = max(highest_score, 70.0)

        # Check if job specifically requires master or phd
        job_desc = job.description.lower()
        if "phd" in job_desc or "doctorate" in job_desc:
            if highest_score < 100.0:
                highest_score *= 0.7
        elif "master" in job_desc or "m.s." in job_desc:
            if highest_score < 90.0:
                highest_score *= 0.85

        return round(min(100.0, highest_score), 1)

    def compute_overall_score(
        self,
        candidate: CandidateProfile,
        job: Job,
        semantic_similarity: float = 0.5,
    ) -> Tuple[float, ScoreBreakdown]:
        """Compute multi-factor score breakdown and final composite score with substantive gap guards.

        Anti-False-Positive Guard:
        If a position has critical required technical skills and the candidate possesses none
        or very few of them (skill_score < 25), the overall score is strictly capped to prevent
        superficial text similarity or buzzwords from triggering an inflated recommendation.
        """
        skill_score = self.calculate_skill_score(candidate, job)
        exp_score = self.calculate_experience_score(candidate, job)
        edu_score = self.calculate_education_score(candidate, job)
        semantic_score = round(max(0.0, min(1.0, semantic_similarity)) * 100.0, 1)

        raw_overall = (
            self.skill_weight * skill_score
            + self.experience_weight * exp_score
            + self.education_weight * edu_score
            + self.semantic_weight * semantic_score
        )

        # Substantive domain mismatch protection (Test 5 requirement):
        # Even if semantic similarity is high, missing core technical requirements bounds overall score.
        if job.required_skills and skill_score < 25.0:
            overall_score = min(raw_overall, 35.0)
        elif job.required_skills and skill_score < 50.0:
            overall_score = min(raw_overall, 65.0)
        else:
            overall_score = raw_overall

        overall_score = max(0.0, min(100.0, round(overall_score, 1)))

        breakdown = ScoreBreakdown(
            skill_score=skill_score,
            experience_score=exp_score,
            education_score=edu_score,
        )

        return overall_score, breakdown
