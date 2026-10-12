"""Text normalization, prompt-injection defense, and entity canonicalization utilities for NLP."""

from datetime import datetime, timezone
import hashlib
import re
from typing import Dict, List, Optional, Tuple


# Canonical skill alias dictionary
CANONICAL_SKILL_ALIASES: Dict[str, str] = {
    "js": "JavaScript",
    "javascript": "JavaScript",
    "ts": "TypeScript",
    "typescript": "TypeScript",
    "py": "Python",
    "python": "Python",
    "golang": "Go",
    "go": "Go",
    "k8s": "Kubernetes",
    "kubernetes": "Kubernetes",
    "kube": "Kubernetes",
    "postgres": "PostgreSQL",
    "postgresql": "PostgreSQL",
    "pgsql": "PostgreSQL",
    "mongo": "MongoDB",
    "mongodb": "MongoDB",
    "react": "React",
    "reactjs": "React",
    "react.js": "React",
    "node": "Node.js",
    "nodejs": "Node.js",
    "node.js": "Node.js",
    "next": "Next.js",
    "nextjs": "Next.js",
    "next.js": "Next.js",
    "vue": "Vue.js",
    "vuejs": "Vue.js",
    "vue.js": "Vue.js",
    "aws": "AWS",
    "amazon web services": "AWS",
    "gcp": "GCP",
    "google cloud": "GCP",
    "google cloud platform": "GCP",
    "azure": "Azure",
    "microsoft azure": "Azure",
    "docker": "Docker",
    "git": "Git",
    "github": "Git",
    "gitlab": "Git",
    "rest": "REST API",
    "restful": "REST API",
    "rest api": "REST API",
    "restful api": "REST API",
    "rest apis": "REST API",
    "graphql": "GraphQL",
    "ci/cd": "CI/CD",
    "cicd": "CI/CD",
    "ci / cd": "CI/CD",
    "tf": "Terraform",
    "terraform": "Terraform",
    "fastapi": "FastAPI",
    "django": "Django",
    "flask": "Flask",
    "redis": "Redis",
    "mysql": "MySQL",
    "sqlite": "SQLite",
    "linux": "Linux",
    "unix": "Linux",
    "bash": "Bash",
    "shell": "Bash",
    "powershell": "PowerShell",
    "c++": "C++",
    "cpp": "C++",
    "c#": "C#",
    "csharp": "C#",
    "dotnet": ".NET",
    ".net": ".NET",
    "java": "Java",
    "rust": "Rust",
    "ruby": "Ruby",
    "rails": "Ruby on Rails",
    "ruby on rails": "Ruby on Rails",
}


def sanitize_text(text: str) -> str:
    """Strip non-printable/control characters, normalize newlines, and collapse extra spaces."""
    if not text:
        return ""

    # Normalize Windows and Mac line endings
    cleaned = text.replace("\r\n", "\n").replace("\r", "\n")

    # Filter out unprintable and ASCII control characters, preserving \n, \t
    cleaned = "".join(
        ch for ch in cleaned
        if ch in ("\n", "\t") or (ord(ch) >= 32 and ord(ch) != 127)
    )

    # Collapse excessive blank lines (more than 3 consecutive)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)

    return cleaned.strip()


def wrap_untrusted_content(raw_text: str, tag_name: str = "untrusted_resume_content") -> str:
    """Frame untrusted candidate or scraper text inside defensive XML delimiters.

    Escapes any rogue XML closing tags embedded inside untrusted text to prevent
    jailbreak/delimiter escape attacks against downstream LLM prompts.
    """
    sanitized = sanitize_text(raw_text)

    # Neutralize closing delimiter tag injections
    closing_tag = f"</{tag_name}>"
    opening_tag = f"<{tag_name}>"
    neutralized = sanitized.replace(closing_tag, f"&lt;/{tag_name}&gt;")
    neutralized = neutralized.replace(opening_tag, f"&lt;{tag_name}&gt;")

    return f"<{tag_name}>\n{neutralized}\n</{tag_name}>"


def canonicalize_skill_name(raw_name: str) -> str:
    """Map skill synonyms and abbreviations to canonical standard taxonomy naming."""
    trimmed = raw_name.strip()
    key = trimmed.lower()
    return CANONICAL_SKILL_ALIASES.get(key, trimmed)


def compute_sha256(content: bytes) -> str:
    """Compute deterministic SHA-256 hash for deduplication and provenance."""
    return hashlib.sha256(content).hexdigest()


MONTH_MAP = {
    "jan": "01", "january": "01",
    "feb": "02", "february": "02",
    "mar": "03", "march": "03",
    "apr": "04", "april": "04",
    "may": "05",
    "jun": "06", "june": "06",
    "jul": "07", "july": "07",
    "aug": "08", "august": "08",
    "sep": "09", "september": "09", "sept": "09",
    "oct": "10", "october": "10",
    "nov": "11", "november": "11",
    "dec": "12", "december": "12",
}


def normalize_date_range(raw_range: str) -> Tuple[Optional[str], Optional[str]]:
    """Parse informal date ranges into ISO 8601 (YYYY-MM or YYYY) strings.

    Examples:
        'Jan 2021 - Present' -> ('2021-01', 'Present')
        '2020 - 2023' -> ('2020', '2023')
        '03/2019 to 08/2022' -> ('2019-03', '2022-08')
    """
    if not raw_range:
        return None, None

    cleaned = raw_range.strip()
    parts = re.split(r"\s*(?:-|–|—|to)\s*", cleaned, maxsplit=1, flags=re.IGNORECASE)

    start_raw = parts[0].strip() if len(parts) > 0 else ""
    end_raw = parts[1].strip() if len(parts) > 1 else ""

    start_iso = _parse_single_date(start_raw)
    end_iso = _parse_single_date(end_raw)

    return start_iso, end_iso


def _parse_single_date(date_str: str) -> Optional[str]:
    """Helper to parse varied date string formats into standard ISO string."""
    if not date_str:
        return None

    cleaned = date_str.strip().lower()
    if cleaned in ("present", "current", "now", "ongoing"):
        return "Present"

    # Match Month Year (e.g., 'Jan 2021' or 'January 2021')
    month_year_match = re.search(r"([a-z]+)\.?\s+(\d{4})", cleaned)
    if month_year_match:
        month_word, year = month_year_match.group(1), month_year_match.group(2)
        month_num = MONTH_MAP.get(month_word)
        if month_num:
            return f"{year}-{month_num}"

    # Match MM/YYYY or MM-YYYY
    mm_yyyy_match = re.search(r"(\d{1,2})[/-](\d{4})", cleaned)
    if mm_yyyy_match:
        month, year = int(mm_yyyy_match.group(1)), mm_yyyy_match.group(2)
        if 1 <= month <= 12:
            return f"{year}-{month:02d}"

    # Match standalone 4-digit year (e.g., '2022')
    year_match = re.search(r"\b(19\d{2}|20\d{2})\b", cleaned)
    if year_match:
        return year_match.group(1)

    return date_str.strip()
