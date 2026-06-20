#!/usr/bin/env python3
"""Rule candidate hypothesis layer over promoted research findings."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from memory_store import connect, init_db, json_dump, json_load
from research_findings_engine import init_research_findings_store, research_finding_row


CANDIDATE_TYPES = frozenset(
    {
        "direction_filter",
        "sector_filter",
        "gate_weight_candidate",
        "trend_override",
        "risk_filter",
        "system_note",
    }
)
CANDIDATE_STATUSES = frozenset({"proposed", "testing", "validated", "rejected", "archived"})
ACTIVE_CANDIDATE_STATUSES = frozenset({"proposed", "testing"})

BEARISH_UNDERPERFORMANCE_TITLE = "Bearish signals underperforming"
LEADERSHIP_TREND_TITLE = "Leadership cohort favors bullish exposure over bearish calls"


def init_rule_candidates_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rule_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            source_finding_id INTEGER REFERENCES research_findings(id) ON DELETE SET NULL,
            candidate_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            title TEXT NOT NULL,
            hypothesis TEXT NOT NULL,
            proposed_rule TEXT NOT NULL,
            rationale TEXT NOT NULL,
            affected_scope_json TEXT NOT NULL DEFAULT '{}',
            supporting_metrics_json TEXT NOT NULL DEFAULT '{}',
            validation_plan TEXT NOT NULL,
            validation_result_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_rule_candidates_status
            ON rule_candidates(status, candidate_type);

        CREATE INDEX IF NOT EXISTS idx_rule_candidates_source
            ON rule_candidates(source_finding_id, candidate_type, title, status);
        """
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def rule_candidate_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "updatedAt": row["updated_at"],
        "sourceFindingId": row["source_finding_id"],
        "candidateType": row["candidate_type"],
        "status": row["status"],
        "title": row["title"],
        "hypothesis": row["hypothesis"],
        "proposedRule": row["proposed_rule"],
        "rationale": row["rationale"],
        "affectedScope": json_load(row["affected_scope_json"]) or {},
        "supportingMetrics": json_load(row["supporting_metrics_json"]) or {},
        "validationPlan": row["validation_plan"],
        "validationResult": json_load(row["validation_result_json"]) or {},
    }


