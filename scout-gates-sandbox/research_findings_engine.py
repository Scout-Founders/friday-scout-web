#!/usr/bin/env python3
"""Research findings intelligence layer over completed research job runs."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from memory_store import connect, init_db, json_dump, json_load
from research_job_runner import init_research_job_store, research_job_run_row


FINDING_TYPES = frozenset(
    {
        "direction_failure",
        "sector_failure",
        "gate_strength",
        "gate_weakness",
        "leadership_trend",
        "anomaly",
        "system_note",
    }
)
SEVERITIES = frozenset({"info", "watch", "warning", "critical"})
CONFIDENCE_LEVELS = frozenset({"low", "medium", "high"})
FINDING_STATUSES = frozenset({"open", "reviewed", "dismissed", "promoted_to_rule_candidate"})

BEARISH_EXPECTANCY_THRESHOLD = -3.0
GATE_STRENGTH_EXPECTANCY_THRESHOLD = 2.0
MIN_FINDING_SIGNAL_COUNT = 10
HIGH_CONFIDENCE_SIGNAL_COUNT = 20
LEADERSHIP_GROUP_IDS = ("ai_infrastructure", "semiconductors", "mega_cap_ai_leaders")


def init_research_findings_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS research_findings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            finding_type TEXT NOT NULL,
            severity TEXT NOT NULL,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            confidence TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'open',
            source_job_run_id INTEGER REFERENCES research_job_runs(id) ON DELETE SET NULL,
            supporting_metrics_json TEXT NOT NULL DEFAULT '{}',
            related_tickers_json TEXT NOT NULL DEFAULT '[]',
            related_sectors_json TEXT NOT NULL DEFAULT '[]',
            related_gates_json TEXT NOT NULL DEFAULT '[]',
            recommended_next_test TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_research_findings_status
            ON research_findings(status, severity, finding_type);

        CREATE INDEX IF NOT EXISTS idx_research_findings_source
            ON research_findings(source_job_run_id, finding_type, title, status);
        """
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def research_finding_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "findingType": row["finding_type"],
        "severity": row["severity"],
        "title": row["title"],
        "description": row["description"],
        "confidence": row["confidence"],
        "status": row["status"],
        "sourceJobRunId": row["source_job_run_id"],
        "supportingMetrics": json_load(row["supporting_metrics_json"]) or {},
        "relatedTickers": json_load(row["related_tickers_json"]) or [],
        "relatedSectors": json_load(row["related_sectors_json"]) or [],
        "relatedGates": json_load(row["related_gates_json"]) or [],
        "recommendedNextTest": row["recommended_next_test"],
    }


