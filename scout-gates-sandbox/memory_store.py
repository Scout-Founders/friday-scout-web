#!/usr/bin/env python3
"""Local SQLite research memory for Scout sandbox scans."""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional


SANDBOX_DIR = Path(__file__).resolve().parent
DB_PATH = SANDBOX_DIR / "scout_memory.db"
OUTCOME_COLUMNS = {
    "is_test_record": "INTEGER DEFAULT 0",
    "outcome": "TEXT",
    "entry_price": "REAL",
    "price_after_1d": "REAL",
    "price_after_3d": "REAL",
    "price_after_5d": "REAL",
    "price_after_10d": "REAL",
    "price_after_20d": "REAL",
    "return_1d": "REAL",
    "return_3d": "REAL",
    "return_5d": "REAL",
    "return_10d": "REAL",
    "return_20d": "REAL",
    "option_entry_price": "REAL",
    "option_price_after_1d": "REAL",
    "option_price_after_3d": "REAL",
    "option_price_after_5d": "REAL",
    "option_price_after_10d": "REAL",
    "option_return_1d": "REAL",
    "option_return_3d": "REAL",
    "option_return_5d": "REAL",
    "option_return_10d": "REAL",
    "stock_outcome_label": "TEXT",
    "option_outcome_label": "TEXT",
    "max_favorable_move": "REAL",
    "max_adverse_move": "REAL",
    "result_notes": "TEXT",
    "outcome_last_updated_at": "TEXT",
}


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
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(scan_results)").fetchall()
        }
        for column, column_type in OUTCOME_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE scan_results ADD COLUMN {column} {column_type}")


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


def create_outcome_test_record(
    ticker: Optional[str] = None,
    days_old: int = 30,
) -> dict[str, Any]:
    """Clone a saved result with an older timestamp for sandbox outcome testing."""
    init_db()
    synthetic_timestamp = (
        datetime.now(timezone.utc) - timedelta(days=max(days_old, 1))
    ).isoformat()

    with connect() as conn:
        source_filter = "COALESCE(is_test_record, 0) = 0"
        if ticker:
            source = conn.execute(
                f"""
                SELECT * FROM scan_results
                WHERE ticker = ? AND {source_filter}
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """,
                (ticker.upper(),),
            ).fetchone()
        else:
            source = conn.execute(
                f"""
                SELECT * FROM scan_results
                WHERE {source_filter}
                ORDER BY timestamp DESC, id DESC
                LIMIT 1
                """
            ).fetchone()

        if source is None:
            raise ValueError("No saved recommendation was found to copy.")

        run_cursor = conn.execute(
            """
            INSERT INTO scan_runs (
                timestamp, universe_mode, pick_mode, timeout, candidates_json, api_url
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                synthetic_timestamp,
                "sandbox_test",
                "outcome_test_copy",
                None,
                json_dump([source["ticker"]]),
                "local-test-copy",
            ),
        )
        run_id = int(run_cursor.lastrowid)

        raw_inputs = json_load(source["raw_fmp_inputs_json"]) or {}
        if not isinstance(raw_inputs, dict):
            raw_inputs = {"source_raw_fmp_inputs": raw_inputs}
        raw_inputs.update(
            {
                "sandbox_test_copy": True,
                "source_result_id": source["id"],
                "synthetic_timestamp": synthetic_timestamp,
            }
        )

        raw_result = json_load(source["raw_result_json"]) or {}
        if isinstance(raw_result, dict):
            raw_result = {
                **raw_result,
                "sandbox_test_copy": True,
                "source_result_id": source["id"],
            }

        conn.execute(
            """
            INSERT INTO scan_results (
                run_id, timestamp, ticker, scout_score, bull_score, bear_score,
                net_direction, final_direction, final_option_pick_json,
                gates_json, gate_explanations_json, failed_gates_json,
                failure_reasons_json, raw_fmp_inputs_json, raw_result_json,
                entry_price, option_entry_price, stock_outcome_label,
                option_outcome_label, result_notes, is_test_record, outcome,
                return_1d, return_3d, return_5d, return_10d, return_20d
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                synthetic_timestamp,
                source["ticker"],
                source["scout_score"],
                source["bull_score"],
                source["bear_score"],
                source["net_direction"],
                source["final_direction"],
                source["final_option_pick_json"],
                source["gates_json"],
                source["gate_explanations_json"],
                source["failed_gates_json"],
                source["failure_reasons_json"],
                json_dump(raw_inputs),
                json_dump(raw_result),
                source["entry_price"],
                source["option_entry_price"],
                "PENDING",
                "PENDING",
                (
                    f"SANDBOX TEST COPY of scan_results.id={source['id']} "
                    f"with synthetic timestamp {synthetic_timestamp}."
                ),
                1,
                "Pending",
                None,
                None,
                None,
                None,
                None,
            ),
        )
        result_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])

    return {
        "ok": True,
        "source_result_id": source["id"],
        "test_result_id": result_id,
        "ticker": source["ticker"],
        "timestamp": synthetic_timestamp,
        "days_old": days_old,
    }


