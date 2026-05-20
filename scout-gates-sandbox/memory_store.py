#!/usr/bin/env python3
"""Local SQLite research memory for Scout sandbox scans."""

from __future__ import annotations

import csv
import io
import json
import math
import re
import sqlite3
import sys
import time
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from dataclasses import dataclass
from typing import Any, Optional

from engine_version import current_engine_version, current_git_commit_hash
from feature_store import (
    build_feature_vector as build_institutional_feature_vector,
    get_feature_intelligence_summary,
    init_feature_store,
    refresh_feature_vector_labels,
    save_feature_vector,
)
from schema_registry import validate_schema
from pattern_engine import (
    get_pattern_intelligence,
    init_pattern_store,
    rebuild_pattern_intelligence,
)


SANDBOX_DIR = Path(__file__).resolve().parent
DB_PATH = SANDBOX_DIR / "scout_memory.db"
GATE_SNAPSHOT_SCHEMA_VERSION = 1
MEMORY_COLUMNS = {
    "engine_version": "TEXT",
    "gate_snapshot_json": "TEXT",
    "feature_vector_json": "TEXT",
    "explanation_json": "TEXT",
}
SCAN_RUN_COLUMNS = {
    "universe_snapshot_json": "TEXT",
    "engine_version": "TEXT",
    "git_commit_hash": "TEXT",
    "created_at_utc": "TEXT",
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
    "outcome_horizon_debug_json": "TEXT",
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


def parse_iso_datetime(value: Any) -> Optional[datetime]:
    if value in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


_DB_INITIALIZED = False


def init_db() -> None:
    global _DB_INITIALIZED
    if _DB_INITIALIZED:
        return
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

            CREATE TABLE IF NOT EXISTS institutional_audit_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                event_key TEXT NOT NULL UNIQUE,
                timestamp TEXT NOT NULL,
                scan_id INTEGER,
                recommendation_id INTEGER,
                ticker TEXT,
                engine_version TEXT,
                event_type TEXT NOT NULL,
                event_details_json TEXT NOT NULL,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            );

            CREATE INDEX IF NOT EXISTS idx_institutional_audit_timestamp
                ON institutional_audit_log(timestamp DESC);
            CREATE INDEX IF NOT EXISTS idx_institutional_audit_scan
                ON institutional_audit_log(scan_id, timestamp DESC);

            CREATE TABLE IF NOT EXISTS gate_attributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                gate_name TEXT NOT NULL,
                gate_score REAL,
                gate_weight REAL NOT NULL,
                contribution_pct REAL NOT NULL,
                gate_rank INTEGER NOT NULL,
                regime_tag TEXT,
                created_at_utc TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_gate_attributions_scan
                ON gate_attributions(scan_id, ticker, gate_rank);
            CREATE INDEX IF NOT EXISTS idx_gate_attributions_created
                ON gate_attributions(created_at_utc DESC);

            CREATE TABLE IF NOT EXISTS gate_alpha_metrics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                gate_name TEXT NOT NULL,
                sector TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                volatility_regime TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                losses INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                avg_return REAL NOT NULL,
                expectancy REAL NOT NULL,
                confidence_score REAL NOT NULL,
                last_updated_utc TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_gate_alpha_identity
                ON gate_alpha_metrics(gate_name, sector, market_regime, volatility_regime);
            CREATE INDEX IF NOT EXISTS idx_gate_alpha_expectancy
                ON gate_alpha_metrics(expectancy DESC, confidence_score DESC);

            CREATE TABLE IF NOT EXISTS regime_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                sector TEXT,
                market_trend TEXT NOT NULL,
                volatility_regime TEXT NOT NULL,
                liquidity_regime TEXT NOT NULL,
                earnings_proximity TEXT NOT NULL,
                macro_bias TEXT NOT NULL,
                timestamp TEXT NOT NULL
            );

            CREATE UNIQUE INDEX IF NOT EXISTS idx_regime_snapshots_identity
                ON regime_snapshots(scan_id, ticker);
            CREATE INDEX IF NOT EXISTS idx_regime_snapshots_regime
                ON regime_snapshots(market_trend, volatility_regime, liquidity_regime);

            CREATE TABLE IF NOT EXISTS gate_intelligence_metrics (
                gate_key TEXT PRIMARY KEY,
                gate_name TEXT NOT NULL,
                total_occurrences INTEGER NOT NULL,
                total_passes INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                win_count INTEGER NOT NULL,
                loss_count INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                avg_1d_return REAL,
                avg_3d_return REAL,
                avg_5d_return REAL,
                avg_10d_return REAL,
                avg_20d_return REAL,
                bullish_win_rate REAL NOT NULL,
                bearish_win_rate REAL NOT NULL,
                predictive_score REAL NOT NULL,
                confidence TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
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
        init_feature_store(conn)
        init_pattern_store(conn)
        ensure_performance_indexes(conn)
    _DB_INITIALIZED = True


PRIMARY_RETURN_SQL = "COALESCE(return_20d, return_10d, return_5d, return_3d, return_1d)"

MAX_HISTORY_PAGE_SIZE = 500
MAX_HISTORY_EXPORT_BATCH = 500


@dataclass
class HistoryFilters:
    ticker: Optional[str] = None
    outcome: Optional[str] = None
    direction: Optional[str] = None
    failed_gate: Optional[str] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None


# UI placeholder / autofill noise — never treat as an active filter.
_FAILED_GATE_PLACEHOLDER_VALUES = frozenset(
    {
        "threat scan",
        "e.g. threat scan",
        "e.g. specter",
        "gate name",
    }
)


def normalize_history_filters(filters: Optional[HistoryFilters]) -> HistoryFilters:
    """Drop empty or placeholder filter values so defaults show all rows."""
    filters = filters or HistoryFilters()
    failed_gate = (filters.failed_gate or "").strip().lower() or None
    if failed_gate in _FAILED_GATE_PLACEHOLDER_VALUES:
        failed_gate = None
    min_score = filters.min_score
    max_score = filters.max_score
    # min=0 max=0 (or max=0 alone) is almost always accidental and matches no rows.
    if max_score == 0:
        max_score = None
    if min_score == 0 and max_score in (0, None):
        min_score = None
    return HistoryFilters(
        ticker=(filters.ticker or "").strip().upper() or None,
        outcome=(filters.outcome or "").strip().upper() or None,
        direction=(filters.direction or "").strip() or None,
        failed_gate=failed_gate,
        min_score=min_score,
        max_score=max_score,
    )


def parse_history_filters(params: dict[str, list[str]]) -> HistoryFilters:
    def first(key: str) -> str:
        values = params.get(key) or []
        return str(values[0]).strip() if values else ""

    def optional_float(raw: str) -> Optional[float]:
        if not raw:
            return None
        try:
            return float(raw)
        except ValueError:
            return None

    ticker = first("ticker").upper() or None
    outcome = first("outcome").upper() or None
    direction = first("direction") or None
    failed_gate = first("failedGate").lower() or None
    min_score = optional_float(first("minScore"))
    max_score = optional_float(first("maxScore"))
    return normalize_history_filters(
        HistoryFilters(
            ticker=ticker,
            outcome=outcome if outcome else None,
            direction=direction if direction else None,
            failed_gate=failed_gate if failed_gate else None,
            min_score=min_score,
            max_score=max_score,
        )
    )


def history_where_clause(filters: Optional[HistoryFilters]) -> tuple[str, list[Any]]:
    filters = normalize_history_filters(filters)
    clauses: list[str] = []
    params: list[Any] = []

    if filters.ticker:
        clauses.append("ticker LIKE ?")
        params.append(f"%{filters.ticker}%")
    if filters.direction:
        clauses.append("final_direction = ?")
        params.append(filters.direction)
    if filters.min_score is not None:
        clauses.append("scout_score >= ?")
        params.append(filters.min_score)
    if filters.max_score is not None:
        clauses.append("scout_score <= ?")
        params.append(filters.max_score)
    if filters.failed_gate:
        clauses.append("LOWER(COALESCE(failed_gates_json, '')) LIKE ?")
        params.append(f"%{filters.failed_gate.lower()}%")
    if filters.outcome == "PENDING":
        clauses.append(
            f"(stock_outcome_label IS NULL OR stock_outcome_label = 'PENDING') "
            f"AND {PRIMARY_RETURN_SQL} IS NULL"
        )
    elif filters.outcome in ("WIN", "LOSS", "FLAT"):
        clauses.append("stock_outcome_label = ?")
        params.append(filters.outcome)

    if clauses:
        return " WHERE " + " AND ".join(clauses), params
    return "", []

HISTORY_SELECT_SQL = """
    id, run_id, timestamp, created_at, ticker, scout_score, bull_score, bear_score,
    net_direction, final_direction, engine_version,
    gates_json, failed_gates_json, failure_reasons_json, gate_snapshot_json,
    entry_price, price_after_1d, price_after_3d, price_after_5d, price_after_10d, price_after_20d,
    return_1d, return_3d, return_5d, return_10d, return_20d,
    stock_outcome_label, stock_outcome_label_1d, stock_outcome_label_3d, stock_outcome_label_5d,
    stock_outcome_label_10d, stock_outcome_label_20d,
    outcome_last_updated_at, result_notes, outcome_horizon_debug_json,
    option_entry_price, final_option_pick_json, max_favorable_move, max_adverse_move
"""


def ensure_performance_indexes(conn: sqlite3.Connection) -> None:
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_results_outcome_label "
        "ON scan_results(stock_outcome_label)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_results_final_direction "
        "ON scan_results(final_direction)"
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_scan_results_ticker_ts "
        "ON scan_results(ticker, timestamp DESC)"
    )


@contextmanager
def log_timing(timings: dict[str, float], key: str):
    start = time.perf_counter()
    yield
    timings[key] = round((time.perf_counter() - start) * 1000, 2)


def log_memory_load(timings: dict[str, float], label: str) -> None:
    print(f"[memory-load] {label} {json.dumps(timings, sort_keys=True)}", file=sys.stderr, flush=True)


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


def attribution_gate_score(gate: dict[str, Any]) -> float:
    raw_score = to_optional_float(gate.get("raw_score"))
    if raw_score is not None:
        return raw_score
    return 1.0 if gate.get("passed") is True else 0.35


def attribution_regime_tag(result: dict[str, Any], feature_vector: dict[str, Any]) -> str:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    source = {**raw, **result, **feature_vector}
    direction = str(first_present(source, ("direction", "final_direction")) or "neutral").lower()
    sector = str(first_present(source, ("sector",)) or "cross-sector")
    risk = str(first_present(source, ("risk_regime", "trend_state", "trend")) or "standard-regime")
    return f"{direction}:{sector}:{risk}"


def build_gate_attributions(
    result: dict[str, Any],
    gate_snapshot: dict[str, Any],
    feature_vector: dict[str, Any],
    scan_id: int,
    created_at_utc: str,
) -> list[dict[str, Any]]:
    gates = [gate for gate in gate_snapshot.get("gates", []) if isinstance(gate, dict)]
    weighted: list[dict[str, Any]] = []
    for gate in gates:
        gate_score = attribution_gate_score(gate)
        gate_weight = max(abs(gate_score), 0.01)
        weighted.append(
            {
                "scan_id": scan_id,
                "ticker": str(result.get("ticker") or gate_snapshot.get("ticker") or ""),
                "gate_name": str(gate.get("code") or gate.get("key") or gate.get("name") or "UNKNOWN").upper(),
                "gate_score": gate_score,
                "gate_weight": gate_weight,
                "regime_tag": attribution_regime_tag(result, feature_vector),
                "created_at_utc": created_at_utc,
            }
        )

    if not weighted:
        return []

    total_weight = sum(row["gate_weight"] for row in weighted) or 1.0
    running_pct = 0.0
    ranked = sorted(weighted, key=lambda row: row["gate_weight"], reverse=True)
    for index, row in enumerate(ranked, start=1):
        if index == len(ranked):
            contribution = round(max(0.0, 100.0 - running_pct), 2)
        else:
            contribution = round(row["gate_weight"] / total_weight * 100.0, 2)
            running_pct += contribution
        row["contribution_pct"] = contribution
        row["gate_rank"] = index
    return ranked


