#!/usr/bin/env python3
"""Manifest rows for future Horizon derived builds.

This module only records build identity and status. It does not open a
database, publish artifact rows, or rebuild intelligence.
"""

from __future__ import annotations

import re
import secrets
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from observation_evidence import PopulationStamp


class DerivedBuildError(Exception):
    """A manifest write violated the derived-build state machine."""


ARTIFACT_SHORT_NAMES = {
    "pattern_intelligence": "pattern",
    "gate_alpha_metrics": "gate-alpha",
    "gate_intelligence_metrics": "gate-intelligence",
}

_BUILDER_VERSION_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_COLUMNS = (
    "build_id",
    "artifact_type",
    "classifier_version",
    "eligible_population_count",
    "eligible_population_hash",
    "builder_version",
    "status",
    "started_at",
    "built_at",
    "artifact_row_count",
    "error_text",
)


def generate_build_id(
    artifact_type: str,
    builder_version: str,
    *,
    now: Optional[datetime] = None,
    token_hex: Optional[str] = None,
) -> str:
    """Return a sortable TEXT key that can repeat over one evidence population."""
    short_name = _short_name(artifact_type)
    version = _builder_version(builder_version)
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        raise DerivedBuildError("build_id timestamp must be timezone-aware")
    stamp = moment.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    token = token_hex if token_hex is not None else secrets.token_hex(4)
    if not re.fullmatch(r"[0-9a-f]{8}", token):
        raise DerivedBuildError("build_id token must be 8 lowercase hex characters")
    return f"{short_name}-{version}-{stamp}-{token}"


def start_build(
    conn: sqlite3.Connection,
    *,
    artifact_type: str,
    builder_version: str,
    stamp: PopulationStamp,
) -> dict[str, Any]:
    """Insert one STARTED manifest row from an observation_evidence stamp."""
    if not isinstance(stamp, PopulationStamp):
        raise DerivedBuildError(
            "population identity must be an observation_evidence.PopulationStamp"
        )
    if not stamp.classifier_version or not stamp.eligible_population_hash:
        raise DerivedBuildError("population stamp is missing classifier version or hash")
    if stamp.eligible_population_count < 0:
        raise DerivedBuildError("population stamp count cannot be negative")
    if not stamp.generated_at:
        raise DerivedBuildError("population stamp is missing generated_at")
    _short_name(artifact_type)
    _builder_version(builder_version)
    build_id = generate_build_id(artifact_type, builder_version)
    try:
        conn.execute(
            """
            INSERT INTO derived_builds (
                build_id, artifact_type, classifier_version,
                eligible_population_count, eligible_population_hash,
                builder_version, status, started_at,
                built_at, artifact_row_count, error_text
            ) VALUES (?, ?, ?, ?, ?, ?, 'STARTED', ?, NULL, NULL, NULL)
            """,
            (
                build_id,
                artifact_type,
                stamp.classifier_version,
                stamp.eligible_population_count,
                stamp.eligible_population_hash,
                builder_version,
                stamp.generated_at,
            ),
        )
    except sqlite3.IntegrityError as exc:
        raise DerivedBuildError(
            f"build_id {build_id} collided; refusing to overwrite an existing manifest"
        ) from exc
    stored = get_build(conn, build_id)
    if stored is None:
        raise DerivedBuildError(f"started build {build_id} could not be read back")
    return stored


def complete_build(
    conn: sqlite3.Connection,
    build_id: str,
    *,
    built_at: str,
    artifact_row_count: int,
) -> dict[str, Any]:
    """Move STARTED to COMPLETED. Every other status is rejected."""
    if not str(built_at or "").strip():
        raise DerivedBuildError("COMPLETED requires built_at")
    if isinstance(artifact_row_count, bool) or not isinstance(artifact_row_count, int):
        raise DerivedBuildError("artifact_row_count must be an integer")
    if artifact_row_count < 0:
        raise DerivedBuildError("artifact_row_count cannot be negative")
    _transition(
        conn,
        build_id,
        from_status="STARTED",
        to_status="COMPLETED",
        built_at=str(built_at).strip(),
        artifact_row_count=artifact_row_count,
        error_text=None,
    )
    return _required(conn, build_id)


def fail_build(conn: sqlite3.Connection, build_id: str, *, error_text: str) -> dict[str, Any]:
    """Move STARTED to FAILED. The error text is required."""
    text = str(error_text or "").strip()
    if not text:
        raise DerivedBuildError("FAILED requires non-empty error_text")
    _transition(
        conn,
        build_id,
        from_status="STARTED",
        to_status="FAILED",
        built_at=None,
        artifact_row_count=None,
        error_text=text,
    )
    return _required(conn, build_id)


def mark_rolled_back(conn: sqlite3.Connection, build_id: str) -> dict[str, Any]:
    """Move COMPLETED to ROLLED_BACK. STARTED and FAILED cannot roll back."""
    current = _required(conn, build_id)
    _transition(
        conn,
        build_id,
        from_status="COMPLETED",
        to_status="ROLLED_BACK",
        built_at=current["built_at"],
        artifact_row_count=current["artifact_row_count"],
        error_text=current["error_text"],
    )
    return _required(conn, build_id)


def get_build(conn: sqlite3.Connection, build_id: str) -> Optional[dict[str, Any]]:
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM derived_builds WHERE build_id = ?",
        (build_id,),
    ).fetchone()
    if row is None:
        return None
    return {column: row[column] for column in _COLUMNS}


def latest_completed_build(
    conn: sqlite3.Connection,
    artifact_type: str,
) -> Optional[dict[str, Any]]:
    _short_name(artifact_type)
    row = conn.execute(
        f"""
        SELECT {', '.join(_COLUMNS)}
        FROM derived_builds
        WHERE artifact_type = ? AND status = 'COMPLETED'
        ORDER BY built_at DESC, started_at DESC, build_id DESC
        LIMIT 1
        """,
        (artifact_type,),
    ).fetchone()
    if row is None:
        return None
    return {column: row[column] for column in _COLUMNS}


def _short_name(artifact_type: str) -> str:
    try:
        return ARTIFACT_SHORT_NAMES[artifact_type]
    except KeyError as exc:
        raise DerivedBuildError(f"unknown artifact_type {artifact_type!r}") from exc


def _builder_version(builder_version: str) -> str:
    if not isinstance(builder_version, str) or _BUILDER_VERSION_RE.fullmatch(builder_version) is None:
        raise DerivedBuildError("builder_version must be a short version token")
    return builder_version


def _required(conn: sqlite3.Connection, build_id: str) -> dict[str, Any]:
    stored = get_build(conn, build_id)
    if stored is None:
        raise DerivedBuildError(f"build {build_id} does not exist")
    return stored


def _transition(
    conn: sqlite3.Connection,
    build_id: str,
    *,
    from_status: str,
    to_status: str,
    built_at: Optional[str],
    artifact_row_count: Optional[int],
    error_text: Optional[str],
) -> None:
    current = _required(conn, build_id)
    if current["status"] != from_status:
        raise DerivedBuildError(
            f"cannot transition {current['status']} to {to_status}"
        )
    cursor = conn.execute(
        """
        UPDATE derived_builds
        SET status = ?, built_at = ?, artifact_row_count = ?, error_text = ?
        WHERE build_id = ? AND status = ?
        """,
        (to_status, built_at, artifact_row_count, error_text, build_id, from_status),
    )
    if cursor.rowcount != 1:
        raise DerivedBuildError(
            f"refusing to transition {build_id}; status is no longer {from_status}"
        )
