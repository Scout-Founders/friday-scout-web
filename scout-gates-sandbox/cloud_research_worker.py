#!/usr/bin/env python3
"""Cloud research worker environment validation for GitHub Actions."""

from __future__ import annotations

import os
from typing import Any


REQUIRED_SECRET_ENV_VARS = (
    "SCOUT_RESEARCH_WORKER_SECRET",
    "SCOUT_CLOUD_RESEARCH_ENABLED",
)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


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
