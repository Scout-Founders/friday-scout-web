#!/usr/bin/env python3
"""Import a read-only research snapshot into the isolated cloud research database."""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Optional

from cloud_research_worker import DEFAULT_CLOUD_RESEARCH_DB_NAME, RESEARCH_DB_PATH_ENV
from export_research_snapshot import (
    OPTIONAL_EXPORT_TABLES,
    REQUIRED_EXPORT_TABLES,
    manifest_path_for,
    open_readonly_database,
    sha256_file,
    table_exists,
)


SANDBOX_DIR = Path(__file__).resolve().parent
DEFAULT_SNAPSHOT_DB = SANDBOX_DIR / "research_snapshot.db"
DEFAULT_TARGET_DB = SANDBOX_DIR / DEFAULT_CLOUD_RESEARCH_DB_NAME
LOCAL_DASHBOARD_DB_NAME = "scout_memory.db"

PRESERVED_RESEARCH_TABLES = (
    "research_jobs",
    "research_job_runs",
    "research_findings",
    "rule_candidates",
    "rule_validations",
)

SNAPSHOT_TABLES_DROP_ORDER = ("feature_vectors", "scan_results", "scan_runs")
SNAPSHOT_TABLES_CREATE_ORDER = REQUIRED_EXPORT_TABLES + OPTIONAL_EXPORT_TABLES


def resolve_target_db_path(explicit_path: Optional[Path] = None) -> Path:
    if explicit_path is not None:
        return explicit_path.expanduser().resolve()
    env_override = os.environ.get(RESEARCH_DB_PATH_ENV, "").strip()
    if env_override:
        return Path(env_override).expanduser().resolve()
    return DEFAULT_TARGET_DB.resolve()


def validate_target_path(target_db_path: Path, *, allow_nonstandard_target: bool) -> None:
    target_name = target_db_path.name
    if target_name == LOCAL_DASHBOARD_DB_NAME:
        raise ValueError(
            f"Refusing to import into local dashboard database: {target_db_path}"
        )
    if not target_name.endswith(DEFAULT_CLOUD_RESEARCH_DB_NAME) and not allow_nonstandard_target:
        raise ValueError(
            "Target database must end with "
            f"{DEFAULT_CLOUD_RESEARCH_DB_NAME} unless --allow-nonstandard-target is set."
        )


def load_manifest(manifest_file_path: Path) -> dict[str, Any]:
    if not manifest_file_path.exists():
        raise FileNotFoundError(f"Snapshot manifest not found: {manifest_file_path}")
    return json.loads(manifest_file_path.read_text(encoding="utf-8"))


def validate_manifest(manifest: dict[str, Any], snapshot_db_path: Path) -> None:
    exported = manifest.get("tables_exported") or []
    missing_required = [
        table for table in REQUIRED_EXPORT_TABLES if table not in exported
    ]
    if missing_required:
        raise ValueError(
            "Snapshot manifest is missing required exported table(s): "
            + ", ".join(missing_required)
        )

    expected_checksum = str(manifest.get("checksum_sha256") or "").strip()
    if not expected_checksum:
        raise ValueError("Snapshot manifest is missing checksum_sha256.")
    actual_checksum = sha256_file(snapshot_db_path)
    if expected_checksum != actual_checksum:
        raise ValueError(
            "Snapshot checksum mismatch: manifest checksum does not match snapshot database file."
        )


def validate_snapshot_database(snapshot_conn: sqlite3.Connection) -> list[str]:
    missing_required = [
        table for table in REQUIRED_EXPORT_TABLES if not table_exists(snapshot_conn, table)
    ]
    if missing_required:
        raise ValueError(
            "Snapshot database is missing required table(s): "
            + ", ".join(missing_required)
        )

    warnings: list[str] = []
    for table in OPTIONAL_EXPORT_TABLES:
        if not table_exists(snapshot_conn, table):
            warnings.append(f"Optional snapshot table not present and will be skipped: {table}")
    return warnings


def list_user_tables(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute(
        """
        SELECT name FROM sqlite_master
        WHERE type = 'table'
          AND name NOT LIKE 'sqlite_%'
        ORDER BY name ASC
        """
    ).fetchall()
    return [str(row[0]) for row in rows]


def count_rows(conn: sqlite3.Connection, table_name: str) -> int:
    if not table_exists(conn, table_name):
        return 0
    row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0] if row is not None else 0)


def tables_to_import(snapshot_conn: sqlite3.Connection, manifest: dict[str, Any]) -> list[str]:
    exported = list(manifest.get("tables_exported") or [])
    tables = [table for table in SNAPSHOT_TABLES_CREATE_ORDER if table in exported]
    tables = [table for table in tables if table_exists(snapshot_conn, table)]
    missing_required = [table for table in REQUIRED_EXPORT_TABLES if table not in tables]
    if missing_required:
        raise ValueError(
            "Snapshot database is missing required import table(s): "
            + ", ".join(missing_required)
        )
    return tables


def preserved_table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {table: count_rows(conn, table) for table in PRESERVED_RESEARCH_TABLES}


def ensure_target_database(target_db_path: Path) -> None:
    target_db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ[RESEARCH_DB_PATH_ENV] = str(target_db_path)
    import memory_store as ms

    ms._DB_INITIALIZED = False
    ms.init_db()


def drop_snapshot_tables(conn: sqlite3.Connection) -> None:
    for table_name in SNAPSHOT_TABLES_DROP_ORDER:
        if table_exists(conn, table_name):
            conn.execute(f"DROP TABLE {table_name}")


