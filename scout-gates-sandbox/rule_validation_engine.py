#!/usr/bin/env python3
"""Read-only historical validation for rule candidates."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from backtest_engine import (
    BacktestFilters,
    compute_core_metrics,
    fetch_backtest_signals,
    passed_gate_keys,
    signal_in_trend_leadership_group,
    trend_leadership_group_definitions,
)
from memory_store import connect, init_db, json_dump, json_load
from rule_candidates_engine import init_rule_candidates_store, rule_candidate_row


VALIDATION_STATUSES = frozenset({"pending", "running", "completed", "failed"})


def init_rule_validations_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS rule_validations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL REFERENCES rule_candidates(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL DEFAULT 'pending',
            baseline_signal_count INTEGER NOT NULL DEFAULT 0,
            candidate_signal_count INTEGER NOT NULL DEFAULT 0,
            baseline_win_rate REAL,
            candidate_win_rate REAL,
            baseline_expectancy REAL,
            candidate_expectancy REAL,
            baseline_avg_signal_return REAL,
            candidate_avg_signal_return REAL,
            baseline_max_drawdown REAL,
            candidate_max_drawdown REAL,
            win_rate_delta REAL,
            expectancy_delta REAL,
            return_delta REAL,
            drawdown_delta REAL,
            confidence_score REAL,
            validation_summary TEXT,
            validation_details_json TEXT NOT NULL DEFAULT '{}'
        );

        CREATE INDEX IF NOT EXISTS idx_rule_validations_candidate
            ON rule_validations(candidate_id, created_at DESC);

        CREATE INDEX IF NOT EXISTS idx_rule_validations_status
            ON rule_validations(status, created_at DESC);
        """
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _validate_status(value: str) -> str:
    normalized = str(value or "").strip()
    if normalized not in VALIDATION_STATUSES:
        raise ValueError(f"Invalid validation status: {value}")
    return normalized


def rule_validation_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "candidateId": row["candidate_id"],
        "createdAt": row["created_at"],
        "completedAt": row["completed_at"],
        "status": row["status"],
        "baselineSignalCount": int(row["baseline_signal_count"] or 0),
        "candidateSignalCount": int(row["candidate_signal_count"] or 0),
        "baselineWinRate": row["baseline_win_rate"],
        "candidateWinRate": row["candidate_win_rate"],
        "baselineExpectancy": row["baseline_expectancy"],
        "candidateExpectancy": row["candidate_expectancy"],
        "baselineAvgSignalReturn": row["baseline_avg_signal_return"],
        "candidateAvgSignalReturn": row["candidate_avg_signal_return"],
        "baselineMaxDrawdown": row["baseline_max_drawdown"],
        "candidateMaxDrawdown": row["candidate_max_drawdown"],
        "winRateDelta": row["win_rate_delta"],
        "expectancyDelta": row["expectancy_delta"],
        "returnDelta": row["return_delta"],
        "drawdownDelta": row["drawdown_delta"],
        "confidenceScore": row["confidence_score"],
        "validationSummary": row["validation_summary"],
        "validationDetails": json_load(row["validation_details_json"]) or {},
    }


def _load_candidate(conn: sqlite3.Connection, candidate_id: int) -> Optional[sqlite3.Row]:
    init_rule_candidates_store(conn)
    return conn.execute(
        "SELECT * FROM rule_candidates WHERE id = ?",
        (candidate_id,),
    ).fetchone()