def save_gate_attributions(
    conn: sqlite3.Connection,
    attributions: list[dict[str, Any]],
) -> None:
    if not attributions:
        return
    conn.executemany(
        """
        INSERT INTO gate_attributions (
            scan_id, ticker, gate_name, gate_score, gate_weight,
            contribution_pct, gate_rank, regime_tag, created_at_utc
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                row["scan_id"],
                row["ticker"],
                row["gate_name"],
                row["gate_score"],
                row["gate_weight"],
                row["contribution_pct"],
                row["gate_rank"],
                row["regime_tag"],
                row["created_at_utc"],
            )
            for row in attributions
        ],
    )


def lower_text(value: Any) -> str:
    return str(value or "").strip().lower()


def classify_market_trend(source: dict[str, Any]) -> str:
    trend_text = lower_text(
        first_present(source, ("market_trend", "marketTrend", "spy_trend", "spyTrend", "trend_state", "trendState", "trend"))
    )
    if any(token in trend_text for token in ("bull", "uptrend", "risk_on", "positive")):
        return "bullish"
    if any(token in trend_text for token in ("bear", "downtrend", "risk_off", "negative")):
        return "bearish"
    edge = to_optional_float(first_present(source, ("netDirectionalEdge", "net_direction", "breadth_score", "breadthScore")))
    if edge is not None:
        if edge >= 10:
            return "bullish"
        if edge <= -10:
            return "bearish"
    return "neutral"


def classify_volatility_regime(source: dict[str, Any]) -> str:
    iv = to_optional_float(first_present(source, ("iv_percentile", "ivPercentile", "iv_rank", "ivRank")))
    if iv is not None:
        if iv >= 85:
            return "extreme"
        if iv >= 60:
            return "elevated"
        if iv <= 30:
            return "low"
        return "normal"
    vix = to_optional_float(first_present(source, ("vix_level", "vixLevel", "vix")))
    if vix is not None:
        if vix >= 30:
            return "extreme"
        if vix >= 20:
            return "elevated"
        if vix <= 15:
            return "low"
        return "normal"
    return "normal"


def classify_liquidity_regime(source: dict[str, Any]) -> str:
    relative_volume = to_optional_float(first_present(source, ("relative_volume", "relativeVolume", "volume_ratio", "volumeRatio")))
    if relative_volume is not None:
        if relative_volume >= 1.5:
            return "strong"
        if relative_volume <= 0.8:
            return "weak"
        return "normal"
    options_score = to_optional_float(first_present(source, ("options_volume_score", "optionsVolumeScore")))
    if options_score is not None:
        if options_score >= 70:
            return "strong"
        if options_score <= 35:
            return "weak"
    return "normal"


def classify_earnings_proximity(source: dict[str, Any]) -> str:
    days = to_optional_float(first_present(source, ("earnings_days_away", "earnings_days", "earningsDays", "days_to_earnings")))
    if days is None:
        return "unknown"
    if days <= 7:
        return "immediate"
    if days <= 30:
        return "near"
    return "clear"


def classify_macro_bias(source: dict[str, Any]) -> str:
    text = lower_text(first_present(source, ("macro_bias", "macroBias", "risk_regime", "riskRegime")))
    if any(token in text for token in ("bull", "risk_on", "positive", "expansion")):
        return "bullish"
    if any(token in text for token in ("bear", "risk_off", "negative", "contraction", "defensive")):
        return "bearish"
    breadth = to_optional_float(first_present(source, ("breadth_score", "breadthScore")))
    if breadth is not None:
        if breadth >= 60:
            return "bullish"
        if breadth <= 40:
            return "bearish"
    return "neutral"


def build_regime_snapshot(
    result: dict[str, Any],
    feature_vector: dict[str, Any],
    scan_id: int,
    timestamp: str,
) -> dict[str, Any]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    direction = result.get("directionBreakdown") if isinstance(result.get("directionBreakdown"), dict) else {}
    source = {**raw, **result, **direction, **feature_vector}
    return {
        "scan_id": scan_id,
        "ticker": str(result.get("ticker") or raw.get("ticker") or ""),
        "sector": first_present(source, ("sector", "sector_name", "sectorName")),
        "market_trend": classify_market_trend(source),
        "volatility_regime": classify_volatility_regime(source),
        "liquidity_regime": classify_liquidity_regime(source),
        "earnings_proximity": classify_earnings_proximity(source),
        "macro_bias": classify_macro_bias(source),
        "timestamp": timestamp,
    }


def save_regime_snapshot(conn: sqlite3.Connection, snapshot: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO regime_snapshots (
            scan_id, ticker, sector, market_trend, volatility_regime,
            liquidity_regime, earnings_proximity, macro_bias, timestamp
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            snapshot["scan_id"],
            snapshot["ticker"],
            snapshot.get("sector"),
            snapshot["market_trend"],
            snapshot["volatility_regime"],
            snapshot["liquidity_regime"],
            snapshot["earnings_proximity"],
            snapshot["macro_bias"],
            snapshot["timestamp"],
        ),
    )


def attribution_phrase(gate_name: str, gate_score: Optional[float]) -> str:
    weak = gate_score is not None and gate_score < 0.5
    descriptors = {
        "SENTINEL": ("supportive market filter", "weak market filter"),
        "ATLAS": ("strong core fundamentals", "deteriorating core fundamentals"),
        "ORACLE": ("constructive forward vision", "weak forward vision"),
        "PHANTOM": ("smart-money confirmation", "weak smart-money confirmation"),
        "CATALYST": ("active catalyst support", "thin catalyst support"),
        "SPECTER": ("contained threat profile", "elevated threat profile"),
        "MERIDIAN": ("sector tailwinds", "sector headwinds"),
        "AEGIS": ("earnings risk control", "earnings risk pressure"),
        "COMPASS": ("trend alignment", "trend deterioration"),
        "PULSE": ("volatility confirmation", "unstable volatility"),
        "SIGNAL": ("intel feed confirmation", "weak intel feed"),
        "CURRENT": ("flow momentum", "weak flow momentum"),
        "ARCHER": ("strategy fit", "limited strategy fit"),
        "FORTRESS": ("risk discipline", "risk pressure"),
    }
    strong_phrase, weak_phrase = descriptors.get(gate_name.upper(), (f"{gate_name} strength", f"weak {gate_name} signal"))
    return weak_phrase if weak else strong_phrase


def build_why_this_trade(
    ticker: str,
    direction: Optional[str],
    sector: Optional[str],
    attributions: list[dict[str, Any]],
) -> str:
    top = attributions[:3]
    phrases = [attribution_phrase(str(row.get("gate_name")), to_optional_float(row.get("gate_score"))) for row in top]
    direction_text = str(direction or "directional").lower()
    sector_text = str(sector or "cross-sector").lower()
    if not phrases:
        return f"Scout identified {direction_text} conditions for {ticker} from the saved gate stack."
    condition_scope = "cross-sector" if sector_text == "cross-sector" else f"{sector_text} sector"
    if len(phrases) == 1:
        driver_text = phrases[0]
    else:
        driver_text = f"{', '.join(phrases[:-1])}, and {phrases[-1]}"
    return f"Scout identified {direction_text} {condition_scope} conditions for {ticker} driven primarily by {driver_text}."


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


def to_optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def gate_names_by_state(gate_snapshot: dict[str, Any], passed: bool) -> list[str]:
    return [
        str(gate.get("name") or gate.get("key") or gate.get("code"))
        for gate in gate_snapshot.get("gates", [])
        if isinstance(gate, dict) and gate.get("passed") is passed
    ]


def risk_flags_for_result(result: dict[str, Any], failed: list[str]) -> list[str]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    source = {**raw, **result}
    flags = []
    earnings_days = to_optional_float(
        first_present(source, ("earnings_days", "earningsDays", "days_to_earnings"))
    )
    short_interest = to_optional_float(
        first_present(source, ("short_interest", "shortInterest", "short_percent_float", "shortPercentFloat"))
    )
    beta = to_optional_float(first_present(source, ("beta",)))
    net_edge = to_optional_float(
        first_present(result.get("directionBreakdown") or {}, ("netDirectionalEdge",))
    )
    if earnings_days is not None and earnings_days <= 7:
        flags.append("Earnings Soon")
    if short_interest is not None and short_interest >= 20:
        flags.append("High Short Interest")
    if beta is not None and beta >= 1.5:
        flags.append("High Beta")
    if net_edge is not None and abs(net_edge) < 10:
        flags.append("Low Directional Edge")
    if any("threat" in gate.lower() for gate in failed):
        flags.append("Threat Scan")
    return flags


def build_rank_maps(results: list[dict[str, Any]]) -> tuple[dict[str, int], dict[str, int]]:
    ranked = sorted(results, key=lambda item: item.get("score") or 0, reverse=True)
    universe_ranks = {
        str(result.get("ticker")): index
        for index, result in enumerate(ranked, start=1)
        if result.get("ticker")
    }
    sector_groups: dict[str, list[dict[str, Any]]] = {}
    for result in results:
        raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
        sector = first_present({**raw, **result}, ("sector",))
        if sector:
            sector_groups.setdefault(str(sector), []).append(result)
    sector_ranks = {}
    for sector_results in sector_groups.values():
        for index, result in enumerate(
            sorted(sector_results, key=lambda item: item.get("score") or 0, reverse=True),
            start=1,
        ):
            if result.get("ticker"):
                sector_ranks[str(result["ticker"])] = index
    return universe_ranks, sector_ranks


def build_decision_explanation(
    result: dict[str, Any],
    payload: dict[str, Any],
    gate_snapshot: dict[str, Any],
    engine_version: str,
    scan_id: int,
    universe_rank: Optional[int],
    sector_rank: Optional[int],
) -> dict[str, Any]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    source = {**raw, **result}
    passed = gate_names_by_state(gate_snapshot, True)
    failed = gate_names_by_state(gate_snapshot, False)
    score = result.get("score")
    if score is None:
        score = raw.get("scout_score")
    return {
        "passed_gates": passed,
        "failed_gates": failed,
        "total_passed": len(passed),
        "total_failed": len(failed),
        "score": score,
        "direction": result.get("direction") or raw.get("direction"),
        "universe_rank": universe_rank,
        "universe_size": len(payload.get("candidates") or payload.get("results") or []),
        "sector_rank": sector_rank,
        "sector_name": first_present(source, ("sector",)),
        "confidence_score": score,
        "risk_flags": risk_flags_for_result(result, failed),
        "engine_version": engine_version,
        "scan_id": scan_id,
    }


def save_scan_result(payload: dict[str, Any]) -> int:
    """Persist one completed dashboard run and all ticker rows."""
    init_db()
    timestamp = str(payload.get("runTimestamp") or "")
    engine_version = current_engine_version()
    git_commit_hash = current_git_commit_hash()
    created_at_utc = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cursor = conn.execute(
            """
            INSERT INTO scan_runs (
                timestamp, universe_mode, pick_mode, timeout, candidates_json, api_url,
                engine_version, git_commit_hash, created_at_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                timestamp,
                payload.get("universeMode"),
                payload.get("pickMode"),
                payload.get("timeout"),
                json_dump(payload.get("candidates") or []),
                payload.get("apiUrl"),
                engine_version,
                git_commit_hash,
                created_at_utc,
            ),
        )
        run_id = int(cursor.lastrowid)
        log_institutional_audit_event(
            conn,
            f"scan_created:{run_id}",
            timestamp or created_at_utc,
            run_id,
            None,
            None,
            engine_version,
            "scan_created",
            {
                "scan_id": run_id,
                "universe_mode": payload.get("universeMode"),
                "pick_mode": payload.get("pickMode"),
                "git_commit_hash": git_commit_hash,
                "created_at_utc": created_at_utc,
            },
        )
        universe_snapshot = build_universe_snapshot(payload, run_id)
        conn.execute(
            "UPDATE scan_runs SET universe_snapshot_json = ? WHERE id = ?",
            (json_dump(universe_snapshot), run_id),
        )

        results = [row for row in payload.get("results", []) if isinstance(row, dict)]
        universe_ranks, sector_ranks = build_rank_maps(results)
        for result in results:
            direction = result.get("directionBreakdown") or {}
            option_pick = result.get("optionPick")
            gates = compact_gate_results(result)
            explanations = result.get("explanation") or {}
            failed = failed_gate_names(result)
            reasons = failure_reasons(result)
            gate_snapshot = build_gate_snapshot(result, payload, engine_version)
            feature_vector = build_feature_vector(result, engine_version, gate_snapshot)
            ticker = str(result.get("ticker") or "")
            decision_explanation = build_decision_explanation(
                result,
                payload,
                gate_snapshot,
                engine_version,
                run_id,
                universe_ranks.get(ticker),
                sector_ranks.get(ticker),
            )
            raw_fmp_inputs = {
                "raw_gate_response": result.get("raw"),
                "option_pick": option_pick,
                "direction_breakdown": direction,
            }
            cursor = conn.execute(
                """
                INSERT INTO scan_results (
                    run_id, timestamp, ticker, scout_score, bull_score, bear_score,
                    net_direction, final_direction, final_option_pick_json,
                    gates_json, gate_explanations_json, failed_gates_json,
                    failure_reasons_json, raw_fmp_inputs_json, raw_result_json,
                    gate_snapshot_json, feature_vector_json, explanation_json,
                    engine_version, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    json_dump(decision_explanation),
                    engine_version,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            recommendation_id = int(cursor.lastrowid)
            log_institutional_audit_event(
                conn,
                f"recommendation_created:{recommendation_id}",
                timestamp or created_at_utc,
                run_id,
                recommendation_id,
                result.get("ticker"),
                engine_version,
                "recommendation_created",
                {
                    "row_id": recommendation_id,
                    "ticker": result.get("ticker"),
                    "score": result.get("score"),
                    "direction": direction.get("direction") or result.get("direction"),
                    "scan_id": run_id,
                },
            )
            save_gate_attributions(
                conn,
                build_gate_attributions(
                    result,
                    gate_snapshot,
                    feature_vector,
                    run_id,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            save_regime_snapshot(
                conn,
                build_regime_snapshot(
                    result,
                    feature_vector,
                    run_id,
                    timestamp or created_at_utc,
                ),
            )
            try:
                save_feature_vector(
                    conn,
                    build_institutional_feature_vector(
                        result,
                        payload,
                        gate_snapshot,
                        engine_version,
                        run_id,
                        recommendation_id,
                        sector_ranks.get(ticker),
                    ),
                )
            except Exception:
                pass
        rebuild_pattern_intelligence(conn)
        return run_id


def saved_scan_run_id(payload: dict[str, Any]) -> Optional[int]:
    init_db()
    timestamp = str(payload.get("runTimestamp") or "")
    if not timestamp:
        return None
    candidates_json = json_dump(payload.get("candidates") or [])
    with connect() as conn:
        row = conn.execute(
            """
            SELECT id
            FROM scan_runs
            WHERE timestamp = ?
              AND COALESCE(candidates_json, '') = ?
              AND COALESCE(api_url, '') = COALESCE(?, '')
              AND COALESCE(universe_mode, '') = COALESCE(?, '')
              AND COALESCE(pick_mode, '') = COALESCE(?, '')
            ORDER BY id ASC
            LIMIT 1
            """,
            (
                timestamp,
                candidates_json,
                payload.get("apiUrl"),
                payload.get("universeMode"),
                payload.get("pickMode"),
            ),
        ).fetchone()
    return int(row["id"]) if row else None


def save_scan_result_once(payload: dict[str, Any]) -> dict[str, Any]:
    """Persist a preview scan once, using its immutable run timestamp as the batch key."""
    results = [row for row in payload.get("results", []) if isinstance(row, dict)]
    if not results:
        raise ValueError("No completed recommendations were available to save.")
    existing_id = saved_scan_run_id(payload)
    if existing_id is not None:
        return {
            "ok": True,
            "alreadySaved": True,
            "memoryRunId": existing_id,
            "recommendationsSaved": len(results),
            "message": "Already saved.",
        }
    run_id = save_scan_result(payload)
    return {
        "ok": True,
        "alreadySaved": False,
        "memoryRunId": run_id,
        "recommendationsSaved": len(results),
        "message": f"Saved {len(results)} recommendations to Research Memory.",
    }


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
                gate_snapshot_json, feature_vector_json, explanation_json, engine_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                source["explanation_json"],
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


def create_gate_alpha_test_record() -> dict[str, Any]:
    """Create one marked sandbox outcome row linked to the latest attributed scan."""
    init_db()
    created_at = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        source = conn.execute(
            """
            SELECT sr.*
            FROM scan_results sr
            JOIN (
                SELECT scan_id, ticker
                FROM gate_attributions
                WHERE scan_id = (SELECT MAX(scan_id) FROM gate_attributions)
                ORDER BY gate_rank ASC
                LIMIT 1
            ) ga
              ON ga.scan_id = sr.run_id AND ga.ticker = sr.ticker
            WHERE COALESCE(sr.is_test_record, 0) = 0
            ORDER BY sr.scout_score DESC, sr.id ASC
            LIMIT 1
            """
        ).fetchone()
        if source is None:
            raise ValueError("No attributed scan was found. Run a new sandbox scan before creating Gate Alpha test data.")

        existing = conn.execute(
            """
            SELECT *
            FROM scan_results
            WHERE run_id = ?
              AND ticker = ?
              AND COALESCE(is_test_record, 0) = 1
              AND outcome = 'SANDBOX_GATE_ALPHA_TEST'
            ORDER BY id DESC
            LIMIT 1
            """,
            (source["run_id"], source["ticker"]),
        ).fetchone()
        if existing is not None:
            refresh_gate_alpha_metrics(conn)
            return {
                "ok": True,
                "created": False,
                "source_result_id": source["id"],
                "test_result_id": existing["id"],
                "scan_id": source["run_id"],
                "ticker": source["ticker"],
                "label": "Sandbox test data",
                "message": "Existing Gate Alpha sandbox test record reused.",
                "gate_alpha": get_gate_alpha_summary(conn),
            }

        raw_inputs = json_load(source["raw_fmp_inputs_json"]) or {}
        if not isinstance(raw_inputs, dict):
            raw_inputs = {"source_raw_fmp_inputs": raw_inputs}
        raw_inputs.update(
            {
                "sandbox_test_data": True,
                "gate_alpha_test_bridge": True,
                "source_result_id": source["id"],
                "source_scan_id": source["run_id"],
                "created_at_utc": created_at,
            }
        )

        raw_result = json_load(source["raw_result_json"]) or {}
        if isinstance(raw_result, dict):
            raw_result = {
                **raw_result,
                "sandbox_test_data": True,
                "gate_alpha_test_bridge": True,
                "source_result_id": source["id"],
                "source_scan_id": source["run_id"],
            }

        cursor = conn.execute(
            """
            INSERT INTO scan_results (
                run_id, timestamp, ticker, scout_score, bull_score, bear_score,
                net_direction, final_direction, final_option_pick_json,
                gates_json, gate_explanations_json, failed_gates_json,
                failure_reasons_json, raw_fmp_inputs_json, raw_result_json,
                entry_price, option_entry_price, stock_outcome_label,
                option_outcome_label, result_notes, is_test_record, outcome,
                return_1d, return_3d, return_5d, return_10d, return_20d,
                gate_snapshot_json, feature_vector_json, explanation_json, engine_version,
                outcome_last_updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                source["run_id"],
                created_at,
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
                "WIN",
                "PENDING",
                (
                    "Sandbox test data: Gate Alpha bridge completed outcome. "
                    f"Source scan_results.id={source['id']} scan_id={source['run_id']}. "
                    "Not real trade history."
                ),
                1,
                "SANDBOX_GATE_ALPHA_TEST",
                1.0,
                2.0,
                3.0,
                4.0,
                5.0,
                source["gate_snapshot_json"],
                source["feature_vector_json"],
                source["explanation_json"],
                source["engine_version"] or current_engine_version(),
                created_at,
            ),
        )
        test_result_id = int(cursor.lastrowid)
        log_outcome_update_audit(
            conn,
            created_at,
            source["ticker"],
            test_result_id,
            {},
            {
                "stock_outcome_label": "WIN",
                "return_20d": 5.0,
                "is_test_record": 1,
                "outcome": "SANDBOX_GATE_ALPHA_TEST",
                "label": "Sandbox test data",
            },
            "sandbox_gate_alpha_test_bridge",
            source["engine_version"] or current_engine_version(),
        )
        refresh_gate_alpha_metrics(conn)
        return {
            "ok": True,
            "created": True,
            "source_result_id": source["id"],
            "test_result_id": test_result_id,
            "scan_id": source["run_id"],
            "ticker": source["ticker"],
            "label": "Sandbox test data",
            "message": "Gate Alpha sandbox test record created.",
            "gate_alpha": get_gate_alpha_summary(conn),
        }


