"""Skill taxonomy, alias resolution, context-aware confidence scoring, and experience extraction."""

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from app.nlp.normalizer import canonicalize_skill_name


# Master skill taxonomy: (Canonical Name, Category, Regex Pattern, Alternate Aliases)
SKILL_TAXONOMY: List[Tuple[str, str, str, List[str]]] = [
    # Languages
    ("Python", "language", r"\b(?:python|py)\b", ["python", "py"]),
    ("JavaScript", "language", r"\b(?:javascript|js)\b", ["javascript", "js"]),
    ("TypeScript", "language", r"\b(?:typescript|ts)\b", ["typescript", "ts"]),
    ("Go", "language", r"\b(?:golang|go)\b", ["golang", "go"]),
    ("Java", "language", r"\bjava\b(?!script)", ["java"]),
    ("Rust", "language", r"\brust\b", ["rust"]),
    ("C++", "language", r"(?:\bc\+\+|\bcpp\b)", ["c++", "cpp"]),
    ("C#", "language", r"(?:\bc#|\bcsharp\b)", ["c#", "csharp"]),
    ("C", "language", r"\bc\b", ["c"]),
    ("Ruby", "language", r"\bruby\b", ["ruby"]),
    ("PHP", "language", r"\bphp\b", ["php"]),
    ("Swift", "language", r"\bswift\b", ["swift"]),
    ("Kotlin", "language", r"\bkotlin\b", ["kotlin"]),
    ("SQL", "language", r"\bsql\b", ["sql"]),
    ("Bash", "language", r"\b(?:bash|shell)\b", ["bash", "shell"]),
    ("Scala", "language", r"\bscala\b", ["scala"]),
    ("R", "language", r"\br\b", ["r"]),

    # Frameworks & Libraries
    ("FastAPI", "framework", r"\bfastapi\b", ["fastapi"]),
    ("Django", "framework", r"\bdjango\b", ["django"]),
    ("Flask", "framework", r"\bflask\b", ["flask"]),
    ("React", "framework", r"\b(?:react|reactjs|react\.js)\b", ["react", "reactjs", "react.js"]),
    ("Angular", "framework", r"\bangular\b", ["angular"]),
    ("Vue.js", "framework", r"\b(?:vue|vuejs|vue\.js)\b", ["vue", "vuejs", "vue.js"]),
    ("Node.js", "framework", r"\b(?:node|nodejs|node\.js)\b", ["node", "nodejs", "node.js"]),
    ("Next.js", "framework", r"\b(?:next|nextjs|next\.js)\b", ["next", "nextjs", "next.js"]),
    ("Express", "framework", r"\b(?:express|expressjs)\b", ["express", "expressjs"]),
    ("NestJS", "framework", r"\b(?:nestjs|nest\.js)\b", ["nestjs", "nest.js"]),
    ("Spring Boot", "framework", r"\b(?:spring\s+boot|spring)\b", ["spring boot", "spring"]),
    (".NET", "framework", r"(?:\b\.net\b|\bdotnet\b|\basp\.net\b)", [".net", "dotnet", "asp.net"]),
    ("Ruby on Rails", "framework", r"\b(?:rails|ruby\s+on\s+rails)\b", ["rails", "ruby on rails"]),
    ("PyTorch", "framework", r"\bpytorch\b", ["pytorch"]),
    ("TensorFlow", "framework", r"\btensorflow\b", ["tensorflow"]),

    # Databases
    ("PostgreSQL", "database", r"\b(?:postgresql|postgres|pgsql)\b", ["postgresql", "postgres", "pgsql"]),
    ("MySQL", "database", r"\bmysql\b", ["mysql"]),
    ("SQLite", "database", r"\bsqlite\b", ["sqlite"]),
    ("MongoDB", "database", r"\b(?:mongodb|mongo)\b", ["mongodb", "mongo"]),
    ("Redis", "database", r"\bredis\b", ["redis"]),
    ("Cassandra", "database", r"\bcassandra\b", ["cassandra"]),
    ("DynamoDB", "database", r"\bdynamodb\b", ["dynamodb"]),
    ("Elasticsearch", "database", r"\b(?:elasticsearch|elastic\s+search)\b", ["elasticsearch"]),

    # Tools & DevOps
    ("Docker", "tool", r"\bdocker\b", ["docker"]),
    ("Kubernetes", "tool", r"\b(?:kubernetes|k8s|kube)\b", ["kubernetes", "k8s"]),
    ("Git", "tool", r"\b(?:git|github|gitlab)\b", ["git", "github", "gitlab"]),
    ("GitHub Actions", "tool", r"\bgithub\s+actions\b", ["github actions"]),
    ("Terraform", "tool", r"\b(?:terraform|tf)\b", ["terraform", "tf"]),
    ("Ansible", "tool", r"\bansible\b", ["ansible"]),
    ("Jenkins", "tool", r"\bjenkins\b", ["jenkins"]),
    ("CI/CD", "tool", r"\b(?:ci/cd|cicd|ci\s*/\s*cd)\b", ["ci/cd", "cicd"]),
    ("Linux", "tool", r"\b(?:linux|unix)\b", ["linux", "unix"]),
    ("Nginx", "tool", r"\bnginx\b", ["nginx"]),
    ("Prometheus", "tool", r"\bprometheus\b", ["prometheus"]),
    ("Grafana", "tool", r"\bgrafana\b", ["grafana"]),

    # Cloud Providers
    ("AWS", "cloud", r"\b(?:aws|amazon\s+web\s+services)\b", ["aws"]),
    ("GCP", "cloud", r"\b(?:gcp|google\s+cloud(?:platform)?)\b", ["gcp", "google cloud"]),
    ("Azure", "cloud", r"\b(?:azure|microsoft\s+azure)\b", ["azure"]),

    # Architecture & General
    ("REST API", "general", r"\b(?:rest|restful|rest\s+api|restful\s+api)\b", ["rest", "rest api"]),
    ("GraphQL", "general", r"\bgraphql\b", ["graphql"]),
    ("Microservices", "general", r"\bmicroservices?\b", ["microservices"]),
    ("System Design", "general", r"\bsystem\s+design\b", ["system design"]),
    ("Agile", "general", r"\b(?:agile|scrum)\b", ["agile", "scrum"]),
    ("TDD", "general", r"\b(?:tdd|test[- ]driven\s+development)\b", ["tdd"]),
]


