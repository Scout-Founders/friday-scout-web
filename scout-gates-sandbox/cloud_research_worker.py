#!/usr/bin/env python3
"""Cloud research worker environment validation for GitHub Actions."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any


REQUIRED_SECRET_ENV_VARS = (
    "SCOUT_RESEARCH_WORKER_SECRET",
    "SCOUT_CLOUD_RESEARCH_ENABLED",
)

RESEARCH_DB_PATH_ENV = "SCOUT_RESEARCH_DB_PATH"
DEFAULT_CLOUD_RESEARCH_DB_NAME = "scout_research_cloud.db"

# Tables the cloud worker may mutate during normal operation.
CLOUD_RESEARCH_WRITE_TABLES = frozenset(
    {
        "research_jobs",
        "research_job_runs",
        "research_findings",
        "rule_candidates",
        "rule_validations",
        "research_daily_reports",
    }
)

# Tables read by preview_backtest analytics during research runs.
CLOUD_RESEARCH_PREVIEW_READ_TABLES = frozenset(
    {
        "scan_results",
        "scan_runs",
    }
)

# Shared sandbox tables that must not receive cloud-worker data writes.
CLOUD_RESEARCH_PROTECTED_TABLES = frozenset(
    {
        "scan_runs",
        "scan_results",
        "outcome_update_audit",
        "institutional_audit_log",
        "gate_attributions",
        "gate_alpha_metrics",
        "regime_snapshots",
        "gate_intelligence_metrics",
        "feature_vectors",
        "pattern_intelligence",
        "backtest_runs",
        "backtest_signals",
        "backtest_metrics",
    }
)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def resolve_cloud_research_db_path(*, sandbox_dir: Path | None = None) -> Path:
    explicit = os.environ.get(RESEARCH_DB_PATH_ENV, "").strip()
    if explicit:
        return Path(explicit).expanduser().resolve()
    root = sandbox_dir or Path(__file__).resolve().parent
    return (root / DEFAULT_CLOUD_RESEARCH_DB_NAME).resolve()


def configure_cloud_research_database(*, sandbox_dir: Path | None = None) -> Path:
    """Point Scout memory at a dedicated cloud research database file."""
    db_path = resolve_cloud_research_db_path(sandbox_dir=sandbox_dir)
    os.environ[RESEARCH_DB_PATH_ENV] = str(db_path)
    import memory_store as ms

    ms._DB_INITIALIZED = False
    db_path.parent.mkdir(parents=True, exist_ok=True)
    return db_path


def cloud_research_isolation_summary(db_path: Path | None = None) -> dict[str, Any]:
    return {
        "databasePath": str(db_path or resolve_cloud_research_db_path()),
        "writeTables": sorted(CLOUD_RESEARCH_WRITE_TABLES),
        "previewReadTables": sorted(CLOUD_RESEARCH_PREVIEW_READ_TABLES),
        "protectedTables": sorted(CLOUD_RESEARCH_PROTECTED_TABLES),
        "previewBacktestPersistsRuns": False,
        "usesSeparateReportRegistry": True,
    }


def validate_cloud_worker_environment() -> dict[str, Any]:
    """Validate GitHub Actions cloud worker secrets before running research jobs."""
    errors: list[str] = []

    worker_secret = os.environ.get("SCOUT_RESEARCH_WORKER_SECRET", "").strip()
    if not worker_secret:
        errors.append(
            "Missing required GitHub secret SCOUT_RESEARCH_WORKER_SECRET "
            "(export as env SCOUT_RESEARCH_WORKER_SECRET)."
        )

    enabled_value = os.environ.get("SCOUT_CLOUD_RESEARCH_ENABLED", "").strip()
    if not enabled_value:
        errors.append(
            "Missing required GitHub secret SCOUT_CLOUD_RESEARCH_ENABLED "
            "(set to true to allow scheduled cloud research runs)."
        )
    elif not _truthy(enabled_value):
        errors.append(
            "SCOUT_CLOUD_RESEARCH_ENABLED must be true; cloud research worker is disabled."
        )

    return {
        "ok": not errors,
        "errors": errors,
        "githubActions": os.environ.get("GITHUB_ACTIONS", "").strip().lower() == "true",
    }


def format_cloud_worker_validation_errors(result: dict[str, Any]) -> str:
    lines = ["Scout cloud research worker configuration error"]
    for error in result.get("errors") or []:
        lines.append(f"- {error}")
    lines.append(
        "Configure repository secrets before running the Scout Cloud Research Worker workflow."
    )
    return "\n".join(lines)
