"""Resume processing and candidate profile pipeline orchestration."""

import hashlib
import os
import re
from typing import Optional
import uuid

from app.adapters.resume_parser import (
    DefaultResumeParser,
    IResumeParser,
    ResumeParsingError,
    UnreadableCVError,
    UnsupportedFileFormatError,
)
from app.core.config import Settings, get_settings
from app.core.exceptions import (
    BadRequestError,
    PayloadTooLargeError,
    UnsupportedMediaTypeError,
)
from app.repositories.resume_repo import IResumeRepository, get_resume_repository
from app.schemas.resume import (
    CandidateProfile,
    EducationItem,
    ExperienceItem,
    SkillItem,
)


# Dangerous executable/script signatures
FORBIDDEN_MAGIC_SIGNATURES = [
    (b"MZ", "Windows Executable (MZ)"),
    (b"\x7fELF", "Linux ELF Executable"),
    (b"<?php", "PHP Script"),
    (b"#!/", "Unix Shell Script"),
    (b"\xca\xfe\xba\xbe", "Mach-O / Java Class Binary"),
    (b"\xfe\xed\xfa\xce", "Mach-O 32-bit"),
    (b"\xfe\xed\xfa\xcf", "Mach-O 64-bit"),
]


class ResumePipeline:
    """Orchestrates document validation, safe extraction, candidate profile normalization, and persistence."""

    def __init__(
        self,
        parser: Optional[IResumeParser] = None,
        repo: Optional[IResumeRepository] = None,
        settings: Optional[Settings] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.parser = parser or DefaultResumeParser()
        self.repo = repo or get_resume_repository()

    async def process_resume(
        self,
        file_bytes: bytes,
        filename: str,
        content_type: Optional[str] = None,
        user_id: str = "",
    ) -> CandidateProfile:
        """Validate, parse, normalize, and store a candidate CV."""
        if not user_id:
            raise BadRequestError("A valid user_id must be provided for candidate isolation.")

        # 1. Size Validation
        if len(file_bytes) > self.settings.MAX_CV_SIZE_BYTES:
            max_mb = self.settings.MAX_CV_SIZE_BYTES / (1024 * 1024)
            raise PayloadTooLargeError(f"Uploaded file exceeds maximum size limit of {max_mb:.1f} MB.")

        if len(file_bytes) == 0:
            raise BadRequestError("Uploaded file is empty (0 bytes).")

        # 2. Filename Sanitization
        safe_filename = os.path.basename(filename or "").strip()
        if not safe_filename:
            safe_filename = "resume.pdf"

        # Check for forbidden binary headers
        for sig, desc in FORBIDDEN_MAGIC_SIGNATURES:
            if file_bytes.startswith(sig):
                raise UnsupportedMediaTypeError(f"Prohibited file type detected: {desc}.")

        # 3. MIME and Extension Verification
        lower_name = safe_filename.lower()
        allowed_extensions = (".pdf", ".docx", ".txt")
        if not any(lower_name.endswith(ext) for ext in allowed_extensions):
            raise UnsupportedMediaTypeError(
                f"File '{safe_filename}' has an unsupported extension. Allowed formats: PDF, DOCX, TXT."
            )

        # Magic byte verification
        if lower_name.endswith(".pdf") and not file_bytes.startswith(b"%PDF-"):
            raise UnsupportedMediaTypeError("File has .pdf extension but lacks %PDF- header magic bytes.")
        if lower_name.endswith(".docx") and not file_bytes.startswith(b"PK\x03\x04"):
            raise UnsupportedMediaTypeError("File has .docx extension but lacks valid Zip package header.")

        # 4. Hash Computation and Deduplication
        raw_hash = hashlib.sha256(file_bytes).hexdigest()
        existing_profile = await self.repo.get_by_raw_hash(user_id=user_id, raw_hash=raw_hash)
        if existing_profile:
            return existing_profile

        # 5. Safe Parsing via Parser Adapter
        try:
            parsed = await self.parser.parse_cv(file_bytes, safe_filename)
        except UnreadableCVError as exc:
            raise BadRequestError(f"Unreadable CV document: {str(exc)}")
        except UnsupportedFileFormatError as exc:
            raise UnsupportedMediaTypeError(str(exc))
        except Exception as exc:
            raise BadRequestError(f"Failed to process CV: {str(exc)}")

        # 6. Profile Normalization
        profile_id = f"prof_{uuid.uuid4().hex[:8]}"

        # Normalize skills
        validated_skills = []
        for s in parsed.get("skills", []):
            try:
                name = str(s.get("name", "")).strip()
                if not name:
                    continue
                confidence = float(s.get("confidence", 1.0))
                confidence = max(0.0, min(1.0, confidence))  # clamp
                years_exp = s.get("years_experience")
                if years_exp is not None:
                    years_exp = max(0.0, float(years_exp))

                validated_skills.append(
                    SkillItem(
                        name=name,
                        category=s.get("category", "general"),
                        confidence=confidence,
                        years_experience=years_exp,
                    )
                )
            except Exception:
                continue

        # Normalize experience entries
        validated_experience = []
        for exp in parsed.get("experience", []):
            try:
                title = str(exp.get("title", "")).strip() or "Engineer"
                company = str(exp.get("company", "")).strip() or "Organization"
                validated_experience.append(
                    ExperienceItem(
                        title=title[:200],
                        company=company[:200],
                        start_date=exp.get("start_date"),
                        end_date=exp.get("end_date"),
                        description=exp.get("description"),
                    )
                )
            except Exception:
                continue

        # Normalize education entries
        validated_education = []
        for edu in parsed.get("education", []):
            try:
                institution = str(edu.get("institution", "")).strip() or "University"
                degree = str(edu.get("degree", "")).strip() or "Degree"
                grad_year = edu.get("graduation_year")
                if grad_year is not None:
                    grad_year = int(grad_year)
                    if grad_year < 1950 or grad_year > 2100:
                        grad_year = None

                validated_education.append(
                    EducationItem(
                        institution=institution[:200],
                        degree=degree[:200],
                        field_of_study=edu.get("field_of_study"),
                        graduation_year=grad_year,
                    )
                )
            except Exception:
                continue

        candidate_profile = CandidateProfile(
            id=profile_id,
            user_id=user_id,
            raw_hash=raw_hash,
            full_name=parsed.get("full_name"),
            email=parsed.get("email"),
            summary=parsed.get("summary"),
            skills=validated_skills,
            experience=validated_experience,
            education=validated_education,
        )

        # 7. Persistence
        saved_profile = await self.repo.save_profile(candidate_profile)
        return saved_profile
