#!/usr/bin/env python3
"""Create the empty derived_builds manifest table in scout_memory.db.

Dry-run is the default. Nothing is written unless --apply is passed.
The script adds one table and one index. It does not alter artifact tables
and does not insert manifest rows for existing intelligence.
"""

from __future__ import annotations

import argparse
import hashlib
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from observation_evidence import assert_research_population, population_stamp


SANDBOX_DIR = Path(__file__).resolve().parent
CANONICAL_DB = SANDBOX_DIR / "scout_memory.db"
ARCHIVE_DB = SANDBOX_DIR / "data-archives" / "scout_memory_pre_horizon_migration_2026-09-28.db"
ARCHIVE_SHA256 = "dbfb8fb4394e4af7419ac8544d004d431c5d1cb526ef3a7b5d18a806cd8de436"
EXPECTED_DIGEST = "e14857d1029dc0759b76b7a34d681f0a6f7ca053a1e6a889d44826698d2ed358"
EXPECTED_CLASSIFIER = "b0-2026-09-28"
EXPECTED_ELIGIBLE_COUNT = 227
EXPECTED_ELIGIBLE_HASH = "eaa0e0ec2c10ff7bd5ac99fa8d9ed5cdaed763579e0a7aa4bb24e84292763fc8"

EXPECTED_COUNTS = {
    "scan_results": 274,
    "observation_provenance": 274,
    "pattern_intelligence": 36,
    "gate_alpha_metrics": 350,
    "gate_intelligence_metrics": 14,
    "regime_snapshots": 243,
}
UNTOUCHED_TABLES = (
    "pattern_intelligence",
    "gate_alpha_metrics",
    "gate_intelligence_metrics",
    "regime_snapshots",
    "scan_results",
    "observation_provenance",
)

CREATE_TABLE_SQL = """
CREATE TABLE derived_builds (
    build_id TEXT PRIMARY KEY,
    artifact_type TEXT NOT NULL CHECK (
        artifact_type IN (
            'pattern_intelligence',
            'gate_alpha_metrics',
            'gate_intelligence_metrics'
        )
    ),
    classifier_version TEXT NOT NULL,
    eligible_population_count INTEGER NOT NULL,
    eligible_population_hash TEXT NOT NULL,
    builder_version TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('STARTED', 'COMPLETED', 'FAILED', 'ROLLED_BACK')
    ),
    started_at TEXT NOT NULL,
    built_at TEXT,
    artifact_row_count INTEGER,
    error_text TEXT
)
""".strip()

CREATE_INDEX_SQL = """
CREATE INDEX idx_derived_builds_artifact_status_started
ON derived_builds(artifact_type, status, started_at)
""".strip()

TABLE_NAME = "derived_builds"
INDEX_NAME = "idx_derived_builds_artifact_status_started"


class MigrationAbort(Exception):
    """Stop the migration without leaving a partial schema."""


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def digest_raw_result_json(conn: sqlite3.Connection) -> str:
    hasher = hashlib.sha256()
    for scan_id, raw in conn.execute(
        "SELECT id, raw_result_json FROM scan_results ORDER BY id ASC"
    ):
        hasher.update(b"id:")
        hasher.update(str(scan_id).encode("ascii"))
        hasher.update(b"\0")
        if raw is None:
            hasher.update(b"null")
        else:
            data = raw.encode("utf-8")
            hasher.update(b"text:")
            hasher.update(str(len(data)).encode("ascii"))
            hasher.update(b":")
            hasher.update(data)
        hasher.update(b"\n")
    return hasher.hexdigest()


def normalize_sql(sql: Optional[str]) -> str:
    return " ".join((sql or "").split())


def schema_status(conn: sqlite3.Connection) -> str:
    """Return missing, already_applied, or conflict. Performs no writes."""
    table_sql = _master_sql(conn, "table", TABLE_NAME)
    index_sql = _master_sql(conn, "index", INDEX_NAME)
    if table_sql is None and index_sql is None:
        return "missing"
    if table_sql is None or index_sql is None:
        return "conflict"
    if normalize_sql(table_sql) != normalize_sql(CREATE_TABLE_SQL):
        return "conflict"
    if normalize_sql(index_sql) != normalize_sql(CREATE_INDEX_SQL):
        return "conflict"
    return "already_applied"


