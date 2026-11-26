#!/usr/bin/env python3
"""Schema for persistent scoring decompositions.

Table ``scoring_decompositions`` is the current derived generation.
Table ``scoring_decompositions_backup`` holds the immediately previous
generation. Neither table is an evidence identity. Rows point at
``observation_provenance.observation_uid`` and do not copy market inputs
or replace ``scan_results.scout_score``.

``derived_builds.artifact_type`` gains ``scoring_decompositions``. The
original manifest migration stays unchanged so an already-applied live
database does not look like a conflict. This script widens the check
only when it is explicitly applied to a connection.

Dry-run is the default. ``--apply`` refuses the canonical research
database unless ``--allow-canonical`` is also passed. Phase E2 does not
pass that flag.

Schema version 1 is this unapplied contract, including the identity
triggers and the fingerprint columns. A scoring table that lacks either
protection is a conflict. It is not treated as already migrated.

The triggers reject a row unless ``observation_provenance`` contains that
exact observation_uid and scan_result_id. They run when foreign keys are
off. This migration does not alter observation_provenance.

``scoring_input_fingerprint`` is the hash of the payloads the decomposer
read. ``source_provenance_fingerprint`` is the hash of the source metadata
copied onto the row and not used as a scoring input, currently
``scan_results.engine_version``. The shared manifest schema is not widened
for these hashes. Readers take them from the scoring rows.
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Optional

from migrate_derived_builds import (
    CANONICAL_DB,
    CREATE_INDEX_SQL,
    CREATE_TABLE_SQL,
    INDEX_NAME,
    TABLE_NAME,
    MigrationAbort,
    widened_manifest_sql,
)


ARTIFACT_TYPE = "scoring_decompositions"
LIVE_TABLE = "scoring_decompositions"
BACKUP_TABLE = "scoring_decompositions_backup"
CLASS_INDEX = "idx_scoring_decompositions_class"

COLUMNS = (
    "observation_uid",
    "scan_result_id",
    "methodology_id",
    "historical_methodology_id",
    "source_snapshot_sha256",
    "deployment_verification",
    "verified_deployment_id",
    "historical_compatibility",
    "decomposer_version",
    "schema_version",
    "sandbox_engine_version",
    "reconstruction_class",
    "formula_confirmation",
    "captured_scout_score",
    "unclamped_score",
    "clamped_score",
    "reconstruction_error",
    "direction",
    "direction_status",
    "direction_argument",
    "direction_argument_origin",
    "contributions_json",
    "penalty_candidates_json",
    "direction_candidates_json",
    "input_completeness_json",
    "warnings_json",
    "disagreements_json",
    "decomposition_error",
    "build_id",
    "eligible_population_hash",
    "scoring_input_fingerprint",
    "source_provenance_fingerprint",
)

_COLUMN_SQL = """
    observation_uid TEXT NOT NULL,
    scan_result_id INTEGER NOT NULL,
    methodology_id TEXT NOT NULL,
    historical_methodology_id TEXT,
    source_snapshot_sha256 TEXT NOT NULL,
    deployment_verification TEXT NOT NULL CHECK (
        deployment_verification IN ('unverified', 'verified')
    ),
    verified_deployment_id TEXT,
    historical_compatibility TEXT NOT NULL,
    decomposer_version TEXT NOT NULL,
    schema_version INTEGER NOT NULL CHECK (schema_version = 1),
    sandbox_engine_version TEXT,
    reconstruction_class TEXT NOT NULL,
    formula_confirmation TEXT NOT NULL,
    captured_scout_score INTEGER,
    unclamped_score INTEGER,
    clamped_score INTEGER,
    reconstruction_error INTEGER,
    direction TEXT,
    direction_status TEXT NOT NULL,
    direction_argument TEXT,
    direction_argument_origin TEXT,
    contributions_json TEXT NOT NULL,
    penalty_candidates_json TEXT,
    direction_candidates_json TEXT NOT NULL,
    input_completeness_json TEXT NOT NULL,
    warnings_json TEXT NOT NULL,
    disagreements_json TEXT NOT NULL,
    decomposition_error TEXT,
    build_id TEXT NOT NULL,
    eligible_population_hash TEXT NOT NULL,
    scoring_input_fingerprint TEXT NOT NULL CHECK (
        length(scoring_input_fingerprint) = 64
    ),
    source_provenance_fingerprint TEXT NOT NULL CHECK (
        length(source_provenance_fingerprint) = 64
    ),
    PRIMARY KEY (observation_uid, methodology_id),
    FOREIGN KEY (observation_uid)
        REFERENCES observation_provenance(observation_uid)
        ON DELETE RESTRICT,
    FOREIGN KEY (scan_result_id)
        REFERENCES scan_results(id)
        ON DELETE RESTRICT,
    FOREIGN KEY (build_id)
        REFERENCES derived_builds(build_id)
        ON DELETE RESTRICT,
    CHECK (
        reconstruction_class != 'C_ambiguous'
        OR (
            unclamped_score IS NULL
            AND clamped_score IS NULL
            AND reconstruction_error IS NULL
            AND penalty_candidates_json IS NOT NULL
        )
    ),
    CHECK (
        reconstruction_class NOT IN (
            'A_evidence_determined',
            'B_algebraically_inferred'
        )
        OR (
            unclamped_score IS NOT NULL
            AND clamped_score IS NOT NULL
            AND reconstruction_error IS NOT NULL
        )
    )
