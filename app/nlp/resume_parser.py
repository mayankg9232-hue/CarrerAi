"""Developer B NLP Resume Parser with multi-format text extraction, section splitting, and entity normalization."""

from abc import ABC, abstractmethod
import io
import re
from typing import Any, Dict, List, Optional, Tuple
import xml.etree.ElementTree as ET
import zipfile

try:
    import fitz  # PyMuPDF
except ImportError:
    fitz = None

try:
    import pypdf
except ImportError:
    pypdf = None

from app.nlp.normalizer import (
    normalize_date_range,
    sanitize_text,
    wrap_untrusted_content,
)
from app.nlp.skill_extractor import SkillExtractor


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
        """Extract structured profile fields from file bytes."""
        pass


# Regex definitions for section headers
SECTION_PATTERNS = {
    "summary": r"(?i)^\s*(?:professional\s+summary|summary|profile|about\s+me|objective)\s*$",
    "skills": r"(?i)^\s*(?:technical\s+skills|core\s+competencies|skills\s*(?:&|and)\s*technologies|skills|technologies|tools)\s*$",
    "experience": r"(?i)^\s*(?:work\s+experience|professional\s+experience|employment\s+history|experience|career\s+history|project\s+experience)\s*$",
    "education": r"(?i)^\s*(?:education|academic\s+background|qualifications|academics)\s*$",
    "projects": r"(?i)^\s*(?:(?:selected|key|notable|personal|academic)\s+)?projects\s*$",
    "other": r"(?i)^\s*(?:relevant\s+coursework.*|additional\s+information|certifications?|awards?|languages?)\s*$",
}


