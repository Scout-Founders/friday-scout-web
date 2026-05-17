#!/usr/bin/env python3
"""Local SQLite research memory for Scout sandbox scans."""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from engine_version import current_engine_version


SANDBOX_DIR = Path(__file__).resolve().parent
DB_PATH = SANDBOX_DIR / "scout_memory.db"
GATE_SNAPSHOT_SCHEMA_VERSION = 1
MEMORY_COLUMNS = {
    "engine_version": "TEXT",
    "gate_snapshot_json": "TEXT",
    "feature_vector_json": "TEXT",
}
SCAN_RUN_COLUMNS = {
    "universe_snapshot_json": "TEXT",
}
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
    "stock_outcome_label_1d": "TEXT",
    "stock_outcome_label_3d": "TEXT",
    "stock_outcome_label_5d": "TEXT",
    "stock_outcome_label_10d": "TEXT",
    "stock_outcome_label_20d": "TEXT",
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

            CREATE TABLE IF NOT EXISTS outcome_update_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                ticker TEXT NOT NULL,
                row_id INTEGER NOT NULL,
                old_values_json TEXT NOT NULL,
                new_values_json TEXT NOT NULL,
                source_endpoint TEXT,
                engine_version TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_outcome_audit_timestamp
                ON outcome_update_audit(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_outcome_audit_row
                ON outcome_update_audit(row_id, timestamp DESC);
            """
        )
        existing = {
            row["name"] for row in conn.execute("PRAGMA table_info(scan_results)").fetchall()
        }
        for column, column_type in OUTCOME_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE scan_results ADD COLUMN {column} {column_type}")
        for column, column_type in MEMORY_COLUMNS.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE scan_results ADD COLUMN {column} {column_type}")
        existing_runs = {
            row["name"] for row in conn.execute("PRAGMA table_info(scan_runs)").fetchall()
        }
        for column, column_type in SCAN_RUN_COLUMNS.items():
            if column not in existing_runs:
                conn.execute(f"ALTER TABLE scan_runs ADD COLUMN {column} {column_type}")


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


def gate_score_from_text(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    match = re.search(r"\bscore\s*:\s*(-?\d+(?:\.\d+)?)", str(value), re.I)
    if not match:
        return None
    return float(match.group(1))


def gate_engine_version(result: dict[str, Any], payload: dict[str, Any], engine_version: str) -> Any:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    for source in (raw, result, payload):
        for key in ("gate_engine_version", "gateEngineVersion", "engine_version", "version"):
            value = source.get(key) if isinstance(source, dict) else None
            if value not in (None, ""):
                return value
    return engine_version


def build_gate_snapshot(
    result: dict[str, Any],
    payload: dict[str, Any],
    engine_version: str,
) -> dict[str, Any]:
    explanations = result.get("explanation") or {}
    if not isinstance(explanations, dict):
        explanations = {}
    explanation_by_key = {
        str(gate.get("gate_key")): gate
        for gate in explanations.get("gates", [])
        if isinstance(gate, dict) and gate.get("gate_key")
    }
    snapshot_gates = []
    for gate in result.get("gates", []):
        if not isinstance(gate, dict):
            continue
        gate_key = str(gate.get("key") or gate.get("code") or gate.get("name") or "")
        explanation = explanation_by_key.get(gate_key, {})
        actual_value = explanation.get("actual_value")
        snapshot_gates.append(
            {
                "index": gate.get("index"),
                "key": gate_key,
                "code": gate.get("code"),
                "name": gate.get("name") or explanation.get("gate_name"),
                "passed": gate.get("passed") is True,
                "raw_score": gate_score_from_text(actual_value),
                "actual_value": actual_value,
                "required_value": explanation.get("required_value"),
                "notes": explanation.get("explanation"),
                "source_field": explanation.get("source_field"),
            }
        )
    return {
        "snapshot_schema_version": GATE_SNAPSHOT_SCHEMA_VERSION,
        "gate_engine_version": gate_engine_version(result, payload, engine_version),
        "engine_version": engine_version,
        "ticker": result.get("ticker"),
        "recommendation_timestamp": payload.get("runTimestamp"),
        "api_url": payload.get("apiUrl"),
        "gates": snapshot_gates,
    }


def first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def market_cap_bucket(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    try:
        market_cap = float(value)
    except (TypeError, ValueError):
        return None
    if market_cap >= 200_000_000_000:
        return "mega"
    if market_cap >= 10_000_000_000:
        return "large"
    if market_cap >= 2_000_000_000:
        return "mid"
    if market_cap >= 300_000_000:
        return "small"
    return "micro"


def build_feature_vector(
    result: dict[str, Any],
    engine_version: str,
    gate_snapshot: dict[str, Any],
) -> dict[str, Any]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    source = {**raw, **result}
    market_cap = first_present(source, ("market_cap", "marketCap", "mktCap"))
    gate_scores = {
        str(gate.get("key") or gate.get("code") or gate.get("name")): gate.get("raw_score")
        for gate in gate_snapshot.get("gates", [])
        if isinstance(gate, dict)
    }
    return {
        "ticker": result.get("ticker") or raw.get("ticker"),
        "sector": first_present(source, ("sector",)),
        "market_cap_bucket": market_cap_bucket(market_cap),
        "iv_rank": first_present(source, ("iv_rank", "ivRank", "iv_percentile", "ivPercentile")),
        "beta": first_present(source, ("beta",)),
        "atr": first_present(source, ("atr", "ATR")),
        "rsi": first_present(source, ("rsi", "RSI")),
        "volume_ratio": first_present(source, ("volume_ratio", "volumeRatio", "relative_volume", "relativeVolume")),
        "float": first_present(source, ("float", "share_float", "floatShares", "sharesFloat")),
        "earnings_days": first_present(source, ("earnings_days", "earningsDays", "days_to_earnings")),
        "short_interest": first_present(source, ("short_interest", "shortInterest", "short_percent_float", "shortPercentFloat")),
        "trend_state": first_present(source, ("trend_state", "trendState", "trend")),
        "gate_scores": gate_scores,
        "final_score": result.get("score") or raw.get("scout_score"),
        "direction": result.get("direction") or raw.get("direction"),
        "engine_version": engine_version,
    }


def rejection_reason(result: dict[str, Any]) -> str:
    explanation = result.get("explanation") if isinstance(result.get("explanation"), dict) else {}
    if explanation.get("summary"):
        return str(explanation["summary"])
    first_failed = result.get("firstFailedGate") if isinstance(result.get("firstFailedGate"), dict) else {}
    if first_failed:
        return f"Failed {first_failed.get('name') or first_failed.get('code') or 'gate'}."
    failed = failed_gate_names(result)
    if failed:
        return f"Failed gates: {', '.join(failed)}."
    return "Not selected as final recommendation."


def build_universe_snapshot(payload: dict[str, Any], scan_id: int) -> dict[str, Any]:
    scanned_tickers = [str(ticker) for ticker in payload.get("candidates") or []]
    results = [row for row in payload.get("results", []) if isinstance(row, dict)]
    rejected = [
        {
            "ticker": row.get("ticker"),
            "score": row.get("score"),
            "reason": rejection_reason(row),
        }
        for row in payload.get("rejected", [])
        if isinstance(row, dict)
    ]
    top_scores = [
        {
            "ticker": row.get("ticker"),
            "score": row.get("score"),
            "direction": row.get("direction"),
            "passed_all_gates": row.get("passedAllGates"),
        }
        for row in sorted(results, key=lambda item: item.get("score") or 0, reverse=True)[:20]
    ]
    return {
        "scan_id": scan_id,
        "timestamp": payload.get("runTimestamp"),
        "universe_size": len(scanned_tickers),
        "all_scanned_tickers": scanned_tickers,
        "all_rejected_tickers": rejected,
        "top_20_scores": top_scores,
    }


def save_scan_result(payload: dict[str, Any]) -> int:
    """Persist one completed dashboard run and all ticker rows."""
    init_db()
    timestamp = str(payload.get("runTimestamp") or "")
    engine_version = current_engine_version()
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
        universe_snapshot = build_universe_snapshot(payload, run_id)
        conn.execute(
            "UPDATE scan_runs SET universe_snapshot_json = ? WHERE id = ?",
            (json_dump(universe_snapshot), run_id),
        )

        for result in payload.get("results", []):
            direction = result.get("directionBreakdown") or {}
            option_pick = result.get("optionPick")
            gates = compact_gate_results(result)
            explanations = result.get("explanation") or {}
            failed = failed_gate_names(result)
            reasons = failure_reasons(result)
            gate_snapshot = build_gate_snapshot(result, payload, engine_version)
            feature_vector = build_feature_vector(result, engine_version, gate_snapshot)
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
                    failure_reasons_json, raw_fmp_inputs_json, raw_result_json,
                    gate_snapshot_json, feature_vector_json, engine_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json_dump(gate_snapshot),
                    json_dump(feature_vector),
                    engine_version,
                    datetime.now(timezone.utc).isoformat(),
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
        test_universe_snapshot = {
            "scan_id": run_id,
            "timestamp": synthetic_timestamp,
            "universe_size": 1,
            "all_scanned_tickers": [source["ticker"]],
            "all_rejected_tickers": [],
            "top_20_scores": [
                {
                    "ticker": source["ticker"],
                    "score": source["scout_score"],
                    "direction": source["final_direction"],
                    "passed_all_gates": None,
                }
            ],
        }
        conn.execute(
            "UPDATE scan_runs SET universe_snapshot_json = ? WHERE id = ?",
            (json_dump(test_universe_snapshot), run_id),
        )

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
                return_1d, return_3d, return_5d, return_10d, return_20d,
                gate_snapshot_json, feature_vector_json, engine_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                source["gate_snapshot_json"],
                source["feature_vector_json"],
                source["engine_version"] or current_engine_version(),
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
        "scan_id": row["run_id"],
        "timestamp": row["timestamp"],
        "created_at": row["created_at"],
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
    for column in MEMORY_COLUMNS:
        result[column.removesuffix("_json")] = (
            json_load(row[column]) if column.endswith("_json") else row[column]
        )
    return result


def log_outcome_update_audit(
    conn: sqlite3.Connection,
    timestamp: str,
    ticker: str,
    row_id: int,
    old_values: dict[str, Any],
    new_values: dict[str, Any],
    source_endpoint: str,
    engine_version: Optional[str],
) -> None:
    conn.execute(
        """
        INSERT INTO outcome_update_audit (
            timestamp, ticker, row_id, old_values_json, new_values_json,
            source_endpoint, engine_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            timestamp,
            ticker,
            row_id,
            json_dump(old_values),
            json_dump(new_values),
            source_endpoint,
            engine_version,
        ),
    )


def get_outcome_audit_log(limit: int = 100) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM outcome_update_audit
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    return [
        {
            "id": row["id"],
            "timestamp": row["timestamp"],
            "ticker": row["ticker"],
            "row_id": row["row_id"],
            "old_values": json_load(row["old_values_json"]) or {},
            "new_values": json_load(row["new_values_json"]) or {},
            "source_endpoint": row["source_endpoint"],
            "engine_version": row["engine_version"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]


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
