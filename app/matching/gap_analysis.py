"""Truthful skill gap analysis, overlap identification, and career recommendations."""

import re
from typing import List, Set
from pydantic import BaseModel, Field

from app.nlp.normalizer import canonicalize_skill_name
from app.schemas.job import Job
from app.schemas.resume import CandidateProfile


class GapAnalysisResult(BaseModel):
    """Detailed skill overlap, gap analysis, and truthful improvement suggestions."""

    matched_skills: List[str] = Field(default_factory=list, description="Skills possessed by candidate and required by job")
    missing_skills: List[str] = Field(default_factory=list, description="Job required skills absent from candidate profile")
    uncertain_skills: List[str] = Field(default_factory=list, description="Skills mentioned ambiguously or with low confidence")
    improvement_suggestions: List[str] = Field(
        default_factory=list,
        description="Truthful, actionable resume improvement advice (no fabricated credentials)",
    )


class GapAnalyzer:
    """Computes technical skill overlap, missing skills, and truthful gap remediation."""

    def __init__(self, low_confidence_threshold: float = 0.70) -> None:
        self.low_confidence_threshold = low_confidence_threshold

    def analyze(self, candidate: CandidateProfile, job: Job) -> GapAnalysisResult:
        """Perform deterministic gap analysis between candidate profile and job requirements."""
        # 1. Canonicalize candidate skills map: canonical_name -> confidence
        candidate_skills_map = {}
        uncertain_skills_set: Set[str] = set()

        for s in candidate.skills:
            canonical = canonicalize_skill_name(s.name)
            is_verified = getattr(s, "is_verified", False)
            candidate_skills_map[canonical.lower()] = (canonical, s.confidence, is_verified)
            if s.confidence < self.low_confidence_threshold and not is_verified:
                uncertain_skills_set.add(canonical)

        # Also inspect summary for skills mentioned casually without explicit validation
        summary_text = (candidate.summary or "").lower()
        exp_text = " ".join([e.description or "" for e in candidate.experience]).lower()
        combined_body = f"{summary_text} {exp_text}"

        # 2. Canonicalize job required skills
        job_required_canonical: List[str] = []
        for r in job.required_skills:
            c = canonicalize_skill_name(r)
            if c not in job_required_canonical:
                job_required_canonical.append(c)

        matched: List[str] = []
        missing: List[str] = []

        for req in job_required_canonical:
            req_lower = req.lower()
            if req_lower in candidate_skills_map:
                matched_name, conf, is_ver = candidate_skills_map[req_lower]
                matched.append(matched_name)
            else:
                # Check if it was casually mentioned in text without structured skill entry
                if re.search(rf"\b{re.escape(req_lower)}\b", combined_body):
                    uncertain_skills_set.add(req)
                missing.append(req)

        # Verified skills are never uncertain
        verified_names_lower = {
            canonicalize_skill_name(s.name).lower()
            for s in candidate.skills
            if getattr(s, "is_verified", False)
        }
        uncertain_skills_set = {
            u for u in uncertain_skills_set
            if canonicalize_skill_name(u).lower() not in verified_names_lower
        }

        # Also evaluate preferred skills
        for pref in job.preferred_skills:
            pref_canonical = canonicalize_skill_name(pref)
            pref_lower = pref_canonical.lower()
            if pref_lower in candidate_skills_map:
                matched_name, _, _ = candidate_skills_map[pref_lower]
                if matched_name not in matched:
                    matched.append(matched_name)

        # 3. Formulate Truthful Improvement Suggestions
        suggestions: List[str] = []

        # Rule 1: Never suggest fabricating skills
        if missing:
            top_missing = missing[:3]
            suggestions.append(
                f"Consider gaining verified experience or building practical projects with {', '.join(top_missing)} "
                f"to directly address core required competencies for this position."
            )

        if matched:
            top_matched = matched[:3]
            suggestions.append(
                f"Clearly articulate and quantify your proven achievements using {', '.join(top_matched)} "
                f"in your recent professional experience descriptions."
            )

        if uncertain_skills_set:
            top_uncertain = list(uncertain_skills_set)[:2]
            suggestions.append(
                f"Provide explicit context, tools, and duration for {', '.join(top_uncertain)} "
                f"to remove ambiguity regarding your proficiency level."
            )

        if not candidate.summary or len(candidate.summary.strip()) < 30:
            suggestions.append(
                "Add a focused professional summary highlighting your core engineering domain and key achievements."
            )

        if not suggestions:
            suggestions.append("Ensure your resume emphasizes metrics and technical outcomes for all listed projects.")

        return GapAnalysisResult(
            matched_skills=matched,
            missing_skills=missing,
            uncertain_skills=sorted(list(uncertain_skills_set)),
            improvement_suggestions=suggestions,
        )
