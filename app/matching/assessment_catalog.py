"""Developer B Assessment Challenge Catalog and Skill Mapping Bank (Phase 7 & 8).

Provides structured coding challenges, starter code templates, and deterministic
test suites (visible + hidden) for candidate skill verification.
"""

from typing import Dict, List, Optional
from pydantic import BaseModel, Field

from app.nlp.normalizer import canonicalize_skill_name
from app.schemas.assessment import SupportedLanguage, TestCase


class AssessmentChallenge(BaseModel):
    """Structured coding challenge definition for candidate skill verification."""

    id: str = Field(..., description="Unique challenge identifier")
    title: str = Field(..., description="Human-readable challenge title")
    skill: str = Field(..., description="Target canonical skill verified by this challenge")
    language: SupportedLanguage = Field(..., description="Programming language runtime")
    difficulty: str = Field(default="intermediate", description="Difficulty tier: beginner, intermediate, advanced")
    description: str = Field(..., description="Full problem specification, inputs, outputs, and constraints")
    starter_code: str = Field(..., description="Starter code template provided to the candidate")
    test_cases: List[TestCase] = Field(default_factory=list, description="Verification test cases")
    reference_solution: Optional[str] = Field(None, description="Internal reference solution for benchmark verification")


# ==============================================================================
# CANONICAL CHALLENGE DEFINITIONS
# ==============================================================================

