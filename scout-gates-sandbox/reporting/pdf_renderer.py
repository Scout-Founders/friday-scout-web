#!/usr/bin/env python3
"""Headless Chrome PDF rendering for Scout reports."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Iterable

from reporting.config import MIN_PDF_BYTES


# macOS application bundles
MAC_CHROME_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
)

# Common Linux absolute paths
LINUX_CHROME_CANDIDATES = (
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/snap/bin/chromium",
)

# Names resolved via PATH (macOS and Linux)
PATH_CHROME_NAMES = (
    "google-chrome-stable",
    "google-chrome",
    "chromium",
    "chromium-browser",
)

CHROME_CANDIDATES: tuple[str, ...] = (
    *MAC_CHROME_CANDIDATES,
    *LINUX_CHROME_CANDIDATES,
    *PATH_CHROME_NAMES,
)


def discover_chrome_candidates() -> tuple[str, ...]:
    """Return ordered Chrome/Chromium candidate paths/names for the current platform."""
    return CHROME_CANDIDATES


def resolve_chrome_binary(candidates: Iterable[str] | None = None) -> str:
    """Resolve the first available Chrome/Chromium executable."""
    ordered = tuple(candidates or discover_chrome_candidates())
    for candidate in ordered:
        path = Path(candidate)
        if path.is_file():
            return str(path)
    for candidate in ordered:
        if "/" in candidate:
            continue
        found = shutil.which(candidate)
        if found:
            return found
        legacy = subprocess.run(["which", candidate], capture_output=True, text=True)
        if legacy.returncode == 0 and legacy.stdout.strip():
            return legacy.stdout.strip()
    raise RuntimeError(
        "Chrome/Chromium not found. Install Google Chrome or Chromium to generate PDF reports."
    )


def chrome_binary() -> str:
    return resolve_chrome_binary()


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