class NLPResumeParser(IResumeParser):
    """Production NLP Resume Parser supporting PDF, DOCX, and TXT with section splitting and taxonomy matching."""

    def __init__(self, skill_extractor: Optional[SkillExtractor] = None) -> None:
        self.skill_extractor = skill_extractor or SkillExtractor()

    async def parse_cv(self, file_bytes: bytes, filename: str) -> Dict[str, Any]:
        """Validate format, extract raw text, isolate sections, and extract structured candidate fields."""
        if not file_bytes:
            raise UnreadableCVError("Uploaded file is empty (0 bytes).")

        raw_text = self._extract_text(file_bytes, filename)
        cleaned_text = sanitize_text(raw_text)

        if not cleaned_text or len(cleaned_text) < 15:
            raise UnreadableCVError("Resume contains no readable text or document is blank/scanned image.")

        sections = self._split_sections(cleaned_text)
        structured_data = self._extract_fields(cleaned_text, sections)

        # Include sanitized XML wrapper for downstream LLM prompt defense
        structured_data["raw_content_xml"] = wrap_untrusted_content(cleaned_text)
        return structured_data

    def _extract_text(self, file_bytes: bytes, filename: str) -> str:
        """Extract plain text using format-specific safe decoders."""
        fname = filename.lower()

        is_pdf = file_bytes.startswith(b"%PDF-") or fname.endswith(".pdf")
        is_docx = file_bytes.startswith(b"PK\x03\x04") or fname.endswith(".docx")

        if is_pdf:
            if not file_bytes.startswith(b"%PDF-"):
                raise UnsupportedFileFormatError(
                    "File extension is .pdf but document header does not contain %PDF- magic bytes."
                )
            return self._extract_pdf_text(file_bytes)

        if is_docx:
            if not file_bytes.startswith(b"PK\x03\x04"):
                raise UnsupportedFileFormatError(
                    "File extension is .docx but document header is not a valid Zip package."
                )
            return self._extract_docx_text(file_bytes)

        # Plain text decoding
        try:
            return file_bytes.decode("utf-8")
        except UnicodeDecodeError:
            try:
                return file_bytes.decode("latin-1")
            except Exception as exc:
                raise UnreadableCVError(f"Failed to decode plain text document: {str(exc)}")

    def _extract_pdf_text(self, file_bytes: bytes) -> str:
        """Extract text from PDF pages using PyMuPDF (fitz) or pypdf."""
        # 1. Prefer PyMuPDF (fitz) for speed and layout-preserving text extraction
        if fitz is not None:
            try:
                doc = fitz.open(stream=file_bytes, filetype="pdf")
                if len(doc) == 0:
                    raise UnreadableCVError("PDF contains zero pages.")
                extracted_pages = []
                for page in doc:
                    txt = page.get_text()
                    if txt and txt.strip():
                        extracted_pages.append(txt.strip())
                doc.close()
                combined = "\n".join(extracted_pages).strip()
                if combined:
                    return combined
            except UnreadableCVError:
                raise
            except Exception:
                pass  # Fall back to pypdf

        # 2. Fall back to pypdf
        if pypdf is None and fitz is None:
            raise ResumeParsingError("Neither PyMuPDF nor pypdf dependency is installed on this system.")

        try:
            reader = pypdf.PdfReader(io.BytesIO(file_bytes))
            if len(reader.pages) == 0:
                raise UnreadableCVError("PDF contains zero pages.")

            extracted_pages = []
            for page in reader.pages:
                txt = page.extract_text()
                if txt:
                    extracted_pages.append(txt)

            combined = "\n".join(extracted_pages).strip()
            if not combined:
                raise UnreadableCVError("PDF contains no extractable text (may be image-only scan or encrypted).")
            return combined
        except UnreadableCVError:
            raise
        except Exception as exc:
            raise UnreadableCVError(f"Corrupted or invalid PDF structure: {str(exc)}")

    def _extract_docx_text(self, file_bytes: bytes) -> str:
        """Extract text from Word document XML structure."""
        try:
            with zipfile.ZipFile(io.BytesIO(file_bytes)) as z:
                if "word/document.xml" not in z.namelist():
                    raise UnreadableCVError("Invalid DOCX structure: missing word/document.xml.")
                xml_content = z.read("word/document.xml")

            tree = ET.fromstring(xml_content)
            namespaces = {"w": "http://schemas.openxmlformats.org/wordprocessingml/2006/main"}
            text_nodes = tree.findall(".//w:t", namespaces)
            if not text_nodes:
                text_nodes = [elem for elem in tree.iter() if elem.tag.endswith("}t")]

            extracted = "\n".join([node.text for node in text_nodes if node.text]).strip()
            if not extracted:
                raise UnreadableCVError("DOCX archive contains no extractable text nodes.")
            return extracted
        except zipfile.BadZipFile:
            raise UnreadableCVError("Corrupted DOCX archive file.")
        except UnreadableCVError:
            raise
        except Exception as exc:
            raise UnreadableCVError(f"Failed to extract text from DOCX: {str(exc)}")

    def _split_sections(self, text: str) -> Dict[str, str]:
        """Split document lines into identified semantic sections."""
        lines = text.split("\n")
        sections: Dict[str, List[str]] = {
            "header": [],
            "summary": [],
            "skills": [],
            "experience": [],
            "education": [],
            "projects": [],
            "other": [],
        }

        current_section = "header"

        for line in lines:
            line_str = line.strip()
            if not line_str:
                continue

            # Check if this line is a section header
            matched_header = False
            for sec_name, pattern in SECTION_PATTERNS.items():
                if re.match(pattern, line_str):
                    current_section = sec_name
                    matched_header = True
                    break

            if not matched_header:
                sections[current_section].append(line_str)

        return {k: "\n".join(v) for k, v in sections.items()}

    def _extract_fields(self, full_text: str, sections: Dict[str, str]) -> Dict[str, Any]:
        """Extract structured entities (name, contact, skills, experience, education)."""
        lines = [line.strip() for line in full_text.split("\n") if line.strip()]

        # 1. Contact Extraction
        email_match = re.search(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+", full_text)
        email = email_match.group(0) if email_match else None

        phone_match = re.search(
            r"(?:\+\d{1,3}[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\d{5}[-.\s]?\d{5}|\b\d{10}\b)",
            full_text,
        )
        phone = phone_match.group(0) if phone_match else None

        # Full Name Heuristic (top line in header without contact symbols or watermark/headline labels)
        full_name = None
        header_lines = sections.get("header", "").split("\n") or lines[:5]
        for hl in header_lines:
            hl_clean = hl.strip()
            if (
                hl_clean
                and "@" not in hl_clean
                and not re.search(r"http|www|\d{5,}", hl_clean)
                and not re.search(r"(?i)\b(?:fictional|sample|synthetic|resume|curriculum\s+vitae|cv|intern|developer|engineer)\b", hl_clean)
                and len(hl_clean) < 50
            ):
                full_name = hl_clean
                break

        # 2. Summary
        summary = sections.get("summary")
        if not summary and len(lines) > 1 and len(lines[1]) > 20 and "@" not in lines[1]:
            summary = lines[1]


        # 3. Skills Extraction via Taxonomy
        # First check dedicated skills section
        skills_text = sections.get("skills", "")
        extracted_skills = []
        if skills_text:
            extracted_skills = self.skill_extractor.extract_skills(skills_text, section_context="skills")

        # Supplement with skills found across the entire resume text
        all_text_skills = self.skill_extractor.extract_skills(full_text, section_context="body")
        existing_names = {s["name"].lower() for s in extracted_skills}
        for item in all_text_skills:
            if item["name"].lower() not in existing_names:
                extracted_skills.append(item)
                existing_names.add(item["name"].lower())

        # 4. Work Experience Extraction
        experience_text = sections.get("experience", "")
        experience = self._parse_experience(experience_text or full_text)

        # 5. Education Extraction
        education_text = sections.get("education", "")
        education = self._parse_education(education_text or full_text)

        clean_summary = summary.strip() if summary and summary.strip() else None

        return {
            "full_name": full_name or "Anonymous Candidate",
            "email": email,
            "phone": phone,
            "summary": clean_summary,
            "skills": extracted_skills,
            "experience": experience,
            "education": education,
        }

    def _parse_experience(self, exp_text: str) -> List[Dict[str, Any]]:
        """Parse employment history into structured job roles."""
        exp_lines = [line.strip() for line in exp_text.split("\n") if line.strip()]
        experience: List[Dict[str, Any]] = []

        exp_keywords = ["engineer", "developer", "architect", "lead", "manager", "intern", "consultant", "analyst"]

        DATE_RE = r"(?i)(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4}|\d{4})\s*(?:-|–|—|to)\s*(?:present|current|\d{4}|(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?\s+\d{4})"

        for i, line in enumerate(exp_lines):
            line_lower = line.lower()
            if any(k in line_lower for k in exp_keywords) and len(line) < 80:
                start_iso, end_iso = None, None
                title = line
                company = "Technology Organization"
                description = None

                m_curr = re.search(DATE_RE, line)
                if m_curr:
                    start_iso, end_iso = normalize_date_range(m_curr.group(0))
                    title = line[: m_curr.start()].strip(" |-–,")

                for offset in (1, 2, 3):
                    if i + offset >= len(exp_lines):
                        break
                    cand = exp_lines[i + offset]
                    if any(k in cand.lower() for k in exp_keywords):
                        break

                    m_date = re.search(DATE_RE, cand)
                    if m_date and not start_iso:
                        start_iso, end_iso = normalize_date_range(m_date.group(0))
                    elif company == "Technology Organization" and len(cand) < 80 and not m_date:
                        company = cand
                    elif not description and not m_date:
                        description = cand

                experience.append({
                    "title": title or line,
                    "company": company,
                    "start_date": start_iso,
                    "end_date": end_iso,
                    "description": description,
                })
                if len(experience) >= 5:
                    break

        return experience

    def _parse_education(self, edu_text: str) -> List[Dict[str, Any]]:
        """Parse academic degrees and institutions."""
        edu_lines = [line.strip() for line in edu_text.split("\n") if line.strip()]
        education: List[Dict[str, Any]] = []

        edu_keywords = ["university", "college", "institute", "school", "academy", "polytechnic"]
        deg_keywords = [
            "b.s.", "m.s.", "ph.d", "bachelor", "master", "doctor", "degree", "associate",
            "b.tech", "m.tech", "btech", "mtech", "b.e.", "m.e.", "bca", "mca",
        ]

        for i, line in enumerate(edu_lines):
            line_lower = line.lower()
            if (any(k in line_lower for k in edu_keywords) or any(d in line_lower for d in deg_keywords)) and len(line) < 250:
                institution = "Educational Institution"
                degree = "B.S. in Computer Science or Related Field"
                field_of_study = "Computer Science"
                grad_year = None

                for offset in (-1, 0, 1, 2):
                    idx = i + offset
                    if idx < 0 or idx >= len(edu_lines):
                        continue
                    cand = edu_lines[idx]
                    cand_lower = cand.lower()

                    if not grad_year:
                        year_match = re.search(r"\b(19\d{2}|20\d{2})\b", cand)
                        if year_match:
                            grad_year = int(year_match.group(1))

                    if any(k in cand_lower for k in edu_keywords):
                        inst_cand = cand.split("|")[0].strip()
                        if any(k in inst_cand.lower() for k in edu_keywords):
                            institution = inst_cand
                        else:
                            institution = cand[:80].strip()

                    if any(d in cand_lower for d in deg_keywords):
                        deg_cand = cand.split("|")[0].strip()
                        if "master" in cand_lower or "m.s." in cand_lower or "m.tech" in cand_lower:
                            degree = "M.S. in Computer Science"
                        elif "ph.d" in cand_lower or "doctor" in cand_lower:
                            degree = "Ph.D. in Computer Science"
                        elif "bachelor" in cand_lower or "b.s." in cand_lower:
                            degree = "B.S. in Computer Science"
                        elif "b.tech" in cand_lower or "btech" in cand_lower or "b.e." in cand_lower:
                            degree = deg_cand or "B.Tech. in Computer Science"
                        else:
                            degree = deg_cand or cand

                    if "information technology" in cand_lower:
                        field_of_study = "Information Technology"
                    elif "computer science" in cand_lower:
                        field_of_study = "Computer Science"
                    elif "data science" in cand_lower:
                        field_of_study = "Data Science"

                if not any(e["institution"] == institution for e in education):
                    education.append({
                        "institution": institution,
                        "degree": degree,
                        "field_of_study": field_of_study,
                        "graduation_year": grad_year,
                    })
                if len(education) >= 3:
                    break

        return education
