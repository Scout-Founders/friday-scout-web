#!/usr/bin/env python3
"""Local SQLite research memory for Scout sandbox scans."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from pathlib import Path
from typing import Any, Optional


SANDBOX_DIR = Path(__file__).resolve().parent
DB_PATH = SANDBOX_DIR / "scout_memory.db"


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_load(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def init_db() -> None:
    with connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS scan_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                universe_mode TEXT,
                pick_mode TEXT,
                timeout REAL,
                candidates_json TEXT,
                api_url TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE TABLE IF NOT EXISTS scan_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES scan_runs(id) ON DELETE CASCADE,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                scout_score REAL,
                bull_score REAL,
                bear_score REAL,
                net_direction REAL,
                final_direction TEXT,
                final_option_pick_json TEXT,
                gates_json TEXT,
                gate_explanations_json TEXT,
                failed_gates_json TEXT,
                failure_reasons_json TEXT,
                raw_fmp_inputs_json TEXT,
                raw_result_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_scan_results_ticker
                ON scan_results(ticker, timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_scan_results_timestamp
                ON scan_results(timestamp DESC);
            """
        )


def failed_gate_names(result: dict[str, Any]) -> list[str]:
    explanation = result.get("explanation") or {}
    failed = explanation.get("failed_gates")
    if isinstance(failed, list):
        return [str(item) for item in failed]
    return [
        str(gate.get("name") or gate.get("code") or gate.get("key"))
        for gate in result.get("gates", [])
        if gate.get("passed") is False
    ]


def failure_reasons(result: dict[str, Any]) -> list[str]:
    explanation = result.get("explanation") or {}
    gates = explanation.get("gates") if isinstance(explanation.get("gates"), list) else []
    reasons = [
        str(gate.get("explanation"))
        for gate in gates
        if gate.get("status") == "FAIL" and gate.get("explanation")
    ]
    return reasons


def compact_gate_results(result: dict[str, Any]) -> dict[str, bool]:
    return {
        str(gate.get("key") or gate.get("code") or gate.get("name")): bool(gate.get("passed"))
        for gate in result.get("gates", [])
    }