def row_to_history_list(row: sqlite3.Row) -> dict[str, Any]:
    """Lightweight row for Research Memory tables (skips large raw payloads)."""
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
        "engine_version": row["engine_version"],
        "gates": json_load(row["gates_json"]) or {},
        "failed_gates": json_load(row["failed_gates_json"]) or [],
        "failure_reasons": json_load(row["failure_reasons_json"]) or [],
        "gate_snapshot": json_load(row["gate_snapshot_json"]),
        "final_option_pick": json_load(row["final_option_pick_json"]),
    }
    for column in OUTCOME_COLUMNS:
        if column == "outcome_horizon_debug_json":
            continue
        if column in row.keys():
            result[column] = row[column]
    if "outcome_horizon_debug_json" in row.keys():
        result["outcome_horizon_debug"] = json_load(row["outcome_horizon_debug_json"])
    return result


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
    result["outcome_horizon_debug"] = json_load(row["outcome_horizon_debug_json"])
    for column in MEMORY_COLUMNS:
        result[column.removesuffix("_json")] = (
            json_load(row[column]) if column.endswith("_json") else row[column]
        )
    return result


def get_recommendation_explanation(scan_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        row = conn.execute(
            """
            SELECT * FROM scan_results
            WHERE id = ?
            LIMIT 1
            """,
            (scan_id,),
        ).fetchone()
        if row is None:
            row = conn.execute(
                """
                SELECT * FROM scan_results
                WHERE run_id = ?
                ORDER BY scout_score DESC, id ASC
                LIMIT 1
                """,
                (scan_id,),
            ).fetchone()
    if row is None:
        return None
    recommendation = row_to_result(row)
    return {
        "ok": True,
        "recommendation": {
            "id": recommendation["id"],
            "scan_id": recommendation["scan_id"],
            "ticker": recommendation["ticker"],
            "score": recommendation["scout_score"],
            "direction": recommendation["final_direction"],
            "engine_version": recommendation.get("engine_version"),
        },
        "explanation_json": recommendation.get("explanation"),
    }


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


def log_institutional_audit_event(
    conn: sqlite3.Connection,
    event_key: str,
    timestamp: str,
    scan_id: Optional[int],
    recommendation_id: Optional[int],
    ticker: Optional[str],
    engine_version: Optional[str],
    event_type: str,
    event_details: dict[str, Any],
) -> bool:
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO institutional_audit_log (
            event_key, timestamp, scan_id, recommendation_id, ticker,
            engine_version, event_type, event_details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_key,
            timestamp,
            scan_id,
            recommendation_id,
            ticker,
            engine_version,
            event_type,
            json_dump(event_details),
        ),
    )
    return cursor.rowcount > 0


def compact_gate_snapshot_from_row(row: sqlite3.Row) -> dict[str, Any]:
    gates = json_load(row["gates_json"]) or {}
    explanations = json_load(row["gate_explanations_json"]) or {}
    feature_vector = json_load(row["feature_vector_json"]) or {}
    gate_scores = feature_vector.get("gate_scores") if isinstance(feature_vector, dict) else {}
    explanation_gates = explanations.get("gates") if isinstance(explanations, dict) else []
    explanation_by_key = {
        str(gate.get("gate_key") or gate.get("key") or gate.get("name")): gate
        for gate in explanation_gates or []
        if isinstance(gate, dict)
    }
    snapshot_gates = []
    if isinstance(gates, dict):
        for index, (key, passed) in enumerate(gates.items(), start=1):
            explanation = explanation_by_key.get(str(key), {})
            raw_score = gate_scores.get(str(key)) if isinstance(gate_scores, dict) else None
            if raw_score is None:
                raw_score = gate_score_from_text(explanation.get("actual_value"))
            snapshot_gates.append(
                {
                    "index": index,
                    "key": str(key),
                    "code": None,
                    "name": explanation.get("gate_name") or str(key),
                    "passed": passed is True,
                    "raw_score": raw_score,
                    "actual_value": explanation.get("actual_value"),
                    "required_value": explanation.get("required_value"),
                    "notes": explanation.get("explanation"),
                    "source_field": explanation.get("source_field"),
                }
            )
    return {
        "snapshot_schema_version": GATE_SNAPSHOT_SCHEMA_VERSION,
        "backfilled": True,
        "scan_id": row["run_id"],
        "timestamp": row["timestamp"],
        "ticker": row["ticker"],
        "engine_version": row["engine_version"] or current_engine_version(),
        "gate_engine_version": row["engine_version"] or current_engine_version(),
        "gates": snapshot_gates,
    }