_CHALLENGES: List[AssessmentChallenge] = [
    # --------------------------------------------------------------------------
    # Python Challenge 1: Two-Sum Target Finder (Skill: Python / Algorithms)
    # --------------------------------------------------------------------------
    AssessmentChallenge(
        id="py-two-sum",
        title="Two-Sum Target Finder",
        skill="Python",
        language=SupportedLanguage.PYTHON,
        difficulty="beginner",
        description=(
            "Given a comma-separated list of integers on the first line and a target integer "
            "on the second line of standard input, find the two 0-based indices whose elements sum "
            "to the target. Print the two indices separated by a space (smaller index first).\n"
            "Example Input:\n2,7,11,15\n9\nExample Output:\n0 1"
        ),
        starter_code=(
            "import sys\n\n"
            "def solve():\n"
            "    lines = [line.strip() for line in sys.stdin if line.strip()]\n"
            "    if len(lines) < 2:\n"
            "        return\n"
            "    nums = [int(x.strip()) for x in lines[0].split(',')]\n"
            "    target = int(lines[1])\n"
            "    # Your implementation here\n"
            "    pass\n\n"
            "if __name__ == '__main__':\n"
            "    solve()\n"
        ),
        reference_solution=(
            "import sys\n\n"
            "def solve():\n"
            "    lines = [line.strip() for line in sys.stdin if line.strip()]\n"
            "    if len(lines) < 2:\n"
            "        return\n"
            "    nums = [int(x.strip()) for x in lines[0].split(',')]\n"
            "    target = int(lines[1])\n"
            "    seen = {}\n"
            "    for i, num in enumerate(nums):\n"
            "        diff = target - num\n"
            "        if diff in seen:\n"
            "            print(f\"{seen[diff]} {i}\")\n"
            "            return\n"
            "        seen[num] = i\n\n"
            "if __name__ == '__main__':\n"
            "    solve()\n"
        ),
        test_cases=[
            TestCase(id="tc_py_01", input_data="2,7,11,15\n9", expected_output="0 1", is_hidden=False),
            TestCase(id="tc_py_02", input_data="3,2,4\n6", expected_output="1 2", is_hidden=False),
            TestCase(id="tc_py_03", input_data="3,3\n6", expected_output="0 1", is_hidden=True),
            TestCase(id="tc_py_04", input_data="1,5,8,12,20\n21", expected_output="0 4", is_hidden=True),
        ],
    ),
    # --------------------------------------------------------------------------
    # Python Challenge 2: Array Deduplication and Frequency (Skill: Python / Data Structures)
    # --------------------------------------------------------------------------
    AssessmentChallenge(
        id="py-array-dedup",
        title="Sorted Unique Frequency Counter",
        skill="Python",
        language=SupportedLanguage.PYTHON,
        difficulty="intermediate",
        description=(
            "Given a comma-separated list of items from standard input, count the frequency of each unique item. "
            "Print each unique item and its count in the format '<item>:<count>', sorted alphabetically by item, "
            "separated by commas.\n"
            "Example Input:\napple,banana,apple,orange,banana,apple\n"
            "Example Output:\napple:3,banana:2,orange:1"
        ),
        starter_code=(
            "import sys\n\n"
            "def solve():\n"
            "    data = sys.stdin.read().strip()\n"
            "    # Your implementation here\n"
            "    pass\n\n"
            "if __name__ == '__main__':\n"
            "    solve()\n"
        ),
        reference_solution=(
            "import sys\n"
            "from collections import Counter\n\n"
            "def solve():\n"
            "    data = sys.stdin.read().strip()\n"
            "    if not data:\n"
            "        return\n"
            "    items = [x.strip() for x in data.split(',') if x.strip()]\n"
            "    counts = Counter(items)\n"
            "    pairs = [f\"{k}:{counts[k]}\" for k in sorted(counts.keys())]\n"
            "    print(','.join(pairs))\n\n"
            "if __name__ == '__main__':\n"
            "    solve()\n"
        ),
        test_cases=[
            TestCase(
                id="tc_py_dedup_01",
                input_data="apple,banana,apple,orange,banana,apple",
                expected_output="apple:3,banana:2,orange:1",
                is_hidden=False,
            ),
            TestCase(
                id="tc_py_dedup_02",
                input_data="python,fastapi,docker,python,fastapi",
                expected_output="docker:1,fastapi:2,python:2",
                is_hidden=False,
            ),
            TestCase(
                id="tc_py_dedup_03",
                input_data="single",
                expected_output="single:1",
                is_hidden=True,
            ),
        ],
    ),
    # --------------------------------------------------------------------------
    # JavaScript Challenge 1: Array Filtering & Sorting (Skill: JavaScript)
    # --------------------------------------------------------------------------
    AssessmentChallenge(
        id="js-array-filter",
        title="Even Numbers Squared and Sorted",
        skill="JavaScript",
        language=SupportedLanguage.JAVASCRIPT,
        difficulty="beginner",
        description=(
            "Read a comma-separated list of integers from stdin. Filter for even numbers, "
            "square each even number, sort them in ascending order, and print them as a comma-separated string.\n"
            "Example Input:\n5,2,8,1,4\nExample Output:\n4,16,64"
        ),
        starter_code=(
            "const fs = require('fs');\n"
            "const input = fs.readFileSync(0, 'utf-8').trim();\n"
            "// Your implementation here\n"
        ),
        reference_solution=(
            "const fs = require('fs');\n"
            "const input = fs.readFileSync(0, 'utf-8').trim();\n"
            "if (!input) process.exit(0);\n"
            "const nums = input.split(',').map(x => parseInt(x.trim(), 10));\n"
            "const evensSquared = nums.filter(n => n % 2 === 0).map(n => n * n).sort((a, b) => a - b);\n"
            "console.log(evensSquared.join(','));\n"
        ),
        test_cases=[
            TestCase(id="tc_js_01", input_data="5,2,8,1,4", expected_output="4,16,64", is_hidden=False),
            TestCase(id="tc_js_02", input_data="1,3,5,7", expected_output="", is_hidden=False),
            TestCase(id="tc_js_03", input_data="10,6,2", expected_output="4,36,100", is_hidden=True),
        ],
    ),
    # --------------------------------------------------------------------------
    # TypeScript Challenge 1: Typed Dictionary Key Formatter (Skill: TypeScript)
    # --------------------------------------------------------------------------
    AssessmentChallenge(
        id="ts-typed-dict",
        title="JSON Key Case Converter",
        skill="TypeScript",
        language=SupportedLanguage.TYPESCRIPT,
        difficulty="intermediate",
        description=(
            "Read a single-line JSON object from stdin with camelCase keys. "
            "Convert all top-level keys to snake_case and print the resulting JSON string with keys in alphabetical order.\n"
            "Example Input:\n{\"firstName\":\"John\",\"lastName\":\"Doe\"}\n"
            "Example Output:\n{\"first_name\":\"John\",\"last_name\":\"Doe\"}"
        ),
        starter_code=(
            "import * as fs from 'fs';\n"
            "const input = fs.readFileSync(0, 'utf-8').trim();\n"
            "// Your implementation here\n"
        ),
        reference_solution=(
            "import * as fs from 'fs';\n"
            "const input = fs.readFileSync(0, 'utf-8').trim();\n"
            "if (!input) process.exit(0);\n"
            "const obj = JSON.parse(input);\n"
            "const toSnake = (s: string) => s.replace(/([A-Z])/g, '_$1').toLowerCase();\n"
            "const result: Record<string, any> = {};\n"
            "const sortedKeys = Object.keys(obj).sort();\n"
            "for (const k of sortedKeys) {\n"
            "  result[toSnake(k)] = obj[k];\n"
            "}\n"
            "console.log(JSON.stringify(result));\n"
        ),
        test_cases=[
            TestCase(
                id="tc_ts_01",
                input_data='{"firstName":"John","lastName":"Doe"}',
                expected_output='{"first_name":"John","last_name":"Doe"}',
                is_hidden=False,
            ),
            TestCase(
                id="tc_ts_02",
                input_data='{"userId":101,"isActive":true}',
                expected_output='{"is_active":true,"user_id":101}',
                is_hidden=False,
            ),
        ],
    ),
]


# ==============================================================================
# CATALOG RETRIEVAL APIS
# ==============================================================================

def get_all_challenges() -> List[AssessmentChallenge]:
    """Return all available assessment coding challenges."""
    return list(_CHALLENGES)


def get_challenge_by_id(challenge_id: str) -> Optional[AssessmentChallenge]:
    """Retrieve challenge by unique identifier."""
    norm = challenge_id.strip().lower()
    for ch in _CHALLENGES:
        if ch.id.lower() == norm:
            return ch
    return None


def get_challenges_by_skill(skill: str, language: Optional[str] = None) -> List[AssessmentChallenge]:
    """Filter challenges by target skill name (canonicalized) and optional language."""
    canonical = canonicalize_skill_name(skill).lower()
    norm_lang = language.strip().lower() if language else None

    matches = []
    for ch in _CHALLENGES:
        ch_skill = canonicalize_skill_name(ch.skill).lower()
        if ch_skill == canonical or canonical in ch_skill:
            if norm_lang is None or ch.language.value.lower() == norm_lang:
                matches.append(ch)
    return matches
