#!/usr/bin/env python3
"""Research-only mirror of Scout daily reports (Scout v6 / Monday Coffee).

One-way ingest paths:
  1. Artifact bundle JSON (preferred for cloud research)
  2. Firestore scout_reports read-only (optional local/dev fallback)
  → research_daily_reports (SQLite research DB)  # full report/archive history
  → research_sent_picks (email_sent=true emailed picks from raw_report_text)
  → research_findings (optional adapter; independent of sent-pick ledger)

Official sent picks are parsed from raw_report_text (SMTP email body).
structured_report_json.picks remain scan candidates only (passed[:6]).

Never writes to Firestore, SMTP, live scans, scoring, gates, or trades.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Union

from memory_store import connect, get_db_path, init_db, json_dump, json_load
from sent_pick_parser import (
    PARSE_MODE_RAW_SCAN_FALLBACK,
    PARSE_MODE_UNRECOGNIZED,
    extract_scan_candidates,
    parse_scout_email_picks,
)


SCOUT_REPORTS_COLLECTION = "scout_reports"
SOURCE_SYSTEM = "firestore_scout_reports"
SOURCE_SYSTEM_ARTIFACT = "artifact_scout_daily_reports"

BUNDLE_SCHEMA_VERSION = 1
SUPPORTED_BUNDLE_SCHEMA_VERSIONS = frozenset({BUNDLE_SCHEMA_VERSION})
BUNDLE_FILENAME = "scout_daily_reports.json"
MANIFEST_FILENAME = "scout_daily_reports.manifest.json"
MANIFEST_FILENAME_ALT = "scout_daily_reports_manifest.json"
DAILY_REPORTS_ARTIFACT_NAME = "scout-daily-reports"

REPORT_TYPE_DAILY_SCAN = "daily_scan"
REPORT_TYPE_MONDAY_COFFEE = "monday_coffee"

REPORT_TYPE_ALIASES = {
    "daily_scan": REPORT_TYPE_DAILY_SCAN,
    "daily": REPORT_TYPE_DAILY_SCAN,
    "scout_v6": REPORT_TYPE_DAILY_SCAN,
    "scout-v6": REPORT_TYPE_DAILY_SCAN,
    "v6": REPORT_TYPE_DAILY_SCAN,
    "scout_v6_daily": REPORT_TYPE_DAILY_SCAN,
    "monday_coffee": REPORT_TYPE_MONDAY_COFFEE,
    "monday-coffee": REPORT_TYPE_MONDAY_COFFEE,
    "monday coffee": REPORT_TYPE_MONDAY_COFFEE,
    "coffee": REPORT_TYPE_MONDAY_COFFEE,
}

FINDING_TYPE_DAILY_REPORT = "daily_report_observation"
FINDING_TYPE_SENT_PICK = "sent_pick_observation"

SENT_PICK_TABLE_COLUMNS: dict[str, str] = {
    "sent_pick_id": "TEXT NOT NULL PRIMARY KEY",
    "report_id": "TEXT NOT NULL",
    "report_type": "TEXT",
    "market_date": "TEXT",
    "generated_at": "TEXT",
    "email_sent_at": "TEXT",
    "ticker": "TEXT NOT NULL",
    "email_classification": "TEXT NOT NULL",
    "direction": "TEXT",
    "strike": "REAL",
    "expiration": "TEXT",
    "strategy_text": "TEXT",
    "parser_confidence": "REAL",
    "parse_mode": "TEXT",
    "source_section": "TEXT",
    "source_excerpt": "TEXT",
    "pick_json": "TEXT NOT NULL DEFAULT '{}'",
    "created_at": "TEXT NOT NULL",
    "updated_at": "TEXT NOT NULL",
}


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_research_daily_reports_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS research_daily_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_id TEXT NOT NULL UNIQUE,
            report_type TEXT,
            report_version TEXT,
            market_date TEXT,
            generated_at TEXT,
            status_prefix TEXT,
            email_subject TEXT,
            raw_report_text TEXT,
            structured_report_json TEXT NOT NULL DEFAULT '{}',
            source_scan_run_id TEXT,
            claude_model TEXT,
            underlying_data_timestamp TEXT,
            email_attempted INTEGER NOT NULL DEFAULT 0,
            email_sent INTEGER NOT NULL DEFAULT 0,
            email_sent_at TEXT,
            email_error TEXT,
            ingested_at TEXT NOT NULL,
            source_system TEXT NOT NULL DEFAULT 'firestore_scout_reports'
        );

        CREATE INDEX IF NOT EXISTS idx_research_daily_reports_type
            ON research_daily_reports(report_type, market_date DESC);

        CREATE INDEX IF NOT EXISTS idx_research_daily_reports_market_date
            ON research_daily_reports(market_date DESC);

        CREATE INDEX IF NOT EXISTS idx_research_daily_reports_report_id
            ON research_daily_reports(report_id);
        """
    )
    init_research_sent_picks_store(conn)


def _table_column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    names: set[str] = set()
    for row in rows:
        if isinstance(row, sqlite3.Row):
            names.add(str(row["name"]))
        else:
            names.add(str(row[1]))
    return names


def init_research_sent_picks_store(conn: sqlite3.Connection) -> None:
    """Create/migrate research_sent_picks. Safe for existing scout_memory.db."""
    # Create table first, then migrate columns, then indexes — indexes that
    # reference newer columns must not run before ALTER TABLE on older DBs.
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_sent_picks (
            sent_pick_id TEXT NOT NULL PRIMARY KEY,
            report_id TEXT NOT NULL,
            report_type TEXT,
            market_date TEXT,
            generated_at TEXT,
            email_sent_at TEXT,
            ticker TEXT NOT NULL,
            email_classification TEXT NOT NULL DEFAULT 'unknown',
            direction TEXT,
            strike REAL,
            expiration TEXT,
            strategy_text TEXT,
            parser_confidence REAL,
            parse_mode TEXT,
            source_section TEXT,
            source_excerpt TEXT,
            pick_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )
    existing = _table_column_names(conn, "research_sent_picks")
    alter_specs = [
        ("email_classification", "TEXT NOT NULL DEFAULT 'unknown'"),
        ("direction", "TEXT"),
        ("strike", "REAL"),
        ("expiration", "TEXT"),
        ("strategy_text", "TEXT"),
        ("parser_confidence", "REAL"),
        ("parse_mode", "TEXT"),
        ("source_section", "TEXT"),
        ("source_excerpt", "TEXT"),
        ("pick_json", "TEXT NOT NULL DEFAULT '{}'"),
        ("created_at", "TEXT"),
        ("updated_at", "TEXT"),
    ]
    for column, col_type in alter_specs:
        if column not in existing:
            conn.execute(
                f"ALTER TABLE research_sent_picks ADD COLUMN {column} {col_type}"
            )
    conn.executescript(
        """
        CREATE INDEX IF NOT EXISTS idx_research_sent_picks_report_id
            ON research_sent_picks(report_id);

        CREATE INDEX IF NOT EXISTS idx_research_sent_picks_ticker
            ON research_sent_picks(ticker, market_date DESC);

        CREATE INDEX IF NOT EXISTS idx_research_sent_picks_market_date
            ON research_sent_picks(market_date DESC);

        CREATE INDEX IF NOT EXISTS idx_research_sent_picks_direction
            ON research_sent_picks(direction, market_date DESC);

        CREATE INDEX IF NOT EXISTS idx_research_sent_picks_report_type
            ON research_sent_picks(report_type, market_date DESC);

        CREATE INDEX IF NOT EXISTS idx_research_sent_picks_classification
            ON research_sent_picks(email_classification, market_date DESC);
        """
    )


