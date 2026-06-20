#!/usr/bin/env python3
"""Configuration for Scout sandbox PDF reporting."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


REPORT_TYPE_SCORING_BREAKDOWN = "scoring_breakdown"

SUPPORTED_REPORT_TYPES = (REPORT_TYPE_SCORING_BREAKDOWN,)

MANIFEST_FILENAME = ".report-manifest.json"
REGISTRY_DB_FILENAME = ".report-registry.db"
MAX_REGISTRY_ENTRIES = 5000
MAX_MANIFEST_ENTRIES = MAX_REGISTRY_ENTRIES  # backward compat
MIN_PDF_BYTES = 500
JOB_POLL_INTERVAL_SEC = 0.75
JOB_RETENTION_DAYS = 30
DEFAULT_ASYNC_EXPORT = os.environ.get("SCOUT_REPORTS_ASYNC", "").strip().lower() in (
    "1",
    "true",
    "yes",
)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def default_exports_dir() -> Path:
    override = os.environ.get("SCOUT_REPORTS_DIR", "").strip()
    if override:
        path = Path(override).expanduser()
    else:
        path = repo_root() / "exports" / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def manifest_path(exports_dir: Path | None = None) -> Path:
    return (exports_dir or default_exports_dir()) / MANIFEST_FILENAME


def registry_db_path(exports_dir: Path | None = None) -> Path:
    return (exports_dir or default_exports_dir()) / REGISTRY_DB_FILENAME


@dataclass(frozen=True)
class ReportConfig:
    exports_dir: Path
    report_type: str = REPORT_TYPE_SCORING_BREAKDOWN
    keep_html_artifacts: bool = True

    @property
    def manifest_file(self) -> Path:
        return manifest_path(self.exports_dir)

    @property
    def relative_exports_path(self) -> str:
        try:
            return str(self.exports_dir.relative_to(repo_root()))
        except ValueError:
            return str(self.exports_dir)
