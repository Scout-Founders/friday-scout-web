#!/usr/bin/env python3
"""Add nullable pattern build_id and an empty pattern backup table.

Dry-run is the default. Nothing is written unless --apply is passed.
Existing pattern rows stay unchanged and unstamped. No manifest row is inserted.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

from migrate_derived_builds import (
    CANONICAL_DB,
    EXPECTED_COUNTS,
    MigrationAbort,
    assert_live_preflight,
    open_readonly,
    preflight_archive,
)


PATTERN_VALUE_COLUMNS = (
    "pattern_id",
    "pattern_signature",
    "sample_size",
    "win_rate",
    "loss_rate",
    "avg_1d",
    "avg_3d",
    "avg_5d",
    "avg_10d",
    "avg_20d",
    "expectancy_score",
    "confidence_score",
    "created_at_utc",
)
PATTERN_COLUMNS = PATTERN_VALUE_COLUMNS + ("build_id",)
BACKUP_TABLE = "pattern_intelligence_backup"
UNTOUCHED_TABLES = (
    "gate_alpha_metrics",
    "gate_intelligence_metrics",
    "regime_snapshots",
    "derived_builds",
    "scan_results",
    "observation_provenance",
)

CREATE_BACKUP_SQL = """
CREATE TABLE pattern_intelligence_backup (
    pattern_id TEXT PRIMARY KEY,
    pattern_signature TEXT NOT NULL,
    sample_size INTEGER NOT NULL,
    win_rate REAL NOT NULL,
    loss_rate REAL NOT NULL,
    avg_1d REAL,
    avg_3d REAL,
    avg_5d REAL,
    avg_10d REAL,
    avg_20d REAL,
    expectancy_score REAL NOT NULL,
    confidence_score REAL NOT NULL,
    created_at_utc TEXT NOT NULL,
    build_id TEXT
)
""".strip()


def schema_status(conn: sqlite3.Connection) -> str:
    """Return missing, already_applied, or conflict. Performs no writes."""
    if not _table_exists(conn, "pattern_intelligence"):
        return "conflict"
    pattern_columns = _table_columns(conn, "pattern_intelligence")
    backup_exists = _table_exists(conn, BACKUP_TABLE)
    has_build_id = "build_id" in pattern_columns
    if not has_build_id and not backup_exists:
        return "missing"
    if not has_build_id or not backup_exists:
        return "conflict"
    if pattern_columns != list(PATTERN_COLUMNS):
        return "conflict"
    if not _nullable_text(conn, "pattern_intelligence", "build_id"):
        return "conflict"
    if _table_columns(conn, BACKUP_TABLE) != list(PATTERN_COLUMNS):
        return "conflict"
    return "already_applied"


def apply_schema(conn: sqlite3.Connection) -> str:
    """Add nullable build_id and an empty backup table in one transaction."""
    status = schema_status(conn)
    if status == "conflict":
        raise MigrationAbort("pattern build schema conflicts with the approved infrastructure")
    if status == "already_applied":
        return "already_applied"
    before_rows = _pattern_values(conn)
    before_schemas = {name: _master_sql(conn, name) for name in UNTOUCHED_TABLES}
    before_manifest_rows = _manifest_count(conn)
    conn.execute("BEGIN IMMEDIATE")
    try:
        if schema_status(conn) != "missing":
            raise MigrationAbort("pattern schema changed before infrastructure migration")
        conn.execute("ALTER TABLE pattern_intelligence ADD COLUMN build_id TEXT")
        conn.execute(CREATE_BACKUP_SQL)
        if schema_status(conn) != "already_applied":
            raise MigrationAbort("pattern infrastructure schema does not match after migration")
        if _pattern_values(conn) != before_rows:
            raise MigrationAbort("pattern row values changed")
        stamped = int(
            conn.execute(
                "SELECT COUNT(*) FROM pattern_intelligence WHERE build_id IS NOT NULL"
            ).fetchone()[0]
        )
        if stamped:
            raise MigrationAbort("legacy pattern rows were stamped")
        backup_rows = int(conn.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0])
        if backup_rows != 0:
            raise MigrationAbort("pattern backup was not empty after creation")
        if _manifest_count(conn) != before_manifest_rows:
            raise MigrationAbort("derived_builds changed during pattern infrastructure migration")
        for name, previous in before_schemas.items():
            if _master_sql(conn, name) != previous:
                raise MigrationAbort(f"{name} schema changed during pattern infrastructure migration")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return "applied"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Add pattern build infrastructure to scout_memory.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the pattern column and backup table. Without this flag the script only reports.",
    )
    args = parser.parse_args(argv)
    try:
        preflight_archive()
        if not CANONICAL_DB.is_file():
            raise MigrationAbort("scout_memory.db is missing")
        read_conn = open_readonly(CANONICAL_DB)
        try:
            assert_live_preflight(read_conn)
            _assert_pattern_preflight(read_conn)
            status = schema_status(read_conn)
        finally:
            read_conn.close()

        mode = "apply" if args.apply else "dry-run"
        print(f"mode={mode}")
        print(f"schema_status={status}")
        print("planned_manifest_rows=0")
        print("planned_backup_rows=0")
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
            _assert_pattern_preflight(conn)
            outcome = apply_schema(conn)
            assert_live_preflight(conn)
            _assert_legacy_unstamped(conn)
            integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
            if integrity != ["ok"]:
                raise MigrationAbort(f"integrity_check {integrity}")
        finally:
            conn.close()
        print(f"result={outcome}")
        print("pattern_build_id_null=36")
        print("pattern_backup_rows=0")
        print("derived_builds_rows=0")
        print(f"integrity_check={integrity[0]}")
        return 0
    except MigrationAbort as exc:
        print("result=aborted", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2


def _assert_pattern_preflight(conn: sqlite3.Connection) -> None:
    count = int(conn.execute("SELECT COUNT(*) FROM pattern_intelligence").fetchone()[0])
    if count != EXPECTED_COUNTS["pattern_intelligence"]:
        raise MigrationAbort(f"pattern_intelligence count {count} != 36")
    status = schema_status(conn)
    if status == "conflict":
        raise MigrationAbort("pattern build schema conflicts with the approved infrastructure")


def _assert_legacy_unstamped(conn: sqlite3.Connection) -> None:
    count = int(conn.execute("SELECT COUNT(*) FROM pattern_intelligence").fetchone()[0])
    stamped = int(
        conn.execute(
            "SELECT COUNT(*) FROM pattern_intelligence WHERE build_id IS NOT NULL"
        ).fetchone()[0]
    )
    backup_rows = int(conn.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0])
    manifest_rows = _manifest_count(conn)
    if count != 36 or stamped != 0 or backup_rows != 0 or manifest_rows != 0:
        raise MigrationAbort(
            f"legacy pattern state count={count} stamped={stamped} "
            f"backup={backup_rows} manifests={manifest_rows}"
        )


def _pattern_values(conn: sqlite3.Connection) -> list[tuple[Any, ...]]:
    listed = ", ".join(PATTERN_VALUE_COLUMNS)
    return [
        tuple(row)
        for row in conn.execute(
            f"SELECT {listed} FROM pattern_intelligence ORDER BY pattern_id ASC"
        )
    ]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _nullable_text(conn: sqlite3.Connection, table: str, column: str) -> bool:
    for row in conn.execute(f"PRAGMA table_info({table})"):
        if row[1] == column:
            return str(row[2]).upper() == "TEXT" and int(row[3]) == 0
    return False


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def _master_sql(conn: sqlite3.Connection, table: str) -> Optional[str]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table,),
    ).fetchone()
    if row is None:
        return None
    return row[0]


def _manifest_count(conn: sqlite3.Connection) -> int:
    if not _table_exists(conn, "derived_builds"):
        return 0
    return int(conn.execute("SELECT COUNT(*) FROM derived_builds").fetchone()[0])


if __name__ == "__main__":
    sys.exit(main())
