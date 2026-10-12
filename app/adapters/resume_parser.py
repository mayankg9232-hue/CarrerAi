"""Resume parser interface and implementations for extracting structured candidate profiles."""

from abc import ABC, abstractmethod
import io
import re
from typing import Any, Dict, List, Optional
import xml.etree.ElementTree as ET
import zipfile

try:
    import pypdf
except ImportError:
    pypdf = None


class ResumeParsingError(Exception):
    """Base exception for CV and resume extraction errors."""
    pass


class UnreadableCVError(ResumeParsingError):
    """Raised when CV document is corrupted, blank, or cannot be parsed into text."""
    pass


class UnsupportedFileFormatError(ResumeParsingError):
    """Raised when document does not match allowed MIME types or magic byte headers."""
    pass


class IResumeParser(ABC):
    """Abstract interface for resume parsing and structured field extraction."""

    @abstractmethod
    async def parse_cv(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        """Extract structured profile fields (name, email, skills, experience, education) from file bytes."""
        pass


KNOWN_SKILLS = [
    ("Python", "language"),
    ("JavaScript", "language"),
    ("TypeScript", "language"),
    ("Go", "language"),
    ("Java", "language"),
    ("Rust", "language"),
    ("C++", "language"),
    ("FastAPI", "framework"),
    ("Django", "framework"),
    ("Flask", "framework"),
    ("React", "framework"),
    ("Node.js", "framework"),
    ("Next.js", "framework"),
    ("Docker", "tool"),
    ("Kubernetes", "tool"),
    ("Git", "tool"),
    ("PostgreSQL", "database"),
    ("MySQL", "database"),
    ("MongoDB", "database"),
    ("Redis", "database"),
    ("AWS", "tool"),
    ("GCP", "tool"),
    ("Azure", "tool"),
    ("Linux", "tool"),
    ("REST API", "general"),
    ("GraphQL", "general"),
    ("CI/CD", "tool"),
    ("SQL", "language"),
]


from app.nlp.resume_parser import NLPResumeParser


class DefaultResumeParser(NLPResumeParser):
    """Production-grade safe document extractor supporting PDF, DOCX, and TXT with magic byte validation."""
    pass

    def _extract_text(self, file_bytes: bytes, filename: str) -> str:
        fname = filename.lower()

        # Magic byte detection
        is_pdf = file_bytes.startswith(b"%PDF-") or fname.endswith(".pdf")
        is_docx = file_bytes.startswith(b"PK\x03\x04") or fname.endswith(".docx")
        is_txt = fname.endswith(".txt") or not (is_pdf or is_docx)

        if is_pdf:
            if not file_bytes.startswith(b"%PDF-"):
                raise UnsupportedFileFormatError("File extension is .pdf but document header does not contain %PDF- magic bytes.")
            return self._extract_pdf_text(file_bytes)

        if is_docx:
            if not file_bytes.startswith(b"PK\x03\x04"):
                raise UnsupportedFileFormatError("File extension is .docx but document header is not a valid Zip package.")
            return self._extract_docx_text(file_bytes)

        # Plain text
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return file_bytes.decode("latin-1")
            except Exception as exc:
                raise UnreadableCVError(f"Failed to decode plain text document: {str(exc)}")

    def _extract_pdf_text(self, file_bytes: bytes) -> str:
        if pypdf is None:
            raise ResumeParsingError("pypdf dependency is not installed on this system.")

        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            if len(reader.pages) == 0:
                raise UnreadableCVError("PDF contains zero pages.")

            extracted_text = []
            for page in reader.pages:
                txt = page.extract_text()
                if txt:
                    extracted_text.append(txt)

            combined = "\n".join(extracted_text).strip()
            if not combined:
                raise UnreadableCVError("PDF contains no extractable text (may be image-only scan or encrypted).")
            return combined
        except UnreadableCVError:
            raise
        except Exception as exc:
            raise UnreadableCVError(f"Corrupted or invalid PDF structure: {str(exc)}")

    def _extract_docx_text(self, file_bytes: bytes) -> str:
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                if "word/document.xml" not in z.namelist():
                    raise UnreadableCVError("Invalid DOCX structure: missing word/document.xml.")
                xml_content = z.read("word/document.xml")

            tree = ET.fromstring(xml_content)
            # Find all text elements in the OpenXML namespace
            namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text_nodes = tree.findall(".//w:t", namespaces)
            if not text_nodes:
                # Fallback to general tag search
                text_nodes = [elem for elem in tree.iter() if elem.tag.endswith("}t")]

            return "\n".join([node.text for node in text_nodes if node.text]).strip()
        except zipfile.BadZipFile:
            raise UnreadableCVError("Corrupted DOCX archive file.")
        except Exception as exc:
            raise UnreadableCVError(f"Failed to extract text from DOCX: {str(exc)}")

    def _extract_structured_fields(self, text: str) -> Dict[str, Any]:
        lines = [line.strip() for line in text.split("\n") if line.strip()]

        # Extract Email
        email_match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", text)
        email = email_match.group(0) if email_match else None

        # Extract Full Name (typically top non-empty line without special characters)
        full_name = None
        for line in lines[:5]:
            if "@" not in line and not re.search(r"http|www|\d{5,}", line) and len(line) < 50:
                full_name = line
                break

        # Extract Skills
        text_lower = text.lower()
        extracted_skills = []
        seen_skills = set()
        for skill_name, category in KNOWN_SKILLS:
            # Match whole words
            pattern = rf"\b{re.escape(skill_name.lower())}\b"
            if re.search(pattern, text_lower):
                if skill_name.lower() not in seen_skills:
                    seen_skills.add(skill_name.lower())
                    extracted_skills.append({
                        "name": skill_name,
                        "category": category,
                        "confidence": 0.95,
                        "years_experience": None,
                    })

        # Summary
        summary = lines[1] if len(lines) > 1 and len(lines[1]) > 20 else None

        # Experience heuristics
        experience = []
        exp_keywords = ["engineer", "developer", "architect", "lead", "manager", "intern", "consultant"]
        for i, line in enumerate(lines):
            line_lower = line.lower()
            if any(k in line_lower for k in exp_keywords) and len(line) < 80:
                experience.append({
                    "title": line,
                    "company": lines[i + 1] if i + 1 < len(lines) else "Technology Organization",
                    "start_date": None,
                    "end_date": None,
                    "description": lines[i + 2] if i + 2 < len(lines) else None,
                })
                if len(experience) >= 3:
                    break

        # Education heuristics
        education = []
        edu_keywords = ["university", "college", "institute", "b.s.", "m.s.", "ph.d", "bachelor", "master"]
        for line in lines:
            line_lower = line.lower()
            if any(k in line_lower for k in edu_keywords) and len(line) < 120:
                education.append({
                    "institution": line,
                    "degree": "Degree in Computer Science or Related Field",
                    "field_of_study": "Computer Science",
                    "graduation_year": None,
                })
                if len(education) >= 2:
                    break

        return {
            "full_name": full_name or "Anonymous Candidate",
            "email": email,
            "summary": summary,
            "skills": extracted_skills,
            "experience": experience,
            "education": education,
        }


class MockResumeParser(IResumeParser):
    """Configurable mock parser for deterministic pipeline tests."""

    def __init__(
        self,
        canned_response: Optional[Dict[str, Any]] = None,
        simulate_unreadable: bool = False,
        simulate_unsupported_format: bool = False,
    ) -> None:
        self.canned_response = canned_response
        self.simulate_unreadable = simulate_unreadable
        self.simulate_unsupported_format = simulate_unsupported_format

    async def parse_cv(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        if self.simulate_unreadable:
            raise UnreadableCVError("Simulated unreadable document.")

        if self.simulate_unsupported_format:
            raise UnsupportedFileFormatError("Simulated unsupported file format.")

        if self.canned_response is not None:
            return self.canned_response

        return {
            "full_name": "Test Candidate",
            "email": "candidate@example.com",
            "summary": "Experienced software developer.",
            "skills": [
                {"name": "Python", "category": "language", "confidence": 0.95, "years_experience": 3.0},
                {"name": "FastAPI", "category": "framework", "confidence": 0.90, "years_experience": 2.0},
            ],
            "experience": [
                {
                    "title": "Software Engineer",
                    "company": "Tech Corp",
                    "start_date": "2023-01",
                    "end_date": "Present",
                    "description": "Building backend APIs.",
                }
            ],
            "education": [
                {
                    "institution": "University of Tech",
                    "degree": "B.S. Computer Science",
                    "field_of_study": "Computer Science",
                    "graduation_year": 2022,
                }
            ],
        }
