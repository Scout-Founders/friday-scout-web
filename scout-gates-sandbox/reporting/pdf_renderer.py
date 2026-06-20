#!/usr/bin/env python3
"""Headless Chrome PDF rendering for Scout reports."""

from __future__ import annotations

import subprocess
from pathlib import Path

from reporting.config import MIN_PDF_BYTES


CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "google-chrome",
    "chromium",
)


def chrome_binary() -> str:
    for candidate in CHROME_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return str(path)
    for candidate in CHROME_CANDIDATES[2:]:
        found = subprocess.run(["which", candidate], capture_output=True, text=True)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    raise RuntimeError(
        "Chrome/Chromium not found. Install Google Chrome to generate PDF reports."
    )


def chrome_available() -> bool:
    try:
        chrome_binary()
        return True
    except RuntimeError:
        return False


def render_html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    html_path = html_path.resolve()
    pdf_path = pdf_path.resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    command = [
        chrome_binary(),
        "--headless=new",
        "--disable-gpu",
        "--no-pdf-header-footer",
        f"--print-to-pdf={pdf_path}",
        html_path.as_uri(),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(result.stderr or result.stdout or "PDF generation failed")
    if not pdf_path.is_file() or pdf_path.stat().st_size < MIN_PDF_BYTES:
        raise RuntimeError(f"PDF was not created or is too small: {pdf_path}")


def write_html_and_render_pdf(html_content: str, html_path: Path, pdf_path: Path) -> None:
    html_path.write_text(html_content, encoding="utf-8")
    render_html_to_pdf(html_path, pdf_path)
