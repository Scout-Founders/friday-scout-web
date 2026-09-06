#!/usr/bin/env python3
"""Read-only research snapshot exporter for Scout sandbox signal history."""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SANDBOX_DIR = Path(__file__).resolve().parent
DEFAULT_SOURCE_DB = SANDBOX_DIR / "scout_memory.db"
DEFAULT_OUTPUT_DB = SANDBOX_DIR / "research_snapshot.db"
SNAPSHOT_SCHEMA_VERSION = "1.0"

REQUIRED_EXPORT_TABLES = ("scan_runs", "scan_results")
OPTIONAL_EXPORT_TABLES = ("feature_vectors",)
EXPORT_TABLES = REQUIRED_EXPORT_TABLES + OPTIONAL_EXPORT_TABLES

EXCLUDED_TABLES = frozenset(
    {
        "research_jobs",
        "research_job_runs",
        "research_findings",
        "rule_candidates",
        "rule_validations",
        "research_daily_reports",
        "research_sent_picks",
        "backtest_runs",
        "backtest_signals",
        "backtest_metrics",
        "report_jobs",
        "report_registry",
        "gate_intelligence_metrics",
        "gate_alpha_metrics",
        "gate_attributions",
        "regime_snapshots",
        "pattern_intelligence",
        "outcome_update_audit",
        "institutional_audit_log",
    }
)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def manifest_path_for(output_db_path: Path) -> Path:
    return output_db_path.with_name(f"{output_db_path.stem}.manifest.json")


def open_readonly_database(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"Source database not found: {db_path}")
    uri = f"file:{db_path.resolve()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone()
    return row is not None


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
    row = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()
    return int(row[0] if row is not None else 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def validate_source_database(source_conn: sqlite3.Connection) -> list[str]:
    missing_required = [
        table for table in REQUIRED_EXPORT_TABLES if not table_exists(source_conn, table)
    ]
    if missing_required:
        raise ValueError(
            "Source database is missing required table(s): "
            + ", ".join(missing_required)
        )

    warnings: list[str] = []
    for table in OPTIONAL_EXPORT_TABLES:
        if not table_exists(source_conn, table):
            warnings.append(f"Optional table not found and skipped: {table}")
    return warnings


def copy_table_from_attached(
    dest_conn: sqlite3.Connection,
    *,
    attached_alias: str,
    table_name: str,
) -> int:
    dest_conn.execute(f"CREATE TABLE {table_name} AS SELECT * FROM {attached_alias}.{table_name}")
    return count_rows(dest_conn, table_name)


def export_research_snapshot(
    *,
    source_db_path: Path,
    output_db_path: Path,
    manifest_file_path: Optional[Path] = None,
) -> dict[str, Any]:
    """Export a read-only research snapshot from the local sandbox database."""
    source_db_path = source_db_path.expanduser().resolve()
    output_db_path = output_db_path.expanduser().resolve()
    manifest_file_path = (manifest_file_path or manifest_path_for(output_db_path)).resolve()

    warnings: list[str] = []
    with open_readonly_database(source_db_path) as source_conn:
        warnings.extend(validate_source_database(source_conn))
        tables_to_export = list(REQUIRED_EXPORT_TABLES)
        for table in OPTIONAL_EXPORT_TABLES:
            if table_exists(source_conn, table):
                tables_to_export.append(table)

        if output_db_path.exists():
            output_db_path.unlink()
        output_db_path.parent.mkdir(parents=True, exist_ok=True)

        dest_conn = sqlite3.connect(output_db_path)
        try:
            dest_conn.execute("PRAGMA foreign_keys = OFF")
            attached_uri = f"file:{source_db_path}?mode=ro"
            dest_conn.execute(f"ATTACH DATABASE '{attached_uri}' AS snapshot_src")
            row_counts: dict[str, int] = {}
            for table_name in tables_to_export:
                row_counts[table_name] = copy_table_from_attached(
                    dest_conn,
                    attached_alias="snapshot_src",
                    table_name=table_name,
                )
            dest_conn.commit()
        finally:
            dest_conn.close()

    checksum = sha256_file(output_db_path)
    manifest = {
        "created_at": utc_now_iso(),
        "source_db_path": str(source_db_path),
        "output_db_path": str(output_db_path),
        "tables_exported": tables_to_export,
        "row_counts": row_counts,
        "schema_version": SNAPSHOT_SCHEMA_VERSION,
        "checksum_sha256": checksum,
        "read_only_export": True,
    }
    manifest_file_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    exported_tables = list_user_tables(sqlite3.connect(output_db_path))
    unexpected = sorted(set(exported_tables) - set(tables_to_export))
    if unexpected:
        raise RuntimeError(f"Snapshot export produced unexpected tables: {', '.join(unexpected)}")

    return {
        "ok": True,
        "warnings": warnings,
        "exportedTables": tables_to_export,
        "rowCounts": row_counts,
        "outputPath": str(output_db_path),
        "manifestPath": str(manifest_file_path),
        "manifest": manifest,
    }


def format_export_summary(result: dict[str, Any]) -> str:
    lines = [
        "Scout research snapshot export",
        f"exported tables: {', '.join(result.get('exportedTables') or [])}",
    ]
    for table, count in (result.get("rowCounts") or {}).items():
        lines.append(f"{table} rows: {count}")
    lines.append(f"output: {result.get('outputPath')}")
    lines.append(f"manifest: {result.get('manifestPath')}")
    for warning in result.get("warnings") or []:
        lines.append(f"warning: {warning}")
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export a read-only Scout research snapshot from local sandbox signal history. "
            "Does not modify the source database."
        )
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE_DB,
        help=f"Read-only source database path (default: {DEFAULT_SOURCE_DB.name}).",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT_DB,
        help=f"Snapshot database output path (default: {DEFAULT_OUTPUT_DB.name}).",
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=None,
        help="Optional manifest output path (default: <output_stem>.manifest.json).",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = export_research_snapshot(
            source_db_path=args.source,
            output_db_path=args.output,
            manifest_file_path=args.manifest,
        )
    except FileNotFoundError as exc:
        print(f"[research-snapshot] ERROR: {exc}", file=sys.stderr)
        return 2
    except ValueError as exc:
        print(f"[research-snapshot] ERROR: {exc}", file=sys.stderr)
        return 2
    except sqlite3.Error as exc:
        print(f"[research-snapshot] ERROR: SQLite export failed: {exc}", file=sys.stderr)
        return 1

    for warning in result.get("warnings") or []:
        print(f"[research-snapshot] WARNING: {warning}", file=sys.stderr)
    print(format_export_summary(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