def backfilled_universe_snapshot(conn: sqlite3.Connection, run: sqlite3.Row) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT id, ticker, scout_score, final_direction, failed_gates_json, failure_reasons_json
        FROM scan_results
        WHERE run_id = ?
        ORDER BY scout_score DESC, id ASC
        """,
        (run["id"],),
    ).fetchall()
    candidates = json_load(run["candidates_json"]) or []
    scanned_tickers = [str(ticker) for ticker in candidates] or [row["ticker"] for row in rows]
    rejected = []
    for row in rows:
        failed = json_load(row["failed_gates_json"]) or []
        reasons = json_load(row["failure_reasons_json"]) or []
        if failed or reasons:
            rejected.append(
                {
                    "ticker": row["ticker"],
                    "score": row["scout_score"],
                    "reason": "; ".join(str(reason) for reason in reasons)
                    or f"Failed gates: {', '.join(str(gate) for gate in failed)}.",
                }
            )
    top_scores = [
        {
            "ticker": row["ticker"],
            "score": row["scout_score"],
            "direction": row["final_direction"],
            "passed_all_gates": not bool(json_load(row["failed_gates_json"]) or []),
        }
        for row in rows[:20]
    ]
    return {
        "scan_id": run["id"],
        "timestamp": run["timestamp"],
        "universe_size": len(scanned_tickers),
        "all_scanned_tickers": scanned_tickers,
        "all_rejected_tickers": rejected,
        "top_20_scores": top_scores,
        "backfilled": True,
    }


def run_horizon_backfill() -> dict[str, Any]:
    """Backfill immutable institutional records for historical sandbox memory."""
    init_db()
    now = datetime.now(timezone.utc).isoformat()
    result = {
        "ok": True,
        "gate_snapshots_backfilled": 0,
        "universe_snapshots_backfilled": 0,
        "audit_events_created": 0,
        "scans_checked": 0,
        "recommendations_checked": 0,
    }
    with connect() as conn:
        runs = conn.execute("SELECT * FROM scan_runs ORDER BY id ASC").fetchall()
        rows = conn.execute("SELECT * FROM scan_results ORDER BY id ASC").fetchall()
        result["scans_checked"] = len(runs)
        result["recommendations_checked"] = len(rows)

        for row in rows:
            if row["gate_snapshot_json"] in (None, ""):
                snapshot = compact_gate_snapshot_from_row(row)
                conn.execute(
                    "UPDATE scan_results SET gate_snapshot_json = ? WHERE id = ?",
                    (json_dump(snapshot), row["id"]),
                )
                result["gate_snapshots_backfilled"] += 1
                if log_institutional_audit_event(
                    conn,
                    f"snapshot_backfilled:gate:{row['id']}",
                    now,
                    int(row["run_id"]),
                    int(row["id"]),
                    row["ticker"],
                    row["engine_version"],
                    "snapshot_backfilled",
                    {"snapshot_type": "gate_snapshot", "row_id": row["id"]},
                ):
                    result["audit_events_created"] += 1

        for run in runs:
            if run["universe_snapshot_json"] in (None, ""):
                snapshot = backfilled_universe_snapshot(conn, run)
                conn.execute(
                    "UPDATE scan_runs SET universe_snapshot_json = ? WHERE id = ?",
                    (json_dump(snapshot), run["id"]),
                )
                result["universe_snapshots_backfilled"] += 1
                if log_institutional_audit_event(
                    conn,
                    f"snapshot_backfilled:universe:{run['id']}",
                    now,
                    int(run["id"]),
                    None,
                    None,
                    None,
                    "snapshot_backfilled",
                    {"snapshot_type": "universe_snapshot", "scan_id": run["id"]},
                ):
                    result["audit_events_created"] += 1

        for run in runs:
            if log_institutional_audit_event(
                conn,
                f"scan_created:{run['id']}",
                run["timestamp"] or now,
                int(run["id"]),
                None,
                None,
                None,
                "scan_created",
                {
                    "scan_id": run["id"],
                    "universe_mode": run["universe_mode"],
                    "pick_mode": run["pick_mode"],
                },
            ):
                result["audit_events_created"] += 1

        for row in rows:
            if log_institutional_audit_event(
                conn,
                f"recommendation_created:{row['id']}",
                row["timestamp"] or now,
                int(row["run_id"]),
                int(row["id"]),
                row["ticker"],
                row["engine_version"],
                "recommendation_created",
                {
                    "row_id": row["id"],
                    "ticker": row["ticker"],
                    "score": row["scout_score"],
                    "direction": row["final_direction"],
                },
            ):
                result["audit_events_created"] += 1
            if row["outcome_last_updated_at"] not in (None, "") or row["stock_outcome_label"] in ("WIN", "LOSS", "FLAT"):
                if log_institutional_audit_event(
                    conn,
                    f"outcome_updated:{row['id']}",
                    row["outcome_last_updated_at"] or row["timestamp"] or now,
                    int(row["run_id"]),
                    int(row["id"]),
                    row["ticker"],
                    row["engine_version"],
                    "outcome_updated",
                    {
                        "row_id": row["id"],
                        "ticker": row["ticker"],
                        "stock_outcome_label": row["stock_outcome_label"],
                        "return_1d": row["return_1d"],
                        "return_3d": row["return_3d"],
                        "return_5d": row["return_5d"],
                        "return_10d": row["return_10d"],
                        "return_20d": row["return_20d"],
                    },
                ):
                    result["audit_events_created"] += 1

        result["total_institutional_audit_events"] = count_rows(conn, "institutional_audit_log")
    return result


def run_feature_vector_backfill() -> dict[str, Any]:
    """Create feature-store rows for historical recommendations without duplicating vectors."""
    init_db()
    result = {
        "ok": True,
        "recommendations_checked": 0,
        "feature_vectors_created": 0,
        "feature_vectors_total": 0,
    }
    with connect() as conn:
        rows = conn.execute("SELECT * FROM scan_results ORDER BY run_id ASC, id ASC").fetchall()
        result["recommendations_checked"] = len(rows)
        by_run: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            by_run.setdefault(int(row["run_id"]), []).append(row)
        existing_ids = {
            int(row["recommendation_id"])
            for row in conn.execute(
                "SELECT recommendation_id FROM feature_vectors WHERE recommendation_id IS NOT NULL"
            ).fetchall()
        }
        for run_id, run_rows in by_run.items():
            rank_source = [
                {
                    "ticker": row["ticker"],
                    "score": row["scout_score"],
                    "raw": json_load(row["raw_result_json"]) if isinstance(json_load(row["raw_result_json"]), dict) else {},
                }
                for row in run_rows
            ]
            _, sector_ranks = build_rank_maps(rank_source)
            for row in run_rows:
                raw_result = json_load(row["raw_result_json"])
                if not isinstance(raw_result, dict):
                    raw_result = {"ticker": row["ticker"], "score": row["scout_score"], "direction": row["final_direction"]}
                raw_result.setdefault("ticker", row["ticker"])
                raw_result.setdefault("score", row["scout_score"])
                raw_result.setdefault("direction", row["final_direction"])
                gate_snapshot = json_load(row["gate_snapshot_json"]) or compact_gate_snapshot_from_row(row)
                payload = {"runTimestamp": row["timestamp"], "results": rank_source}
                save_feature_vector(
                    conn,
                    build_institutional_feature_vector(
                        raw_result,
                        payload,
                        gate_snapshot,
                        row["engine_version"] or current_engine_version(),
                        int(row["run_id"]),
                        int(row["id"]),
                        sector_ranks.get(str(row["ticker"])),
                    ),
                )
                if int(row["id"]) not in existing_ids:
                    result["feature_vectors_created"] += 1
        refresh_feature_vector_labels(conn)
        result["feature_vectors_total"] = count_rows(conn, "feature_vectors")
    return result


def rebuild_patterns() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        rebuild = rebuild_pattern_intelligence(conn)
        summary = get_pattern_intelligence(conn)
    return {"ok": True, "rebuild": rebuild, "pattern_intelligence": summary}


def rebuild_gate_alpha() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        rebuild = refresh_gate_alpha_metrics(conn)
        summary = get_gate_alpha_summary(conn)
    return {"ok": True, "rebuild": rebuild, "gate_alpha": summary}


def rebuild_regime_intelligence() -> dict[str, Any]:
    init_db()
    return {"ok": True, "regime_intelligence": get_regime_intelligence_summary()}


def average(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def standard_deviation(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    return math.sqrt(variance)


def final_outcome_return(row: sqlite3.Row) -> Optional[float]:
    for field in ("return_20d", "return_10d", "return_5d", "return_3d", "return_1d"):
        value = to_optional_float(row[field])
        if value is not None:
            return value
    return None


def alpha_feature_payload(row: sqlite3.Row) -> dict[str, Any]:
    raw_feature = json_load(row["raw_feature_json"])
    if isinstance(raw_feature, dict):
        return raw_feature
    raw_result = json_load(row["raw_result_json"])
    return raw_result if isinstance(raw_result, dict) else {}


def normalize_segment(value: Any, fallback: str = "UNKNOWN") -> str:
    text = str(value or "").strip()
    return text.upper() if text else fallback


def volatility_regime_from_features(row: sqlite3.Row, feature: dict[str, Any]) -> str:
    iv_percentile = to_optional_float(row["iv_percentile"])
    if iv_percentile is None:
        iv_percentile = to_optional_float(first_present(feature, ("iv_percentile", "ivPercentile", "iv_rank", "ivRank")))
    if iv_percentile is not None:
        if iv_percentile >= 70:
            return "HIGH_VOL"
        if iv_percentile <= 30:
            return "LOW_VOL"
        return "NORMAL_VOL"

    vix_level = to_optional_float(row["vix_level"])
    if vix_level is None:
        vix_level = to_optional_float(first_present(feature, ("vix_level", "vixLevel", "vix")))
    if vix_level is not None:
        if vix_level >= 25:
            return "HIGH_VOL"
        if vix_level <= 15:
            return "LOW_VOL"
        return "NORMAL_VOL"
    return "UNKNOWN"


def alpha_segments(sector: str, market_regime: str, volatility_regime: str) -> list[tuple[str, str, str]]:
    segments = [
        ("GLOBAL", "GLOBAL", "GLOBAL"),
        (sector, "GLOBAL", "GLOBAL"),
        ("GLOBAL", market_regime, "GLOBAL"),
        ("GLOBAL", "GLOBAL", volatility_regime),
    ]
    full_segment = (sector, market_regime, volatility_regime)
    if full_segment not in segments:
        segments.append(full_segment)
    return segments


def gate_alpha_confidence(sample_count: int, win_rate: float, returns: list[float]) -> float:
    if sample_count <= 0:
        return 0.0
    sample_score = min(math.log1p(sample_count) / math.log1p(50), 1.0)
    consistency_score = abs((win_rate / 100.0) - 0.5) * 2
    stability_score = 1.0 / (1.0 + (standard_deviation(returns) / 10.0))
    return round((0.45 * sample_score + 0.35 * consistency_score + 0.20 * stability_score) * 100, 2)


def refresh_gate_alpha_metrics(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT sr.id AS recommendation_id, sr.run_id, sr.ticker, sr.raw_result_json,
               sr.return_1d, sr.return_3d, sr.return_5d, sr.return_10d, sr.return_20d,
               fv.sector_name, fv.risk_regime, fv.iv_percentile, fv.vix_level,
               fv.raw_feature_json
        FROM scan_results sr
        JOIN gate_attributions ga
          ON ga.scan_id = sr.run_id AND ga.ticker = sr.ticker
        LEFT JOIN feature_vectors fv
          ON fv.recommendation_id = sr.id
        WHERE sr.stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
        GROUP BY sr.id
        """
    ).fetchall()
    metrics: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    completed_rows = 0
    attribution_rows = 0
    for row in rows:
        outcome_return = final_outcome_return(row)
        if outcome_return is None:
            continue
        completed_rows += 1
        feature = alpha_feature_payload(row)
        sector = normalize_segment(row["sector_name"] or first_present(feature, ("sector", "sector_name", "sectorName")))
        market_regime = normalize_segment(row["risk_regime"] or first_present(feature, ("risk_regime", "riskRegime", "market_regime", "marketRegime")))
        volatility_regime = volatility_regime_from_features(row, feature)
        gates = conn.execute(
            """
            SELECT gate_name
            FROM gate_attributions
            WHERE scan_id = ? AND ticker = ?
            ORDER BY gate_rank ASC
            """,
            (row["run_id"], row["ticker"]),
        ).fetchall()
        for gate in gates:
            attribution_rows += 1
            gate_name = normalize_segment(gate["gate_name"])
            for segment in alpha_segments(sector, market_regime, volatility_regime):
                key = (gate_name, *segment)
                item = metrics.setdefault(
                    key,
                    {"returns": [], "wins": 0, "losses": 0},
                )
                item["returns"].append(outcome_return)
                if outcome_return > 0:
                    item["wins"] += 1
                else:
                    item["losses"] += 1

    updated_at = datetime.now(timezone.utc).isoformat()
    conn.execute("DELETE FROM gate_alpha_metrics")
    for (gate_name, sector, market_regime, volatility_regime), item in metrics.items():
        sample_count = len(item["returns"])
        wins = item["wins"]
        losses = item["losses"]
        win_rate = round(wins / sample_count * 100, 2) if sample_count else 0.0
        avg_return = average(item["returns"]) or 0.0
        expectancy = round(avg_return * (win_rate / 100.0), 4)
        confidence = gate_alpha_confidence(sample_count, win_rate, item["returns"])
        conn.execute(
            """
            INSERT INTO gate_alpha_metrics (
                gate_name, sector, market_regime, volatility_regime,
                sample_count, wins, losses, win_rate, avg_return,
                expectancy, confidence_score, last_updated_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                gate_name,
                sector,
                market_regime,
                volatility_regime,
                sample_count,
                wins,
                losses,
                win_rate,
                avg_return,
                expectancy,
                confidence,
                updated_at,
            ),
        )
    return {
        "completed_outcomes_checked": completed_rows,
        "attribution_rows_checked": attribution_rows,
        "metric_rows": len(metrics),
        "last_updated_utc": updated_at,
    }


def gate_alpha_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "gate_name": row["gate_name"],
        "sector": row["sector"],
        "market_regime": row["market_regime"],
        "volatility_regime": row["volatility_regime"],
        "sample_count": row["sample_count"],
        "wins": row["wins"],
        "losses": row["losses"],
        "win_rate": row["win_rate"],
        "avg_return": row["avg_return"],
        "expectancy": row["expectancy"],
        "confidence_score": row["confidence_score"],
        "last_updated_utc": row["last_updated_utc"],
    }


def get_gate_alpha_summary(conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    if conn is None:
        init_db()
    should_close = conn is None
    active_conn = conn or connect()
    try:
        global_filter = "sector = 'GLOBAL' AND market_regime = 'GLOBAL' AND volatility_regime = 'GLOBAL'"
        total_metrics = count_rows(active_conn, "gate_alpha_metrics")
        total_global_samples = int(
            active_conn.execute(
                f"SELECT COALESCE(SUM(sample_count), 0) FROM gate_alpha_metrics WHERE {global_filter}"
            ).fetchone()[0]
            or 0
        )
        top = active_conn.execute(
            f"SELECT * FROM gate_alpha_metrics WHERE {global_filter} ORDER BY expectancy DESC, sample_count DESC LIMIT 1"
        ).fetchone()
        worst = active_conn.execute(
            f"SELECT * FROM gate_alpha_metrics WHERE {global_filter} ORDER BY expectancy ASC, sample_count DESC LIMIT 1"
        ).fetchone()
        confidence = active_conn.execute(
            f"SELECT * FROM gate_alpha_metrics WHERE {global_filter} ORDER BY confidence_score DESC, sample_count DESC LIMIT 1"
        ).fetchone()
        most_used = active_conn.execute(
            f"SELECT * FROM gate_alpha_metrics WHERE {global_filter} ORDER BY sample_count DESC, confidence_score DESC LIMIT 1"
        ).fetchone()
        leaderboard = [
            gate_alpha_row(row)
            for row in active_conn.execute(
                f"""
                SELECT * FROM gate_alpha_metrics
                WHERE {global_filter}
                ORDER BY expectancy DESC, confidence_score DESC, sample_count DESC
                LIMIT 20
                """
            ).fetchall()
        ]
        return {
            "ok": True,
            "total_metric_rows": total_metrics,
            "total_global_samples": total_global_samples,
            "top_performing_gate": gate_alpha_row(top) if top else None,
            "worst_performing_gate": gate_alpha_row(worst) if worst else None,
            "highest_confidence_gate": gate_alpha_row(confidence) if confidence else None,
            "most_used_gate": gate_alpha_row(most_used) if most_used else None,
            "leaderboard": leaderboard,
        }
    finally:
        if should_close:
            active_conn.close()


def regime_key(row: sqlite3.Row) -> str:
    return " | ".join(
        [
            f"trend:{row['market_trend']}",
            f"vol:{row['volatility_regime']}",
            f"liq:{row['liquidity_regime']}",
            f"macro:{row['macro_bias']}",
        ]
    )


def regime_summary_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "regime": regime_key(row),
        "market_trend": row["market_trend"],
        "volatility_regime": row["volatility_regime"],
        "liquidity_regime": row["liquidity_regime"],
        "macro_bias": row["macro_bias"],
        "sample_size": row["sample_size"],
        "wins": row["wins"],
        "losses": row["losses"],
        "win_rate": row["win_rate"],
        "avg_return": row["avg_return"],
        "expectancy": row["expectancy"],
    }


def get_regime_intelligence_summary(conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    if conn is None:
        init_db()
    should_close = conn is None
    active_conn = conn or connect()
    try:
        query = """
            SELECT rs.market_trend, rs.volatility_regime, rs.liquidity_regime, rs.macro_bias,
                   COUNT(*) AS sample_size,
                   SUM(CASE WHEN COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) > 0 THEN 1 ELSE 0 END) AS wins,
                   SUM(CASE WHEN COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) <= 0 THEN 1 ELSE 0 END) AS losses,
                   ROUND(
                       SUM(CASE WHEN COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) > 0 THEN 1 ELSE 0 END)
                       * 100.0 / COUNT(*),
                       2
                   ) AS win_rate,
                   ROUND(AVG(COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d)), 4) AS avg_return,
                   ROUND(
                       AVG(COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d))
                       * (
                           SUM(CASE WHEN COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) > 0 THEN 1 ELSE 0 END)
                           * 1.0 / COUNT(*)
                       ),
                       4
                   ) AS expectancy
            FROM regime_snapshots rs
            JOIN scan_results sr
              ON sr.run_id = rs.scan_id AND sr.ticker = rs.ticker
            WHERE sr.stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
              AND COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) IS NOT NULL
            GROUP BY rs.market_trend, rs.volatility_regime, rs.liquidity_regime, rs.macro_bias
        """
        rows = active_conn.execute(query).fetchall()
        sorted_by_expectancy = sorted(rows, key=lambda row: (row["expectancy"] or 0, row["sample_size"]), reverse=True)
        strongest_bullish = next((row for row in sorted_by_expectancy if row["market_trend"] == "bullish"), None)
        strongest_bearish = next((row for row in sorted_by_expectancy if row["market_trend"] == "bearish"), None)
        highest = sorted_by_expectancy[0] if sorted_by_expectancy else None
        weakest = sorted(rows, key=lambda row: (row["expectancy"] or 0, -row["sample_size"]))[0] if rows else None
        return {
            "ok": True,
            "total_regime_snapshots": count_rows(active_conn, "regime_snapshots"),
            "completed_regime_samples": sum(int(row["sample_size"] or 0) for row in rows),
            "strongest_bullish_regime": regime_summary_row(strongest_bullish) if strongest_bullish else None,
            "strongest_bearish_regime": regime_summary_row(strongest_bearish) if strongest_bearish else None,
            "highest_expectancy_regime": regime_summary_row(highest) if highest else None,
            "weakest_regime": regime_summary_row(weakest) if weakest else None,
            "leaderboard": [regime_summary_row(row) for row in sorted_by_expectancy[:12]],
        }
    finally:
        if should_close:
            active_conn.close()


def completed_gate_entries(row: sqlite3.Row) -> list[dict[str, Any]]:
    snapshot = json_load(row["gate_snapshot_json"])
    if isinstance(snapshot, dict) and isinstance(snapshot.get("gates"), list):
        return [
            {
                "key": str(gate.get("key") or gate.get("code") or gate.get("name")),
                "name": str(gate.get("name") or gate.get("key") or gate.get("code")),
                "passed": gate.get("passed") is True,
            }
            for gate in snapshot["gates"]
            if isinstance(gate, dict)
        ]

    gates = json_load(row["gates_json"]) or {}
    if not isinstance(gates, dict):
        return []
    return [
        {
            "key": str(key),
            "name": str(key),
            "passed": value is True,
        }
        for key, value in gates.items()
    ]


def confidence_indicator(total_occurrences: int, predictive_score: float) -> str:
    if total_occurrences >= 30 and predictive_score >= 70:
        return "High"
    if total_occurrences >= 10 and predictive_score >= 50:
        return "Medium"
    return "Low"


def predictive_score_for_gate(item: dict[str, Any]) -> float:
    total = item["total_occurrences"]
    decided = item["win_count"] + item["loss_count"]
    win_rate = item["win_count"] / decided if decided else 0.0
    sample_score = min(math.log1p(total) / math.log1p(50), 1.0)
    avg_returns = [
        abs(value)
        for value in (
            average(item["return_1d"]),
            average(item["return_3d"]),
            average(item["return_5d"]),
            average(item["return_10d"]),
            average(item["return_20d"]),
        )
        if value is not None
    ]
    avg_return_score = sum(avg_returns) / len(avg_returns) if avg_returns else 0.0
    return_score = min(avg_return_score / 20.0, 1.0)
    variance_values = (
        item["return_1d"]
        + item["return_3d"]
        + item["return_5d"]
        + item["return_10d"]
        + item["return_20d"]
    )
    stability_score = 1.0 / (1.0 + (standard_deviation(variance_values) / 10.0))
    score = (
        0.30 * sample_score
        + 0.35 * win_rate
        + 0.20 * return_score
        + 0.15 * stability_score
    )
    return round(score * 100, 2)


def refresh_gate_intelligence_metrics(conn: Optional[sqlite3.Connection] = None) -> list[dict[str, Any]]:
    owns_connection = conn is None
    if owns_connection:
        init_db()
    active_conn = conn or connect()
    try:
        rows = active_conn.execute(
            """
            SELECT *
            FROM scan_results
            WHERE stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
            """
        ).fetchall()
        metrics: dict[str, dict[str, Any]] = {}
        for row in rows:
            outcome = row["stock_outcome_label"]
            direction = row["final_direction"] or ""
            for gate in completed_gate_entries(row):
                key = gate["key"]
                item = metrics.setdefault(
                    key,
                    {
                        "gate_key": key,
                        "gate_name": gate["name"],
                        "total_occurrences": 0,
                        "total_passes": 0,
                        "total_failures": 0,
                        "win_count": 0,
                        "loss_count": 0,
                        "bullish_wins": 0,
                        "bullish_losses": 0,
                        "bearish_wins": 0,
                        "bearish_losses": 0,
                        "return_1d": [],
                        "return_3d": [],
                        "return_5d": [],
                        "return_10d": [],
                        "return_20d": [],
                    },
                )
                item["total_occurrences"] += 1
                if gate["passed"]:
                    item["total_passes"] += 1
                else:
                    item["total_failures"] += 1
                if outcome == "WIN":
                    item["win_count"] += 1
                    if direction == "Bullish":
                        item["bullish_wins"] += 1
                    elif direction == "Bearish":
                        item["bearish_wins"] += 1
                elif outcome == "LOSS":
                    item["loss_count"] += 1
                    if direction == "Bullish":
                        item["bullish_losses"] += 1
                    elif direction == "Bearish":
                        item["bearish_losses"] += 1
                for horizon in (1, 3, 5, 10, 20):
                    value = row[f"return_{horizon}d"]
                    if isinstance(value, (int, float)):
                        item[f"return_{horizon}d"].append(float(value))

        updated_at = datetime.now(timezone.utc).isoformat()
        active_conn.execute("DELETE FROM gate_intelligence_metrics")
        output = []
        for item in metrics.values():
            decided = item["win_count"] + item["loss_count"]
            bullish_decided = item["bullish_wins"] + item["bullish_losses"]
            bearish_decided = item["bearish_wins"] + item["bearish_losses"]
            predictive_score = predictive_score_for_gate(item)
            row = {
                "gate_key": item["gate_key"],
                "gate_name": item["gate_name"],
                "total_occurrences": item["total_occurrences"],
                "total_passes": item["total_passes"],
                "total_failures": item["total_failures"],
                "win_count": item["win_count"],
                "loss_count": item["loss_count"],
                "win_rate": round(item["win_count"] / decided * 100, 2) if decided else 0.0,
                "avg_1d_return": average(item["return_1d"]),
                "avg_3d_return": average(item["return_3d"]),
                "avg_5d_return": average(item["return_5d"]),
                "avg_10d_return": average(item["return_10d"]),
                "avg_20d_return": average(item["return_20d"]),
                "bullish_win_rate": (
                    round(item["bullish_wins"] / bullish_decided * 100, 2)
                    if bullish_decided
                    else 0.0
                ),
                "bearish_win_rate": (
                    round(item["bearish_wins"] / bearish_decided * 100, 2)
                    if bearish_decided
                    else 0.0
                ),
                "predictive_score": predictive_score,
                "confidence": confidence_indicator(item["total_occurrences"], predictive_score),
                "updated_at": updated_at,
            }
            active_conn.execute(
                """
                INSERT INTO gate_intelligence_metrics (
                    gate_key, gate_name, total_occurrences, total_passes, total_failures,
                    win_count, loss_count, win_rate, avg_1d_return, avg_3d_return,
                    avg_5d_return, avg_10d_return, avg_20d_return, bullish_win_rate,
                    bearish_win_rate, predictive_score, confidence, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["gate_key"],
                    row["gate_name"],
                    row["total_occurrences"],
                    row["total_passes"],
                    row["total_failures"],
                    row["win_count"],
                    row["loss_count"],
                    row["win_rate"],
                    row["avg_1d_return"],
                    row["avg_3d_return"],
                    row["avg_5d_return"],
                    row["avg_10d_return"],
                    row["avg_20d_return"],
                    row["bullish_win_rate"],
                    row["bearish_win_rate"],
                    row["predictive_score"],
                    row["confidence"],
                    row["updated_at"],
                ),
            )
            output.append(row)
        if owns_connection:
            active_conn.commit()
        return sorted(output, key=lambda row: row["predictive_score"], reverse=True)
    finally:
        if owns_connection:
            active_conn.close()