def save_scan_result(payload: dict[str, Any]) -> int:
    """Persist one completed dashboard run and all ticker rows."""
    init_db()
    timestamp = str(payload.get("runTimestamp") or "")
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scan_runs (
                timestamp, universe_mode, pick_mode, timeout, candidates_json, api_url
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                payload.get("universeMode"),
                payload.get("pickMode"),
                payload.get("timeout"),
                json_dump(payload.get("candidates") or []),
                payload.get("apiUrl"),
            ),
        )
        run_id = int(cursor.lastrowid)

        for result in payload.get("results", []):
            direction = result.get("directionBreakdown") or {}
            option_pick = result.get("optionPick")
            gates = compact_gate_results(result)
            explanations = result.get("explanation") or {}
            failed = failed_gate_names(result)
            reasons = failure_reasons(result)
            raw_fmp_inputs = {
                "raw_gate_response": result.get("raw"),
                "option_pick": option_pick,
                "direction_breakdown": direction,
            }
            conn.execute(
                """
                INSERT INTO scan_results (
                    run_id, timestamp, ticker, scout_score, bull_score, bear_score,
                    net_direction, final_direction, final_option_pick_json,
                    gates_json, gate_explanations_json, failed_gates_json,
                    failure_reasons_json, raw_fmp_inputs_json, raw_result_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    timestamp,
                    result.get("ticker"),
                    result.get("score"),
                    direction.get("bullConviction"),
                    direction.get("bearConviction"),
                    direction.get("netDirectionalEdge"),
                    direction.get("direction") or result.get("direction"),
                    json_dump(option_pick),
                    json_dump(gates),
                    json_dump(explanations),
                    json_dump(failed),
                    json_dump(reasons),
                    json_dump(raw_fmp_inputs),
                    json_dump(result),
                ),
            )
        return run_id


def row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "run_id": row["run_id"],
        "timestamp": row["timestamp"],
        "ticker": row["ticker"],
        "scout_score": row["scout_score"],
        "bull_score": row["bull_score"],
        "bear_score": row["bear_score"],
        "net_direction": row["net_direction"],
        "final_direction": row["final_direction"],
        "final_option_pick": json_load(row["final_option_pick_json"]),
        "gates": json_load(row["gates_json"]) or {},
        "gate_explanations": json_load(row["gate_explanations_json"]),
        "failed_gates": json_load(row["failed_gates_json"]) or [],
        "failure_reasons": json_load(row["failure_reasons_json"]) or [],
        "raw_fmp_inputs": json_load(row["raw_fmp_inputs_json"]),
        "raw_result": json_load(row["raw_result_json"]),
    }


def get_ticker_history(ticker: Optional[str] = None, limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        if ticker:
            rows = conn.execute(
                """
                SELECT * FROM scan_results
                WHERE ticker = ?
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (ticker.upper(), limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM scan_results
                ORDER BY timestamp DESC, id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [row_to_result(row) for row in rows]


def get_gate_statistics() -> list[dict[str, Any]]:
    history = get_ticker_history(limit=10000)
    stats: dict[str, dict[str, Any]] = {}
    for result in history:
        for gate, passed in (result.get("gates") or {}).items():
            item = stats.setdefault(gate, {"gate": gate, "pass": 0, "fail": 0, "total": 0})
            item["total"] += 1
            item["pass" if passed else "fail"] += 1

    output = []
    for item in stats.values():
        total = item["total"] or 1
        output.append({**item, "pass_rate": round(item["pass"] / total * 100, 1)})
    return sorted(output, key=lambda row: row["gate"])


def get_direction_accuracy() -> list[dict[str, Any]]:
    """Return local direction distribution until realized outcomes are added."""
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT final_direction, COUNT(*) AS total,
                   AVG(scout_score) AS avg_score,
                   AVG(net_direction) AS avg_net_direction
            FROM scan_results
            GROUP BY final_direction
            ORDER BY total DESC
            """
        ).fetchall()
    return [
        {
            "direction": row["final_direction"] or "Unknown",
            "total": row["total"],
            "avg_score": round(row["avg_score"] or 0, 1),
            "avg_net_direction": round(row["avg_net_direction"] or 0, 1),
            "note": "Realized win/loss accuracy is not available until outcomes are recorded.",
        }
        for row in rows
    ]


def get_top_gate_failures(limit: int = 10) -> list[dict[str, Any]]:
    history = get_ticker_history(limit=10000)
    counts: dict[str, dict[str, Any]] = {}
    for result in history:
        reasons = result.get("failure_reasons") or []
        for index, gate in enumerate(result.get("failed_gates") or []):
            item = counts.setdefault(gate, {"gate": gate, "count": 0, "examples": []})
            item["count"] += 1
            if len(item["examples"]) < 3 and index < len(reasons):
                item["examples"].append(reasons[index])
    return sorted(counts.values(), key=lambda row: row["count"], reverse=True)[:limit]


def get_option_pick_history(limit: int = 100) -> list[dict[str, Any]]:
    history = get_ticker_history(limit=limit)
    output = []
    for result in history:
        option = result.get("final_option_pick")
        if isinstance(option, dict) and any(
            option.get(key) not in (None, "") for key in ("contractSymbol", "strike", "expiration")
        ):
            output.append(
                {
                    "timestamp": result["timestamp"],
                    "ticker": result["ticker"],
                    "direction": result["final_direction"],
                    "option": option,
                }
            )
    return output


def export_csv() -> str:
    history = get_ticker_history(limit=100000)
    buffer = io.StringIO()
    writer = csv.DictWriter(
        buffer,
        fieldnames=[
            "timestamp",
            "ticker",
            "scout_score",
            "bull_score",
            "bear_score",
            "net_direction",
            "final_direction",
            "failed_gates",
            "failure_reasons",
            "final_option_pick",
        ],
    )
    writer.writeheader()
    for result in history:
        writer.writerow(
            {
                "timestamp": result["timestamp"],
                "ticker": result["ticker"],
                "scout_score": result["scout_score"],
                "bull_score": result["bull_score"],
                "bear_score": result["bear_score"],
                "net_direction": result["net_direction"],
                "final_direction": result["final_direction"],
                "failed_gates": "; ".join(result.get("failed_gates") or []),
                "failure_reasons": " | ".join(result.get("failure_reasons") or []),
                "final_option_pick": json_dump(result.get("final_option_pick")),
            }
        )
    return buffer.getvalue()