class SkillExtractor:
    """Taxonomy-based skill extractor with context-aware scoring and experience duration inference."""

    def __init__(self) -> None:
        self.taxonomy = SKILL_TAXONOMY

    def extract_skills(
        self,
        text: str,
        section_context: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Extract validated skill entries with taxonomy categories, confidence levels, and years of experience.

        Args:
            text: Raw or sanitized text document (resume section or job description).
            section_context: Optional section identifier (e.g. 'skills', 'experience', 'summary').

        Returns:
            List of dictionaries matching SkillItem schema:
            [{"name": "Python", "category": "language", "confidence": 0.98, "years_experience": 4.0}, ...]
        """
        if not text:
            return []

        text_lower = text.lower()
        extracted: List[Dict[str, Any]] = []
        seen_canonical: Set[str] = set()

        # Determine baseline confidence according to document context
        base_confidence = 0.85
        if section_context:
            ctx = section_context.lower()
            if "skill" in ctx or "technolog" in ctx or "tool" in ctx:
                base_confidence = 0.98
            elif "experience" in ctx or "work" in ctx or "project" in ctx:
                base_confidence = 0.92
            elif "summary" in ctx or "objective" in ctx:
                base_confidence = 0.85

        for canonical_name, category, regex_pat, _ in self.taxonomy:
            if canonical_name in seen_canonical:
                continue

            # Special case single-letter keywords ("C", "R") to avoid false positives
            if len(canonical_name) == 1:
                pattern = rf"(?:\b{re.escape(canonical_name)}\b(?:\s+(?:programming|language|developer))?)"
                match = re.search(pattern, text)
            else:
                match = re.search(regex_pat, text_lower, flags=re.IGNORECASE)

            if match:
                seen_canonical.add(canonical_name)

                # Extract years of experience if mentioned in vicinity of the skill
                years_exp = self.extract_years_for_skill(canonical_name, text)

                # Boost confidence if years of experience or strong context is observed
                confidence = base_confidence
                if years_exp is not None:
                    confidence = min(1.0, confidence + 0.05)

                extracted.append({
                    "name": canonical_name,
                    "category": category,
                    "confidence": round(confidence, 2),
                    "years_experience": years_exp,
                })

        return extracted

    def extract_years_for_skill(self, skill_name: str, text: str) -> Optional[float]:
        """Infer years of experience for a skill using common CV phrased patterns."""
        skill_re = re.escape(skill_name.lower())

        # Patterns like: "5+ years of experience with Python", "3 years in Python", "Python (4 years)"
        patterns = [
            # "5+ years of Python" / "5 years experience in Python"
            rf"(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)\s*(?:of\s*(?:experience\s*(?:with|in)?)?)?\s+(?:with\s+|in\s+)?{skill_re}",
            # "Python: 4+ years" / "Python (3 years)" / "Python - 5 yrs"
            rf"{skill_re}\s*(?:[:\-–(]|\s+for\s+)\s*(\d+(?:\.\d+)?)\+?\s*(?:years?|yrs?)",
        ]

        text_lower = text.lower()
        for pat in patterns:
            match = re.search(pat, text_lower)
            if match:
                try:
                    val = float(match.group(1))
                    if 0.1 <= val <= 40.0:
                        return val
                except (ValueError, IndexError):
                    continue

        return None