def _validate_finding_type(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in FINDING_TYPES:
        raise ValueError(f"Invalid finding_type: {value}")
    return normalized


def _validate_severity(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in SEVERITIES:
        raise ValueError(f"Invalid severity: {value}")
    return normalized


def _validate_confidence(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in CONFIDENCE_LEVELS:
        raise ValueError(f"Invalid confidence: {value}")
    return normalized


def _validate_status(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in FINDING_STATUSES:
        raise ValueError(f"Invalid status: {value}")
    return normalized


def duplicate_open_finding_exists(
    conn: sqlite3.Connection,
    *,
    source_job_run_id: Optional[int],
    finding_type: str,
    title: str,
) -> bool:
    row = conn.execute(
        """
        SELECT id FROM research_findings
        WHERE source_job_run_id IS ?
          AND finding_type = ?
          AND title = ?
          AND status = 'open'
        """,
        (source_job_run_id, finding_type, title),
    ).fetchone()
    return row is not None


def create_research_finding(
    *,
    finding_type: str,
    severity: str,
    title: str,
    description: str,
    confidence: str,
    status: str = "open",
    source_job_run_id: Optional[int] = None,
    supporting_metrics: Optional[dict[str, Any]] = None,
    related_tickers: Optional[list[str]] = None,
    related_sectors: Optional[list[str]] = None,
    related_gates: Optional[list[str]] = None,
    recommended_next_test: Optional[str] = None,
    skip_duplicate: bool = True,
) -> dict[str, Any]:
    init_db()
    finding_type = _validate_finding_type(finding_type)
    severity = _validate_severity(severity)
    confidence = _validate_confidence(confidence)
    status = _validate_status(status)
    now = utc_now_iso()

    with connect() as conn:
        init_research_job_store(conn)
        init_research_findings_store(conn)
        if skip_duplicate and duplicate_open_finding_exists(
            conn,
            source_job_run_id=source_job_run_id,
            finding_type=finding_type,
            title=title,
        ):
            existing = conn.execute(
                """
                SELECT * FROM research_findings
                WHERE source_job_run_id IS ?
                  AND finding_type = ?
                  AND title = ?
                  AND status = 'open'
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_job_run_id, finding_type, title),
            ).fetchone()
            return {
                "ok": True,
                "created": False,
                "finding": research_finding_row(existing),
            }

        cursor = conn.execute(
            """
            INSERT INTO research_findings (
                created_at, updated_at, finding_type, severity, title, description,
                confidence, status, source_job_run_id, supporting_metrics_json,
                related_tickers_json, related_sectors_json, related_gates_json,
                recommended_next_test
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                finding_type,
                severity,
                title.strip(),
                description.strip(),
                confidence,
                status,
                source_job_run_id,
                json_dump(supporting_metrics or {}),
                json_dump(related_tickers or []),
                json_dump(related_sectors or []),
                json_dump(related_gates or []),
                recommended_next_test,
            ),
        )
        finding_id = int(cursor.lastrowid)
        row = conn.execute(
            "SELECT * FROM research_findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        return {
            "ok": True,
            "created": True,
            "finding": research_finding_row(row),
        }


def list_research_findings(
    *,
    status: Optional[str] = None,
    severity: Optional[str] = None,
    finding_type: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db()
    clauses = ["1 = 1"]
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(_validate_status(status))
    if severity:
        clauses.append("severity = ?")
        params.append(_validate_severity(severity))
    if finding_type:
        clauses.append("finding_type = ?")
        params.append(_validate_finding_type(finding_type))
    bounded_limit = min(max(int(limit), 1), 500)
    params.append(bounded_limit)

    with connect() as conn:
        init_research_findings_store(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM research_findings
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [research_finding_row(row) for row in rows]


def update_research_finding_status(finding_id: int, status: str) -> dict[str, Any]:
    init_db()
    status = _validate_status(status)
    with connect() as conn:
        init_research_findings_store(conn)
        row = conn.execute(
            "SELECT * FROM research_findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "message": f"Research finding {finding_id} was not found."}
        now = utc_now_iso()
        conn.execute(
            "UPDATE research_findings SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, finding_id),
        )
        updated = conn.execute(
            "SELECT * FROM research_findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        return {"ok": True, "finding": research_finding_row(updated)}


def _confidence_from_signal_count(signal_count: int) -> str:
    return "high" if signal_count >= HIGH_CONFIDENCE_SIGNAL_COUNT else "medium"


def _numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def finding_payloads_from_job_run(
    run_row: sqlite3.Row,
    *,
    job_name: str,
) -> list[dict[str, Any]]:
    if run_row["status"] != "completed":
        return []

    summary = json_load(run_row["summary_json"]) or {}
    audit = summary.get("audit") or {}
    signals_count = int(run_row["signals_count"] or 0)
    job_run_id = int(run_row["id"])
    payloads: list[dict[str, Any]] = []

    if signals_count == 0:
        payloads.append(
            {
                "finding_type": "system_note",
                "severity": "info",
                "title": "Research job completed with no matched signals",
                "description": (
                    f"{job_name} completed with zero matched signals. "
                    "Verify job filters, preset mapping, and available historical sample."
                ),
                "confidence": "high",
                "source_job_run_id": job_run_id,
                "supporting_metrics": {
                    "jobName": job_name,
                    "signalsCount": signals_count,
                    "status": run_row["status"],
                },
                "recommended_next_test": (
                    "Verify job filters, preset mapping, and available historical sample."
                ),
            }
        )
        return payloads

    if audit.get("kind") == "bearish_failure":
        bearish = audit.get("summary") or {}
        bearish_count = int(bearish.get("signal_count") or 0)
        bearish_expectancy = _numeric(bearish.get("expectancy"))
        if (
            bearish_count >= MIN_FINDING_SIGNAL_COUNT
            and bearish_expectancy is not None
            and bearish_expectancy < BEARISH_EXPECTANCY_THRESHOLD
        ):
            payloads.append(
                {
                    "finding_type": "direction_failure",
                    "severity": "warning",
                    "title": "Bearish signals underperforming",
                    "description": (
                        f"Bearish failure audit for {job_name} shows "
                        f"{bearish_count} bearish signals with expectancy "
                        f"{bearish_expectancy:.2f}%."
                    ),
                    "confidence": _confidence_from_signal_count(bearish_count),
                    "source_job_run_id": job_run_id,
                    "supporting_metrics": {
                        "jobName": job_name,
                        "bearishSignalCount": bearish_count,
                        "bearishExpectancy": bearish_expectancy,
                        "bearishWinRate": bearish.get("win_rate"),
                        "avgSignalReturn": bearish.get("avg_signal_return"),
                    },
                    "recommended_next_test": "Investigate trend override logic for bearish calls.",
                }
            )

    gate_audit = audit if audit.get("kind") == "specter_positive" else None
    if gate_audit is not None:
        gate_count = int(gate_audit.get("signalCount") or gate_audit.get("signal_count") or 0)
        gate_expectancy = _numeric(gate_audit.get("expectancy"))
        gate_name = str(gate_audit.get("gateCode") or "SPECTER").upper()
        if (
            gate_count >= MIN_FINDING_SIGNAL_COUNT
            and gate_expectancy is not None
            and gate_expectancy > GATE_STRENGTH_EXPECTANCY_THRESHOLD
        ):
            payloads.append(
                {
                    "finding_type": "gate_strength",
                    "severity": "info",
                    "title": f"{gate_name} showing positive expectancy",
                    "description": (
                        f"{gate_name} passed on {gate_count} signals with expectancy "
                        f"{gate_expectancy:.2f}% in {job_name}."
                    ),
                    "confidence": _confidence_from_signal_count(gate_count),
                    "source_job_run_id": job_run_id,
                    "supporting_metrics": {
                        "jobName": job_name,
                        "gateCode": gate_name,
                        "signalCount": gate_count,
                        "expectancy": gate_expectancy,
                        "winRate": gate_audit.get("winRate") or gate_audit.get("win_rate"),
                        "avgSignalReturn": gate_audit.get("avgSignalReturn")
                        or gate_audit.get("avg_signal_return"),
                    },
                    "related_gates": [gate_name],
                    "recommended_next_test": (
                        f"Test {gate_name} intersections and direction split."
                    ),
                }
            )

    if audit.get("kind") == "trend_leadership":
        for group in audit.get("groups") or []:
            group_id = str(group.get("id") or "")
            if group_id not in LEADERSHIP_GROUP_IDS:
                continue
            bullish_expectancy = _numeric(group.get("bullishExpectancy"))
            bearish_expectancy = _numeric(group.get("bearishExpectancy"))
            if (
                bullish_expectancy is not None
                and bearish_expectancy is not None
                and bullish_expectancy > 0
                and bearish_expectancy < 0
            ):
                label = group.get("label") or group_id.replace("_", " ").title()
                payloads.append(
                    {
                        "finding_type": "leadership_trend",
                        "severity": "watch",
                        "title": "Leadership cohort favors bullish exposure over bearish calls",
                        "description": (
                            f"{label} shows bullish expectancy {bullish_expectancy:.2f}% "
                            f"and bearish expectancy {bearish_expectancy:.2f}% in {job_name}."
                        ),
                        "confidence": "medium",
                        "source_job_run_id": job_run_id,
                        "supporting_metrics": {
                            "jobName": job_name,
                            "groupId": group_id,
                            "groupLabel": label,
                            "signalCount": group.get("signalCount"),
                            "bullishExpectancy": bullish_expectancy,
                            "bearishExpectancy": bearish_expectancy,
                        },
                        "related_sectors": [label],
                        "recommended_next_test": (
                            "Compare leadership bullish/bearish performance across future runs."
                        ),
                    }
                )

    return payloads


def generate_findings_from_job_run(job_run_id: int) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        init_research_job_store(conn)
        init_research_findings_store(conn)
        run_row = conn.execute(
            """
            SELECT r.*, j.name AS job_name
            FROM research_job_runs r
            JOIN research_jobs j ON j.id = r.job_id
            WHERE r.id = ?
            """,
            (job_run_id,),
        ).fetchone()
        if run_row is None:
            return {"ok": False, "message": f"Research job run {job_run_id} was not found."}

    payloads = finding_payloads_from_job_run(run_row, job_name=str(run_row["job_name"]))
    created_findings: list[dict[str, Any]] = []
    skipped = 0
    for payload in payloads:
        result = create_research_finding(**payload)
        if result.get("created"):
            created_findings.append(result["finding"])
        else:
            skipped += 1

    return {
        "ok": True,
        "jobRunId": job_run_id,
        "generated": len(created_findings),
        "skippedDuplicates": skipped,
        "findings": created_findings,
        "run": {
            **research_job_run_row(run_row),
            "jobName": run_row["job_name"],
        },
    }


def generate_findings_from_recent_runs(limit: int = 20) -> dict[str, Any]:
    init_db()
    bounded_limit = min(max(int(limit), 1), 200)
    with connect() as conn:
        init_research_job_store(conn)
        rows = conn.execute(
            """
            SELECT id FROM research_job_runs
            WHERE status = 'completed'
            ORDER BY completed_at DESC, id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        run_ids = [int(row["id"]) for row in rows]

    results: list[dict[str, Any]] = []
    total_generated = 0
    total_skipped = 0
    for run_id in run_ids:
        result = generate_findings_from_job_run(run_id)
        results.append(result)
        if result.get("ok"):
            total_generated += int(result.get("generated") or 0)
            total_skipped += int(result.get("skippedDuplicates") or 0)

    return {
        "ok": True,
        "runsProcessed": len(run_ids),
        "generated": total_generated,
        "skippedDuplicates": total_skipped,
        "results": results,
    }