def apply_schema(conn: sqlite3.Connection) -> str:
    """Create the empty manifest table inside one transaction.

    Returns already_applied without writing when the schema already matches.
    """
    status = schema_status(conn)
    if status == "conflict":
        raise MigrationAbort("derived_builds exists with a conflicting schema")
    if status == "already_applied":
        return "already_applied"
    before_schemas = {name: _master_sql(conn, "table", name) for name in UNTOUCHED_TABLES}
    conn.execute("BEGIN IMMEDIATE")
    try:
        if schema_status(conn) != "missing":
            raise MigrationAbort("derived_builds schema changed before creation")
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(CREATE_INDEX_SQL)
        if schema_status(conn) != "already_applied":
            raise MigrationAbort("created derived_builds schema does not match the approved DDL")
        count = int(conn.execute("SELECT COUNT(*) FROM derived_builds").fetchone()[0])
        if count != 0:
            raise MigrationAbort("derived_builds is not empty immediately after creation")
        for name, previous in before_schemas.items():
            if _master_sql(conn, "table", name) != previous:
                raise MigrationAbort(f"{name} schema changed during manifest creation")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return "applied"


def preflight_archive() -> None:
    if not ARCHIVE_DB.is_file():
        raise MigrationAbort(f"archive is missing: {ARCHIVE_DB.name}")
    actual = sha256_file(ARCHIVE_DB)
    if actual != ARCHIVE_SHA256:
        raise MigrationAbort(f"archive SHA-256 mismatch: {actual}")


def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def assert_live_preflight(conn: sqlite3.Connection) -> None:
    for name, expected in EXPECTED_COUNTS.items():
        actual = int(conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0])
        if actual != expected:
            raise MigrationAbort(f"{name} count {actual} != {expected}")
    digest = digest_raw_result_json(conn)
    if digest != EXPECTED_DIGEST:
        raise MigrationAbort(f"raw_result_json digest mismatch: {digest}")
    assert_research_population(conn)
    stamp = population_stamp(conn, generated_at="preflight")
    if stamp.classifier_version != EXPECTED_CLASSIFIER:
        raise MigrationAbort(f"classifier_version mismatch: {stamp.classifier_version}")
    if stamp.eligible_population_count != EXPECTED_ELIGIBLE_COUNT:
        raise MigrationAbort(f"eligible count mismatch: {stamp.eligible_population_count}")
    if stamp.eligible_population_hash != EXPECTED_ELIGIBLE_HASH:
        raise MigrationAbort(f"eligible hash mismatch: {stamp.eligible_population_hash}")
    status = schema_status(conn)
    if status == "conflict":
        raise MigrationAbort("derived_builds exists with a conflicting schema")


def _master_sql(conn: sqlite3.Connection, kind: str, name: str) -> Optional[str]:
    row = conn.execute(
        """
        SELECT sql FROM sqlite_master
        WHERE type = ? AND name = ?
        """,
        (kind, name),
    ).fetchone()
    if row is None:
        return None
    return row[0]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create empty derived_builds in scout_memory.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the manifest table. Without this flag the script only reports.",
    )
    args = parser.parse_args(argv)
    try:
        preflight_archive()
        if not CANONICAL_DB.is_file():
            raise MigrationAbort("scout_memory.db is missing")
        read_conn = open_readonly(CANONICAL_DB)
        try:
            assert_live_preflight(read_conn)
            status = schema_status(read_conn)
        finally:
            read_conn.close()

        mode = "apply" if args.apply else "dry-run"
        print(f"mode={mode}")
        print(f"archive_sha256={ARCHIVE_SHA256}")
        print(f"schema_status={status}")
        print("planned_manifest_rows=0")
        if status == "already_applied":
            print("result=already_applied")
            print("write_skipped=true")
            return 0
        if not args.apply:
            print("result=dry_run_passed")
            print("apply_not_executed=true")
            return 0

        conn = sqlite3.connect(CANONICAL_DB, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys = ON")
            assert_live_preflight(conn)
            outcome = apply_schema(conn)
            assert_live_preflight(conn)
            count = int(conn.execute("SELECT COUNT(*) FROM derived_builds").fetchone()[0])
            if outcome == "applied" and count != 0:
                raise MigrationAbort("derived_builds was not empty after apply")
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise MigrationAbort(f"integrity_check {integrity}")
        finally:
            conn.close()
        print(f"result={outcome}")
        print(f"derived_builds_rows={count}")
        print(f"integrity_check={integrity[0]}")
        return 0
    except MigrationAbort as exc:
        print("result=aborted", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