def import_snapshot_tables(
    target_conn: sqlite3.Connection,
    *,
    snapshot_db_path: Path,
    tables: list[str],
) -> dict[str, int]:
    snapshot_uri = f"file:{snapshot_db_path.resolve()}?mode=ro"
    target_conn.execute("PRAGMA foreign_keys = OFF")
    drop_snapshot_tables(target_conn)
    target_conn.execute(f"ATTACH DATABASE '{snapshot_uri}' AS snapshot_src")
    row_counts: dict[str, int] = {}
    try:
        for table_name in tables:
            target_conn.execute(
                f"CREATE TABLE {table_name} AS SELECT * FROM snapshot_src.{table_name}"
            )
            row_counts[table_name] = count_rows(target_conn, table_name)
    finally:
        target_conn.execute("DETACH DATABASE snapshot_src")
    return row_counts


def import_research_snapshot(
    *,
    snapshot_db_path: Path,
    target_db_path: Optional[Path] = None,
    manifest_file_path: Optional[Path] = None,
    dry_run: bool = False,
    allow_nonstandard_target: bool = False,
) -> dict[str, Any]:
    snapshot_db_path = snapshot_db_path.expanduser().resolve()
    target_db_path = resolve_target_db_path(target_db_path)
    manifest_file_path = (manifest_file_path or manifest_path_for(snapshot_db_path)).resolve()

    if not snapshot_db_path.exists():
        raise FileNotFoundError(f"Snapshot database not found: {snapshot_db_path}")

    validate_target_path(target_db_path, allow_nonstandard_target=allow_nonstandard_target)
    manifest = load_manifest(manifest_file_path)
    validate_manifest(manifest, snapshot_db_path)

    with open_readonly_database(snapshot_db_path) as snapshot_conn:
        warnings = validate_snapshot_database(snapshot_conn)
        import_tables = tables_to_import(snapshot_conn, manifest)
        snapshot_row_counts = {table: count_rows(snapshot_conn, table) for table in import_tables}

    preserved_before: dict[str, int] = {}
    if target_db_path.exists() and not dry_run:
        with sqlite3.connect(target_db_path) as existing_conn:
            preserved_before = preserved_table_counts(existing_conn)
    elif target_db_path.exists() and dry_run:
        with sqlite3.connect(target_db_path) as existing_conn:
            preserved_before = preserved_table_counts(existing_conn)

    if dry_run:
        return {
            "ok": True,
            "dryRun": True,
            "importedTables": import_tables,
            "rowCounts": snapshot_row_counts,
            "targetPath": str(target_db_path),
            "manifestPath": str(manifest_file_path),
            "preservedResearchTables": PRESERVED_RESEARCH_TABLES,
            "preservedTableCounts": preserved_before,
            "warnings": warnings,
        }

    ensure_target_database(target_db_path)
    with sqlite3.connect(target_db_path) as target_conn:
        preserved_before = preserved_table_counts(target_conn)
        imported_row_counts = import_snapshot_tables(
            target_conn,
            snapshot_db_path=snapshot_db_path,
            tables=import_tables,
        )
        preserved_after = preserved_table_counts(target_conn)
        target_conn.commit()

    if preserved_before != preserved_after:
        raise RuntimeError(
            "Import modified preserved research tables; aborting with inconsistent target state."
        )

    return {
        "ok": True,
        "dryRun": False,
        "importedTables": import_tables,
        "rowCounts": imported_row_counts,
        "targetPath": str(target_db_path),
        "manifestPath": str(manifest_file_path),
        "preservedResearchTables": list(PRESERVED_RESEARCH_TABLES),
        "preservedTableCounts": preserved_after,
        "warnings": warnings,
    }


def format_import_summary(result: dict[str, Any]) -> str:
    lines = [
        "Scout research snapshot import",
        f"mode: {'dry-run' if result.get('dryRun') else 'write'}",
        f"imported tables: {', '.join(result.get('importedTables') or [])}",
    ]
    for table, count in (result.get("rowCounts") or {}).items():
        lines.append(f"{table} rows: {count}")
    lines.append(f"target: {result.get('targetPath')}")
    lines.append(
        "preserved research tables: "
        + ", ".join(result.get("preservedResearchTables") or [])
    )
    preserved_counts = result.get("preservedTableCounts") or {}
    if preserved_counts:
        for table, count in preserved_counts.items():
            lines.append(f"preserved {table} rows: {count}")
    for warning in result.get("warnings") or []:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Import a Scout research snapshot into the isolated cloud research database. "
            "Replaces signal snapshot tables only."
        )
    )
    parser.add_argument(
        "--snapshot",
        type=Path,
        default=DEFAULT_SNAPSHOT_DB,
        help=f"Snapshot database path (default: {DEFAULT_SNAPSHOT_DB.name}).",
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        help=(
            "Target cloud research database path "
            f"(default: {RESEARCH_DB_PATH_ENV} env or {DEFAULT_CLOUD_RESEARCH_DB_NAME})."
        ),
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Snapshot manifest path (default: <snapshot_stem>.manifest.json).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print the import plan without writing to the target database.",
    )
    parser.add_argument(
        "--allow-nonstandard-target",
        action="store_true",
        help=f"Allow target paths that do not end with {DEFAULT_CLOUD_RESEARCH_DB_NAME}.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = import_research_snapshot(
            snapshot_db_path=args.snapshot,
            target_db_path=args.target,
            manifest_file_path=args.manifest,
            dry_run=args.dry_run,
            allow_nonstandard_target=args.allow_nonstandard_target,
        )
    except FileNotFoundError as exc:
        print(f"[research-snapshot-import] ERROR: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[research-snapshot-import] ERROR: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"[research-snapshot-import] ERROR: SQLite import failed: {exc}", file=sys.stderr)
        return 1

    for warning in result.get("warnings") or []:
        print(f"[research-snapshot-import] WARNING: {warning}", file=sys.stderr)
    print(format_import_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