def get_gate_intelligence_metrics() -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM gate_intelligence_metrics
            ORDER BY predictive_score DESC, total_occurrences DESC, gate_name ASC
            """
        ).fetchall()
    return [dict(row) for row in rows]


def count_rows(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] or 0)


def freshness_status(value: Optional[str], stale_days: int = 7) -> str:
    parsed = parse_iso_datetime(value)
    if parsed is None:
        return "NOT READY"
    age = datetime.now(timezone.utc) - parsed
    return "READY" if age <= timedelta(days=stale_days) else "IN PROGRESS"


def readiness_status(ready: bool, in_progress: bool = False) -> str:
    if ready:
        return "READY"
    if in_progress:
        return "IN PROGRESS"
    return "NOT READY"


def health_status(online: bool, warning: bool = False) -> str:
    if online and not warning:
        return "ONLINE"
    if online or warning:
        return "WARNING"
    return "OFFLINE"


def progress_percent(value: float) -> int:
    return max(0, min(100, int(round(value))))


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    if not table_exists(conn, table):
        return set()
    return {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def audit_item(category: str, name: str, status: str, message: str) -> dict[str, str]:
    return {
        "category": category,
        "name": name,
        "status": status,
        "message": message,
    }


def anomaly_item(
    anomaly_type: str,
    message: str,
    scan_id: Optional[int],
    ticker: Optional[str],
    timestamp: Optional[str],
) -> dict[str, Any]:
    return {
        "type": anomaly_type,
        "message": message,
        "scan_id": scan_id,
        "ticker": ticker,
        "timestamp": timestamp,
    }


def get_anomaly_monitor(conn: sqlite3.Connection) -> dict[str, Any]:
    anomalies: list[dict[str, Any]] = []
    for row in conn.execute(
        """
        SELECT id, run_id, ticker, timestamp, return_1d, return_3d, return_5d,
               return_10d, return_20d, entry_price, price_after_1d,
               price_after_3d, price_after_5d, price_after_10d, price_after_20d
        FROM scan_results
        """
    ).fetchall():
        for field in ("return_1d", "return_3d", "return_5d", "return_10d", "return_20d"):
            value = row[field]
            if isinstance(value, (int, float)) and abs(float(value)) > 300:
                anomalies.append(
                    anomaly_item(
                        "returns > 300%",
                        f"{row['ticker']} has {field}={value}%.",
                        row["run_id"],
                        row["ticker"],
                        row["timestamp"],
                    )
                )
        for field in (
            "entry_price",
            "price_after_1d",
            "price_after_3d",
            "price_after_5d",
            "price_after_10d",
            "price_after_20d",
        ):
            value = row[field]
            if isinstance(value, (int, float)) and float(value) < 0:
                anomalies.append(
                    anomaly_item(
                        "negative prices",
                        f"{row['ticker']} has {field}={value}.",
                        row["run_id"],
                        row["ticker"],
                        row["timestamp"],
                    )
                )

    duplicate_rows = conn.execute(
        """
        SELECT recommendation_id, scan_id, ticker, COUNT(*) AS total, MAX(timestamp) AS latest
        FROM feature_vectors
        GROUP BY recommendation_id
        HAVING recommendation_id IS NOT NULL AND COUNT(*) > 1
        """
    ).fetchall()
    for row in duplicate_rows:
        anomalies.append(
            anomaly_item(
                "duplicate vectors",
                f"{row['ticker']} has {row['total']} feature vectors for recommendation {row['recommendation_id']}.",
                row["scan_id"],
                row["ticker"],
                row["latest"],
            )
        )

    for row in conn.execute(
        """
        SELECT scan_id, ticker, timestamp, sector_name
        FROM feature_vectors
        WHERE sector_name IS NULL OR sector_name = ''
        """
    ).fetchall():
        anomalies.append(
            anomaly_item(
                "missing sector labels",
                f"{row['ticker']} feature vector is missing sector_name.",
                row["scan_id"],
                row["ticker"],
                row["timestamp"],
            )
        )

    for row in conn.execute(
        """
        SELECT scan_id, ticker, timestamp
        FROM feature_vectors
        WHERE timestamp IS NULL OR timestamp = ''
        """
    ).fetchall():
        anomalies.append(
            anomaly_item(
                "missing timestamps",
                f"{row['ticker']} feature vector is missing timestamp.",
                row["scan_id"],
                row["ticker"],
                row["timestamp"],
            )
        )

    for row in conn.execute(
        """
        SELECT scan_id, ticker, timestamp, relative_volume, volume_spike_percent, options_volume_score
        FROM feature_vectors
        WHERE relative_volume = 0 OR volume_spike_percent = 0 OR options_volume_score = 0
        """
    ).fetchall():
        anomalies.append(
            anomaly_item(
                "zero-volume entries",
                f"{row['ticker']} has zero volume-derived feature values.",
                row["scan_id"],
                row["ticker"],
                row["timestamp"],
            )
        )

    latest = sorted(
        anomalies,
        key=lambda item: item.get("timestamp") or "",
        reverse=True,
    )[0] if anomalies else None
    return {
        "anomaly_count": len(anomalies),
        "latest_anomaly": latest,
        "affected_scan_id": latest.get("scan_id") if latest else None,
        "anomalies": anomalies[:50],
    }


def get_gate_attribution_summary() -> dict[str, Any]:
    """Return the latest forward-generated gate attribution payload for Horizon-1."""
    init_db()
    with connect() as conn:
        latest_scan_id_row = conn.execute(
            "SELECT MAX(scan_id) AS scan_id FROM gate_attributions"
        ).fetchone()
        latest_scan_id = latest_scan_id_row["scan_id"] if latest_scan_id_row else None
        if latest_scan_id is None:
            return {
                "ok": True,
                "scan_id": None,
                "ticker": None,
                "attributions": [],
                "contribution_sum": 0,
                "why_this_trade": "Gate Attribution will appear after the next sandbox scan.",
            }

        top_result = conn.execute(
            """
            SELECT id, run_id, ticker, scout_score, final_direction, raw_result_json, feature_vector_json
            FROM scan_results
            WHERE run_id = ?
              AND ticker IN (
                  SELECT DISTINCT ticker FROM gate_attributions WHERE scan_id = ?
              )
            ORDER BY scout_score DESC, id ASC
            LIMIT 1
            """,
            (latest_scan_id, latest_scan_id),
        ).fetchone()
        if top_result is None:
            return {
                "ok": True,
                "scan_id": latest_scan_id,
                "ticker": None,
                "attributions": [],
                "contribution_sum": 0,
                "why_this_trade": "Gate Attribution rows exist, but the matching recommendation was not found.",
            }

        attribution_rows = [
            {
                "gate_name": row["gate_name"],
                "gate_score": row["gate_score"],
                "gate_weight": row["gate_weight"],
                "contribution_pct": row["contribution_pct"],
                "gate_rank": row["gate_rank"],
                "regime_tag": row["regime_tag"],
                "created_at_utc": row["created_at_utc"],
            }
            for row in conn.execute(
                """
                SELECT gate_name, gate_score, gate_weight, contribution_pct,
                       gate_rank, regime_tag, created_at_utc
                FROM gate_attributions
                WHERE scan_id = ? AND ticker = ?
                ORDER BY gate_rank ASC
                """,
                (latest_scan_id, top_result["ticker"]),
            ).fetchall()
        ]
        feature_vector = json_load(top_result["feature_vector_json"])
        if not isinstance(feature_vector, dict):
            raw_result = json_load(top_result["raw_result_json"])
            feature_vector = raw_result if isinstance(raw_result, dict) else {}
        sector = first_present(feature_vector, ("sector", "sector_name"))
        contribution_sum = round(
            sum(float(row.get("contribution_pct") or 0) for row in attribution_rows),
            2,
        )
        return {
            "ok": True,
            "scan_id": latest_scan_id,
            "recommendation_id": top_result["id"],
            "ticker": top_result["ticker"],
            "direction": top_result["final_direction"],
            "sector": sector,
            "attributions": attribution_rows,
            "contribution_sum": contribution_sum,
            "why_this_trade": build_why_this_trade(
                str(top_result["ticker"]),
                top_result["final_direction"],
                str(sector) if sector else None,
                attribution_rows,
            ),
        }


def get_control_summary(fmp_key_present: bool = False) -> dict[str, Any]:
    """Return Scout Horizon-1 sandbox control metrics without touching production systems."""
    init_db()
    schema_integrity = validate_schema(DB_PATH)
    db_size_bytes = DB_PATH.stat().st_size if DB_PATH.exists() else 0
    db_size_mb = round(db_size_bytes / 1024 / 1024, 3)
    now = datetime.now(timezone.utc)

    with connect() as conn:
        rows_by_table = {
            "scan_runs": count_rows(conn, "scan_runs"),
            "scan_results": count_rows(conn, "scan_results"),
            "outcome_update_audit": count_rows(conn, "outcome_update_audit"),
            "institutional_audit_log": count_rows(conn, "institutional_audit_log"),
            "gate_intelligence_metrics": count_rows(conn, "gate_intelligence_metrics"),
            "feature_vectors": count_rows(conn, "feature_vectors"),
            "pattern_intelligence": count_rows(conn, "pattern_intelligence"),
            "gate_attributions": count_rows(conn, "gate_attributions"),
            "gate_alpha_metrics": count_rows(conn, "gate_alpha_metrics"),
            "regime_snapshots": count_rows(conn, "regime_snapshots"),
        }
        feature_intelligence = get_feature_intelligence_summary(conn)
        anomaly_monitor = get_anomaly_monitor(conn)
        pattern_intelligence = get_pattern_intelligence(conn)
        gate_attribution = get_gate_attribution_summary()
        gate_alpha = get_gate_alpha_summary(conn)
        regime_intelligence = get_regime_intelligence_summary(conn)
        totals = {
            "saved_scans": rows_by_table["scan_runs"],
            "saved_recommendations": rows_by_table["scan_results"],
            "completed_outcomes": int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM scan_results
                    WHERE stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
                    """
                ).fetchone()[0]
                or 0
            ),
            "pending_outcomes": int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM scan_results
                    WHERE stock_outcome_label IS NULL
                       OR stock_outcome_label = 'PENDING'
                       OR stock_outcome_label = 'Pending'
                    """
                ).fetchone()[0]
                or 0
            ),
            "gate_snapshots": int(
                conn.execute(
                    "SELECT COUNT(*) FROM scan_results WHERE gate_snapshot_json IS NOT NULL AND gate_snapshot_json != ''"
                ).fetchone()[0]
                or 0
            ),
            "universe_snapshots": int(
                conn.execute(
                    "SELECT COUNT(*) FROM scan_runs WHERE universe_snapshot_json IS NOT NULL AND universe_snapshot_json != ''"
                ).fetchone()[0]
                or 0
            ),
            "audit_log_entries": rows_by_table["outcome_update_audit"] + rows_by_table["institutional_audit_log"],
            "feature_vectors": feature_intelligence["total_feature_vectors"],
            "gate_attributions": rows_by_table["gate_attributions"],
            "gate_alpha_metrics": rows_by_table["gate_alpha_metrics"],
            "regime_snapshots": rows_by_table["regime_snapshots"],
        }
        unique_tickers = int(
            conn.execute("SELECT COUNT(DISTINCT ticker) FROM scan_results").fetchone()[0] or 0
        )
        engine_versions = [
            row["engine_version"]
            for row in conn.execute(
                """
                SELECT DISTINCT engine_version FROM scan_results
                WHERE engine_version IS NOT NULL AND engine_version != ''
                ORDER BY engine_version
                """
            ).fetchall()
        ]
        latest_scan = conn.execute("SELECT MAX(timestamp) AS value FROM scan_runs").fetchone()["value"]
        latest_outcome_update = conn.execute(
            "SELECT MAX(outcome_last_updated_at) AS value FROM scan_results"
        ).fetchone()["value"]
        latest_outcome_audit_event = conn.execute(
            "SELECT MAX(timestamp) AS value FROM outcome_update_audit"
        ).fetchone()["value"]
        latest_institutional_audit_event = conn.execute(
            "SELECT MAX(timestamp) AS value FROM institutional_audit_log"
        ).fetchone()["value"]
        latest_audit_event = max(
            [value for value in (latest_outcome_audit_event, latest_institutional_audit_event) if value],
            default=None,
        )
        audit_backed_recommendations = int(
            conn.execute(
                """
                SELECT COUNT(DISTINCT recommendation_id)
                FROM institutional_audit_log
                WHERE event_type IN ('recommendation_created', 'outcome_updated')
                  AND recommendation_id IS NOT NULL
                """
            ).fetchone()[0]
            or 0
        )
        latest_universe_snapshot = conn.execute(
            """
            SELECT MAX(timestamp) AS value FROM scan_runs
            WHERE universe_snapshot_json IS NOT NULL AND universe_snapshot_json != ''
            """
        ).fetchone()["value"]
        latest_immutable_run_row = conn.execute(
            """
            SELECT id, engine_version, git_commit_hash, created_at_utc
            FROM scan_runs
            WHERE engine_version IS NOT NULL
               OR git_commit_hash IS NOT NULL
               OR created_at_utc IS NOT NULL
            ORDER BY id DESC
            LIMIT 1
            """
        ).fetchone()
        missing_feature_vectors = totals["saved_recommendations"] - totals["feature_vectors"]
        missing_gate_snapshots = totals["saved_recommendations"] - totals["gate_snapshots"]
        missing_universe_snapshots = totals["saved_scans"] - totals["universe_snapshots"]
        stale_pending_outcomes = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scan_results
                WHERE (stock_outcome_label IS NULL OR stock_outcome_label IN ('PENDING', 'Pending'))
                  AND datetime(timestamp) < datetime('now', '-7 days')
                """
            ).fetchone()[0]
            or 0
        )
        duplicate_test_rows = int(
            conn.execute(
                """
                SELECT COALESCE(SUM(extra_rows), 0)
                FROM (
                    SELECT COUNT(*) - 1 AS extra_rows
                    FROM scan_results
                    WHERE COALESCE(is_test_record, 0) = 1
                    GROUP BY ticker, timestamp
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            or 0
        )
    completed = totals["completed_outcomes"]
    pending = totals["pending_outcomes"]
    recommendations = totals["saved_recommendations"]
    gate_intelligence_count = rows_by_table["gate_intelligence_metrics"]
    audit_backed_records = min(audit_backed_recommendations, recommendations)
    stale_scan = latest_scan and freshness_status(latest_scan) != "READY"
    stale_outcome = latest_outcome_update and freshness_status(latest_outcome_update) != "READY"

    warnings = []
    if not fmp_key_present:
        warnings.append("Missing FMP API key. Outcome refreshes will remain limited.")
    if completed < 25:
        warnings.append("Too few completed outcomes for reliable gate-level conclusions.")
    if recommendations and pending / max(recommendations, 1) > 0.5:
        warnings.append("Too many pending outcomes relative to saved recommendations.")
    if duplicate_test_rows:
        warnings.append(f"Duplicate sandbox test rows detected: {duplicate_test_rows}.")
    if missing_feature_vectors:
        warnings.append(f"Missing feature vectors: {missing_feature_vectors}.")
    if missing_gate_snapshots:
        warnings.append(f"Missing gate snapshots: {missing_gate_snapshots}.")
    if missing_universe_snapshots:
        warnings.append(f"Missing universe snapshots: {missing_universe_snapshots}.")
    if stale_pending_outcomes:
        warnings.append(f"Stale pending outcomes older than 7 days: {stale_pending_outcomes}.")
    if stale_scan:
        warnings.append("Latest scan is stale. Run a sandbox scan to refresh memory.")
    if stale_outcome:
        warnings.append("Latest outcome update is stale. Run Update Outcomes when ready.")
    if schema_integrity["status"] == "WARNING":
        warnings.extend(schema_integrity.get("warnings") or [])
    if schema_integrity["status"] == "DRIFT DETECTED":
        warnings.append("SCHEMA DRIFT warning: live SQLite schema differs from registry.")
    if anomaly_monitor["anomaly_count"]:
        warnings.append(f"Anomaly Monitor warning: {anomaly_monitor['anomaly_count']} anomalies detected.")

    learning_readiness = [
        {
            "name": "Gate Intelligence",
            "status": readiness_status(gate_intelligence_count > 0, completed > 0),
        },
        {
            "name": "Outcome Tracking",
            "status": readiness_status(completed > 0, recommendations > 0),
        },
        {
            "name": "Feature Store",
            "status": readiness_status(
                recommendations > 0 and missing_feature_vectors == 0,
                totals["feature_vectors"] > 0,
            ),
        },
        {
            "name": "Universe Snapshot",
            "status": readiness_status(
                totals["saved_scans"] > 0 and missing_universe_snapshots == 0,
                totals["universe_snapshots"] > 0,
            ),
        },
        {
            "name": "Audit Trail",
            "status": readiness_status(totals["audit_log_entries"] > 0, completed > 0),
        },
        {
            "name": "Pattern Learning",
            "status": readiness_status(
                pattern_intelligence["total_patterns_discovered"] > 0,
                completed > 0,
            ),
        },
        {
            "name": "Gate Attribution",
            "status": readiness_status(totals["gate_attributions"] > 0, recommendations > 0),
        },
        {
            "name": "Gate Alpha",
            "status": readiness_status(totals["gate_alpha_metrics"] > 0, completed > 0),
        },
        {
            "name": "Regime Intelligence",
            "status": readiness_status(totals["regime_snapshots"] > 0, completed > 0),
        },
    ]

    progress = [
        {"name": "Data Collection", "percent": progress_percent(recommendations / 1000 * 100)},
        {
            "name": "Outcome Coverage",
            "percent": progress_percent(completed / max(recommendations, 1) * 100),
        },
        {"name": "Gate Intelligence", "percent": progress_percent(gate_intelligence_count / 14 * 100)},
        {
            "name": "Feature Store",
            "percent": progress_percent(totals["feature_vectors"] / max(recommendations, 1) * 100),
        },
        {"name": "Pattern Learning", "percent": progress_percent(pattern_intelligence["total_patterns_discovered"] / 25 * 100)},
        {"name": "Gate Attribution", "percent": progress_percent(totals["gate_attributions"] / max(recommendations * 10, 1) * 100)},
        {"name": "Gate Alpha", "percent": progress_percent(totals["gate_alpha_metrics"] / 100 * 100)},
        {"name": "Regime Intelligence", "percent": progress_percent(regime_intelligence["completed_regime_samples"] / max(completed, 1) * 100)},
    ]

    system_health = [
        {"name": "FMP API", "status": health_status(fmp_key_present, not fmp_key_present)},
        {"name": "SQLite", "status": health_status(DB_PATH.exists())},
        {"name": "Scheduler", "status": "WARNING", "detail": "Production scheduler not touched by sandbox."},
        {"name": "Sandbox Engine", "status": health_status(True)},
        {"name": "Memory Layer", "status": health_status(True, recommendations == 0)},
        {"name": "Outcome Engine", "status": health_status(completed > 0, recommendations > 0 and completed == 0)},
        {"name": "Feature Store", "status": health_status(totals["feature_vectors"] > 0, missing_feature_vectors > 0)},
        {"name": "Gate Attribution", "status": health_status(totals["gate_attributions"] > 0, recommendations > 0)},
        {"name": "Gate Alpha", "status": health_status(totals["gate_alpha_metrics"] > 0, completed > 0)},
        {"name": "Regime Intelligence", "status": health_status(totals["regime_snapshots"] > 0, completed > 0)},
    ]
    latest_immutable_run = (
        {
            "scan_id": latest_immutable_run_row["id"],
            "engine_version": latest_immutable_run_row["engine_version"],
            "git_commit_hash": latest_immutable_run_row["git_commit_hash"],
            "created_at_utc": latest_immutable_run_row["created_at_utc"],
        }
        if latest_immutable_run_row is not None
        else None
    )

    return {
        "ok": True,
        "generated_at": now.isoformat(),
        "database": {
            "path": str(DB_PATH),
            "size_bytes": db_size_bytes,
            "size_mb": db_size_mb,
            "size_label": f"{db_size_mb:.3f} MB" if db_size_mb < 1024 else f"{db_size_mb / 1024:.2f} GB",
            "rows_by_table": rows_by_table,
        },
        "data_bank_health": {
            "sqlite_database_size": db_size_bytes,
            **totals,
        },
        "learning_readiness": learning_readiness,
        "data_volume": {
            "estimated_db_size": f"{db_size_mb:.3f} MB",
            "rows_by_table": rows_by_table,
            "unique_tickers": unique_tickers,
            "unique_sectors": feature_intelligence["unique_sectors"],
            "engine_versions": engine_versions,
            "feature_vector_count": totals["feature_vectors"],
        },
        "feature_intelligence": feature_intelligence,
        "pattern_intelligence": pattern_intelligence,
        "gate_attribution": gate_attribution,
        "gate_alpha": gate_alpha,
        "regime_intelligence": regime_intelligence,
        "schema_integrity": schema_integrity,
        "anomaly_monitor": anomaly_monitor,
        "latest_immutable_run": latest_immutable_run,
        "versioning": {
            "current_engine_version": current_engine_version(),
            "latest_scan_timestamp": latest_scan,
            "latest_outcome_update": latest_outcome_update,
            "latest_audit_event": latest_audit_event,
            "latest_universe_snapshot": latest_universe_snapshot,
        },
        "warnings": warnings,
        "learning_progress": progress,
        "future_training_readiness": {
            "label": "Future GPT/LLM Training Readiness",
            "training_records_available": recommendations,
            "completed_labeled_outcomes": completed,
            "feature_vectors_available": totals["feature_vectors"],
            "audit_backed_records": audit_backed_records,
            "exportable_dataset_status": readiness_status(completed > 0 and totals["feature_vectors"] > 0),
            "minimum_recommended": "1,000+ labeled records",
            "recommended_next_milestone": (
                "Reach 1,000+ completed labeled outcomes with feature vectors and audit coverage."
                if completed < 1000
                else "Expand cross-sector coverage and monitor drift before export."
            ),
        },
        "system_health": system_health,
        "endpoints": [
            {"path": "/control", "description": "Scout Horizon-1 mission control page."},
            {"path": "/api/control/summary", "description": "JSON summary for sandbox memory health and training readiness."},
            {"path": "/api/control/self-audit", "description": "Runs Phase 4 infrastructure readiness checks."},
            {"path": "/api/control/backfill", "description": "Backfills missing institutional snapshots and audit events."},
            {"path": "/api/control/patterns", "description": "Rebuilds and returns Pattern Intelligence from immutable records."},
            {"path": "/api/control/attribution", "description": "Returns the latest Gate Attribution Intelligence payload."},
            {"path": "/api/control/gate-alpha", "description": "Returns Gate Alpha Intelligence from completed attributed outcomes."},
            {"path": "/api/control/regime-intelligence", "description": "Returns regime performance from completed forward regime snapshots."},
        ],
        "safety": {
            "active_model_training": False,
            "scope": "Sandbox only",
            "production_scheduler_touched": False,
        },
    }


def get_horizon_self_audit(
    fmp_key_present: bool = False,
    control_route_available: bool = True,
) -> dict[str, Any]:
    """Verify sandbox Phase 4 infrastructure connectivity and measurable readiness."""
    summary = get_control_summary(fmp_key_present=fmp_key_present)
    checks: list[dict[str, str]] = []
    rows_by_table = summary["database"]["rows_by_table"]
    health = summary["data_bank_health"]
    versioning = summary["versioning"]
    warnings = summary["warnings"]

    with connect() as conn:
        scan_runs_exists = table_exists(conn, "scan_runs")
        scan_results_exists = table_exists(conn, "scan_results")
        scan_result_columns = table_columns(conn, "scan_results")
        scan_run_columns = table_columns(conn, "scan_runs")
        audit_exists = table_exists(conn, "outcome_update_audit") and table_exists(conn, "institutional_audit_log")
        intelligence_exists = table_exists(conn, "gate_intelligence_metrics")
        feature_vector_table_exists = table_exists(conn, "feature_vectors")
        outcome_fields = {
            "return_1d",
            "return_3d",
            "return_5d",
            "return_10d",
            "return_20d",
            "stock_outcome_label",
        }
        duplicate_test_records = int(
            conn.execute(
                """
                SELECT COALESCE(SUM(extra_rows), 0)
                FROM (
                    SELECT COUNT(*) - 1 AS extra_rows
                    FROM scan_results
                    WHERE COALESCE(is_test_record, 0) = 1
                    GROUP BY ticker, timestamp
                    HAVING COUNT(*) > 1
                )
                """
            ).fetchone()[0]
            or 0
        )
        stale_outcomes = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scan_results
                WHERE (stock_outcome_label IS NULL OR stock_outcome_label IN ('PENDING', 'Pending'))
                  AND datetime(timestamp) < datetime('now', '-7 days')
                """
            ).fetchone()[0]
            or 0
        )

    checks.extend(
        [
            audit_item(
                "SYSTEM",
                "/control route loads",
                "PASS" if control_route_available else "FAIL",
                "PASS - Horizon-1 route file is available."
                if control_route_available
                else "FAIL - /control route file is missing.",
            ),
            audit_item(
                "SYSTEM",
                "/api/control/summary returns valid JSON",
                "PASS" if summary.get("ok") else "FAIL",
                "PASS - Control summary returned valid JSON."
                if summary.get("ok")
                else "FAIL - Control summary did not return a valid payload.",
            ),
            audit_item(
                "SYSTEM",
                "SQLite database is reachable",
                "PASS" if DB_PATH.exists() else "WARNING",
                "PASS - SQLite database is reachable."
                if DB_PATH.exists()
                else "WARNING - SQLite database initialized but no prior memory existed.",
            ),
            audit_item(
                "SYSTEM",
                "FMP API key is detected",
                "PASS" if fmp_key_present else "WARNING",
                "PASS - FMP API key detected."
                if fmp_key_present
                else "WARNING - Missing FMP API key; live outcome refreshes are limited.",
            ),
            audit_item(
                "SYSTEM",
                "FMP API status can be checked",
                "PASS" if fmp_key_present else "WARNING",
                "PASS - FMP status check is ready because a key is present."
                if fmp_key_present
                else "WARNING - FMP status cannot be checked until an API key is loaded.",
            ),
        ]
    )

    checks.extend(
        [
            audit_item(
                "DATA LAYERS",
                "Research memory table exists",
                "PASS" if scan_runs_exists else "FAIL",
                "PASS - scan_runs table exists."
                if scan_runs_exists
                else "FAIL - scan_runs table is missing.",
            ),
            audit_item(
                "DATA LAYERS",
                "Recommendations table exists",
                "PASS" if scan_results_exists and "ticker" in scan_result_columns else "FAIL",
                "PASS - scan_results recommendation table exists."
                if scan_results_exists and "ticker" in scan_result_columns
                else "FAIL - scan_results table is missing.",
            ),
            audit_item(
                "DATA LAYERS",
                "Outcome fields exist",
                "PASS" if outcome_fields.issubset(scan_result_columns) else "FAIL",
                "PASS - Outcome fields are present."
                if outcome_fields.issubset(scan_result_columns)
                else "FAIL - One or more outcome fields are missing.",
            ),
            audit_item(
                "DATA LAYERS",
                "Gate snapshots exist",
                "PASS" if "gate_snapshot_json" in scan_result_columns else "FAIL",
                "PASS - Gate snapshot column exists."
                if "gate_snapshot_json" in scan_result_columns
                else "FAIL - Gate snapshot column is missing.",
            ),
            audit_item(
                "DATA LAYERS",
                "Universe snapshots exist",
                "PASS" if "universe_snapshot_json" in scan_run_columns else "FAIL",
                "PASS - Universe snapshot column exists."
                if "universe_snapshot_json" in scan_run_columns
                else "FAIL - Universe snapshot column is missing.",
            ),
            audit_item(
                "DATA LAYERS",
                "Audit log exists",
                "PASS" if audit_exists else "FAIL",
                "PASS - Outcome audit table exists."
                if audit_exists
                else "FAIL - Outcome audit table is missing.",
            ),
            audit_item(
                "DATA LAYERS",
                "Feature vector store exists",
                "PASS" if "feature_vector_json" in scan_result_columns or feature_vector_table_exists else "FAIL",
                "PASS - Feature vector JSON store exists."
                if "feature_vector_json" in scan_result_columns
                else "FAIL - Feature vector store is missing.",
            ),
        ]
    )

    checks.extend(
        [
            audit_item(
                "DATA PRESENCE",
                "At least one scan exists",
                "PASS" if health["saved_scans"] > 0 else "WARNING",
                f"{'PASS' if health['saved_scans'] > 0 else 'WARNING'} - {health['saved_scans']} saved scans found.",
            ),
            audit_item(
                "DATA PRESENCE",
                "At least one recommendation exists",
                "PASS" if health["saved_recommendations"] > 0 else "WARNING",
                f"{'PASS' if health['saved_recommendations'] > 0 else 'WARNING'} - {health['saved_recommendations']} recommendations found.",
            ),
            audit_item(
                "DATA PRESENCE",
                "At least one completed outcome exists",
                "PASS" if health["completed_outcomes"] > 0 else "WARNING",
                f"{'PASS' if health['completed_outcomes'] > 0 else 'WARNING'} - {health['completed_outcomes']} completed outcomes found.",
            ),
        ]
    )

    checks.extend(
        [
            audit_item(
                "INTELLIGENCE",
                "Gate intelligence data exists",
                "PASS" if rows_by_table["gate_intelligence_metrics"] > 0 else "WARNING",
                f"{'PASS' if rows_by_table['gate_intelligence_metrics'] > 0 else 'WARNING'} - {rows_by_table['gate_intelligence_metrics']} gate intelligence rows found.",
            ),
            audit_item(
                "INTELLIGENCE",
                "Engine version exists",
                "PASS" if versioning.get("current_engine_version") else "FAIL",
                "PASS - Engine version is available."
                if versioning.get("current_engine_version")
                else "FAIL - Engine version is missing.",
            ),
            audit_item(
                "INTELLIGENCE",
                "Latest scan timestamp exists",
                "PASS" if versioning.get("latest_scan_timestamp") else "WARNING",
                "PASS - Latest scan timestamp exists."
                if versioning.get("latest_scan_timestamp")
                else "WARNING - No latest scan timestamp yet.",
            ),
            audit_item(
                "INTELLIGENCE",
                "Latest outcome timestamp exists",
                "PASS" if versioning.get("latest_outcome_update") else "WARNING",
                "PASS - Latest outcome update timestamp exists."
                if versioning.get("latest_outcome_update")
                else "WARNING - No outcome update timestamp yet.",
            ),
            audit_item(
                "INTELLIGENCE",
                "Latest audit event exists",
                "PASS" if versioning.get("latest_audit_event") else "WARNING",
                "PASS - Latest audit event exists."
                if versioning.get("latest_audit_event")
                else "WARNING - No audit event exists yet.",
            ),
        ]
    )

    missing_feature_vectors = max(health["saved_recommendations"] - health["feature_vectors"], 0)
    missing_gate_snapshots = max(health["saved_recommendations"] - health["gate_snapshots"], 0)
    missing_universe_snapshots = max(health["saved_scans"] - health["universe_snapshots"], 0)
    stale_scans = 1 if versioning.get("latest_scan_timestamp") and freshness_status(versioning["latest_scan_timestamp"]) != "READY" else 0
    checks.extend(
        [
            audit_item(
                "QUALITY",
                "Duplicate test records count",
                "PASS" if duplicate_test_records == 0 else "WARNING",
                f"{'PASS' if duplicate_test_records == 0 else 'WARNING'} - Duplicate test records: {duplicate_test_records}.",
            ),
            audit_item(
                "QUALITY",
                "Stale outcomes check",
                "PASS" if stale_outcomes == 0 else "WARNING",
                f"{'PASS' if stale_outcomes == 0 else 'WARNING'} - Stale pending outcomes: {stale_outcomes}.",
            ),
            audit_item(
                "QUALITY",
                "Stale scans check",
                "PASS" if stale_scans == 0 else "WARNING",
                "PASS - Latest scan freshness is acceptable."
                if stale_scans == 0
                else "WARNING - Latest scan is stale.",
            ),
            audit_item(
                "QUALITY",
                "Missing feature vectors check",
                "PASS" if missing_feature_vectors == 0 else "WARNING",
                f"{'PASS' if missing_feature_vectors == 0 else 'WARNING'} - Missing feature vectors: {missing_feature_vectors}.",
            ),
            audit_item(
                "QUALITY",
                "Missing snapshots check",
                "PASS" if missing_gate_snapshots == 0 and missing_universe_snapshots == 0 else "WARNING",
                (
                    "PASS - Gate and universe snapshots are complete."
                    if missing_gate_snapshots == 0 and missing_universe_snapshots == 0
                    else f"WARNING - Missing gate snapshots: {missing_gate_snapshots}; missing universe snapshots: {missing_universe_snapshots}."
                ),
            ),
        ]
    )

    passes = sum(1 for item in checks if item["status"] == "PASS")
    warning_count = sum(1 for item in checks if item["status"] == "WARNING")
    failures = sum(1 for item in checks if item["status"] == "FAIL")
    if failures:
        next_action = "Fix failing infrastructure checks before starting Phase 5."
    elif warning_count:
        if missing_gate_snapshots or missing_universe_snapshots:
            next_action = "Resolve warnings by adding fresh scans, completed outcomes, and complete snapshots before Phase 5."
        elif missing_feature_vectors:
            next_action = "Resolve remaining warnings; feature vectors are the next institutional record gap."
        else:
            next_action = "Resolve remaining warnings before Phase 5 planning."
    else:
        next_action = "Phase 4 infrastructure is connected and ready for Phase 5 planning."

    return {
        "ok": True,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "summary": {
            "total_checks": len(checks),
            "passes": passes,
            "warnings": warning_count,
            "failures": failures,
            "recommended_next_action": next_action,
        },
        "checks": checks,
        "warnings": warnings,
    }


