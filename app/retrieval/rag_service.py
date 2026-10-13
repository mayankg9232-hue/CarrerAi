"""RAG context assembler providing grounded, injection-resistant context for matching and explanation."""

from typing import Any, Dict
from app.nlp.normalizer import wrap_untrusted_content
from app.schemas.job import Job
from app.schemas.resume import CandidateProfile


class RAGContextAssembler:
    """Assembles grounded candidate and job context while enforcing prompt-injection defenses."""

    @staticmethod
    def build_candidate_factual_summary(candidate: CandidateProfile) -> str:
        """Construct sanitized factual representation of candidate profile."""
        skills_str = ", ".join(
            [f"{s.name} ({s.category}, conf={s.confidence:.2f})" for s in candidate.skills]
        ) or "None listed"

        exp_entries = []
        for e in candidate.experience:
            duration = f"{e.start_date or '?'} to {e.end_date or '?'}"
            exp_entries.append(f"- {e.title} at {e.company} ({duration}): {e.description or 'No description'}")
        exp_str = "\n".join(exp_entries) if exp_entries else "No work experience listed."

        edu_entries = []
        for d in candidate.education:
            year = f" ({d.graduation_year})" if d.graduation_year else ""
            edu_entries.append(f"- {d.degree} from {d.institution}{year}")
        edu_str = "\n".join(edu_entries) if edu_entries else "No formal education listed."

        return (
            f"Candidate ID: {candidate.id}\n"
            f"Full Name: {candidate.full_name or 'Anonymous'}\n"
            f"Summary: {candidate.summary or 'None provided'}\n\n"
            f"Verified Skills:\n{skills_str}\n\n"
            f"Work History:\n{exp_str}\n\n"
            f"Education:\n{edu_str}"
        )

    @staticmethod
    def build_job_factual_summary(job: Job) -> str:
        """Construct sanitized factual representation of job requirements."""
        req_skills = ", ".join(job.required_skills) if job.required_skills else "None specified"
        pref_skills = ", ".join(job.preferred_skills) if job.preferred_skills else "None specified"

        return (
            f"Job ID: {job.id}\n"
            f"Title: {job.title}\n"
            f"Company: {job.company} ({job.location})\n"
            f"Experience Tier: {job.experience_level or 'Unspecified'}\n"
            f"Required Skills: {req_skills}\n"
            f"Preferred Skills: {pref_skills}\n\n"
            f"Full Description:\n{job.description}"
        )

    @classmethod
    def assemble_grounded_context(cls, candidate: CandidateProfile, job: Job) -> Dict[str, str]:
        """Assemble structured, prompt-injection defended context for candidate and job.

        Both external sources are wrapped within defensive XML boundaries to ensure
        instructions inside resumes or job descriptions cannot hijack system policies.
        """
        raw_candidate = cls.build_candidate_factual_summary(candidate)
        raw_job = cls.build_job_factual_summary(job)

        safe_candidate_xml = wrap_untrusted_content(raw_candidate, tag_name="untrusted_candidate_data")
        safe_job_xml = wrap_untrusted_content(raw_job, tag_name="untrusted_job_data")

        grounded_prompt = (
            f"{safe_candidate_xml}\n\n"
            f"{safe_job_xml}"
        )

        return {
            "candidate_xml": safe_candidate_xml,
            "job_xml": safe_job_xml,
            "grounded_prompt": grounded_prompt,
        }