def normalize_report_type(value: Any) -> Optional[str]:
    if value is None:
        return None
    raw = " ".join(str(value).strip().lower().replace("-", " ").replace("_", " ").split())
    compact = raw.replace(" ", "_")
    if compact in REPORT_TYPE_ALIASES:
        return REPORT_TYPE_ALIASES[compact]
    if raw in REPORT_TYPE_ALIASES:
        return REPORT_TYPE_ALIASES[raw]
    if "monday" in raw and "coffee" in raw:
        return REPORT_TYPE_MONDAY_COFFEE
    if "v6" in raw or "daily" in raw:
        return REPORT_TYPE_DAILY_SCAN
    return compact or None


def research_daily_report_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "reportId": row["report_id"],
        "reportType": row["report_type"],
        "reportVersion": row["report_version"],
        "marketDate": row["market_date"],
        "generatedAt": row["generated_at"],
        "statusPrefix": row["status_prefix"],
        "emailSubject": row["email_subject"],
        "rawReportText": row["raw_report_text"],
        "structuredReport": json_load(row["structured_report_json"]) or {},
        "sourceScanRunId": row["source_scan_run_id"],
        "claudeModel": row["claude_model"],
        "underlyingDataTimestamp": row["underlying_data_timestamp"],
        "emailAttempted": bool(row["email_attempted"]),
        "emailSent": bool(row["email_sent"]),
        "emailSentAt": row["email_sent_at"],
        "emailError": row["email_error"],
        "ingestedAt": row["ingested_at"],
        "sourceSystem": row["source_system"],
    }


def research_sent_pick_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "sentPickId": row["sent_pick_id"],
        "reportId": row["report_id"],
        "reportType": row["report_type"],
        "marketDate": row["market_date"],
        "generatedAt": row["generated_at"],
        "emailSentAt": row["email_sent_at"],
        "ticker": row["ticker"],
        "emailClassification": row["email_classification"]
        if "email_classification" in keys
        else None,
        "direction": (row["direction"] or None),
        "strike": row["strike"] if "strike" in keys else None,
        "expiration": row["expiration"] if "expiration" in keys else None,
        "strategyText": row["strategy_text"] if "strategy_text" in keys else None,
        "parserConfidence": row["parser_confidence"]
        if "parser_confidence" in keys
        else None,
        "parseMode": row["parse_mode"] if "parse_mode" in keys else None,
        "sourceSection": row["source_section"] if "source_section" in keys else None,
        "sourceExcerpt": row["source_excerpt"] if "source_excerpt" in keys else None,
        "pick": json_load(row["pick_json"]) if "pick_json" in keys else {},
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        # Role label so APIs never conflate with scan candidates.
        "role": "emailed_pick",
    }