def count_scan_results(
    filters: Optional[HistoryFilters] = None,
    conn: Optional[sqlite3.Connection] = None,
) -> int:
    init_db()
    where_sql, params = history_where_clause(filters)
    if conn is not None:
        row = conn.execute(f"SELECT COUNT(*) AS total FROM scan_results{where_sql}", params).fetchone()
        return int(row["total"] or 0)
    with connect() as c:
        row = c.execute(f"SELECT COUNT(*) AS total FROM scan_results{where_sql}", params).fetchone()
    return int(row["total"] or 0)


def query_scan_results(
    *,
    limit: int = 100,
    offset: int = 0,
    filters: Optional[HistoryFilters] = None,
    lightweight: bool = True,
    conn: Optional[sqlite3.Connection] = None,
) -> list[dict[str, Any]]:
    init_db()
    safe_limit = min(max(int(limit), 1), MAX_HISTORY_PAGE_SIZE)
    safe_offset = max(int(offset), 0)
    where_sql, params = history_where_clause(filters)
    row_factory = row_to_history_list if lightweight else row_to_result
    query = f"""
        SELECT {HISTORY_SELECT_SQL}
        FROM scan_results
        {where_sql}
        ORDER BY timestamp DESC, id DESC
        LIMIT ? OFFSET ?
    """
    query_params = [*params, safe_limit, safe_offset]

    if conn is not None:
        rows = conn.execute(query, query_params).fetchall()
    else:
        with connect() as c:
            rows = c.execute(query, query_params).fetchall()
    return [row_factory(row) for row in rows]