def validation_signal_pool(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return fetch_backtest_signals(
        conn,
        BacktestFilters(require_completed_outcomes=True),
    )


def _leadership_group_for_scope(scope: dict[str, Any]) -> Optional[dict[str, Any]]:
    group_id = str(scope.get("groupId") or "").strip()
    groups = trend_leadership_group_definitions()
    if group_id:
        match = next((group for group in groups if group["id"] == group_id), None)
        if match is not None:
            return match
    return next((group for group in groups if group["id"] == "mega_cap_ai_leaders"), None)


def signal_matches_candidate_exclusion(
    candidate: dict[str, Any],
    signal: dict[str, Any],
) -> bool:
    scope = candidate.get("affectedScope") or {}
    scoped_direction = scope.get("direction")
    if scoped_direction and signal.get("direction") != scoped_direction:
        return False

    candidate_type = str(candidate.get("candidateType") or "")
    if candidate_type == "direction_filter":
        if scope.get("scopeType") == "leadership_cohort":
            group = _leadership_group_for_scope(scope)
            if group is None:
                return False
            return signal_in_trend_leadership_group(signal, group)
        return bool(scoped_direction and signal.get("direction") == scoped_direction)

    if candidate_type == "trend_override":
        if scoped_direction:
            return signal.get("direction") == scoped_direction
        return False

    return False


def simulate_candidate_signals(
    candidate: dict[str, Any],
    baseline_signals: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    candidate_type = str(candidate.get("candidateType") or "")
    scope = candidate.get("affectedScope") or {}
    simulation: dict[str, Any] = {
        "candidateType": candidate_type,
        "baselineCount": len(baseline_signals),
        "method": "unknown",
    }

    if candidate_type == "gate_weight_candidate":
        gate_codes = [
            str(code).strip().upper()
            for code in (scope.get("gates") or ["SPECTER"])
            if str(code).strip()
        ]
        required_gate = gate_codes[0] if gate_codes else "SPECTER"
        candidate_signals = [
            signal
            for signal in baseline_signals
            if required_gate in passed_gate_keys(signal)
        ]
        simulation.update(
            {
                "method": "require_passed_gate",
                "requiredGate": required_gate,
                "excludedCount": len(baseline_signals) - len(candidate_signals),
            }
        )
        return candidate_signals, simulation

    excluded = [
        signal
        for signal in baseline_signals
        if signal_matches_candidate_exclusion(candidate, signal)
    ]
    excluded_ids = {id(signal) for signal in excluded}
    candidate_signals = [
        signal for signal in baseline_signals if id(signal) not in excluded_ids
    ]
    simulation.update(
        {
            "method": "exclude_matching_signals",
            "excludedCount": len(excluded),
        }
    )
    return candidate_signals, simulation


def compute_metric_deltas(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
) -> dict[str, Optional[float]]:
    def delta(candidate_value: Any, baseline_value: Any) -> Optional[float]:
        if candidate_value is None or baseline_value is None:
            return None
        return round(float(candidate_value) - float(baseline_value), 4)

    return {
        "win_rate_delta": delta(candidate_metrics.get("win_rate"), baseline_metrics.get("win_rate")),
        "expectancy_delta": delta(candidate_metrics.get("expectancy"), baseline_metrics.get("expectancy")),
        "return_delta": delta(candidate_metrics.get("avg_return"), baseline_metrics.get("avg_return")),
        "drawdown_delta": delta(
            candidate_metrics.get("max_drawdown"),
            baseline_metrics.get("max_drawdown"),
        ),
    }


def compute_confidence_score(
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    *,
    deltas: dict[str, Optional[float]],
) -> float:
    baseline_count = int(baseline_metrics.get("sample_size") or 0)
    candidate_count = int(candidate_metrics.get("sample_size") or 0)
    if baseline_count < 5 or candidate_count < 1:
        return round(min(max(baseline_count * 4.0, 0.0), 25.0), 1)

    exp_delta = float(deltas.get("expectancy_delta") or 0.0)
    win_delta = float(deltas.get("win_rate_delta") or 0.0)
    drawdown_delta = float(deltas.get("drawdown_delta") or 0.0)

    sample_score = min(baseline_count / 50.0, 1.0) * 35.0
    improvement_score = 0.0
    if exp_delta > 0:
        improvement_score += min(exp_delta * 8.0, 25.0)
    if win_delta > 0:
        improvement_score += min(win_delta * 0.5, 15.0)
    if drawdown_delta > 0:
        improvement_score += min(drawdown_delta * 2.0, 15.0)

    consistency_score = 10.0 if exp_delta >= 0 and win_delta >= 0 else 0.0
    retention_score = min(candidate_count / max(baseline_count, 1), 1.0) * 15.0
    total = sample_score + improvement_score + consistency_score + retention_score
    return round(min(max(total, 0.0), 100.0), 1)


def _format_pct(value: Optional[float]) -> str:
    if value is None:
        return "n/a"
    return f"{value:+.2f}%"


def build_validation_summary(
    candidate: dict[str, Any],
    baseline_metrics: dict[str, Any],
    candidate_metrics: dict[str, Any],
    *,
    deltas: dict[str, Optional[float]],
    simulation: dict[str, Any],
) -> str:
    title = str(candidate.get("title") or "Rule candidate").strip()
    baseline_count = int(baseline_metrics.get("sample_size") or 0)
    candidate_count = int(candidate_metrics.get("sample_size") or 0)
    baseline_expectancy = baseline_metrics.get("expectancy")
    candidate_expectancy = candidate_metrics.get("expectancy")
    exp_delta = deltas.get("expectancy_delta")
    candidate_type = str(candidate.get("candidateType") or "")

    if candidate_count == 0:
        return (
            f"{title} would remove or filter all {baseline_count} historical signals in this "
            "validation pool, so no candidate metrics could be computed."
        )

    if candidate_type == "gate_weight_candidate":
        gate_code = simulation.get("requiredGate") or "SPECTER"
        return (
            f"Requiring {gate_code} would change expectancy from "
            f"{_format_pct(baseline_expectancy)} to {_format_pct(candidate_expectancy)} "
            f"across {candidate_count} of {baseline_count} historical signals."
        )

    if candidate_type == "direction_filter":
        return (
            f"{title} would have changed expectancy from "
            f"{_format_pct(baseline_expectancy)} to {_format_pct(candidate_expectancy)} "
            f"across {candidate_count} historical signals "
            f"(excluding {simulation.get('excludedCount', 0)} matching signals)."
        )

    drawdown_delta = deltas.get("drawdown_delta")
    drawdown_clause = ""
    if drawdown_delta is not None and drawdown_delta > 0:
        drawdown_clause = " while reducing drawdown"

    if exp_delta is not None and exp_delta > 0:
        return (
            f"{title} would have improved expectancy from "
            f"{_format_pct(baseline_expectancy)} to {_format_pct(candidate_expectancy)} "
            f"across {candidate_count} historical signals{drawdown_clause}."
        )

    return (
        f"{title} would have changed expectancy from "
        f"{_format_pct(baseline_expectancy)} to {_format_pct(candidate_expectancy)} "
        f"across {candidate_count} historical signals."
    )


def create_rule_validation(
    candidate_id: int,
    *,
    skip_duplicate: bool = True,
) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        init_rule_candidates_store(conn)
        init_rule_validations_store(conn)
        candidate_row = _load_candidate(conn, candidate_id)
        if candidate_row is None:
            return {"ok": False, "message": f"Rule candidate {candidate_id} was not found."}

        candidate = rule_candidate_row(candidate_row)
        if candidate["status"] != "testing":
            return {
                "ok": False,
                "message": "Only rule candidates in testing status can be validated.",
                "candidate": candidate,
            }

        if skip_duplicate:
            existing = conn.execute(
                """
                SELECT * FROM rule_validations
                WHERE candidate_id = ? AND status = 'pending'
                ORDER BY id DESC
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            if existing is not None:
                return {
                    "ok": True,
                    "created": False,
                    "validation": rule_validation_row(existing),
                    "candidate": candidate,
                }

        now = utc_now_iso()
        cursor = conn.execute(
            """
            INSERT INTO rule_validations (
                candidate_id, created_at, status, validation_details_json
            ) VALUES (?, ?, 'pending', ?)
            """,
            (candidate_id, now, json_dump({"candidateTitle": candidate["title"]})),
        )
        validation_id = int(cursor.lastrowid)
        row = conn.execute(
            "SELECT * FROM rule_validations WHERE id = ?",
            (validation_id,),
        ).fetchone()
        return {
            "ok": True,
            "created": True,
            "validation": rule_validation_row(row),
            "candidate": candidate,
        }


def list_rule_validations(
    *,
    status: Optional[str] = None,
    candidate_id: Optional[int] = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    init_db()
    clauses = ["1 = 1"]
    params: list[Any] = []
    if status:
        clauses.append("rv.status = ?")
        params.append(_validate_status(status))
    if candidate_id is not None:
        clauses.append("rv.candidate_id = ?")
        params.append(int(candidate_id))
    bounded_limit = min(max(int(limit), 1), 500)
    params.append(bounded_limit)

    with connect() as conn:
        init_rule_validations_store(conn)
        init_rule_candidates_store(conn)
        rows = conn.execute(
            f"""
            SELECT rv.*, rc.title AS candidate_title, rc.candidate_type
            FROM rule_validations rv
            JOIN rule_candidates rc ON rc.id = rv.candidate_id
            WHERE {' AND '.join(clauses)}
            ORDER BY rv.created_at DESC, rv.id DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        validations: list[dict[str, Any]] = []
        for row in rows:
            payload = rule_validation_row(row)
            payload["candidateTitle"] = row["candidate_title"]
            payload["candidateType"] = row["candidate_type"]
            validations.append(payload)
        return validations


def _execute_validation(
    conn: sqlite3.Connection,
    *,
    candidate: dict[str, Any],
    validation_id: int,
) -> dict[str, Any]:
    baseline_signals = validation_signal_pool(conn)
    candidate_signals, simulation = simulate_candidate_signals(candidate, baseline_signals)
    baseline_metrics = compute_core_metrics(baseline_signals)
    candidate_metrics = compute_core_metrics(candidate_signals)
    deltas = compute_metric_deltas(baseline_metrics, candidate_metrics)
    confidence = compute_confidence_score(
        baseline_metrics,
        candidate_metrics,
        deltas=deltas,
    )
    summary = build_validation_summary(
        candidate,
        baseline_metrics,
        candidate_metrics,
        deltas=deltas,
        simulation=simulation,
    )
    details = {
        "candidateId": candidate["id"],
        "candidateType": candidate["candidateType"],
        "candidateTitle": candidate["title"],
        "simulation": simulation,
        "baselineMetrics": baseline_metrics,
        "candidateMetrics": candidate_metrics,
        "deltas": deltas,
    }
    completed_at = utc_now_iso()
    conn.execute(
        """
        UPDATE rule_validations
        SET completed_at = ?,
            status = 'completed',
            baseline_signal_count = ?,
            candidate_signal_count = ?,
            baseline_win_rate = ?,
            candidate_win_rate = ?,
            baseline_expectancy = ?,
            candidate_expectancy = ?,
            baseline_avg_signal_return = ?,
            candidate_avg_signal_return = ?,
            baseline_max_drawdown = ?,
            candidate_max_drawdown = ?,
            win_rate_delta = ?,
            expectancy_delta = ?,
            return_delta = ?,
            drawdown_delta = ?,
            confidence_score = ?,
            validation_summary = ?,
            validation_details_json = ?
        WHERE id = ?
        """,
        (
            completed_at,
            baseline_metrics["sample_size"],
            candidate_metrics["sample_size"],
            baseline_metrics["win_rate"],
            candidate_metrics["win_rate"],
            baseline_metrics["expectancy"],
            candidate_metrics["expectancy"],
            baseline_metrics["avg_return"],
            candidate_metrics["avg_return"],
            baseline_metrics["max_drawdown"],
            candidate_metrics["max_drawdown"],
            deltas["win_rate_delta"],
            deltas["expectancy_delta"],
            deltas["return_delta"],
            deltas["drawdown_delta"],
            confidence,
            summary,
            json_dump(details),
            validation_id,
        ),
    )
    row = conn.execute(
        "SELECT * FROM rule_validations WHERE id = ?",
        (validation_id,),
    ).fetchone()
    return rule_validation_row(row)


def run_rule_validation(
    candidate_id: int,
    *,
    validation_id: Optional[int] = None,
    create_if_missing: bool = True,
) -> dict[str, Any]:
    init_db()
    with connect() as conn:
        init_rule_candidates_store(conn)
        init_rule_validations_store(conn)
        candidate_row = _load_candidate(conn, candidate_id)
        if candidate_row is None:
            return {"ok": False, "message": f"Rule candidate {candidate_id} was not found."}

        candidate = rule_candidate_row(candidate_row)
        if candidate["status"] != "testing":
            return {
                "ok": False,
                "message": "Only rule candidates in testing status can be validated.",
                "candidate": candidate,
            }

        target_validation_id = validation_id
        if target_validation_id is None:
            pending = conn.execute(
                """
                SELECT id FROM rule_validations
                WHERE candidate_id = ? AND status = 'pending'
                ORDER BY id DESC
                LIMIT 1
                """,
                (candidate_id,),
            ).fetchone()
            if pending is not None:
                target_validation_id = int(pending["id"])
            elif create_if_missing:
                created = create_rule_validation(candidate_id, skip_duplicate=False)
                if not created.get("ok"):
                    return created
                target_validation_id = int(created["validation"]["id"])
            else:
                return {
                    "ok": False,
                    "message": "No pending validation exists for this candidate.",
                    "candidate": candidate,
                }

        conn.execute(
            "UPDATE rule_validations SET status = 'running' WHERE id = ?",
            (target_validation_id,),
        )

        try:
            validation = _execute_validation(
                conn,
                candidate=candidate,
                validation_id=int(target_validation_id),
            )
            return {
                "ok": True,
                "validation": validation,
                "candidate": candidate,
            }
        except Exception as exc:
            conn.execute(
                """
                UPDATE rule_validations
                SET status = 'failed',
                    completed_at = ?,
                    validation_summary = ?,
                    validation_details_json = ?
                WHERE id = ?
                """,
                (
                    utc_now_iso(),
                    f"Validation failed: {exc}",
                    json_dump({"error": str(exc)}),
                    target_validation_id,
                ),
            )
            failed = conn.execute(
                "SELECT * FROM rule_validations WHERE id = ?",
                (target_validation_id,),
            ).fetchone()
            return {
                "ok": False,
                "message": str(exc),
                "validation": rule_validation_row(failed),
                "candidate": candidate,
            }


def run_pending_validations(*, limit: int = 20) -> dict[str, Any]:
    init_db()
    bounded_limit = min(max(int(limit), 1), 100)
    with connect() as conn:
        init_rule_validations_store(conn)
        rows = conn.execute(
            """
            SELECT id, candidate_id
            FROM rule_validations
            WHERE status = 'pending'
            ORDER BY created_at ASC, id ASC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        pending = [(int(row["id"]), int(row["candidate_id"])) for row in rows]

    results: list[dict[str, Any]] = []
    completed = 0
    failed = 0
    for validation_id, candidate_id in pending:
        result = run_rule_validation(candidate_id, validation_id=validation_id)
        results.append(result)
        if result.get("ok"):
            completed += 1
        else:
            failed += 1

    return {
        "ok": failed == 0,
        "ran": len(results),
        "completed": completed,
        "failed": failed,
        "results": results,
    }


def generate_validations_from_testing_candidates(*, limit: int = 50) -> dict[str, Any]:
    from rule_candidates_engine import list_rule_candidates

    candidates = list_rule_candidates(status="testing", limit=limit)
    created_validations: list[dict[str, Any]] = []
    skipped = 0
    for candidate in candidates:
        result = create_rule_validation(int(candidate["id"]))
        if result.get("created"):
            created_validations.append(result["validation"])
        else:
            skipped += 1

    return {
        "ok": True,
        "candidatesProcessed": len(candidates),
        "created": len(created_validations),
        "skippedDuplicates": skipped,
        "validations": created_validations,
    }
