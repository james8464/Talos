"""Bounded PDF text extraction using the macOS pdftotext utility when present."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

MAX_CHARS = 200_000


def extract(path: Path | str, *, runner=subprocess.run) -> str:
    source = Path(path)
    if source.suffix.casefold() != ".pdf":
        raise ValueError("Expected a PDF file.")
    if not source.is_file():
        raise FileNotFoundError(source)
    executable = shutil.which("pdftotext")
    if not executable:
        raise RuntimeError("Install pdftotext (poppler) to read PDFs.")
    completed = runner([executable, "-layout", str(source), "-"],
                       capture_output=True, text=True, timeout=30, check=False)
    if completed.returncode != 0:
        raise RuntimeError("PDF text extraction failed.")
    return (completed.stdout or "").strip()[:MAX_CHARS]
