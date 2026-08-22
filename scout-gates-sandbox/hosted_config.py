#!/usr/bin/env python3
"""Hosted deployment configuration for Scout gate sandbox."""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import Any, Optional

SERVICE_NAME = "scout-gates-sandbox"

HOSTED_MODE_ENV = "SCOUT_HOSTED_MODE"
MAINTENANCE_MODE_ENV = "SCOUT_MAINTENANCE_MODE"
SQLITE_BUSY_TIMEOUT_ENV = "SCOUT_SQLITE_BUSY_TIMEOUT_MS"

DEFAULT_SQLITE_BUSY_TIMEOUT_MS = 5000

HOSTED_MAINTENANCE_POST_PATHS = frozenset(
    {
        "/api/memory/create-outcome-test-record",
        "/api/control/backfill",
        "/api/control/gate-alpha",
        "/api/control/gate-alpha/test-record",
        "/api/control/regime-intelligence",
        "/api/research-daily-reports/ingest",
    }
)

HOSTED_MAINTENANCE_GET_PATHS = frozenset(
    {
        "/api/control/patterns",
    }
)

HOSTED_MAINTENANCE_BLOCKED_MESSAGE = (
    "This maintenance endpoint is disabled in hosted mode. "
    "Set SCOUT_MAINTENANCE_MODE=1 temporarily to allow maintenance operations."
)


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def is_hosted_mode() -> bool:
    return _truthy(os.environ.get(HOSTED_MODE_ENV, ""))


def maintenance_allowed() -> bool:
    if not is_hosted_mode():
        return True
    return _truthy(os.environ.get(MAINTENANCE_MODE_ENV, ""))


def hosted_maintenance_blocked(path: str, method: str) -> bool:
    if maintenance_allowed():
        return False
    normalized = path.split("?", 1)[0]
    if method.upper() == "POST":
        return normalized in HOSTED_MAINTENANCE_POST_PATHS
    if method.upper() == "GET":
        return normalized in HOSTED_MAINTENANCE_GET_PATHS
    return False


def firestore_direct_ingest_blocked() -> bool:
    """Block automatic Firestore fetch in hosted mode (artifact ingest remains available)."""
    return is_hosted_mode() and not maintenance_allowed()


def sqlite_busy_timeout_ms() -> int:
    raw = os.environ.get(SQLITE_BUSY_TIMEOUT_ENV, "").strip()
    if not raw:
        return DEFAULT_SQLITE_BUSY_TIMEOUT_MS
    try:
        parsed = int(raw)
    except ValueError:
        return DEFAULT_SQLITE_BUSY_TIMEOUT_MS
    return max(parsed, 0)


def apply_sqlite_connection_pragmas(conn: sqlite3.Connection) -> None:
    """Apply WAL + busy_timeout for low-concurrency multi-thread SQLite usage."""
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(f"PRAGMA busy_timeout={sqlite_busy_timeout_ms()}")


def hosted_maintenance_response() -> dict[str, Any]:
    return {
        "ok": False,
        "message": HOSTED_MAINTENANCE_BLOCKED_MESSAGE,
        "hostedMode": True,
        "maintenanceAllowed": False,
    }


def build_health_status(
    *,
    db_path: Optional[Path] = None,
    reports_dir: Optional[Path] = None,
) -> dict[str, Any]:
    from memory_store import get_db_path
    from reporting.config import default_exports_dir

    resolved_db = db_path or get_db_path()
    resolved_reports = reports_dir or default_exports_dir()

    db_parent = resolved_db.parent
    database_available = db_parent.exists() and os.access(db_parent, os.W_OK)
    if resolved_db.exists():
        database_available = database_available and os.access(resolved_db, os.W_OK)

    reports_directory_available = (
        resolved_reports.exists() and os.access(resolved_reports, os.W_OK)
    )

    return {
        "ok": database_available and reports_directory_available,
        "service": SERVICE_NAME,
        "databaseAvailable": database_available,
        "reportsDirectoryAvailable": reports_directory_available,
    }


def log_startup_config(host: str, port: int) -> None:
    from memory_store import get_db_path
    from reporting.config import default_exports_dir

    mode_label = "hosted" if is_hosted_mode() else "local"
    maintenance_label = "enabled" if maintenance_allowed() else "disabled"
    print("Scout gate sandbox dashboard")
    print(f"Mode: {mode_label}")
    if is_hosted_mode():
        print(f"Maintenance endpoints: {maintenance_label}")
    print(f"Bind address: {host}:{port}")
    print(f"Database: {get_db_path()}")
    print(f"Reports directory: {default_exports_dir()}")
    print("Press Ctrl+C to stop.")