def get_ticker_history(
    ticker: Optional[str] = None,
    limit: int = 100,
    offset: int = 0,
    *,
    lightweight: bool = True,
    filters: Optional[HistoryFilters] = None,
) -> list[dict[str, Any]]:
    merged = filters or HistoryFilters()
    if ticker:
        merged = HistoryFilters(
            ticker=ticker.upper(),
            outcome=merged.outcome,
            direction=merged.direction,
            failed_gate=merged.failed_gate,
            min_score=merged.min_score,
            max_score=merged.max_score,
        )
    return query_scan_results(
        limit=limit,
        offset=offset,
        filters=merged,
        lightweight=lightweight,
    )


def get_gate_statistics(sample_limit: int = 400) -> list[dict[str, Any]]:
    init_db()
    stats: dict[str, dict[str, Any]] = {}
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT gates_json
            FROM scan_results
            WHERE gates_json IS NOT NULL AND gates_json != ''
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (sample_limit,),
        ).fetchall()
    for row in rows:
        gates = json_load(row["gates_json"]) or {}
        if not isinstance(gates, dict):
            continue
        for gate, passed in gates.items():
            item = stats.setdefault(gate, {"gate": gate, "pass": 0, "fail": 0, "total": 0})
            item["total"] += 1
            item["pass" if passed else "fail"] += 1

    output = []
    for item in stats.values():
        total = item["total"] or 1
        output.append({**item, "pass_rate": round(item["pass"] / total * 100, 1)})
    return sorted(output, key=lambda row: row["gate"])


RETURN_HORIZON_FIELDS = (
    "return_20d",
    "return_10d",
    "return_5d",
    "return_3d",
    "return_1d",
)


def primary_forward_return(row: dict[str, Any]) -> Optional[float]:
    """Longest available forward return for a row (20D preferred)."""
    for field in RETURN_HORIZON_FIELDS:
        value = row.get(field)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def is_pending_outcome_row(row: dict[str, Any]) -> bool:
    label = str(row.get("stock_outcome_label") or "").upper()
    if label in ("WIN", "LOSS", "FLAT"):
        return False
    return primary_forward_return(row) is None


def completed_return_rows(history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in history if not is_pending_outcome_row(row)]


def directional_match(direction: str, forward_return: float) -> bool:
    if direction == "Bullish":
        return forward_return > 0
    if direction == "Bearish":
        return forward_return < 0
    return forward_return > 0


def directional_accuracy_stats(
    rows: list[dict[str, Any]],
    direction: str,
) -> dict[str, Any]:
    subset = [
        row
        for row in rows
        if row.get("final_direction") == direction
    ]
    returns = [
        value
        for row in subset
        if (value := primary_forward_return(row)) is not None
    ]
    wins = sum(1 for value in returns if directional_match(direction, value))
    losses = len(returns) - wins
    decided = len(returns)
    accuracy = round(wins / decided * 100, 1) if decided else 0.0
    avg_return = round(sum(returns) / len(returns), 2) if returns else 0.0
    return {
        "completed": decided,
        "wins": wins,
        "losses": losses,
        "directional_accuracy": accuracy,
        "avg_return": avg_return,
    }


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


def _sql_win_rate_percent(wins: Any, losses: Any) -> float:
    wins_i = int(wins or 0)
    losses_i = int(losses or 0)
    decided = wins_i + losses_i
    return round(wins_i / decided * 100, 1) if decided else 0.0


