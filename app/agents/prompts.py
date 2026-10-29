"""System instructions and prompt templates for the Conversational Career Agent.

Enforces docs/AI_Agent_Rulebook.md and Section 6 of the Conversational Agent requirements:
- Career and learning copilot identity
- Supported topics and out-of-scope guidance
- Conversational tone, depth adaptation, and follow-up understanding
- Anti-hallucination and truthfulness rules
- Clean formatting of multi-turn conversation history and tool evidence
"""

from typing import Any, List, Optional
from app.schemas.agent import ChatMessage


CAREER_AGENT_SYSTEM_PROMPT = """You are CareerMatch AI, a knowledgeable, encouraging, direct, and transparent career and learning copilot for the AI Job Matcher platform.

Your mission is to help candidates discover suitable tech careers, understand match evidence, analyze skill gaps, polish CVs and project portfolios, prepare for technical interviews, and follow practical learning roadmaps.

Core Operating Principles:
1. Direct and Helpful: Answer the user's specific question directly in the opening sentence before offering structured guidance, next steps, or examples.
2. Conversational Continuity: Use the conversation history to maintain context. Understand follow-ups (e.g., "Why?", "What should I learn next?", "Can you explain that?") naturally without requiring the user to re-state their background.
3. Tone: Friendly, encouraging, and professional. Avoid excessive pleasantries or repeated introductory greetings in an ongoing conversation.
4. Depth Adaptation: For short questions, be concise. For open-ended roadmap or career transition questions, provide structured, step-by-step guidance. For multi-part questions, address each part distinctly.
5. Scope Boundaries: Your focus is tech careers, programming languages, system design, software engineering, machine learning, data engineering, cloud, cybersecurity, CVs, GitHub projects, job search, interview prep, and technical study topics. If asked about completely unrelated non-career topics (e.g. sports scores, entertainment gossip, politics), politely and briefly state your scope as a career and learning assistant, and invite a career-related question.
6. Strict Truthfulness: Base any match discussions, skill assessments, or recommendations strictly on verified facts and provided tool results. Never invent candidate qualifications, job requirements, company details, or employment guarantees. If essential information is missing, ask one focused clarification question instead of making false assumptions.
7. Disclaimers: Only discuss automated match estimates as guidance, not guarantees of employment or interviews.
8. Grounded Profile Access: When verified candidate context is provided under '=== Verified Candidate Context ===', you HAVE direct, active access to the candidate's verified profile data, skills, and background. NEVER state 'I currently do not have access to your profile data' or ask them to upload a CV/resume when this context is present. Directly utilize their verified skills, experience, and background to answer their questions. If greeted with 'hello' or 'hi', warmly acknowledge them by name, note their core domain (e.g. Backend, Cloud, ML, Full-Stack), and invite their questions about suitable roles, resume polishing, or technical interview preparation.
"""


def build_conversational_prompt(
    task: str,
    history: Optional[List[ChatMessage]] = None,
    candidate_context: Optional[dict] = None,
    job_context: Optional[dict] = None,
    tool_evidence: Optional[str] = None,
) -> str:
    """Assemble structured contextual prompt for LLM conversation generation."""
    sections: List[str] = []

    # 1. Candidate Context (if authenticated and available)
    if candidate_context:
        name = candidate_context.get("full_name") or "Candidate"
        skills = candidate_context.get("skills") or []
        skills_str = ", ".join(skills[:15]) if skills else "Not specified"
        exp_years = candidate_context.get("years_of_experience")
        exp_str = f"{exp_years} years" if exp_years is not None else "Not specified"
        headline = candidate_context.get("headline") or candidate_context.get("current_role") or "Tech Professional"
        summary = candidate_context.get("summary") or ""

        cand_lines = [
            "=== Verified Candidate Context ===",
            f"Name: {name}",
            f"Current Focus / Headline: {headline}",
            f"Confirmed Skills: {skills_str}",
            f"Years of Experience: {exp_str}",
        ]
        if summary:
            cand_lines.append(f"Professional Summary: {summary[:500]}")

        # Work experience history
        exp_list = candidate_context.get("experience") or []
        if exp_list:
            cand_lines.append("Work Experience History:")
            for exp in exp_list[:4]:
                t = exp.get("title", "Role")
                c = exp.get("company", "Company")
                d = f"{exp.get('start_date', '')} – {exp.get('end_date', '')}".strip(" –")
                desc = exp.get("description", "")
                cand_lines.append(f"  • {t} at {c} ({d}): {desc[:150] if desc else 'Documented experience'}")

        # Education
        edu_list = candidate_context.get("education") or []
        if edu_list:
            cand_lines.append("Education:")
            for edu in edu_list[:2]:
                deg = edu.get("degree", "Degree")
                inst = edu.get("institution", "Institution")
                yr = edu.get("graduation_year")
                yr_str = f" ({yr})" if yr else ""
                cand_lines.append(f"  • {deg} from {inst}{yr_str}")

        # Top matching jobs if already evaluated
        matches_list = candidate_context.get("top_matches") or []
        if matches_list:
            cand_lines.append("Top Evaluated Job Matches:")
            for m in matches_list[:5]:
                cand_lines.append(f"  • {m.get('title')} at {m.get('company')} — Match: {m.get('score', 0):.0f}%")

        sections.append("\n".join(cand_lines))

    # 2. Target Job Context (if job-scoped)
    if job_context:
        title = job_context.get("title") or "Position"
        company = job_context.get("company") or "Company"
        req_skills = ", ".join(job_context.get("required_skills", [])[:10]) or "Not specified"
        pref_skills = ", ".join(job_context.get("preferred_skills", [])[:6]) or "None"
        score = job_context.get("match_score")
        score_str = f"Estimated Match Score: {score:.1f}%\n" if score is not None else ""

        sections.append(
            f"=== Target Job Context ===\n"
            f"Role: {title} at {company}\n"
            f"{score_str}"
            f"Required Skills: {req_skills}\n"
            f"Preferred Skills: {pref_skills}"
        )

    # 3. Grounded Tool Evidence (if tools were executed)
    if tool_evidence and tool_evidence.strip():
        sections.append(
            f"=== Grounded Tool Evidence ===\n"
            f"{tool_evidence.strip()}"
        )

    # 4. Multi-turn Conversation History
    if history and len(history) > 0:
        history_lines: List[str] = []
        for msg in history[-10:]:  # Bound to last 10 turns for token budget
            role_label = "User" if msg.role.value == "user" else "CareerMatch AI"
            history_lines.append(f"{role_label}: {msg.content}")
        sections.append(
            f"=== Conversation History ===\n" + "\n".join(history_lines)
        )

    # 5. Current User Input
    sections.append(f"=== Current User Message ===\n{task}")

    return "\n\n".join(sections)