def row_to_result(row: sqlite3.Row) -> dict[str, Any]:
    result = {
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
    for column in OUTCOME_COLUMNS:
        result[column] = row[column]
    return result


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
    """Return local direction outcome distribution when available."""
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT final_direction, COUNT(*) AS total,
                   AVG(scout_score) AS avg_score,
                   AVG(net_direction) AS avg_net_direction,
                   SUM(CASE WHEN stock_outcome_label = 'WIN' THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN stock_outcome_label = 'LOSS' THEN 1 ELSE 0 END) AS losses,
                   SUM(CASE WHEN stock_outcome_label = 'FLAT' THEN 1 ELSE 0 END) AS flats,
                   SUM(CASE WHEN stock_outcome_label = 'PENDING' OR stock_outcome_label IS NULL THEN 1 ELSE 0 END) AS pending
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
            "wins": row["wins"] or 0,
            "losses": row["losses"] or 0,
            "flats": row["flats"] or 0,
            "pending": row["pending"] or 0,
            "win_rate": round((row["wins"] or 0) / max((row["wins"] or 0) + (row["losses"] or 0), 1) * 100, 1),
        }
        for row in rows
    ]


def get_outcome_analytics() -> dict[str, Any]:
    init_db()
    history = get_ticker_history(limit=100000)
    completed = [row for row in history if row.get("stock_outcome_label") in ("WIN", "LOSS", "FLAT")]
    wins = [row for row in completed if row.get("stock_outcome_label") == "WIN"]
    bullish = [row for row in completed if row.get("final_direction") == "Bullish"]
    bearish = [row for row in completed if row.get("final_direction") == "Bearish"]

    def win_rate(rows: list[dict[str, Any]]) -> float:
        decided = [row for row in rows if row.get("stock_outcome_label") in ("WIN", "LOSS")]
        if not decided:
            return 0.0
        return round(
            sum(1 for row in decided if row.get("stock_outcome_label") == "WIN") / len(decided) * 100,
            1,
        )

    def avg(field: str) -> float:
        values = [row.get(field) for row in completed if isinstance(row.get(field), (int, float))]
        return round(sum(values) / len(values), 2) if values else 0.0

    with connect() as conn:
        best = conn.execute(
            """
            SELECT ticker, return_20d, return_10d, return_5d, return_1d
            FROM scan_results
            WHERE stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
            ORDER BY COALESCE(return_20d, return_10d, return_5d, return_1d) DESC
            LIMIT 1
            """
        ).fetchone()
        worst = conn.execute(
            """
            SELECT ticker, return_20d, return_10d, return_5d, return_1d
            FROM scan_results
            WHERE stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
            ORDER BY COALESCE(return_20d, return_10d, return_5d, return_1d) ASC
            LIMIT 1
            """
        ).fetchone()

    gate_stats: dict[str, dict[str, Any]] = {}
    for row in completed:
        gates = row.get("gates") or {}
        for gate, passed in gates.items():
            item = gate_stats.setdefault(gate, {"gate": gate, "wins": 0, "losses": 0, "total": 0})
            item["total"] += 1
            if row.get("stock_outcome_label") == "WIN":
                item["wins"] += 1
            elif row.get("stock_outcome_label") == "LOSS":
                item["losses"] += 1

    predictive = [
        {**item, "win_rate": round(item["wins"] / max(item["wins"] + item["losses"], 1) * 100, 1)}
        for item in gate_stats.values()
        if item["wins"] + item["losses"] > 0
    ]
    predictive.sort(key=lambda item: item["win_rate"], reverse=True)

    return {
        "total_completed": len(completed),
        "win_rate": win_rate(completed),
        "bullish_win_rate": win_rate(bullish),
        "bearish_win_rate": win_rate(bearish),
        "average_1d_return": avg("return_1d"),
        "average_5d_return": avg("return_5d"),
        "average_10d_return": avg("return_10d"),
        "best_performing_ticker": dict(best) if best else None,
        "worst_performing_ticker": dict(worst) if worst else None,
        "most_predictive_gate": predictive[0] if predictive else None,
        "least_predictive_gate": predictive[-1] if predictive else None,
        "pending": sum(1 for row in history if row.get("stock_outcome_label") in (None, "PENDING")),
    }


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
            "entry_price",
            "price_after_1d",
            "price_after_3d",
            "price_after_5d",
            "price_after_10d",
            "price_after_20d",
            "return_1d",
            "return_3d",
            "return_5d",
            "return_10d",
            "return_20d",
            "option_entry_price",
            "option_price_after_1d",
            "option_price_after_3d",
            "option_price_after_5d",
            "option_price_after_10d",
            "option_return_1d",
            "option_return_3d",
            "option_return_5d",
            "option_return_10d",
            "stock_outcome_label",
            "option_outcome_label",
            "max_favorable_move",
            "max_adverse_move",
            "result_notes",
            "outcome_last_updated_at",
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
                **{column: result.get(column) for column in OUTCOME_COLUMNS},
            }
        )
    return buffer.getvalue()
