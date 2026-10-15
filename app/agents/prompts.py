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
        skills_str = ", ".join(skills[:10]) if skills else "Not specified"
        exp_years = candidate_context.get("years_of_experience")
        exp_str = f"{exp_years} years" if exp_years is not None else "Not specified"
        headline = candidate_context.get("headline") or candidate_context.get("current_role") or "Tech Professional"
        sections.append(
            f"=== Verified Candidate Context ===\n"
            f"Name: {name}\n"
            f"Current Focus / Headline: {headline}\n"
            f"Confirmed Skills: {skills_str}\n"
            f"Years of Experience: {exp_str}"
        )

    # 2. Target Job Context (if job-scoped)
    if job_context:
        title = job_context.get("title") or "Position"
        company = job_context.get("company") or "Company"
        req_skills = ", ".join(job_context.get("required_skills", [])[:8]) or "Not specified"
        sections.append(
            f"=== Target Job Context ===\n"
            f"Role: {title} at {company}\n"
            f"Required Skills: {req_skills}"
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