""".strip()

CREATE_LIVE_SQL = f"CREATE TABLE {LIVE_TABLE} (\n{_COLUMN_SQL}\n)"
CREATE_BACKUP_SQL = f"CREATE TABLE {BACKUP_TABLE} (\n{_COLUMN_SQL}\n)"
CREATE_CLASS_INDEX_SQL = (
    f"CREATE INDEX {CLASS_INDEX} ON {LIVE_TABLE}(reconstruction_class)"
)

WIDENED_CREATE_TABLE_SQL = widened_manifest_sql()
IDENTITY_TRIGGER_MESSAGE = "scoring observation_uid does not belong to scan_result_id"


def _identity_trigger(table: str, event: str) -> tuple[str, str]:
    name = f"{table}_identity_{event.lower()}"
    sql = f"""
CREATE TRIGGER {name}
BEFORE {event} ON {table}
FOR EACH ROW
WHEN NOT EXISTS (
    SELECT 1 FROM observation_provenance
    WHERE observation_uid = NEW.observation_uid
      AND scan_result_id = NEW.scan_result_id
)
BEGIN
    SELECT RAISE(ABORT, '{IDENTITY_TRIGGER_MESSAGE}');
END
""".strip()
    return name, sql


IDENTITY_TRIGGERS = tuple(
    _identity_trigger(table, event)
    for table in (LIVE_TABLE, BACKUP_TABLE)
    for event in ("INSERT", "UPDATE")
)


def schema_status(conn: sqlite3.Connection) -> str:
    """Return missing, already_applied, or conflict. Performs no writes."""
    manifest = _master_sql(conn, "table", TABLE_NAME)
    live = _master_sql(conn, "table", LIVE_TABLE)
    backup = _master_sql(conn, "table", BACKUP_TABLE)
    index = _master_sql(conn, "index", CLASS_INDEX)
    trigger_sql = {
        name: _master_sql(conn, "trigger", name) for name, _expected in IDENTITY_TRIGGERS
    }
    widened = manifest is not None and ARTIFACT_TYPE in manifest
    tables_present = live is not None and backup is not None and index is not None
    triggers_present = all(sql is not None for sql in trigger_sql.values())
    any_scoring_object = (
        live is not None
        or backup is not None
        or index is not None
        or any(sql is not None for sql in trigger_sql.values())
    )
    if manifest is None:
        return "conflict"
    if not widened and not any_scoring_object:
        return "missing"
    if not widened or not tables_present or not triggers_present:
        return "conflict"
    if _ddl_body(manifest) != _ddl_body(WIDENED_CREATE_TABLE_SQL):
        return "conflict"
    if _normalize(live) != _normalize(CREATE_LIVE_SQL):
        return "conflict"
    if _normalize(backup) != _normalize(CREATE_BACKUP_SQL):
        return "conflict"
    if _table_columns(conn, LIVE_TABLE) != list(COLUMNS):
        return "conflict"
    if _table_columns(conn, BACKUP_TABLE) != list(COLUMNS):
        return "conflict"
    for name, expected in IDENTITY_TRIGGERS:
        if _normalize(trigger_sql[name]) != _normalize(expected):
            return "conflict"
    return "already_applied"


def apply_schema(conn: sqlite3.Connection) -> str:
    """Widen the manifest check and create empty scoring tables.

    Returns already_applied without writing when the schema matches.
    Existing manifest rows are copied. Scoring tables start empty.
    """
    status = schema_status(conn)
    if status == "conflict":
        raise MigrationAbort("scoring decomposition schema conflicts with the approved DDL")
    if status == "already_applied":
        return "already_applied"
    _end_open_transaction(conn)
    manifest_rows = [
        tuple(row) for row in conn.execute(
            """
            SELECT build_id, artifact_type, classifier_version,
                   eligible_population_count, eligible_population_hash,
                   builder_version, status, started_at, built_at,
                   artifact_row_count, error_text
            FROM derived_builds
            ORDER BY build_id
            """
        )
    ]
    conn.execute("BEGIN IMMEDIATE")
    try:
        if schema_status(conn) != "missing":
            raise MigrationAbort("scoring schema changed before creation")
        _widen_manifest(conn, manifest_rows)
        conn.execute(CREATE_LIVE_SQL)
        conn.execute(CREATE_BACKUP_SQL)
        conn.execute(CREATE_CLASS_INDEX_SQL)
        for _name, trigger_sql in IDENTITY_TRIGGERS:
            conn.execute(trigger_sql)
        if schema_status(conn) != "already_applied":
            raise MigrationAbort("scoring schema does not match after migration")
        for table in (LIVE_TABLE, BACKUP_TABLE):
            count = int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
            if count != 0:
                raise MigrationAbort(f"{table} was not empty immediately after creation")
        copied = [
            tuple(row) for row in conn.execute(
                """
                SELECT build_id, artifact_type, classifier_version,
                       eligible_population_count, eligible_population_hash,
                       builder_version, status, started_at, built_at,
                       artifact_row_count, error_text
                FROM derived_builds
                ORDER BY build_id
                """
            )
        ]
        if copied != manifest_rows:
            raise MigrationAbort("derived_builds rows changed while widening the artifact check")
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    return "applied"


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="Add scoring decomposition tables. Does not publish rows."
    )
    parser.add_argument("--database", type=str, default=str(CANONICAL_DB))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument(
        "--allow-canonical",
        action="store_true",
        help="Required before --apply may write scout_memory.db.",
    )
    args = parser.parse_args(argv)
    target = str(Path(args.database).resolve())
    canonical = str(CANONICAL_DB.resolve())
    try:
        if args.apply and target == canonical and not args.allow_canonical:
            raise MigrationAbort(
                "refusing to migrate the canonical research database without --allow-canonical"
            )
        if not args.apply:
            conn = sqlite3.connect(f"file:{target}?mode=ro", uri=True)
        else:
            conn = sqlite3.connect(target, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            status = schema_status(conn)
            print(f"mode={'apply' if args.apply else 'dry-run'}")
            print(f"schema_status={status}")
            if status == "already_applied" or not args.apply:
                print("result=" + ("already_applied" if status == "already_applied" else "dry_run_passed"))
                print("write_skipped=true" if not args.apply or status == "already_applied" else "write_skipped=false")
                return 0
            outcome = apply_schema(conn)
        finally:
            conn.close()
        print(f"result={outcome}")
        return 0
    except MigrationAbort as exc:
        print("result=aborted", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2


def _widen_manifest(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    current = _master_sql(conn, "table", TABLE_NAME)
    if current is not None and ARTIFACT_TYPE in current:
        return
    source = WIDENED_CREATE_TABLE_SQL.strip()
    prefix = "CREATE TABLE derived_builds ("
    if not source.startswith(prefix):
        raise MigrationAbort("widened manifest DDL does not start from derived_builds")
    conn.execute("DROP INDEX IF EXISTS " + INDEX_NAME)
    conn.execute("CREATE TABLE derived_builds_scoring_widen (" + source[len(prefix) :])
    conn.executemany(
        """
        INSERT INTO derived_builds_scoring_widen (
            build_id, artifact_type, classifier_version,
            eligible_population_count, eligible_population_hash,
            builder_version, status, started_at, built_at,
            artifact_row_count, error_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows,
    )
    conn.execute(f"DROP TABLE {TABLE_NAME}")
    conn.execute(
        f"ALTER TABLE derived_builds_scoring_widen RENAME TO {TABLE_NAME}"
    )
    conn.execute(CREATE_INDEX_SQL)
    stored = _master_sql(conn, "table", TABLE_NAME)
    if stored is None or _ddl_body(stored) != _ddl_body(WIDENED_CREATE_TABLE_SQL):
        raise MigrationAbort("widened derived_builds DDL does not match")


def _normalize(sql: Optional[str]) -> str:
    return " ".join((sql or "").split())


def _ddl_body(sql: Optional[str]) -> str:
    text = _normalize(sql)
    start = text.find("(")
    return text[start:]


def _master_sql(conn: sqlite3.Connection, kind: str, name: str) -> Optional[str]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type = ? AND name = ?",
        (kind, name),
    ).fetchone()
    if row is None:
        return None
    return row[0] if not isinstance(row, sqlite3.Row) else row["sql"]


def _table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]


def _end_open_transaction(conn: sqlite3.Connection) -> None:
    try:
        conn.commit()
    except sqlite3.OperationalError:
        pass


if __name__ == "__main__":
    sys.exit(main())
