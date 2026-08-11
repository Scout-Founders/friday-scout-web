#!/usr/bin/env python3
"""Research-only mirror of Scout Firestore daily reports (Scout v6 / Monday Coffee).

One-way ingest:
  Firestore scout_reports (read-only)
  → research_daily_reports (SQLite research DB)
  → research_findings (optional adapter)

Never writes to Firestore, SMTP, live scans, scoring, gates, or trades.
"""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from memory_store import connect, get_db_path, init_db, json_dump, json_load


SCOUT_REPORTS_COLLECTION = "scout_reports"
SOURCE_SYSTEM = "firestore_scout_reports"

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


def normalize_firestore_document(doc: Any) -> dict[str, Any]:
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
        "source_system": SOURCE_SYSTEM,
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


def ingest_scout_report_document(doc: Any) -> dict[str, Any]:
    """Upsert one report document into research_daily_reports (idempotent by report_id)."""
    init_db()
    payload = normalize_firestore_document(doc)
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
        return {
            "ok": True,
            "action": action,
            "report": research_daily_report_row(row),
        }


def ingest_scout_reports(
    *,
    limit: int = 50,
    since: Optional[str] = None,
    documents: Optional[list[Any]] = None,
) -> dict[str, Any]:
    """Ingest Scout reports from Firestore (or provided documents) into research SQLite."""
    init_db()
    summary: dict[str, Any] = {
        "ok": True,
        "available": True,
        "imported": 0,
        "updated": 0,
        "skipped": 0,
        "errors": [],
        "databasePath": str(get_db_path()),
        "collection": SCOUT_REPORTS_COLLECTION,
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
            }

    for doc in docs or []:
        try:
            result = ingest_scout_report_document(doc)
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
    """Create research findings from ingested daily reports (structured JSON only)."""
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
        description="Ingest Firestore scout_reports into research_daily_reports (research-only)."
    )
    parser.add_argument("--limit", type=int, default=50)
    parser.add_argument("--since", default=None)
    parser.add_argument(
        "--generate-findings",
        action="store_true",
        help="Also generate research findings from newly available structured reports.",
    )
    args = parser.parse_args(argv)
    result = ingest_scout_reports(limit=max(int(args.limit), 1), since=args.since)
    print(json.dumps(result, indent=2))
    if args.generate_findings and result.get("available", True):
        findings = generate_findings_from_daily_reports(limit=max(int(args.limit), 1))
        print(json.dumps(findings, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
