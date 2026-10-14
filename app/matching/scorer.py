"""Deterministic multi-factor scoring engine calculating explainable match scores."""

import re
from typing import List, Optional, Tuple

from app.nlp.normalizer import canonicalize_skill_name
from app.schemas.job import Job
from app.schemas.matching import FactorScoreDetail, MatchingWeights, ScoreBreakdown
from app.schemas.resume import CandidateProfile


class DeterministicScorer:
    """Multi-factor explainable scoring engine aligning skills, experience, education, role, and projects.

    Implements configurable 5-factor weighted scoring:
    - Technical skills: 40% (0.40)
    - Relevant experience: 25% (0.25)
    - Education and qualifications: 10% (0.10)
    - Role and responsibility alignment: 15% (0.15)
    - Projects and domain relevance: 10% (0.10)
    Total nominal weight: 100% (1.00)

    Policy for unavailable factors:
    When a job description lacks information to evaluate a factor (e.g. no education required),
    the factor is marked unavailable (no unearned 100% score awarded), and weights of the remaining
    evaluable factors are renormalized to sum to 100%. If less than 50% of the nominal weight is
    evaluable, the match is flagged as provisional with reduced confidence.
    """

    def __init__(
        self,
        weights: Optional[MatchingWeights] = None,
        # Backward-compatible parameters for legacy callers
        skill_weight: Optional[float] = None,
        experience_weight: Optional[float] = None,
        education_weight: Optional[float] = None,
        semantic_weight: Optional[float] = None,
    ) -> None:
        if weights is not None:
            self.weights = weights
        else:
            self.weights = MatchingWeights()
            if skill_weight is not None:
                self.weights.technical_skills = skill_weight
            if experience_weight is not None:
                self.weights.relevant_experience = experience_weight
            if education_weight is not None:
                self.weights.education = education_weight
            if semantic_weight is not None:
                self.weights.role_alignment = semantic_weight

        # Exposed attributes for legacy callers
        self.skill_weight = self.weights.technical_skills
        self.experience_weight = self.weights.relevant_experience
        self.education_weight = self.weights.education
        self.semantic_weight = self.weights.role_alignment

    # ==========================================================================
    # 1. TECHNICAL SKILLS FACTOR (40%)
    # ==========================================================================
    def calculate_skill_score(self, candidate: CandidateProfile, job: Job) -> float:
        """Legacy helper returning scalar technical skill overlap score (0.0 to 100.0)."""
        score, _, _ = self.calculate_technical_skills_factor(candidate, job)
        return score

    def calculate_technical_skills_factor(
        self, candidate: CandidateProfile, job: Job
    ) -> Tuple[float, bool, str]:
        """Calculate technical skill overlap score (0.0 to 100.0) with alias resolution and verified bonus.

        Returns: (score, is_available, reason)
        """
        if not job.required_skills and not job.preferred_skills:
            return 0.0, False, "Job description specifies no required or preferred technical skills."

        candidate_skills = {
            canonicalize_skill_name(s.name).lower(): (
                1.0 if getattr(s, "is_verified", False) else s.confidence,
                getattr(s, "is_verified", False),
            )
            for s in candidate.skills
        }

        # Check required skills
        req_matched = 0
        req_score_accum = 0.0
        verified_matches = 0

        for req in job.required_skills:
            canonical = canonicalize_skill_name(req).lower()
            if canonical in candidate_skills:
                conf, is_ver = candidate_skills[canonical]
                req_matched += 1
                req_score_accum += conf
                if is_ver:
                    verified_matches += 1

        # Check preferred skills
        pref_matched = 0
        pref_score_accum = 0.0

        for pref in job.preferred_skills:
            canonical = canonicalize_skill_name(pref).lower()
            if canonical in candidate_skills:
                conf, is_ver = candidate_skills[canonical]
                pref_matched += 1
                pref_score_accum += conf
                if is_ver:
                    verified_matches += 1

        total_req = len(job.required_skills)
        total_pref = len(job.preferred_skills)

        req_weight = 1.0
        pref_weight = 0.25

        total_weight = (total_req * req_weight) + (total_pref * pref_weight)
        accumulated_score = (req_score_accum * req_weight) + (pref_score_accum * pref_weight)

        if total_weight > 0:
            raw_score = (accumulated_score / total_weight) * 100.0
        else:
            raw_score = 60.0

        if total_req > 0 and total_pref > 0:
            reason = f"Matched {req_matched}/{total_req} required skills and {pref_matched}/{total_pref} preferred skills."
        elif total_req > 0:
            reason = f"Matched {req_matched}/{total_req} mandatory required skills."
        else:
            reason = f"Matched {pref_matched}/{total_pref} preferred skills."

        # Verification bonus: each verified required/preferred skill adds +5 points (up to 15 max)
        if verified_matches > 0:
            bonus = min(15.0, verified_matches * 5.0)
            raw_score = min(100.0, raw_score + bonus)
            reason += f" Includes +{int(bonus)}pt verified assessment bonus."

        score = max(0.0, min(100.0, round(raw_score, 1)))
        return score, True, reason

    # ==========================================================================
    # 2. RELEVANT EXPERIENCE FACTOR (25%)
    # ==========================================================================
    def calculate_experience_score(self, candidate: CandidateProfile, job: Job) -> float:
        """Legacy helper returning experience alignment score (0.0 to 100.0)."""
        score, _, _, _, _ = self.calculate_experience_factor(candidate, job)
        return score

    def calculate_experience_factor(
        self, candidate: CandidateProfile, job: Job
    ) -> Tuple[float, bool, str, float, float]:
        """Calculate experience alignment based on candidate work history vs job target years.

        Returns: (score, is_available, reason, candidate_years, target_years)
        """
        # Calculate estimated total candidate years
        total_years = 0.0
        for exp in candidate.experience:
            text = f"{exp.title} {exp.description or ''}"
            match = re.search(r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)", text, re.IGNORECASE)
            if match:
                try:
                    total_years += float(match.group(1))
                except ValueError:
                    total_years += 1.5
            else:
                total_years += 1.5

        # Determine target years required
        if getattr(job, "experience_years_required", None) is not None:
            target_years = float(job.experience_years_required)
        else:
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
                desc_match = re.search(
                    r"(\d+(?:\.\d+)?)\s*\+?\s*(?:years?|yrs?)\s+(?:of\s+)?experience",
                    job.description,
                    re.IGNORECASE,
                )
                target_years = float(desc_match.group(1)) if desc_match else 3.0

        if not candidate.experience or total_years <= 0.0:
            return 20.0, True, f"Candidate lists minimal prior work history (Target: {target_years:.1f} yrs).", 0.0, target_years

        if total_years >= target_years:
            score = 95.0 + min(5.0, (total_years - target_years) * 1.0)
            reason = f"Candidate experience ({total_years:.1f} yrs) meets or exceeds requirement ({target_years:.1f} yrs)."
        else:
            score = (total_years / target_years) * 90.0
            reason = f"Candidate has {total_years:.1f} yrs experience towards {target_years:.1f} yrs target."

        final_score = max(15.0, min(100.0, round(score, 1)))
        return final_score, True, reason, total_years, target_years

    # ==========================================================================
    # 3. EDUCATION AND QUALIFICATIONS FACTOR (10%)
    # ==========================================================================
    def calculate_education_score(self, candidate: CandidateProfile, job: Job) -> float:
        """Legacy helper returning educational credential alignment score."""
        score, _, _ = self.calculate_education_factor(candidate, job)
        return score

    def calculate_education_factor(
        self, candidate: CandidateProfile, job: Job
    ) -> Tuple[float, bool, str]:
        """Calculate educational credential alignment against job requirements.

        Returns: (score, is_available, reason)
        """
        # Determine job educational requirement
        job_req = getattr(job, "education_required", None)
        job_desc = job.description.lower()
        has_phd_req = "phd" in job_desc or "doctorate" in job_desc or (job_req and "phd" in job_req.lower())
        has_masters_req = "master" in job_desc or "m.s." in job_desc or (job_req and "master" in job_req.lower())
        has_bachelor_req = "bachelor" in job_desc or "b.s." in job_desc or "degree" in job_desc or (job_req and "bachelor" in job_req.lower())

        is_education_specified = bool(job_req or has_phd_req or has_masters_req or has_bachelor_req)

        # If job does NOT mention education requirements, mark unavailable per policy
        if not is_education_specified and not candidate.education:
            return 0.0, False, "No education requirement specified in job listing or candidate profile."

        if not candidate.education:
            if is_education_specified:
                return 40.0, True, "Job specifies degree requirements but candidate lists no formal education."
            return 50.0, False, "No formal education requirements specified."

        # Score highest degree attained by candidate
        highest_score = 60.0
        degree_name = "Degree / Credential"
        for edu in candidate.education:
            deg = f"{edu.degree} {edu.field_of_study or ''}".lower()
            if any(term in deg for term in ["ph.d", "phd", "doctorate", "doctor"]):
                highest_score = max(highest_score, 100.0)
                degree_name = "Doctorate / Ph.D."
            elif any(term in deg for term in ["m.s.", "ms", "master", "m.tech", "mba"]):
                highest_score = max(highest_score, 90.0)
                degree_name = "Master's Degree"
            elif any(term in deg for term in ["b.s.", "bs", "bachelor", "b.tech", "b.e."]):
                highest_score = max(highest_score, 85.0)
                degree_name = "Bachelor's Degree"
            elif any(term in deg for term in ["associate", "diploma"]):
                highest_score = max(highest_score, 70.0)
                degree_name = "Associate's Degree"

        # Apply requirement scaling
        if has_phd_req and highest_score < 100.0:
            highest_score *= 0.70
            reason = f"Candidate has {degree_name}; position specifies Ph.D. or Doctorate."
        elif has_masters_req and highest_score < 90.0:
            highest_score *= 0.85
            reason = f"Candidate has {degree_name}; position specifies Master's degree."
        elif is_education_specified:
            reason = f"Candidate possesses {degree_name}, satisfying degree qualifications."
        else:
            reason = f"Candidate holds {degree_name} (general educational credential)."

        return round(min(100.0, highest_score), 1), True, reason

    # ==========================================================================
    # 4. ROLE & RESPONSIBILITY ALIGNMENT FACTOR (15%)
    # ==========================================================================
    def calculate_role_alignment_factor(
        self, candidate: CandidateProfile, job: Job, semantic_similarity: float
    ) -> Tuple[float, bool, str]:
        """Calculate role and responsibility alignment based on titles, responsibilities, and semantics.

        Returns: (score, is_available, reason)
        """
        job_title_tokens = set(re.findall(r"\w+", job.title.lower()))
        # Remove common boilerplate stop words from title
        stop_words = {"senior", "junior", "mid", "lead", "staff", "principal", "role", "the", "and", "or", "in", "at", "for"}
        meaningful_job_tokens = job_title_tokens - stop_words

        candidate_role_texts = [getattr(candidate, "headline", None) or "", candidate.summary or ""]
        for exp in candidate.experience:
            candidate_role_texts.append(exp.title or "")
        for skill in candidate.skills:
            candidate_role_texts.append(skill.name)
            if getattr(skill, "category", None):
                candidate_role_texts.append(skill.category)
        combined_candidate_roles = " ".join(candidate_role_texts).lower()

        matched_tokens = sum(1 for token in meaningful_job_tokens if token in combined_candidate_roles)
        if meaningful_job_tokens:
            if matched_tokens > 0:
                title_overlap = matched_tokens / len(meaningful_job_tokens)
            elif not any(exp.title for exp in candidate.experience) and not candidate.summary:
                # Skill-based or minimal profile with no work history specified
                title_overlap = 0.50
            else:
                title_overlap = 0.0
        else:
            title_overlap = 0.70

        # Semantic embedding cosine contribution (0 to 1 -> 0 to 100)
        semantic_score = max(0.0, min(1.0, semantic_similarity)) * 100.0

        # Responsibilities alignment
        job_resps = getattr(job, "responsibilities", []) or []
        resp_alignment = 0.70
        if job_resps:
            cand_exp_desc = " ".join([e.description or "" for e in candidate.experience]).lower()
            matched_resp = sum(
                1 for r in job_resps
                if any(w.lower() in cand_exp_desc for w in re.findall(r"\w+", r) if len(w) > 4)
            )
            resp_alignment = (matched_resp / len(job_resps)) if len(job_resps) > 0 else 0.70

        composite_role_score = (
            (title_overlap * 100.0) * 0.40
            + semantic_score * 0.35
            + (resp_alignment * 100.0) * 0.25
        )

        final_score = max(20.0, min(100.0, round(composite_role_score, 1)))
        reason = f"Role alignment calculated from target title '{job.title}', previous duties, and semantic profile."
        return final_score, True, reason

    # ==========================================================================
    # 5. PROJECTS & DOMAIN RELEVANCE FACTOR (10%)
    # ==========================================================================
    def calculate_projects_domain_factor(
        self, candidate: CandidateProfile, job: Job
    ) -> Tuple[float, bool, str]:
        """Calculate practical projects, domain relevance, and portfolio alignment.

        Returns: (score, is_available, reason)
        """
        job_domain = (getattr(job, "domain", None) or "").lower()
        job_desc = job.description.lower()

        # Keywords indicating project-heavy or domain expertise
        domain_keywords = ["microservices", "distributed", "cloud", "ai", "machine learning", "fintech",
                           "e-commerce", "security", "infra", "analytics", "real-time", "pipeline", "etl"]

        relevant_domains_in_job = [kw for kw in domain_keywords if kw in job_desc or kw in job_domain]

        cand_body = f"{candidate.summary or ''} {' '.join([e.description or '' for e in candidate.experience])}".lower()

        if relevant_domains_in_job:
            matched_domains = [kw for kw in relevant_domains_in_job if kw in cand_body]
            domain_overlap = len(matched_domains) / len(relevant_domains_in_job)
            score = 50.0 + (domain_overlap * 50.0)
            reason = f"Demonstrated domain experience in: {', '.join(matched_domains) if matched_domains else 'general engineering'}."
        else:
            # Baseline project assessment based on depth of experience description
            has_substantive_projects = len(cand_body) > 100
            score = 80.0 if has_substantive_projects else 50.0
            reason = "Evaluated hands-on project accomplishments and technical duties from work history."

        final_score = max(20.0, min(100.0, round(score, 1)))
        return final_score, True, reason

    # ==========================================================================
    # COMPOSITE 5-FACTOR MATCHING WITH WEIGHT RENORMALIZATION
    # ==========================================================================
    def compute_overall_score(
        self,
        candidate: CandidateProfile,
        job: Job,
        semantic_similarity: float = 0.5,
    ) -> Tuple[float, ScoreBreakdown]:
        """Compute 5-factor score breakdown and final composite score with weight renormalization.

        Calculates factor percentages:
        1. Technical skills (nominal 40%)
        2. Relevant experience (nominal 25%)
        3. Education and qualifications (nominal 10%)
        4. Role and responsibility alignment (nominal 15%)
        5. Projects and domain relevance (nominal 10%)

        If any factor is unavailable, its weight is redistributed proportionally among available
        factors, and reduced_confidence is flagged. If < 50% of weight is available, is_provisional is flagged.
        Anti-False-Positive Guard:
        If a position has critical required technical skills and the candidate possesses very few
        (skill_score < 25), overall score is capped to prevent inflated recommendations.
        """
        # 1. Technical Skills
        tech_score, tech_avail, tech_reason = self.calculate_technical_skills_factor(candidate, job)

        # 2. Relevant Experience
        exp_score, exp_avail, exp_reason, cand_yrs, target_yrs = self.calculate_experience_factor(candidate, job)

        # 3. Education
        edu_score, edu_avail, edu_reason = self.calculate_education_factor(candidate, job)

        # 4. Role Alignment
        role_score, role_avail, role_reason = self.calculate_role_alignment_factor(candidate, job, semantic_similarity)

        # 5. Projects & Domain
        proj_score, proj_avail, proj_reason = self.calculate_projects_domain_factor(candidate, job)

        factor_definitions = [
            ("Technical skills", tech_score, self.weights.technical_skills, tech_avail, tech_reason),
            ("Relevant experience", exp_score, self.weights.relevant_experience, exp_avail, exp_reason),
            ("Education and qualifications", edu_score, self.weights.education, edu_avail, edu_reason),
            ("Role and responsibility alignment", role_score, self.weights.role_alignment, role_avail, role_reason),
            ("Projects and domain relevance", proj_score, self.weights.projects, proj_avail, proj_reason),
        ]

        total_nominal_weight = sum(f[2] for f in factor_definitions)
        available_nominal_weight = sum(f[2] for f in factor_definitions if f[3])

        is_provisional = available_nominal_weight < 0.50
        reduced_confidence = available_nominal_weight < total_nominal_weight
        confidence_level = "low" if is_provisional else ("medium" if reduced_confidence else "high")

        factor_details: List[FactorScoreDetail] = []
        raw_composite = 0.0

        for name, score, nominal_weight, available, reason in factor_definitions:
            if available and available_nominal_weight > 0:
                effective_weight = nominal_weight / available_nominal_weight
                weighted_contribution = score * effective_weight
                raw_composite += weighted_contribution
            else:
                effective_weight = 0.0
                weighted_contribution = 0.0

            factor_details.append(
                FactorScoreDetail(
                    name=name,
                    score=round(score, 1),
                    weight=nominal_weight,
                    effective_weight=round(effective_weight, 4),
                    weighted_score=round(weighted_contribution, 1),
                    available=available,
                    reason=reason,
                )
            )

        # Anti-False-Positive Guard (consistent with Phase 5 requirements)
        if job.required_skills and tech_score < 25.0:
            final_overall = min(raw_composite, 35.0)
        elif job.required_skills and tech_score < 50.0:
            final_overall = min(raw_composite, 65.0)
        else:
            final_overall = raw_composite

        overall_score = max(0.0, min(100.0, round(final_overall, 1)))

        breakdown = ScoreBreakdown(
            # Backward-compatible fields
            skill_score=tech_score,
            experience_score=exp_score,
            education_score=edu_score,
            role_alignment_score=role_score,
            projects_score=proj_score,
            # 5-factor breakdown
            technical_skills=factor_details[0],
            relevant_experience=factor_details[1],
            education=factor_details[2],
            role_alignment=factor_details[3],
            projects=factor_details[4],
            factors=factor_details,
            reduced_confidence=reduced_confidence,
            is_provisional=is_provisional,
            confidence_level=confidence_level,
            scoring_version="weighted_v2",
        )

        return overall_score, breakdown

    def compute_final_weighted_percentage(self, breakdown: ScoreBreakdown) -> float:
        """Compute the final weighted percentage from a ScoreBreakdown.
        
        Calculates:
        Final = sum(factor.score * factor.effective_weight)
        for all available factors.
        """
        if breakdown.factors:
            total = sum(f.score * f.effective_weight for f in breakdown.factors if f.available)
            return max(0.0, min(100.0, round(total, 1)))

        factors = [
            breakdown.technical_skills,
            breakdown.relevant_experience,
            breakdown.education,
            breakdown.role_alignment,
            breakdown.projects,
        ]
        available_factors = [f for f in factors if f is not None and f.available]
        if not available_factors:
            return 0.0
        total_avail_weight = sum(f.weight for f in available_factors)
        if total_avail_weight > 0:
            total = sum(f.score * (f.weight / total_avail_weight) for f in available_factors)
            return max(0.0, min(100.0, round(total, 1)))
        return 0.0
