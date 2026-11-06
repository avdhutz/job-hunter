"""Loads the industry / academic resume PDFs as text (cached)."""

from functools import lru_cache

from pypdf import PdfReader

from job_hunter import config
from job_hunter.models import Sector


@lru_cache
def resume_text(sector: Sector) -> str:
    pdf = config.path(config.settings()["resumes"][sector])
    return "\n".join(page.extract_text() or "" for page in PdfReader(pdf).pages)
