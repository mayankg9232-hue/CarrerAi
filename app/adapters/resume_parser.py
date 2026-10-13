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


from app.nlp.resume_parser import (
    IResumeParser,
    NLPResumeParser,
    ResumeParsingError,
    UnreadableCVError,
    UnsupportedFileFormatError,
)


class DefaultResumeParser(NLPResumeParser):
    """Production-grade safe document extractor supporting PDF, DOCX, and TXT with magic byte validation."""
    pass




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