def _directional_bucket(conn: sqlite3.Connection, direction: str) -> dict[str, Any]:
    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS completed,
            SUM(CASE WHEN {PRIMARY_RETURN_SQL} > 0 THEN 1 ELSE 0 END) AS bullish_style_wins,
            SUM(CASE WHEN {PRIMARY_RETURN_SQL} < 0 THEN 1 ELSE 0 END) AS bearish_style_wins,
            AVG({PRIMARY_RETURN_SQL}) AS avg_return
        FROM scan_results
        WHERE final_direction = ?
          AND {PRIMARY_RETURN_SQL} IS NOT NULL
        """,
        (direction,),
    ).fetchone()
    completed = int(row["completed"] or 0)
    if direction == "Bullish":
        wins = int(row["bullish_style_wins"] or 0)
        losses = completed - wins
    else:
        wins = int(row["bearish_style_wins"] or 0)
        losses = completed - wins
    return {
        "completed": completed,
        "wins": wins,
        "losses": losses,
        "directional_accuracy": round(wins / completed * 100, 1) if completed else 0.0,
        "avg_return": round(float(row["avg_return"] or 0), 2),
    }


def get_outcome_analytics(conn: Optional[sqlite3.Connection] = None) -> dict[str, Any]:
    """Aggregate outcome metrics in SQL (no full-table Python scan)."""
    init_db()
    owns_connection = conn is None
    active_conn = conn or connect()
    try:
        label_row = active_conn.execute(
            """
            SELECT
                SUM(CASE WHEN stock_outcome_label IN ('WIN', 'LOSS', 'FLAT') THEN 1 ELSE 0 END) AS total_completed,
                SUM(CASE WHEN stock_outcome_label = 'WIN' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN stock_outcome_label = 'LOSS' THEN 1 ELSE 0 END) AS losses,
                SUM(
                    CASE
                        WHEN (stock_outcome_label IS NULL OR stock_outcome_label = 'PENDING')
                         AND {primary} IS NULL
                        THEN 1 ELSE 0
                    END
                ) AS pending
            FROM scan_results
            """.format(primary=PRIMARY_RETURN_SQL)
        ).fetchone()

        bullish_label = active_conn.execute(
            """
            SELECT
                SUM(CASE WHEN stock_outcome_label = 'WIN' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN stock_outcome_label = 'LOSS' THEN 1 ELSE 0 END) AS losses
            FROM scan_results
            WHERE stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
              AND final_direction = 'Bullish'
            """
        ).fetchone()
        bearish_label = active_conn.execute(
            """
            SELECT
                SUM(CASE WHEN stock_outcome_label = 'WIN' THEN 1 ELSE 0 END) AS wins,
                SUM(CASE WHEN stock_outcome_label = 'LOSS' THEN 1 ELSE 0 END) AS losses
            FROM scan_results
            WHERE stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
              AND final_direction = 'Bearish'
            """
        ).fetchone()

        completed_returns = int(
            active_conn.execute(
                f"SELECT COUNT(*) FROM scan_results WHERE {PRIMARY_RETURN_SQL} IS NOT NULL"
            ).fetchone()[0]
            or 0
        )

        bullish_directional = _directional_bucket(active_conn, "Bullish")
        bearish_directional = _directional_bucket(active_conn, "Bearish")

        avg_row = active_conn.execute(
            f"""
            SELECT
                AVG(return_1d) AS avg_1d,
                AVG(return_5d) AS avg_5d,
                AVG(return_10d) AS avg_10d,
                AVG(CASE WHEN final_direction = 'Bullish' THEN return_5d END) AS avg_5d_bullish,
                AVG(CASE WHEN final_direction = 'Bearish' THEN return_5d END) AS avg_5d_bearish,
                AVG(CASE WHEN final_direction = 'Bullish' THEN return_10d END) AS avg_10d_bullish,
                AVG(CASE WHEN final_direction = 'Bearish' THEN return_10d END) AS avg_10d_bearish
            FROM scan_results
            WHERE {PRIMARY_RETURN_SQL} IS NOT NULL
            """
        ).fetchone()

        best = active_conn.execute(
            f"""
            SELECT ticker, return_20d, return_10d, return_5d, return_1d
            FROM scan_results
            WHERE {PRIMARY_RETURN_SQL} IS NOT NULL
            ORDER BY {PRIMARY_RETURN_SQL} DESC
            LIMIT 1
            """
        ).fetchone()
        worst = active_conn.execute(
            f"""
            SELECT ticker, return_20d, return_10d, return_5d, return_1d
            FROM scan_results
            WHERE {PRIMARY_RETURN_SQL} IS NOT NULL
            ORDER BY {PRIMARY_RETURN_SQL} ASC
            LIMIT 1
            """
        ).fetchone()

        predictive_rows = active_conn.execute(
            """
            SELECT gate_name, win_rate, win_count, loss_count
            FROM gate_intelligence_metrics
            WHERE win_count + loss_count > 0
            ORDER BY win_rate DESC, win_count DESC
            """
        ).fetchall()

        most_predictive = None
        least_predictive = None
        if predictive_rows:
            top = predictive_rows[0]
            bottom = predictive_rows[-1]
            most_predictive = {
                "gate": top["gate_name"],
                "win_rate": round(float(top["win_rate"] or 0), 1),
            }
            least_predictive = {
                "gate": bottom["gate_name"],
                "win_rate": round(float(bottom["win_rate"] or 0), 1),
            }

        return {
            "total_completed": int(label_row["total_completed"] or 0),
            "total_completed_returns": completed_returns,
            "win_rate": _sql_win_rate_percent(label_row["wins"], label_row["losses"]),
            "bullish_win_rate": _sql_win_rate_percent(bullish_label["wins"], bullish_label["losses"]),
            "bearish_win_rate": _sql_win_rate_percent(bearish_label["wins"], bearish_label["losses"]),
            "bullish_directional_accuracy": bullish_directional["directional_accuracy"],
            "bearish_directional_accuracy": bearish_directional["directional_accuracy"],
            "bullish_avg_return": bullish_directional["avg_return"],
            "bearish_avg_return": bearish_directional["avg_return"],
            "bullish_completed_outcomes": bullish_directional["completed"],
            "bearish_completed_outcomes": bearish_directional["completed"],
            "bullish_directional_wins": bullish_directional["wins"],
            "bullish_directional_losses": bullish_directional["losses"],
            "bearish_directional_wins": bearish_directional["wins"],
            "bearish_directional_losses": bearish_directional["losses"],
            "average_1d_return": round(float(avg_row["avg_1d"] or 0), 2),
            "average_5d_return": round(float(avg_row["avg_5d"] or 0), 2),
            "average_10d_return": round(float(avg_row["avg_10d"] or 0), 2),
            "average_5d_return_bullish": round(float(avg_row["avg_5d_bullish"] or 0), 2),
            "average_5d_return_bearish": round(float(avg_row["avg_5d_bearish"] or 0), 2),
            "average_10d_return_bullish": round(float(avg_row["avg_10d_bullish"] or 0), 2),
            "average_10d_return_bearish": round(float(avg_row["avg_10d_bearish"] or 0), 2),
            "best_performing_ticker": dict(best) if best else None,
            "worst_performing_ticker": dict(worst) if worst else None,
            "most_predictive_gate": most_predictive,
            "least_predictive_gate": least_predictive,
            "pending": int(label_row["pending"] or 0),
        }
    finally:
        if owns_connection:
            active_conn.close()


def get_top_gate_failures(limit: int = 10, sample_limit: int = 500) -> list[dict[str, Any]]:
    init_db()
    counts: dict[str, dict[str, Any]] = {}
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT failed_gates_json, failure_reasons_json
            FROM scan_results
            WHERE failed_gates_json IS NOT NULL
              AND failed_gates_json NOT IN ('', '[]', 'null')
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (sample_limit,),
        ).fetchall()
    for row in rows:
        failed_gates = json_load(row["failed_gates_json"]) or []
        reasons = json_load(row["failure_reasons_json"]) or []
        if not isinstance(failed_gates, list):
            continue
        for index, gate in enumerate(failed_gates):
            item = counts.setdefault(gate, {"gate": gate, "count": 0, "examples": []})
            item["count"] += 1
            if len(item["examples"]) < 3 and index < len(reasons):
                item["examples"].append(reasons[index])
    return sorted(counts.values(), key=lambda row: row["count"], reverse=True)[:limit]


def get_option_pick_history(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    output: list[dict[str, Any]] = []
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT timestamp, ticker, final_direction, final_option_pick_json
            FROM scan_results
            WHERE final_option_pick_json IS NOT NULL
              AND final_option_pick_json NOT IN ('', 'null')
            ORDER BY timestamp DESC, id DESC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()
    for row in rows:
        option = json_load(row["final_option_pick_json"])
        if isinstance(option, dict) and any(
            option.get(key) not in (None, "") for key in ("contractSymbol", "strike", "expiration")
        ):
            output.append(
                {
                    "timestamp": row["timestamp"],
                    "ticker": row["ticker"],
                    "direction": row["final_direction"],
                    "option": option,
                }
            )
    return output


def build_memory_summary_payload() -> dict[str, Any]:
    """Fast summary payload: metrics first, history loaded separately."""
    timings: dict[str, float] = {}
    started = time.perf_counter()
    with log_timing(timings, "init_db_ms"):
        init_db()

    payload: dict[str, Any] = {"ok": True, "recentScans": []}
    with connect() as conn:
        with log_timing(timings, "outcome_analytics_ms"):
            payload["outcomeAnalytics"] = get_outcome_analytics(conn)
        with log_timing(timings, "gate_statistics_ms"):
            payload["gateStatistics"] = get_gate_statistics()
        with log_timing(timings, "gate_intelligence_ms"):
            payload["gateIntelligence"] = get_gate_intelligence_metrics()
        with log_timing(timings, "direction_accuracy_ms"):
            payload["directionAccuracy"] = get_direction_accuracy()
        with log_timing(timings, "top_gate_failures_ms"):
            payload["topGateFailures"] = get_top_gate_failures()
        with log_timing(timings, "option_pick_history_ms"):
            payload["optionPickHistory"] = get_option_pick_history()
        with log_timing(timings, "count_ms"):
            payload["historyTotal"] = count_scan_results(conn=conn)
    payload["outcomeAuditLog"] = []

    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    payload["timings"] = timings
    log_memory_load(timings, "summary")
    return payload


def _history_filters_debug(filters: Optional[HistoryFilters]) -> dict[str, Any]:
    normalized = normalize_history_filters(filters)
    return {
        "ticker": normalized.ticker,
        "outcome": normalized.outcome,
        "direction": normalized.direction,
        "failedGate": normalized.failed_gate,
        "minScore": normalized.min_score,
        "maxScore": normalized.max_score,
    }


def build_memory_history_payload(
    *,
    limit: int = 100,
    offset: int = 0,
    filters: Optional[HistoryFilters] = None,
) -> dict[str, Any]:
    timings: dict[str, float] = {}
    started = time.perf_counter()
    init_db()
    normalized_filters = normalize_history_filters(filters)
    safe_limit = min(max(int(limit), 1), MAX_HISTORY_PAGE_SIZE)
    safe_offset = max(int(offset), 0)
    with connect() as conn:
        with log_timing(timings, "row_fetch_ms"):
            history = query_scan_results(
                limit=safe_limit,
                offset=safe_offset,
                filters=normalized_filters,
                lightweight=True,
                conn=conn,
            )
        with log_timing(timings, "count_ms"):
            total = count_scan_results(conn=conn)
            filtered_total = count_scan_results(filters=normalized_filters, conn=conn)
    timings["total_ms"] = round((time.perf_counter() - started) * 1000, 2)
    filter_label = normalized_filters.ticker or "ALL"
    print(
        "[memory-history] "
        f"db={DB_PATH} total={total} filtered={filtered_total} "
        f"returned={len(history)} limit={safe_limit} offset={safe_offset} "
        f"filters={json.dumps(_history_filters_debug(normalized_filters), sort_keys=True)}",
        file=sys.stderr,
        flush=True,
    )
    log_memory_load(
        timings,
        f"history limit={safe_limit} offset={safe_offset} ticker={filter_label}",
    )
    return {
        "ok": True,
        "history": history,
        "total": total,
        "filteredTotal": filtered_total,
        "limit": safe_limit,
        "offset": safe_offset,
        "timings": timings,
        "debug": {
            "dbPath": str(DB_PATH),
            "total": total,
            "filteredTotal": filtered_total,
            "returned": len(history),
            "limit": safe_limit,
            "offset": safe_offset,
            "filters": _history_filters_debug(normalized_filters),
        },
    }


EXPORT_CSV_COLUMNS = [
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
]

EXPORT_SELECT_SQL = """
    timestamp, ticker, scout_score, bull_score, bear_score, net_direction, final_direction,
    failed_gates_json, failure_reasons_json, final_option_pick_json,
    entry_price, price_after_1d, price_after_3d, price_after_5d, price_after_10d, price_after_20d,
    return_1d, return_3d, return_5d, return_10d, return_20d,
    option_entry_price, option_price_after_1d, option_price_after_3d, option_price_after_5d,
    option_price_after_10d, option_return_1d, option_return_3d, option_return_5d, option_return_10d,
    stock_outcome_label, option_outcome_label, max_favorable_move, max_adverse_move,
    result_notes, outcome_last_updated_at
"""


def export_csv_row(row: sqlite3.Row) -> dict[str, Any]:
    failed_gates = json_load(row["failed_gates_json"]) or []
    failure_reasons = json_load(row["failure_reasons_json"]) or []
    if not isinstance(failed_gates, list):
        failed_gates = []
    if not isinstance(failure_reasons, list):
        failure_reasons = []
    return {
        "timestamp": row["timestamp"],
        "ticker": row["ticker"],
        "scout_score": row["scout_score"],
        "bull_score": row["bull_score"],
        "bear_score": row["bear_score"],
        "net_direction": row["net_direction"],
        "final_direction": row["final_direction"],
        "failed_gates": "; ".join(str(item) for item in failed_gates),
        "failure_reasons": " | ".join(str(item) for item in failure_reasons),
        "final_option_pick": row["final_option_pick_json"] or "",
        "entry_price": row["entry_price"],
        "price_after_1d": row["price_after_1d"],
        "price_after_3d": row["price_after_3d"],
        "price_after_5d": row["price_after_5d"],
        "price_after_10d": row["price_after_10d"],
        "price_after_20d": row["price_after_20d"],
        "return_1d": row["return_1d"],
        "return_3d": row["return_3d"],
        "return_5d": row["return_5d"],
        "return_10d": row["return_10d"],
        "return_20d": row["return_20d"],
        "option_entry_price": row["option_entry_price"],
        "option_price_after_1d": row["option_price_after_1d"],
        "option_price_after_3d": row["option_price_after_3d"],
        "option_price_after_5d": row["option_price_after_5d"],
        "option_price_after_10d": row["option_price_after_10d"],
        "option_return_1d": row["option_return_1d"],
        "option_return_3d": row["option_return_3d"],
        "option_return_5d": row["option_return_5d"],
        "option_return_10d": row["option_return_10d"],
        "stock_outcome_label": row["stock_outcome_label"],
        "option_outcome_label": row["option_outcome_label"],
        "max_favorable_move": row["max_favorable_move"],
        "max_adverse_move": row["max_adverse_move"],
        "result_notes": row["result_notes"],
        "outcome_last_updated_at": row["outcome_last_updated_at"],
    }


def export_csv(filters: Optional[HistoryFilters] = None) -> str:
    init_db()
    where_sql, params = history_where_clause(filters)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=EXPORT_CSV_COLUMNS)
    writer.writeheader()
    offset = 0
    with connect() as conn:
        while True:
            rows = conn.execute(
                f"""
                SELECT {EXPORT_SELECT_SQL}
                FROM scan_results
                {where_sql}
                ORDER BY timestamp DESC, id DESC
                LIMIT ? OFFSET ?
                """,
                [*params, MAX_HISTORY_EXPORT_BATCH, offset],
            ).fetchall()
            if not rows:
                break
            for row in rows:
                writer.writerow(export_csv_row(row))
            offset += len(rows)
            if len(rows) < MAX_HISTORY_EXPORT_BATCH:
                break
    return buffer.getvalue()