def _validate_candidate_type(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in CANDIDATE_TYPES:
        raise ValueError(f"Invalid candidate_type: {value}")
    return normalized


def _validate_status(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in CANDIDATE_STATUSES:
        raise ValueError(f"Invalid status: {value}")
    return normalized


def duplicate_active_candidate_exists(
    conn: sqlite3.Connection,
    *,
    source_finding_id: Optional[int],
    candidate_type: str,
    title: str,
) -> bool:
    placeholders = ", ".join("?" for _ in ACTIVE_CANDIDATE_STATUSES)
    row = conn.execute(
        f"""
        SELECT id FROM rule_candidates
        WHERE source_finding_id IS ?
          AND candidate_type = ?
          AND title = ?
          AND status IN ({placeholders})
        """,
        (source_finding_id, candidate_type, title, *sorted(ACTIVE_CANDIDATE_STATUSES)),
    ).fetchone()
    return row is not None


def create_rule_candidate(
    *,
    source_finding_id: Optional[int] = None,
    candidate_type: str,
    title: str,
    hypothesis: str,
    proposed_rule: str,
    rationale: str,
    validation_plan: str,
    status: str = "proposed",
    affected_scope: Optional[dict[str, Any]] = None,
    supporting_metrics: Optional[dict[str, Any]] = None,
    validation_result: Optional[dict[str, Any]] = None,
    skip_duplicate: bool = True,
) -> dict[str, Any]:
    init_db()
    candidate_type = _validate_candidate_type(candidate_type)
    status = _validate_status(status)
    now = utc_now_iso()

    with connect() as conn:
        init_research_findings_store(conn)
        init_rule_candidates_store(conn)
        if skip_duplicate and duplicate_active_candidate_exists(
            conn,
            source_finding_id=source_finding_id,
            candidate_type=candidate_type,
            title=title.strip(),
        ):
            existing = conn.execute(
                """
                SELECT * FROM rule_candidates
                WHERE source_finding_id IS ?
                  AND candidate_type = ?
                  AND title = ?
                  AND status IN ('proposed', 'testing')
                ORDER BY id DESC
                LIMIT 1
                """,
                (source_finding_id, candidate_type, title.strip()),
            ).fetchone()
            return {
                "ok": True,
                "created": False,
                "candidate": rule_candidate_row(existing),
            }

        cursor = conn.execute(
            """
            INSERT INTO rule_candidates (
                created_at, updated_at, source_finding_id, candidate_type, status,
                title, hypothesis, proposed_rule, rationale, affected_scope_json,
                supporting_metrics_json, validation_plan, validation_result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now,
                now,
                source_finding_id,
                candidate_type,
                status,
                title.strip(),
                hypothesis.strip(),
                proposed_rule.strip(),
                rationale.strip(),
                json_dump(affected_scope or {}),
                json_dump(supporting_metrics or {}),
                validation_plan.strip(),
                json_dump(validation_result or {}),
            ),
        )
        candidate_id = int(cursor.lastrowid)
        row = conn.execute(
            "SELECT * FROM rule_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        return {
            "ok": True,
            "created": True,
            "candidate": rule_candidate_row(row),
        }


def list_rule_candidates(
    *,
    status: Optional[str] = None,
    candidate_type: Optional[str] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db()
    clauses = ["1 = 1"]
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(_validate_status(status))
    if candidate_type:
        clauses.append("candidate_type = ?")
        params.append(_validate_candidate_type(candidate_type))
    bounded_limit = min(max(int(limit), 1), 500)
    params.append(bounded_limit)

    with connect() as conn:
        init_rule_candidates_store(conn)
        rows = conn.execute(
            f"""
            SELECT * FROM rule_candidates
            WHERE {' AND '.join(clauses)}
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()
        return [rule_candidate_row(row) for row in rows]


def update_rule_candidate_status(candidate_id: int, status: str) -> dict[str, Any]:
    init_db()
    status = _validate_status(status)
    with connect() as conn:
        init_rule_candidates_store(conn)
        row = conn.execute(
            "SELECT * FROM rule_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "message": f"Rule candidate {candidate_id} was not found."}
        now = utc_now_iso()
        conn.execute(
            "UPDATE rule_candidates SET status = ?, updated_at = ? WHERE id = ?",
            (status, now, candidate_id),
        )
        updated = conn.execute(
            "SELECT * FROM rule_candidates WHERE id = ?",
            (candidate_id,),
        ).fetchone()
        return {"ok": True, "candidate": rule_candidate_row(updated)}


def _title_references_bearish_underperformance(title: str) -> bool:
    normalized = str(title or "").strip().lower()
    return "bearish" in normalized and (
        "underperform" in normalized or "underperforming" in normalized
    )


def _title_references_specter(title: str) -> bool:
    return "specter" in str(title or "").strip().lower()


def candidate_payloads_from_finding(finding: dict[str, Any]) -> list[dict[str, Any]]:
    finding_id = finding.get("id")
    finding_type = str(finding.get("findingType") or finding.get("finding_type") or "")
    title = str(finding.get("title") or "")
    description = str(finding.get("description") or "")
    supporting_metrics = finding.get("supportingMetrics") or finding.get("supporting_metrics") or {}
    related_sectors = finding.get("relatedSectors") or finding.get("related_sectors") or []
    related_gates = finding.get("relatedGates") or finding.get("related_gates") or []
    payloads: list[dict[str, Any]] = []

    if finding_type == "direction_failure" and _title_references_bearish_underperformance(title):
        payloads.append(
            {
                "source_finding_id": finding_id,
                "candidate_type": "trend_override",
                "title": "Test bearish trend override",
                "hypothesis": (
                    "Bearish calls may need stronger trend confirmation before becoming actionable."
                ),
                "proposed_rule": (
                    "Require additional trend weakness confirmation before allowing "
                    "bearish actionable signals."
                ),
                "rationale": description or title,
                "affected_scope": {
                    "direction": "Bearish",
                    "scopeType": "direction_filter",
                },
                "supporting_metrics": supporting_metrics,
                "validation_plan": (
                    "Backtest bearish signals with stronger trend filter before changing live logic."
                ),
            }
        )

    if finding_type == "leadership_trend":
        payloads.append(
            {
                "source_finding_id": finding_id,
                "candidate_type": "direction_filter",
                "title": "Test leadership bearish suppression",
                "hypothesis": (
                    "AI leadership cohorts may favor bullish exposure over bearish calls "
                    "during strong leadership regimes."
                ),
                "proposed_rule": (
                    "Suppress or downgrade bearish signals in leadership cohorts unless "
                    "breakdown confirmation is present."
                ),
                "rationale": description or title,
                "affected_scope": {
                    "direction": "Bearish",
                    "scopeType": "leadership_cohort",
                    "sectors": related_sectors,
                    "groupId": supporting_metrics.get("groupId"),
                    "groupLabel": supporting_metrics.get("groupLabel"),
                },
                "supporting_metrics": supporting_metrics,
                "validation_plan": (
                    "Backtest leadership cohort bearish signals with and without suppression."
                ),
            }
        )

    if finding_type == "gate_strength" and _title_references_specter(title):
        payloads.append(
            {
                "source_finding_id": finding_id,
                "candidate_type": "gate_weight_candidate",
                "title": "Test SPECTER emphasis",
                "hypothesis": (
                    "SPECTER may provide positive expectancy when combined with other gates."
                ),
                "proposed_rule": (
                    "Evaluate higher analytical weight or stricter inclusion for "
                    "SPECTER-positive setups."
                ),
                "rationale": description or title,
                "affected_scope": {
                    "gates": related_gates or ["SPECTER"],
                    "scopeType": "gate_weight",
                },
                "supporting_metrics": supporting_metrics,
                "validation_plan": (
                    "Backtest SPECTER-positive setups across direction, sector, and "
                    "gate intersections before changing weights."
                ),
            }
        )

    return payloads


def generate_rule_candidates_from_finding(finding_id: int) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        init_research_findings_store(conn)
        row = conn.execute(
            "SELECT * FROM research_findings WHERE id = ?",
            (finding_id,),
        ).fetchone()
        if row is None:
            return {"ok": False, "message": f"Research finding {finding_id} was not found."}

    finding = research_finding_row(row)
    payloads = candidate_payloads_from_finding(finding)
    created_candidates: list[dict[str, Any]] = []
    skipped = 0
    for payload in payloads:
        result = create_rule_candidate(**payload)
        if result.get("created"):
            created_candidates.append(result["candidate"])
        else:
            skipped += 1

    return {
        "ok": True,
        "findingId": finding_id,
        "generated": len(created_candidates),
        "skippedDuplicates": skipped,
        "candidates": created_candidates,
        "finding": finding,
    }


def generate_rule_candidates_from_open_findings(limit: int = 20) -> dict[str, Any]:
    init_db()
    bounded_limit = min(max(int(limit), 1), 200)
    with connect() as conn:
        init_research_findings_store(conn)
        rows = conn.execute(
            """
            SELECT id FROM research_findings
            WHERE status = 'open'
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        finding_ids = [int(row["id"]) for row in rows]

    results: list[dict[str, Any]] = []
    total_generated = 0
    total_skipped = 0
    for finding_id in finding_ids:
        result = generate_rule_candidates_from_finding(finding_id)
        results.append(result)
        if result.get("ok"):
            total_generated += int(result.get("generated") or 0)
            total_skipped += int(result.get("skippedDuplicates") or 0)

    return {
        "ok": True,
        "findingsProcessed": len(finding_ids),
        "generated": total_generated,
        "skippedDuplicates": total_skipped,
        "results": results,
    }