def _as_bool_int(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if value in (None, ""):
        return 0
    if isinstance(value, (int, float)):
        return 1 if value else 0
    return 1 if str(value).strip().lower() in {"1", "true", "yes", "on"} else 0


def _as_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _structured_as_json_text(value: Any) -> str:
    if value is None:
        return "{}"
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return "{}"
        try:
            parsed = json.loads(text)
            return json_dump(parsed if isinstance(parsed, (dict, list)) else {"value": parsed})
        except json.JSONDecodeError:
            return json_dump({"unparsed": text})
    if isinstance(value, (dict, list)):
        return json_dump(value)
    return json_dump({"value": value})


def sha256_file(path: Union[str, Path]) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def normalize_firestore_document(
    doc: Any,
    *,
    source_system: str = SOURCE_SYSTEM,
) -> dict[str, Any]:
    """Normalize a Firestore document snapshot or plain dict into ingest fields."""
    if isinstance(doc, dict):
        data = dict(doc)
        report_id = _as_text(data.get("report_id") or data.get("reportId") or data.get("id"))
    else:
        data = dict(getattr(doc, "to_dict", lambda: {})() or {})
        doc_id = getattr(doc, "id", None)
        report_id = _as_text(data.get("report_id") or data.get("reportId") or doc_id)

    if not report_id:
        raise ValueError("Firestore scout_reports document is missing report_id.")

    return {
        "report_id": report_id,
        "report_type": normalize_report_type(data.get("report_type") or data.get("reportType")),
        "report_version": _as_text(data.get("report_version") or data.get("reportVersion")),
        "market_date": _as_text(data.get("market_date") or data.get("marketDate")),
        "generated_at": _as_text(data.get("generated_at") or data.get("generatedAt")),
        "status_prefix": _as_text(data.get("status_prefix") or data.get("statusPrefix")),
        "email_subject": _as_text(data.get("email_subject") or data.get("emailSubject")),
        "raw_report_text": data.get("raw_report_text")
        if data.get("raw_report_text") is not None
        else data.get("rawReportText"),
        "structured_report_json": _structured_as_json_text(
            data.get("structured_report_json")
            if data.get("structured_report_json") is not None
            else data.get("structuredReportJson")
            if data.get("structuredReportJson") is not None
            else data.get("structured_report")
            if data.get("structured_report") is not None
            else data.get("structuredReport")
        ),
        "source_scan_run_id": _as_text(
            data.get("source_scan_run_id") or data.get("sourceScanRunId")
        ),
        "claude_model": _as_text(data.get("claude_model") or data.get("claudeModel")),
        "underlying_data_timestamp": _as_text(
            data.get("underlying_data_timestamp") or data.get("underlyingDataTimestamp")
        ),
        "email_attempted": _as_bool_int(data.get("email_attempted") or data.get("emailAttempted")),
        "email_sent": _as_bool_int(data.get("email_sent") or data.get("emailSent")),
        "email_sent_at": _as_text(data.get("email_sent_at") or data.get("emailSentAt")),
        "email_error": _as_text(data.get("email_error") or data.get("emailError")),
        "source_system": source_system,
    }


def _coerce_bundle_schema_version(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Bundle schema_version must be an integer.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    text = str(value or "").strip()
    if not text:
        raise ValueError("Bundle schema_version is required.")
    return int(text)


def validate_report_bundle_payload(payload: Any) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Validate a parsed report bundle and return normalized metadata + report dicts."""
    if not isinstance(payload, dict):
        raise ValueError("Report bundle must be a JSON object.")

    schema_version = _coerce_bundle_schema_version(payload.get("schema_version"))
    if schema_version not in SUPPORTED_BUNDLE_SCHEMA_VERSIONS:
        raise ValueError(
            f"Unsupported report bundle schema_version: {schema_version}. "
            f"Supported: {sorted(SUPPORTED_BUNDLE_SCHEMA_VERSIONS)}"
        )

    reports = payload.get("reports")
    if not isinstance(reports, list):
        raise ValueError("Report bundle must include a reports array.")

    generated_at = _as_text(payload.get("generated_at"))
    normalized_reports: list[dict[str, Any]] = []
    for index, report in enumerate(reports):
        if not isinstance(report, dict):
            raise ValueError(f"Report bundle reports[{index}] must be an object.")
        normalized_reports.append(report)

    metadata = {
        "schemaVersion": schema_version,
        "generatedAt": generated_at,
        "reportCount": len(normalized_reports),
    }
    return metadata, normalized_reports


def load_report_bundle_json(bundle_path: Union[str, Path]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = Path(bundle_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Report bundle not found: {path}")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Report bundle is not valid JSON: {path}") from exc
    metadata, reports = validate_report_bundle_payload(payload)
    metadata["bundlePath"] = str(path)
    return metadata, reports


def resolve_report_bundle_manifest_path(
    *,
    bundle_path: Path,
    manifest_path: Optional[Path] = None,
    bundle_dir: Optional[Path] = None,
) -> Path:
    if manifest_path is not None:
        resolved = manifest_path.expanduser().resolve()
        if not resolved.exists():
            raise FileNotFoundError(f"Report bundle manifest not found: {resolved}")
        return resolved

    search_dirs = []
    if bundle_dir is not None:
        search_dirs.append(bundle_dir.expanduser().resolve())
    search_dirs.append(bundle_path.parent)

    filenames = (MANIFEST_FILENAME, MANIFEST_FILENAME_ALT)
    for directory in search_dirs:
        for filename in filenames:
            candidate = directory / filename
            if candidate.exists():
                return candidate.resolve()
    expected = ", ".join(filenames)
    raise FileNotFoundError(
        f"Report bundle manifest not found for {bundle_path}. Expected one of: {expected}"
    )


def validate_report_bundle_manifest(
    *,
    manifest_path: Union[str, Path],
    bundle_path: Union[str, Path],
) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser().resolve()
    bundle_file = Path(bundle_path).expanduser().resolve()
    if not manifest_file.exists():
        raise FileNotFoundError(f"Report bundle manifest not found: {manifest_file}")
    if not bundle_file.exists():
        raise FileNotFoundError(f"Report bundle not found: {bundle_file}")

    try:
        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Report bundle manifest is not valid JSON: {manifest_file}") from exc
    if not isinstance(manifest, dict):
        raise ValueError("Report bundle manifest must be a JSON object.")

    schema_version = _coerce_bundle_schema_version(manifest.get("schema_version"))
    if schema_version not in SUPPORTED_BUNDLE_SCHEMA_VERSIONS:
        raise ValueError(
            f"Unsupported report bundle manifest schema_version: {schema_version}. "
            f"Supported: {sorted(SUPPORTED_BUNDLE_SCHEMA_VERSIONS)}"
        )

    expected_checksum = str(manifest.get("checksum_sha256") or "").strip().lower()
    if not expected_checksum:
        raise ValueError("Report bundle manifest is missing checksum_sha256.")
    actual_checksum = sha256_file(bundle_file)
    if expected_checksum != actual_checksum:
        raise ValueError(
            "Report bundle checksum mismatch: manifest checksum does not match bundle file."
        )

    manifest_bundle_name = str(manifest.get("bundle_filename") or BUNDLE_FILENAME).strip()
    if manifest_bundle_name and manifest_bundle_name != bundle_file.name:
        raise ValueError(
            "Report bundle manifest bundle_filename does not match bundle file name."
        )

    _, reports = load_report_bundle_json(bundle_file)
    manifest_report_count = manifest.get("report_count")
    if manifest_report_count is not None:
        try:
            expected_count = int(manifest_report_count)
        except (TypeError, ValueError) as exc:
            raise ValueError("Report bundle manifest report_count must be an integer.") from exc
        if expected_count != len(reports):
            raise ValueError(
                "Report bundle manifest report_count does not match reports array length."
            )

    return {
        "manifestPath": str(manifest_file),
        "bundlePath": str(bundle_file),
        "schemaVersion": schema_version,
        "generatedAt": _as_text(manifest.get("generated_at")),
        "reportCount": len(reports),
        "checksumSha256": actual_checksum,
    }


def resolve_report_bundle_dir(bundle_dir: Union[str, Path]) -> dict[str, Path]:
    directory = Path(bundle_dir).expanduser().resolve()
    if not directory.exists():
        raise FileNotFoundError(f"Report bundle directory not found: {directory}")

    bundle_path = directory / BUNDLE_FILENAME
    if not bundle_path.exists():
        raise FileNotFoundError(
            f"Report bundle not found in directory: {bundle_path}"
        )

    manifest_path = resolve_report_bundle_manifest_path(
        bundle_path=bundle_path,
        bundle_dir=directory,
    )
    return {
        "bundleDir": directory,
        "bundlePath": bundle_path,
        "manifestPath": manifest_path,
    }


def build_report_bundle_manifest(*, bundle_path: Union[str, Path], generated_at: Optional[str] = None) -> dict[str, Any]:
    """Build a manifest dict for a report bundle file (used by tests and external publishers)."""
    path = Path(bundle_path).expanduser().resolve()
    metadata, reports = load_report_bundle_json(path)
    return {
        "schema_version": metadata["schemaVersion"],
        "generated_at": generated_at or metadata.get("generatedAt") or utc_now_iso(),
        "report_count": len(reports),
        "bundle_filename": path.name,
        "checksum_sha256": sha256_file(path),
    }


def firestore_credentials_available() -> bool:
    if os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "").strip():
        return True
    if os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip():
        return True
    if os.environ.get("SCOUT_FIRESTORE_SERVICE_ACCOUNT_JSON", "").strip():
        return True
    if os.environ.get("SCOUT_FIRESTORE_PROJECT_ID", "").strip() and os.environ.get(
        "SCOUT_FIRESTORE_CREDENTIALS", ""
    ).strip():
        return True
    return False


def _load_firestore_client() -> Any:
    """Create a Firestore client from environment credentials (read-only usage)."""
    try:
        from google.cloud import firestore  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "google-cloud-firestore is not installed. "
            "Install it to enable daily report ingest, or skip ingest locally."
        ) from exc

    project_id = (
        os.environ.get("SCOUT_FIRESTORE_PROJECT_ID", "").strip()
        or os.environ.get("GOOGLE_CLOUD_PROJECT", "").strip()
        or os.environ.get("GCLOUD_PROJECT", "").strip()
        or None
    )

    sa_json = (
        os.environ.get("SCOUT_FIRESTORE_SERVICE_ACCOUNT_JSON", "").strip()
        or os.environ.get("FIREBASE_SERVICE_ACCOUNT_JSON", "").strip()
        or os.environ.get("SCOUT_FIRESTORE_CREDENTIALS", "").strip()
    )
    if sa_json:
        from google.oauth2 import service_account  # type: ignore

        info = json.loads(sa_json)
        credentials = service_account.Credentials.from_service_account_info(info)
        return firestore.Client(project=project_id or info.get("project_id"), credentials=credentials)

    return firestore.Client(project=project_id)


def fetch_scout_report_documents(
    *,
    limit: int = 50,
    since: Optional[str] = None,
) -> list[Any]:
    client = _load_firestore_client()
    query = client.collection(SCOUT_REPORTS_COLLECTION)
    if since:
        # Prefer generated_at when present; Firestore may store as string ISO.
        query = query.where("generated_at", ">=", since)
    query = query.order_by("generated_at", direction="DESC").limit(max(int(limit), 1))
    return list(query.stream())


def _validate_artifact_report_fields(doc: dict[str, Any]) -> None:
    report_id = _as_text(doc.get("report_id") or doc.get("reportId") or doc.get("id"))
    if not report_id:
        raise ValueError("Report bundle entry is missing report_id.")
    report_type = doc.get("report_type") or doc.get("reportType")
    if report_type in (None, ""):
        raise ValueError("Report bundle entry is missing report_type.")


def ingest_scout_report_document(
    doc: Any,
    *,
    source_system: Optional[str] = None,
) -> dict[str, Any]:
    """Upsert one report document into research_daily_reports (idempotent by report_id)."""
    init_db()
    resolved_source = source_system or SOURCE_SYSTEM
    if resolved_source == SOURCE_SYSTEM_ARTIFACT:
        if not isinstance(doc, dict):
            raise ValueError("Report bundle entry must be an object.")
        _validate_artifact_report_fields(doc)
    payload = normalize_firestore_document(
        doc,
        source_system=resolved_source,
    )
    now = utc_now_iso()

    with connect() as conn:
        init_research_daily_reports_store(conn)
        existing = conn.execute(
            "SELECT id FROM research_daily_reports WHERE report_id = ?",
            (payload["report_id"],),
        ).fetchone()

        if existing is None:
            cursor = conn.execute(
                """
                INSERT INTO research_daily_reports (
                    report_id, report_type, report_version, market_date, generated_at,
                    status_prefix, email_subject, raw_report_text, structured_report_json,
                    source_scan_run_id, claude_model, underlying_data_timestamp,
                    email_attempted, email_sent, email_sent_at, email_error,
                    ingested_at, source_system
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload["report_id"],
                    payload["report_type"],
                    payload["report_version"],
                    payload["market_date"],
                    payload["generated_at"],
                    payload["status_prefix"],
                    payload["email_subject"],
                    payload["raw_report_text"],
                    payload["structured_report_json"],
                    payload["source_scan_run_id"],
                    payload["claude_model"],
                    payload["underlying_data_timestamp"],
                    payload["email_attempted"],
                    payload["email_sent"],
                    payload["email_sent_at"],
                    payload["email_error"],
                    now,
                    payload["source_system"],
                ),
            )
            report_row_id = int(cursor.lastrowid)
            action = "imported"
        else:
            conn.execute(
                """
                UPDATE research_daily_reports
                SET report_type = ?,
                    report_version = ?,
                    market_date = ?,
                    generated_at = ?,
                    status_prefix = ?,
                    email_subject = ?,
                    raw_report_text = ?,
                    structured_report_json = ?,
                    source_scan_run_id = ?,
                    claude_model = ?,
                    underlying_data_timestamp = ?,
                    email_attempted = ?,
                    email_sent = ?,
                    email_sent_at = ?,
                    email_error = ?,
                    ingested_at = ?,
                    source_system = ?
                WHERE report_id = ?
                """,
                (
                    payload["report_type"],
                    payload["report_version"],
                    payload["market_date"],
                    payload["generated_at"],
                    payload["status_prefix"],
                    payload["email_subject"],
                    payload["raw_report_text"],
                    payload["structured_report_json"],
                    payload["source_scan_run_id"],
                    payload["claude_model"],
                    payload["underlying_data_timestamp"],
                    payload["email_attempted"],
                    payload["email_sent"],
                    payload["email_sent_at"],
                    payload["email_error"],
                    now,
                    payload["source_system"],
                    payload["report_id"],
                ),
            )
            report_row_id = int(existing["id"])
            action = "updated"

        row = conn.execute(
            "SELECT * FROM research_daily_reports WHERE id = ?",
            (report_row_id,),
        ).fetchone()
        report = research_daily_report_row(row)
        try:
            materialize_sent_picks_for_report(report, conn=conn)
        except Exception as materialize_err:
            # Never fail report ingest because of ledger materialization.
            print(f"[SENT_PICKS] materialize failed for {payload['report_id']}: {materialize_err}")
        return {
            "ok": True,
            "action": action,
            "report": report,
        }


def ingest_scout_reports(
    *,
    limit: int = 50,
    since: Optional[str] = None,
    documents: Optional[list[Any]] = None,
    source_system: Optional[str] = None,
) -> dict[str, Any]:
    """Ingest Scout reports from Firestore (or provided documents) into research SQLite."""
    init_db()
    resolved_source = source_system or SOURCE_SYSTEM
    summary: dict[str, Any] = {
        "ok": True,
        "available": True,
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "databasePath": str(get_db_path()),
        "collection": SCOUT_REPORTS_COLLECTION,
        "ingestMode": "documents" if documents is not None else "firestore",
        "sourceSystem": resolved_source,
    }

    docs = documents
    if docs is None:
        if not firestore_credentials_available():
            return {
                "ok": True,
                "available": False,
                "imported": 0,
                "updated": 0,
                "skipped": 0,
                "errors": [],
                "message": "daily report ingest unavailable",
                "databasePath": str(get_db_path()),
                "ingestMode": "firestore",
            }
        try:
            docs = fetch_scout_report_documents(limit=limit, since=since)
        except Exception as exc:
            return {
                "ok": True,
                "available": False,
                "imported": 0,
                "updated": 0,
                "skipped": 0,
                "errors": [str(exc)],
                "message": f"daily report ingest unavailable: {exc}",
                "databasePath": str(get_db_path()),
                "ingestMode": "firestore",
            }

    for doc in docs or []:
        try:
            result = ingest_scout_report_document(doc, source_system=resolved_source)
            action = result.get("action")
            if action == "imported":
                summary["imported"] += 1
            elif action == "updated":
                summary["updated"] += 1
            else:
                summary["skipped"] += 1
        except Exception as exc:
            summary["skipped"] += 1
            summary["errors"].append(str(exc))

    if summary["errors"] and summary["imported"] == 0 and summary["updated"] == 0:
        summary["ok"] = False
    return summary


def ingest_scout_reports_from_file(
    bundle_path: Union[str, Path],
    *,
    manifest_path: Optional[Union[str, Path]] = None,
    validate_checksum: bool = True,
) -> dict[str, Any]:
    """Ingest a research-safe JSON report bundle file without Firestore credentials."""
    path = Path(bundle_path).expanduser().resolve()
    metadata, reports = load_report_bundle_json(path)

    manifest_info: Optional[dict[str, Any]] = None
    if validate_checksum:
        resolved_manifest = resolve_report_bundle_manifest_path(
            bundle_path=path,
            manifest_path=Path(manifest_path).expanduser().resolve() if manifest_path else None,
        )
        manifest_info = validate_report_bundle_manifest(
            manifest_path=resolved_manifest,
            bundle_path=path,
        )

    summary = ingest_scout_reports(
        documents=reports,
        source_system=SOURCE_SYSTEM_ARTIFACT,
    )
    summary["ingestMode"] = "artifact_file"
    summary["available"] = True
    summary["bundlePath"] = str(path)
    summary["schemaVersion"] = metadata.get("schemaVersion")
    summary["reportCount"] = metadata.get("reportCount")
    if manifest_info:
        summary["manifestPath"] = manifest_info.get("manifestPath")
        summary["checksumSha256"] = manifest_info.get("checksumSha256")
    return summary


def ingest_scout_reports_from_bundle_dir(
    bundle_dir: Union[str, Path],
    *,
    validate_checksum: bool = True,
) -> dict[str, Any]:
    """Ingest a report bundle directory containing JSON + manifest."""
    resolved = resolve_report_bundle_dir(bundle_dir)
    return ingest_scout_reports_from_file(
        resolved["bundlePath"],
        manifest_path=resolved["manifestPath"],
        validate_checksum=validate_checksum,
    )


def list_ingested_daily_reports(
    *,
    report_type: Optional[str] = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    init_db()
    clauses = ["1 = 1"]
    params: list[Any] = []
    if report_type:
        clauses.append("report_type = ?")
        params.append(normalize_report_type(report_type) or report_type)
    bounded = min(max(int(limit), 1), 500)
    params.append(bounded)
    with connect() as conn:
        init_research_daily_reports_store(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM research_daily_reports
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(market_date, generated_at, ingested_at) DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [research_daily_report_row(row) for row in rows]


def get_latest_ingested_report(*, report_type: Optional[str] = None) -> Optional[dict[str, Any]]:
    reports = list_ingested_daily_reports(report_type=report_type, limit=1)
    return reports[0] if reports else None


# ---------------------------------------------------------------------------
# Sent-pick ledger (email_sent=true only; parsed from raw_report_text)
# ---------------------------------------------------------------------------


def build_sent_pick_id(
    report_id: str,
    email_classification: str,
    ticker: str,
    ordinal: int = 0,
) -> str:
    """Deterministic identity: report_id + classification + ticker (+ ordinal)."""
    base = f"{report_id}|{email_classification}|{ticker}"
    if ordinal <= 0:
        return base
    return f"{base}|{ordinal}"


def clear_sent_picks_for_report(conn: sqlite3.Connection, report_id: str) -> int:
    cursor = conn.execute(
        "DELETE FROM research_sent_picks WHERE report_id = ?",
        (report_id,),
    )
    return int(cursor.rowcount or 0)


def list_scan_candidates_for_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Deterministic scan candidates from structured_report_json.picks (not emailed)."""
    structured = report.get("structuredReport")
    if structured is None:
        structured = report.get("structured_report")
    if isinstance(structured, str):
        try:
            structured = json.loads(structured)
        except json.JSONDecodeError:
            structured = {}
    if structured is None and report.get("structured_report_json") is not None:
        raw = report.get("structured_report_json")
        if isinstance(raw, str):
            try:
                structured = json.loads(raw)
            except json.JSONDecodeError:
                structured = {}
        else:
            structured = raw
    return extract_scan_candidates(structured or {})


def materialize_sent_picks_for_report(
    report: dict[str, Any],
    *,
    conn: Optional[sqlite3.Connection] = None,
) -> dict[str, Any]:
    """Upsert emailed-pick rows for one ingested report when email_sent is true.

    Source of truth: raw_report_text (SMTP body), NOT structured_report_json.picks.
    Safe on malformed/empty text: clears ledger rows for the report and returns
    without raising. Never fails report ingest.
    """
    report_id = _as_text(report.get("reportId") or report.get("report_id"))
    if not report_id:
        return {
            "ok": True,
            "reportId": None,
            "upserted": 0,
            "removed": 0,
            "skipped": True,
            "reason": "missing_report_id",
            "parseMode": PARSE_MODE_UNRECOGNIZED,
        }

    email_sent = bool(report.get("emailSent") if "emailSent" in report else report.get("email_sent"))
    owns_connection = conn is None

    def _run(active: sqlite3.Connection) -> dict[str, Any]:
        init_research_sent_picks_store(active)
        if not email_sent:
            removed = clear_sent_picks_for_report(active, report_id)
            return {
                "ok": True,
                "reportId": report_id,
                "upserted": 0,
                "removed": removed,
                "skipped": True,
                "reason": "email_not_sent",
                "parseMode": None,
            }

        raw_text = report.get("rawReportText")
        if raw_text is None:
            raw_text = report.get("raw_report_text")
        parsed = parse_scout_email_picks(raw_text if isinstance(raw_text, str) else str(raw_text or ""))
        parse_mode = parsed.get("parse_mode") or PARSE_MODE_UNRECOGNIZED
        picks = parsed.get("picks") or []
        if not isinstance(picks, list):
            picks = []

        if not picks:
            removed = clear_sent_picks_for_report(active, report_id)
            return {
                "ok": True,
                "reportId": report_id,
                "upserted": 0,
                "removed": removed,
                "skipped": True,
                "reason": (
                    "raw_scan_fallback"
                    if parse_mode == PARSE_MODE_RAW_SCAN_FALLBACK
                    else "no_emailed_picks"
                ),
                "parseMode": parse_mode,
            }

        report_type = report.get("reportType") or report.get("report_type")
        market_date = report.get("marketDate") or report.get("market_date")
        generated_at = report.get("generatedAt") or report.get("generated_at")
        email_sent_at = report.get("emailSentAt") or report.get("email_sent_at")
        now = utc_now_iso()
        occurrence: dict[tuple[str, str], int] = {}
        kept_ids: list[str] = []
        upserted = 0

        for pick in picks:
            if not isinstance(pick, dict):
                continue
            ticker = _as_text(pick.get("ticker"))
            classification = _as_text(pick.get("email_classification"))
            if not ticker or not classification:
                continue
            ticker = ticker.upper()
            classification = classification.lower()
            key = (classification, ticker)
            ordinal = occurrence.get(key, 0)
            occurrence[key] = ordinal + 1
            sent_pick_id = build_sent_pick_id(report_id, classification, ticker, ordinal)

            direction = _as_text(pick.get("direction"))
            strike = pick.get("strike")
            try:
                strike_value = float(strike) if strike is not None and strike != "" else None
            except (TypeError, ValueError):
                strike_value = None
            expiration = _as_text(pick.get("expiration"))
            strategy_text = _as_text(pick.get("strategy_text"))
            parser_confidence = pick.get("parser_confidence")
            try:
                confidence_value = (
                    float(parser_confidence) if parser_confidence is not None else None
                )
            except (TypeError, ValueError):
                confidence_value = None
            pick_parse_mode = _as_text(pick.get("parse_mode")) or parse_mode
            source_section = _as_text(pick.get("source_section"))
            source_excerpt = _as_text(pick.get("source_excerpt"))

            existing = active.execute(
                "SELECT created_at FROM research_sent_picks WHERE sent_pick_id = ?",
                (sent_pick_id,),
            ).fetchone()
            created_at = existing["created_at"] if existing else now

            # Store empty string for missing direction to stay compatible with
            # older DBs that created direction as NOT NULL.
            direction_value = direction or ""

            active.execute(
                """
                INSERT INTO research_sent_picks (
                    sent_pick_id, report_id, report_type, market_date, generated_at,
                    email_sent_at, ticker, email_classification, direction, strike,
                    expiration, strategy_text, parser_confidence, parse_mode,
                    source_section, source_excerpt, pick_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(sent_pick_id) DO UPDATE SET
                    report_id = excluded.report_id,
                    report_type = excluded.report_type,
                    market_date = excluded.market_date,
                    generated_at = excluded.generated_at,
                    email_sent_at = excluded.email_sent_at,
                    ticker = excluded.ticker,
                    email_classification = excluded.email_classification,
                    direction = excluded.direction,
                    strike = excluded.strike,
                    expiration = excluded.expiration,
                    strategy_text = excluded.strategy_text,
                    parser_confidence = excluded.parser_confidence,
                    parse_mode = excluded.parse_mode,
                    source_section = excluded.source_section,
                    source_excerpt = excluded.source_excerpt,
                    pick_json = excluded.pick_json,
                    updated_at = excluded.updated_at
                """,
                (
                    sent_pick_id,
                    report_id,
                    report_type,
                    market_date,
                    generated_at,
                    email_sent_at,
                    ticker,
                    classification,
                    direction_value,
                    strike_value,
                    expiration,
                    strategy_text,
                    confidence_value,
                    pick_parse_mode,
                    source_section,
                    source_excerpt,
                    json_dump(pick),
                    created_at,
                    now,
                ),
            )
            kept_ids.append(sent_pick_id)
            upserted += 1

        if kept_ids:
            placeholders = ",".join("?" for _ in kept_ids)
            cursor = active.execute(
                f"""
                DELETE FROM research_sent_picks
                WHERE report_id = ?
                  AND sent_pick_id NOT IN ({placeholders})
                """,
                [report_id, *kept_ids],
            )
            removed = int(cursor.rowcount or 0)
        else:
            removed = clear_sent_picks_for_report(active, report_id)

        return {
            "ok": True,
            "reportId": report_id,
            "upserted": upserted,
            "removed": removed,
            "skipped": upserted == 0,
            "reason": None if upserted else "no_valid_picks",
            "parseMode": parse_mode,
        }

    try:
        if owns_connection:
            init_db()
            with connect() as active:
                return _run(active)
        assert conn is not None
        return _run(conn)
    except Exception as exc:
        return {
            "ok": False,
            "reportId": report_id,
            "upserted": 0,
            "removed": 0,
            "skipped": True,
            "reason": f"materialize_error: {exc}",
            "parseMode": None,
        }


def list_sent_picks(
    *,
    ticker: Optional[str] = None,
    direction: Optional[str] = None,
    report_type: Optional[str] = None,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    report_id: Optional[str] = None,
    email_classification: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db()
    clauses = ["1 = 1"]
    params: list[Any] = []
    if ticker:
        clauses.append("ticker = ?")
        params.append(str(ticker).strip().upper())
    if direction:
        clauses.append("UPPER(COALESCE(direction, '')) = ?")
        params.append(str(direction).strip().upper())
    if report_type:
        clauses.append("report_type = ?")
        params.append(normalize_report_type(report_type) or report_type)
    if start_date:
        clauses.append(
            "COALESCE(market_date, substr(email_sent_at, 1, 10), substr(generated_at, 1, 10)) >= ?"
        )
        params.append(str(start_date).strip()[:10])
    if end_date:
        clauses.append(
            "COALESCE(market_date, substr(email_sent_at, 1, 10), substr(generated_at, 1, 10)) <= ?"
        )
        params.append(str(end_date).strip()[:10])
    if report_id:
        clauses.append("report_id = ?")
        params.append(str(report_id).strip())
    if email_classification:
        clauses.append("LOWER(COALESCE(email_classification, '')) = ?")
        params.append(str(email_classification).strip().lower())
    bounded = min(max(int(limit), 1), 1000)
    params.append(bounded)
    with connect() as conn:
        init_research_sent_picks_store(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM research_sent_picks
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(market_date, email_sent_at, generated_at, updated_at) DESC,
                     CASE LOWER(COALESCE(email_classification, ''))
                       WHEN 'top' THEN 0
                       WHEN 'secondary' THEN 1
                       WHEN 'watch' THEN 2
                       ELSE 3
                     END,
                     email_sent_at DESC,
                     sent_pick_id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [research_sent_pick_row(row) for row in rows]


def list_sent_picks_for_report(report_id: str) -> list[dict[str, Any]]:
    return list_sent_picks(report_id=report_id, limit=500)


def list_sent_picks_for_date(market_date: str, *, limit: int = 100) -> list[dict[str, Any]]:
    day = str(market_date or "").strip()[:10]
    return list_sent_picks(start_date=day, end_date=day, limit=limit)


def get_sent_picks_summary() -> dict[str, Any]:
    """Lightweight counts for research intelligence (no performance claims)."""
    init_db()
    with connect() as conn:
        init_research_sent_picks_store(conn)
        total = conn.execute("SELECT COUNT(*) AS n FROM research_sent_picks").fetchone()["n"]
        reports = conn.execute(
            "SELECT COUNT(DISTINCT report_id) AS n FROM research_sent_picks"
        ).fetchone()["n"]
        latest = conn.execute(
            """
            SELECT COALESCE(market_date, substr(email_sent_at, 1, 10), substr(generated_at, 1, 10)) AS d
            FROM research_sent_picks
            ORDER BY COALESCE(market_date, email_sent_at, generated_at, updated_at) DESC
            LIMIT 1
            """
        ).fetchone()
        by_class_rows = conn.execute(
            """
            SELECT LOWER(COALESCE(email_classification, 'unknown')) AS klass, COUNT(*) AS n
            FROM research_sent_picks
            GROUP BY LOWER(COALESCE(email_classification, 'unknown'))
            """
        ).fetchall()
    by_class = {row["klass"]: int(row["n"]) for row in by_class_rows}
    return {
        "totalSentPicks": int(total or 0),
        "sentReportsRepresented": int(reports or 0),
        "latestSentPickDate": latest["d"] if latest else None,
        "byClassification": by_class,
    }


# ---------------------------------------------------------------------------
# Report → findings adapter (structured_report_json only)
# ---------------------------------------------------------------------------


def _string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        text = value.strip()
        return [text] if text else []
    if isinstance(value, (list, tuple)):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _first_present(payload: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in payload and payload[key] not in (None, "", [], {}):
            return payload[key]
    return None


def _normalize_regime_label(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", " ").replace("_", " ")
    text = " ".join(text.split())
    mapping = {
        "risk on": "strong risk-on",
        "strong risk on": "strong risk-on",
        "riskon": "strong risk-on",
        "risk off": "risk-off",
        "strong risk off": "risk-off",
        "riskoff": "risk-off",
        "mixed": "mixed regime",
        "mixed regime": "mixed regime",
        "defensive": "defensive rotation",
        "defensive rotation": "defensive rotation",
    }
    if text in mapping:
        return mapping[text]
    if "risk on" in text:
        return "strong risk-on"
    if "risk off" in text:
        return "risk-off"
    if "defensive" in text:
        return "defensive rotation"
    if "mixed" in text:
        return "mixed regime"
    return str(value).strip() or None


def _confidence_label_from_parser(value: Any) -> str:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return "medium"
    if score >= 0.8:
        return "high"
    if score >= 0.6:
        return "medium"
    return "low"


def finding_payloads_from_sent_picks(report: dict[str, Any]) -> list[dict[str, Any]]:
    """Build sent_pick_observation findings from the official emailed-pick ledger."""
    if not bool(report.get("emailSent") if "emailSent" in report else report.get("email_sent")):
        return []
    report_id = str(report.get("reportId") or report.get("report_id") or "")
    if not report_id:
        return []
    picks = list_sent_picks_for_report(report_id)
    market_date = report.get("marketDate") or report.get("market_date")
    report_type = report.get("reportType") or report.get("report_type")
    payloads: list[dict[str, Any]] = []
    for pick in picks:
        ticker = pick.get("ticker")
        classification = pick.get("emailClassification") or "unknown"
        direction = pick.get("direction")
        parse_mode = pick.get("parseMode")
        confidence = _confidence_label_from_parser(pick.get("parserConfidence"))
        class_label = str(classification).upper()
        direction_bit = f" ({direction})" if direction else ""
        title = (
            f"Sent pick observation: {ticker} — {class_label}{direction_bit} "
            f"on {market_date or 'unknown date'}"
        )
        payloads.append(
            {
                "finding_type": FINDING_TYPE_SENT_PICK,
                "severity": "info" if classification != "top" else "watch",
                "title": title,
                "description": (
                    f"Scout emailed {ticker} as {class_label} in report {report_id} "
                    f"(parse_mode={parse_mode or 'unknown'}, "
                    f"parser_confidence={pick.get('parserConfidence')})."
                ),
                "confidence": confidence,
                "supporting_metrics": {
                    "reportId": report_id,
                    "reportType": report_type,
                    "marketDate": market_date,
                    "source": "sent_pick",
                    "ticker": ticker,
                    "emailClassification": classification,
                    "direction": direction,
                    "parserConfidence": pick.get("parserConfidence"),
                    "parseMode": parse_mode,
                    "sentPickId": pick.get("sentPickId"),
                },
                "related_sectors": [],
                "related_tickers": [ticker] if ticker else [],
                "recommended_next_test": (
                    "Compare emailed classifications against deterministic scan candidates "
                    "for the same report_id without treating them as interchangeable."
                ),
            }
        )
    return payloads


def finding_payloads_from_daily_report(report: dict[str, Any]) -> list[dict[str, Any]]:
    structured = report.get("structuredReport") or {}
    if not isinstance(structured, dict) or not structured:
        return []

    report_id = report.get("reportId")
    report_type = report.get("reportType")
    market_date = report.get("marketDate")
    payloads: list[dict[str, Any]] = []

    base_metrics = {
        "reportId": report_id,
        "reportType": report_type,
        "marketDate": market_date,
        "source": "daily_report",
    }

    regime_raw = _first_present(
        structured,
        "regime",
        "marketRegime",
        "market_regime",
        "riskRegime",
        "risk_regime",
    )
    regime = _normalize_regime_label(regime_raw)
    if regime:
        payloads.append(
            {
                "finding_type": FINDING_TYPE_DAILY_REPORT,
                "severity": "watch",
                "title": f"Daily market regime observation: {regime}",
                "description": (
                    f"Scout daily report ({report_type or 'unknown'}) for {market_date or 'unknown date'} "
                    f"observed market regime '{regime}'."
                ),
                "confidence": "medium",
                "supporting_metrics": {**base_metrics, "regime": regime, "regimeRaw": regime_raw},
                "related_sectors": [],
                "related_tickers": _string_list(
                    _first_present(structured, "tickers", "relatedTickers", "related_tickers")
                ),
                "recommended_next_test": (
                    "Compare regime-tagged historical scan outcomes against this market day."
                ),
            }
        )

    leading = _string_list(
        _first_present(
            structured,
            "leadingSectors",
            "leading_sectors",
            "sectorLeadership",
            "sector_leadership",
            "leadershipSectors",
        )
    )
    lagging = _string_list(
        _first_present(
            structured,
            "laggingSectors",
            "lagging_sectors",
            "weakSectors",
            "weak_sectors",
        )
    )
    for sector in leading[:5]:
        payloads.append(
            {
                "finding_type": FINDING_TYPE_DAILY_REPORT,
                "severity": "info",
                "title": f"Sector leadership observation: {sector} leading",
                "description": (
                    f"Daily report for {market_date or 'unknown date'} lists {sector} as a leadership sector."
                ),
                "confidence": "medium",
                "supporting_metrics": {
                    **base_metrics,
                    "leadershipRole": "leading",
                    "sector": sector,
                },
                "related_sectors": [sector],
                "related_tickers": [],
                "recommended_next_test": (
                    f"Backtest bullish setups concentrated in {sector} around similar leadership days."
                ),
            }
        )
    for sector in lagging[:5]:
        payloads.append(
            {
                "finding_type": FINDING_TYPE_DAILY_REPORT,
                "severity": "watch",
                "title": f"Sector leadership observation: {sector} lagging",
                "description": (
                    f"Daily report for {market_date or 'unknown date'} lists {sector} as a lagging sector."
                ),
                "confidence": "medium",
                "supporting_metrics": {
                    **base_metrics,
                    "leadershipRole": "lagging",
                    "sector": sector,
                },
                "related_sectors": [sector],
                "related_tickers": [],
                "recommended_next_test": (
                    f"Investigate recurring failures in {sector} during lagging regimes."
                ),
            }
        )

    direction_note = _first_present(
        structured,
        "directionalCaution",
        "directional_caution",
        "directionBias",
        "direction_bias",
        "directionNote",
    )
    if direction_note:
        note = str(direction_note).strip()
        payloads.append(
            {
                "finding_type": FINDING_TYPE_DAILY_REPORT,
                "severity": "warning",
                "title": "Directional caution observation",
                "description": note,
                "confidence": "medium",
                "supporting_metrics": {**base_metrics, "directionalCaution": note},
                "related_sectors": leading[:3],
                "related_tickers": [],
                "recommended_next_test": (
                    "Test direction filters under the reported regime before promoting any rule candidate."
                ),
            }
        )
    elif regime and "risk-on" in regime.lower():
        payloads.append(
            {
                "finding_type": FINDING_TYPE_DAILY_REPORT,
                "severity": "watch",
                "title": "Directional caution observation: bearish setups under pressure in risk-on",
                "description": (
                    "Strong risk-on regime may pressure bearish setups while favoring bullish leadership exposure."
                ),
                "confidence": "low",
                "supporting_metrics": {**base_metrics, "impliedFromRegime": regime},
                "related_sectors": leading[:3],
                "related_tickers": [],
                "recommended_next_test": (
                    "Audit bearish expectancy during risk-on regimes using historical scan outcomes."
                ),
            }
        )

    if report_type == REPORT_TYPE_MONDAY_COFFEE:
        macro_risks = _string_list(
            _first_present(structured, "macroRisks", "macro_risks", "risks", "recurringRisks")
        )
        earnings = _string_list(
            _first_present(
                structured,
                "earningsConcentration",
                "earnings_concentration",
                "earnings",
                "earningsFocus",
            )
        )
        active_trades = _string_list(
            _first_present(
                structured,
                "activeTradeConcerns",
                "active_trade_concerns",
                "activeTrades",
                "tradeConcerns",
            )
        )
        for risk in macro_risks[:3]:
            payloads.append(
                {
                    "finding_type": FINDING_TYPE_DAILY_REPORT,
                    "severity": "watch",
                    "title": f"Monday Coffee recurring context: macro risk — {risk}",
                    "description": f"Monday Coffee report flagged recurring macro risk: {risk}.",
                    "confidence": "medium",
                    "supporting_metrics": {**base_metrics, "macroRisk": risk},
                    "related_sectors": [],
                    "related_tickers": [],
                    "recommended_next_test": (
                        "Track whether this macro risk coincides with elevated scan failure rates."
                    ),
                }
            )
        for item in earnings[:3]:
            payloads.append(
                {
                    "finding_type": FINDING_TYPE_DAILY_REPORT,
                    "severity": "info",
                    "title": f"Monday Coffee recurring context: earnings concentration — {item}",
                    "description": f"Monday Coffee report noted earnings concentration around {item}.",
                    "confidence": "medium",
                    "supporting_metrics": {**base_metrics, "earningsFocus": item},
                    "related_sectors": [],
                    "related_tickers": _string_list(item) if len(str(item)) <= 8 else [],
                    "recommended_next_test": (
                        "Compare post-earnings scan outcomes for concentrated names this week."
                    ),
                }
            )
        for concern in active_trades[:3]:
            payloads.append(
                {
                    "finding_type": FINDING_TYPE_DAILY_REPORT,
                    "severity": "warning",
                    "title": f"Monday Coffee recurring context: active-trade concern — {concern}",
                    "description": f"Monday Coffee report highlighted active-trade concern: {concern}.",
                    "confidence": "medium",
                    "supporting_metrics": {**base_metrics, "activeTradeConcern": concern},
                    "related_sectors": [],
                    "related_tickers": [],
                    "recommended_next_test": (
                        "Review related historical signals before any rule-candidate promotion."
                    ),
                }
            )

    return payloads


def _daily_report_finding_exists(
    conn: sqlite3.Connection,
    *,
    report_id: str,
    finding_type: str,
    title: str,
) -> bool:
    row = conn.execute(
        """
        SELECT id FROM research_findings
        WHERE finding_type = ?
          AND title = ?
          AND status = 'open'
          AND json_extract(supporting_metrics_json, '$.reportId') = ?
        """,
        (finding_type, title, report_id),
    ).fetchone()
    return row is not None


def generate_findings_from_daily_reports(*, limit: int = 20) -> dict[str, Any]:
    """Create research findings from ingested daily reports + emailed sent picks."""
    from research_findings_engine import create_research_finding, init_research_findings_store

    reports = list_ingested_daily_reports(limit=limit)
    created: list[dict[str, Any]] = []
    skipped = 0
    processed = 0

    with connect() as conn:
        init_research_findings_store(conn)
        init_research_daily_reports_store(conn)

        for report in reports:
            processed += 1
            payloads = finding_payloads_from_daily_report(report)
            payloads.extend(finding_payloads_from_sent_picks(report))
            if not payloads:
                continue
            report_id = str(report.get("reportId") or "")
            for payload in payloads:
                title = str(payload["title"]).strip()
                finding_type = str(payload["finding_type"])
                if report_id and _daily_report_finding_exists(
                    conn,
                    report_id=report_id,
                    finding_type=finding_type,
                    title=title,
                ):
                    skipped += 1
                    continue
                result = create_research_finding(
                    finding_type=finding_type,
                    severity=payload["severity"],
                    title=title,
                    description=payload["description"],
                    confidence=payload["confidence"],
                    supporting_metrics=payload.get("supporting_metrics") or {},
                    related_tickers=payload.get("related_tickers") or [],
                    related_sectors=payload.get("related_sectors") or [],
                    recommended_next_test=payload.get("recommended_next_test"),
                    skip_duplicate=False,
                )
                if result.get("created"):
                    created.append(result["finding"])
                else:
                    skipped += 1

    return {
        "ok": True,
        "reportsProcessed": processed,
        "generated": len(created),
        "skippedDuplicates": skipped,
        "findings": created,
    }


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        description=(
            "Ingest Scout daily reports into research_daily_reports (research-only). "
            "Prefer --from-file or --from-bundle-dir for artifact bundles; "
            "Firestore is an optional local-development fallback."
        )
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--since", default=None)
    parser.add_argument(
        "--from-file",
        dest="from_file",
        default=None,
        help="Import a research-safe JSON report bundle file.",
    )
    parser.add_argument(
        "--from-bundle-dir",
        dest="from_bundle_dir",
        default=None,
        help="Import a report bundle directory containing JSON + manifest.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="Optional manifest path when using --from-file.",
    )
    parser.add_argument(
        "--skip-checksum",
        action="store_true",
        help="Skip manifest checksum validation (local diagnostics only).",
    )
    parser.add_argument(
        "--generate-findings",
        action="store_true",
        help="Also generate research findings from newly available structured reports.",
    )
    args = parser.parse_args(argv)

    if args.from_file and args.from_bundle_dir:
        print(
            json.dumps(
                {
                    "ok": False,
                    "message": "Use only one of --from-file or --from-bundle-dir.",
                },
                indent=2,
            )
        )
        return 1

    if args.from_bundle_dir:
        result = ingest_scout_reports_from_bundle_dir(
            args.from_bundle_dir,
            validate_checksum=not args.skip_checksum,
        )
    elif args.from_file:
        result = ingest_scout_reports_from_file(
            args.from_file,
            manifest_path=args.manifest,
            validate_checksum=not args.skip_checksum,
        )
    else:
        result = ingest_scout_reports(limit=max(int(args.limit), 1), since=args.since)

    print(json.dumps(result, indent=2))
    if args.generate_findings and result.get("available", True):
        findings = generate_findings_from_daily_reports(limit=max(int(args.limit), 1))
        print(json.dumps(findings, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
