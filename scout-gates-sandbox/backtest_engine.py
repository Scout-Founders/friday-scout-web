#!/usr/bin/env python3
"""Backtesting Foundation 1.1 — read-only evidence layer over Scout Horizon memory."""

from __future__ import annotations

import json
import sqlite3
import statistics
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from memory_store import (
    ACTIONABLE_DIRECTIONS,
    COMPLETED_OUTCOME_LABELS_SQL,
    connect,
    init_db,
    json_dump,
    json_load,
)


BACKTEST_VERSION = "1.1"
LEGACY_METRICS_WARNING = (
    "This run may use legacy raw-return metrics. "
    "Use Backtesting Foundation 1.1+ runs for direction-adjusted performance."
)

DIRECTION_BREAKDOWN_ORDER = ("Bullish", "Bearish", "Neutral")


def parse_backtest_version(version: Optional[str]) -> tuple[int, ...]:
    if not version:
        return ()
    parts: list[int] = []
    for segment in str(version).strip().split("."):
        digits = ""
        for char in segment:
            if char.isdigit():
                digits += char
            else:
                break
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def backtest_version_at_least(version: Optional[str], minimum: str = BACKTEST_VERSION) -> bool:
    current = parse_backtest_version(version)
    target = parse_backtest_version(minimum)
    if not target:
        return True
    if not current:
        return False
    length = max(len(current), len(target))
    current_padded = current + (0,) * (length - len(current))
    target_padded = target + (0,) * (length - len(target))
    return current_padded >= target_padded


def _ensure_backtest_columns(conn: sqlite3.Connection) -> None:
    existing_runs = {
        row["name"] for row in conn.execute("PRAGMA table_info(backtest_runs)").fetchall()
    }
    if "backtest_version" not in existing_runs:
        conn.execute("ALTER TABLE backtest_runs ADD COLUMN backtest_version TEXT")

    existing_signals = {
        row["name"] for row in conn.execute("PRAGMA table_info(backtest_signals)").fetchall()
    }
    if "signal_return" not in existing_signals:
        conn.execute("ALTER TABLE backtest_signals ADD COLUMN signal_return REAL")


def backtest_run_uses_legacy_metrics(conn: sqlite3.Connection, run_id: int, row: sqlite3.Row) -> bool:
    version = row["backtest_version"] if "backtest_version" in row.keys() else None
    if not backtest_version_at_least(version):
        return True

    missing_signal_return = conn.execute(
        """
        SELECT COUNT(*) AS missing_count
        FROM backtest_signals
        WHERE run_id = ?
          AND signal_return IS NULL
          AND (
            return_5d IS NOT NULL
            OR return_10d IS NOT NULL
            OR return_20d IS NOT NULL
          )
        """,
        (run_id,),
    ).fetchone()
    return int(missing_signal_return["missing_count"] or 0) > 0


def init_backtest_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            name TEXT NOT NULL,
            description TEXT,
            engine_version TEXT,
            backtest_version TEXT,
            start_date TEXT,
            end_date TEXT,
            filters_json TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending'
        );

        CREATE INDEX IF NOT EXISTS idx_backtest_runs_created
            ON backtest_runs(created_at DESC);

        CREATE TABLE IF NOT EXISTS backtest_signals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id INTEGER NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
            recommendation_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            direction TEXT,
            score REAL,
            sector TEXT,
            outcome_label TEXT,
            return_5d REAL,
            return_10d REAL,
            return_20d REAL,
            signal_return REAL,
            UNIQUE(run_id, recommendation_id)
        );

        CREATE INDEX IF NOT EXISTS idx_backtest_signals_run
            ON backtest_signals(run_id, timestamp);

        CREATE TABLE IF NOT EXISTS backtest_metrics (
            run_id INTEGER PRIMARY KEY REFERENCES backtest_runs(id) ON DELETE CASCADE,
            win_rate REAL NOT NULL,
            loss_rate REAL NOT NULL,
            avg_return REAL,
            median_return REAL,
            max_drawdown REAL,
            best_trade REAL,
            worst_trade REAL,
            bullish_win_rate REAL,
            bearish_win_rate REAL,
            actionable_win_rate REAL
        );
        """
    )
    _ensure_backtest_columns(conn)


@dataclass
class BacktestFilters:
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    engine_version: Optional[str] = None
    cohort_class: Optional[str] = None
    scan_purpose: Optional[str] = None
    sector: Optional[str] = None
    min_score: Optional[float] = None
    max_score: Optional[float] = None
    directions: tuple[str, ...] = ACTIONABLE_DIRECTIONS
    exclude_test_records: bool = True
    require_completed_outcomes: bool = True

    def to_dict(self) -> dict[str, Any]:
        return {
            "startDate": self.start_date,
            "endDate": self.end_date,
            "engineVersion": self.engine_version,
            "cohortClass": self.cohort_class,
            "scanPurpose": self.scan_purpose,
            "sector": self.sector,
            "minScore": self.min_score,
            "maxScore": self.max_score,
            "directions": list(self.directions),
            "excludeTestRecords": self.exclude_test_records,
            "requireCompletedOutcomes": self.require_completed_outcomes,
        }


def parse_backtest_filters(payload: Optional[dict[str, Any]] = None) -> BacktestFilters:
    payload = payload or {}
    directions_raw = payload.get("directions")
    if isinstance(directions_raw, list) and directions_raw:
        directions = tuple(str(item) for item in directions_raw)
    else:
        directions = ACTIONABLE_DIRECTIONS

    def optional_float(key: str) -> Optional[float]:
        value = payload.get(key)
        if value in (None, ""):
            return None
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return BacktestFilters(
        start_date=str(payload["startDate"]).strip() if payload.get("startDate") else None,
        end_date=str(payload["endDate"]).strip() if payload.get("endDate") else None,
        engine_version=str(payload["engineVersion"]).strip() if payload.get("engineVersion") else None,
        cohort_class=str(payload["cohortClass"]).strip() if payload.get("cohortClass") else None,
        scan_purpose=str(payload["scanPurpose"]).strip() if payload.get("scanPurpose") else None,
        sector=str(payload["sector"]).strip() if payload.get("sector") else None,
        min_score=optional_float("minScore"),
        max_score=optional_float("maxScore"),
        directions=directions,
        exclude_test_records=payload.get("excludeTestRecords", True) is not False,
        require_completed_outcomes=payload.get("requireCompletedOutcomes", True) is not False,
    )


def primary_return(row: dict[str, Any]) -> Optional[float]:
    """Longest available raw stock return (audit / underlying move)."""
    for key in ("return_20d", "return_10d", "return_5d", "return_3d", "return_1d"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return None


def directional_return(direction: Any, raw_return: Optional[float]) -> Optional[float]:
    """Single source of truth for signal P&L return."""
    if raw_return is None:
        return None
    value = float(raw_return)
    if direction == "Bearish":
        return -value
    return value


def attach_return_fields(signal: dict[str, Any]) -> dict[str, Any]:
    """Populate stock_return (raw), signal_return (direction-adjusted), and primary_return alias."""
    raw = signal.get("stock_return")
    if raw is None:
        raw = primary_return(signal)
    signal["stock_return"] = raw
    signal["primary_return"] = raw
    signal["signal_return"] = directional_return(signal.get("direction"), raw)
    return signal


def signal_return_value(signal: dict[str, Any]) -> Optional[float]:
    value = signal.get("signal_return")
    if value is not None:
        return float(value)
    raw = signal.get("stock_return")
    if raw is None:
        raw = signal.get("primary_return")
    return directional_return(signal.get("direction"), raw)


def signal_returns(signals: list[dict[str, Any]]) -> list[float]:
    return [
        value
        for signal in signals
        if (value := signal_return_value(signal)) is not None
    ]


def stock_returns(signals: list[dict[str, Any]]) -> list[float]:
    return [
        float(value)
        for signal in signals
        if (value := signal.get("stock_return") if signal.get("stock_return") is not None else signal.get("primary_return"))
        is not None
    ]


def compute_directional_expectancy(signal_return_values: list[float]) -> Optional[float]:
    """Mean signal return — proper directional expectancy for mixed Bullish/Bearish samples."""
    if not signal_return_values:
        return None
    return round(statistics.mean(signal_return_values), 4)


def build_signal_query(filters: BacktestFilters) -> tuple[str, list[Any]]:
    clauses = ["1 = 1"]
    params: list[Any] = []

    if filters.require_completed_outcomes:
        clauses.append(f"sr.{COMPLETED_OUTCOME_LABELS_SQL}")

    if filters.exclude_test_records:
        clauses.append("COALESCE(sr.is_test_record, 0) = 0")

    if filters.directions:
        placeholders = ", ".join("?" for _ in filters.directions)
        clauses.append(f"sr.final_direction IN ({placeholders})")
        params.extend(filters.directions)

    if filters.start_date:
        clauses.append("date(substr(sr.timestamp, 1, 10)) >= date(?)")
        params.append(filters.start_date)

    if filters.end_date:
        clauses.append("date(substr(sr.timestamp, 1, 10)) <= date(?)")
        params.append(filters.end_date)

    if filters.engine_version:
        clauses.append("sr.engine_version = ?")
        params.append(filters.engine_version)

    if filters.cohort_class:
        clauses.append("COALESCE(sr.cohort_class, run.cohort_class) = ?")
        params.append(filters.cohort_class)

    if filters.scan_purpose:
        clauses.append("COALESCE(sr.scan_purpose, run.scan_purpose) = ?")
        params.append(filters.scan_purpose)

    if filters.sector:
        clauses.append("LOWER(COALESCE(fv.sector_name, '')) = LOWER(?)")
        params.append(filters.sector)

    if filters.min_score is not None:
        clauses.append("sr.scout_score >= ?")
        params.append(filters.min_score)

    if filters.max_score is not None:
        clauses.append("sr.scout_score <= ?")
        params.append(filters.max_score)

    where = " AND ".join(clauses)
    sql = f"""
        SELECT
            sr.id AS recommendation_id,
            sr.run_id AS scan_id,
            sr.timestamp,
            sr.ticker,
            sr.scout_score,
            sr.final_direction,
            sr.stock_outcome_label,
            sr.return_5d,
            sr.return_10d,
            sr.return_20d,
            sr.return_3d,
            sr.return_1d,
            sr.gate_snapshot_json,
            sr.gates_json,
            COALESCE(fv.sector_name, json_extract(sr.feature_vector_json, '$.sector')) AS sector_name,
            COALESCE(sr.universe_preset_id, run.universe_preset_id) AS universe_preset_id,
            COALESCE(sr.cohort_class, run.cohort_class) AS cohort_class,
            COALESCE(sr.scan_purpose, run.scan_purpose) AS scan_purpose
        FROM scan_results sr
        JOIN scan_runs run ON run.id = sr.run_id
        LEFT JOIN feature_vectors fv ON fv.recommendation_id = sr.id
        WHERE {where}
        ORDER BY sr.timestamp ASC, sr.id ASC
    """
    return sql, params


def row_to_signal(row: sqlite3.Row) -> dict[str, Any]:
    signal = {
        "recommendation_id": int(row["recommendation_id"]),
        "scan_id": int(row["scan_id"]),
        "timestamp": row["timestamp"],
        "ticker": row["ticker"],
        "direction": row["final_direction"],
        "score": row["scout_score"],
        "sector": row["sector_name"],
        "outcome_label": row["stock_outcome_label"],
        "return_5d": row["return_5d"],
        "return_10d": row["return_10d"],
        "return_20d": row["return_20d"],
        "return_3d": row["return_3d"],
        "return_1d": row["return_1d"],
        "gate_snapshot_json": row["gate_snapshot_json"],
        "gates_json": row["gates_json"],
        "universe_preset_id": row["universe_preset_id"],
        "cohort_class": row["cohort_class"],
        "scan_purpose": row["scan_purpose"],
    }
    return attach_return_fields(signal)


def fetch_signals_for_backtest_run(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            sr.id AS recommendation_id,
            sr.run_id AS scan_id,
            sr.timestamp,
            sr.ticker,
            sr.scout_score,
            sr.final_direction,
            sr.stock_outcome_label,
            sr.return_5d,
            sr.return_10d,
            sr.return_20d,
            sr.return_3d,
            sr.return_1d,
            sr.gate_snapshot_json,
            sr.gates_json,
            COALESCE(bs.sector, fv.sector_name, json_extract(sr.feature_vector_json, '$.sector')) AS sector_name,
            COALESCE(sr.universe_preset_id, run.universe_preset_id) AS universe_preset_id,
            COALESCE(sr.cohort_class, run.cohort_class) AS cohort_class,
            COALESCE(sr.scan_purpose, run.scan_purpose) AS scan_purpose
        FROM backtest_signals bs
        JOIN scan_results sr ON sr.id = bs.recommendation_id
        JOIN scan_runs run ON run.id = sr.run_id
        LEFT JOIN feature_vectors fv ON fv.recommendation_id = sr.id
        WHERE bs.run_id = ?
        ORDER BY sr.timestamp ASC, sr.id ASC
        """,
        (run_id,),
    ).fetchall()
    return [row_to_signal(row) for row in rows]


def fetch_backtest_signals(conn: sqlite3.Connection, filters: BacktestFilters) -> list[dict[str, Any]]:
    sql, params = build_signal_query(filters)
    rows = conn.execute(sql, params).fetchall()
    return [row_to_signal(row) for row in rows]


def passed_gate_keys(signal: dict[str, Any]) -> list[str]:
    snapshot = json_load(signal.get("gate_snapshot_json")) or {}
    gates = snapshot.get("gates") if isinstance(snapshot, dict) else None
    if isinstance(gates, list):
        return sorted(
            str(gate.get("key") or gate.get("code") or gate.get("name")).upper()
            for gate in gates
            if isinstance(gate, dict) and gate.get("passed") is True
        )

    compact = json_load(signal.get("gates_json")) or {}
    if isinstance(compact, dict):
        return sorted(str(key).upper() for key, passed in compact.items() if passed is True)
    return []


def gate_combination(signal: dict[str, Any]) -> Optional[str]:
    keys = passed_gate_keys(signal)
    if not keys:
        return None
    return "+".join(keys)


def preset_cohort_label(signal: dict[str, Any]) -> Optional[str]:
    parts = [
        str(value).strip()
        for value in (
            signal.get("universe_preset_id"),
            signal.get("cohort_class"),
        )
        if value not in (None, "")
    ]
    return " / ".join(parts) if parts else None


def trade_audit_row(signal: dict[str, Any]) -> dict[str, Any]:
    timestamp = str(signal.get("timestamp") or "")
    return {
        "recommendation_id": signal.get("recommendation_id"),
        "ticker": signal.get("ticker"),
        "date": timestamp[:10] if timestamp else None,
        "preset_cohort": preset_cohort_label(signal),
        "universe_preset_id": signal.get("universe_preset_id"),
        "cohort_class": signal.get("cohort_class"),
        "sector": signal.get("sector"),
        "direction": signal.get("direction"),
        "score": signal.get("score"),
        "stock_return": signal.get("stock_return"),
        "signal_return": signal_return_value(signal),
        "outcome_label": signal.get("outcome_label"),
        "gate_combination": gate_combination(signal),
    }


def compute_trade_audit(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [trade_audit_row(signal) for signal in signals]
    rows.sort(
        key=lambda row: (
            row["signal_return"] is None,
            row["signal_return"] if row["signal_return"] is not None else 0.0,
        )
    )
    return rows


def compute_max_drawdown(returns: list[float]) -> Optional[float]:
    if not returns:
        return None
    cumulative = 0.0
    peak = 0.0
    max_dd = 0.0
    for value in returns:
        cumulative += value
        peak = max(peak, cumulative)
        max_dd = min(max_dd, cumulative - peak)
    return round(max_dd, 4)


def compute_core_metrics(signals: list[dict[str, Any]]) -> dict[str, Any]:
    if not signals:
        return {
            "win_rate": 0.0,
            "loss_rate": 0.0,
            "avg_return": None,
            "avg_stock_return": None,
            "median_return": None,
            "max_drawdown": None,
            "best_trade": None,
            "worst_trade": None,
            "bullish_win_rate": 0.0,
            "bearish_win_rate": 0.0,
            "actionable_win_rate": 0.0,
            "expectancy": 0.0,
            "sample_size": 0,
        }

    wins = sum(1 for signal in signals if signal.get("outcome_label") == "WIN")
    losses = sum(1 for signal in signals if signal.get("outcome_label") == "LOSS")
    flats = sum(1 for signal in signals if signal.get("outcome_label") == "FLAT")
    sample_size = len(signals)
    pnl_returns = signal_returns(signals)
    raw_returns = stock_returns(signals)

    bullish = [signal for signal in signals if signal.get("direction") == "Bullish"]
    bearish = [signal for signal in signals if signal.get("direction") == "Bearish"]
    actionable = [signal for signal in signals if signal.get("direction") in ACTIONABLE_DIRECTIONS]

    def win_rate_for(rows: list[dict[str, Any]]) -> float:
        if not rows:
            return 0.0
        wins_only = sum(1 for row in rows if row.get("outcome_label") == "WIN")
        decided = sum(1 for row in rows if row.get("outcome_label") in ("WIN", "LOSS"))
        if decided == 0:
            return round(wins_only / len(rows) * 100, 2)
        return round(wins_only / decided * 100, 2)

    win_rate = round(wins / sample_size * 100, 2) if sample_size else 0.0
    loss_rate = round(losses / sample_size * 100, 2) if sample_size else 0.0
    avg_return = round(statistics.mean(pnl_returns), 4) if pnl_returns else None
    avg_stock_return = round(statistics.mean(raw_returns), 4) if raw_returns else None
    median_return = round(statistics.median(pnl_returns), 4) if pnl_returns else None
    expectancy = compute_directional_expectancy(pnl_returns) or 0.0

    return {
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "avg_return": avg_return,
        "avg_stock_return": avg_stock_return,
        "median_return": median_return,
        "max_drawdown": compute_max_drawdown(pnl_returns),
        "best_trade": round(max(pnl_returns), 4) if pnl_returns else None,
        "worst_trade": round(min(pnl_returns), 4) if pnl_returns else None,
        "bullish_win_rate": win_rate_for(bullish),
        "bearish_win_rate": win_rate_for(bearish),
        "actionable_win_rate": win_rate_for(actionable),
        "expectancy": expectancy,
        "sample_size": sample_size,
        "flat_count": flats,
    }


def summarize_group(rows: list[dict[str, Any]], label: str) -> dict[str, Any]:
    metrics = compute_core_metrics(rows)
    return {
        "label": label,
        "sample_size": metrics["sample_size"],
        "win_rate": metrics["win_rate"],
        "loss_rate": metrics["loss_rate"],
        "avg_return": metrics["avg_return"],
        "avg_stock_return": metrics["avg_stock_return"],
        "expectancy": metrics["expectancy"],
    }


def compute_sector_performance(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        sector = str(signal.get("sector") or "Unknown")
        grouped.setdefault(sector, []).append(signal)
    rows = [summarize_group(items, label) for label, items in grouped.items() if items]
    return sorted(rows, key=lambda row: (row.get("avg_return") or 0.0, row["win_rate"]), reverse=True)


def compute_direction_performance(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        direction = str(signal.get("direction") or "Unknown")
        grouped.setdefault(direction, []).append(signal)
    return [summarize_group(items, label) for label, items in sorted(grouped.items())]


def empty_direction_breakdown(direction: str) -> dict[str, Any]:
    return {
        "direction": direction,
        "signal_count": 0,
        "win_rate": None,
        "avg_signal_return": None,
        "avg_stock_return": None,
        "expectancy": None,
    }


def win_rate_for_signals(rows: list[dict[str, Any]]) -> Optional[float]:
    if not rows:
        return None
    wins_only = sum(1 for row in rows if row.get("outcome_label") == "WIN")
    decided = sum(1 for row in rows if row.get("outcome_label") in ("WIN", "LOSS"))
    if decided:
        return round(wins_only / decided * 100, 2)
    return round(wins_only / len(rows) * 100, 2)


def compute_direction_breakdown(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Structured Bullish / Bearish / Neutral summary rows for the backtest UI."""
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        direction = str(signal.get("direction") or "Unknown")
        grouped.setdefault(direction, []).append(signal)

    rows: list[dict[str, Any]] = []
    for direction in DIRECTION_BREAKDOWN_ORDER:
        items = grouped.get(direction, [])
        if direction == "Neutral" and not items:
            continue
        if not items:
            rows.append(empty_direction_breakdown(direction))
            continue
        metrics = compute_core_metrics(items)
        rows.append(
            {
                "direction": direction,
                "signal_count": metrics["sample_size"],
                "win_rate": win_rate_for_signals(items),
                "avg_signal_return": metrics["avg_return"],
                "avg_stock_return": metrics["avg_stock_return"],
                "expectancy": metrics["expectancy"],
            }
        )
    return rows


def audit_ticker_row(signal: dict[str, Any]) -> dict[str, Any]:
    return {
        "ticker": signal.get("ticker"),
        "direction": signal.get("direction"),
        "stock_return": signal.get("stock_return"),
        "signal_return": signal_return_value(signal),
        "outcome_label": signal.get("outcome_label"),
        "recommendation_id": signal.get("recommendation_id"),
        "timestamp": signal.get("timestamp"),
    }


def compute_sector_audit(signals: list[dict[str, Any]], losing_limit: int = 5) -> dict[str, Any]:
    """Highlight the worst sector by avg signal return and its biggest losing tickers."""
    if not signals:
        return {"worst_sector": None}

    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        sector = str(signal.get("sector") or "Unknown")
        grouped.setdefault(sector, []).append(signal)

    sector_rows: list[dict[str, Any]] = []
    for sector, items in grouped.items():
        metrics = compute_core_metrics(items)
        if metrics["avg_return"] is None:
            continue
        sector_rows.append(
            {
                "sector": sector,
                "signal_count": metrics["sample_size"],
                "win_rate": win_rate_for_signals(items),
                "avg_signal_return": metrics["avg_return"],
                "avg_stock_return": metrics["avg_stock_return"],
                "items": items,
            }
        )

    if not sector_rows:
        return {"worst_sector": None}

    worst = min(
        sector_rows,
        key=lambda row: (
            row["avg_signal_return"],
            row["win_rate"] if row["win_rate"] is not None else 100.0,
            -row["signal_count"],
        ),
    )
    losers = sorted(
        (signal for signal in worst["items"] if signal_return_value(signal) is not None),
        key=signal_return_value,
    )[:losing_limit]

    return {
        "worst_sector": {
            "sector": worst["sector"],
            "signal_count": worst["signal_count"],
            "win_rate": worst["win_rate"],
            "avg_signal_return": worst["avg_signal_return"],
            "avg_stock_return": worst["avg_stock_return"],
            "top_losing_tickers": [audit_ticker_row(signal) for signal in losers],
        }
    }


def compute_gate_performance(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        for gate_name in passed_gate_keys(signal):
            grouped.setdefault(gate_name, []).append(signal)
    rows = [summarize_group(items, label) for label, items in grouped.items() if items]
    return sorted(rows, key=lambda row: (row.get("expectancy") or 0.0, row["win_rate"]), reverse=True)


def compute_gate_combinations(signals: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for signal in signals:
        keys = passed_gate_keys(signal)
        if len(keys) < 2:
            continue
        combo = "+".join(keys[:5])
        grouped.setdefault(combo, []).append(signal)
    rows = [summarize_group(items, label) for label, items in grouped.items() if len(items) >= 2]
    rows.sort(key=lambda row: (row.get("expectancy") or 0.0, row["sample_size"]), reverse=True)
    return rows[:limit]


def compute_setups(signals: list[dict[str, Any]], limit: int = 5) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ranked = sorted(
        [
            {
                "ticker": signal.get("ticker"),
                "timestamp": signal.get("timestamp"),
                "direction": signal.get("direction"),
                "sector": signal.get("sector"),
                "score": signal.get("score"),
                "outcome_label": signal.get("outcome_label"),
                "stock_return": signal.get("stock_return"),
                "signal_return": signal_return_value(signal),
                "primary_return": signal.get("stock_return"),
                "recommendation_id": signal.get("recommendation_id"),
            }
            for signal in signals
            if signal_return_value(signal) is not None
        ],
        key=lambda row: row["signal_return"],
    )
    best = list(reversed(ranked[-limit:])) if ranked else []
    worst = ranked[:limit]
    return best, worst


def compute_analytics(signals: list[dict[str, Any]]) -> dict[str, Any]:
    metrics = compute_core_metrics(signals)
    best, worst = compute_setups(signals)
    return {
        "total_signals": metrics["sample_size"],
        "expectancy": metrics["expectancy"],
        "sector_performance": compute_sector_performance(signals),
        "direction_performance": compute_direction_performance(signals),
        "direction_breakdown": compute_direction_breakdown(signals),
        "sector_audit": compute_sector_audit(signals),
        "trade_audit": compute_trade_audit(signals),
        "gate_performance": compute_gate_performance(signals),
        "top_gate_combinations": compute_gate_combinations(signals),
        "best_setups": best,
        "worst_setups": worst,
    }


def persist_backtest_signals(conn: sqlite3.Connection, run_id: int, signals: list[dict[str, Any]]) -> int:
    conn.executemany(
        """
        INSERT INTO backtest_signals (
            run_id, recommendation_id, ticker, timestamp, direction, score,
            sector, outcome_label, return_5d, return_10d, return_20d, signal_return
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                run_id,
                signal["recommendation_id"],
                signal["ticker"],
                signal["timestamp"],
                signal.get("direction"),
                signal.get("score"),
                signal.get("sector"),
                signal.get("outcome_label"),
                signal.get("return_5d"),
                signal.get("return_10d"),
                signal.get("return_20d"),
                signal_return_value(signal),
            )
            for signal in signals
        ],
    )
    return len(signals)


def persist_backtest_metrics(conn: sqlite3.Connection, run_id: int, metrics: dict[str, Any]) -> None:
    conn.execute(
        """
        INSERT INTO backtest_metrics (
            run_id, win_rate, loss_rate, avg_return, median_return, max_drawdown,
            best_trade, worst_trade, bullish_win_rate, bearish_win_rate, actionable_win_rate
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            run_id,
            metrics["win_rate"],
            metrics["loss_rate"],
            metrics.get("avg_return"),
            metrics.get("median_return"),
            metrics.get("max_drawdown"),
            metrics.get("best_trade"),
            metrics.get("worst_trade"),
            metrics["bullish_win_rate"],
            metrics["bearish_win_rate"],
            metrics["actionable_win_rate"],
        ),
    )


def run_backtest(
    *,
    name: str,
    description: Optional[str] = None,
    filters: Optional[BacktestFilters] = None,
) -> dict[str, Any]:
    """Execute a read-only backtest over saved scan_results."""
    init_db()
    filters = filters or BacktestFilters()
    created_at = datetime.now(timezone.utc).isoformat()

    with connect() as conn:
        init_backtest_store(conn)
        cursor = conn.execute(
            """
            INSERT INTO backtest_runs (
                created_at, name, description, engine_version, backtest_version,
                start_date, end_date, filters_json, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'running')
            """,
            (
                created_at,
                name.strip() or "Untitled Backtest",
                description,
                filters.engine_version,
                BACKTEST_VERSION,
                filters.start_date,
                filters.end_date,
                json_dump(filters.to_dict()),
            ),
        )
        run_id = int(cursor.lastrowid)

        try:
            signals = fetch_backtest_signals(conn, filters)
            if not signals:
                conn.execute(
                    "UPDATE backtest_runs SET status = 'completed' WHERE id = ?",
                    (run_id,),
                )
                return {
                    "ok": True,
                    "runId": run_id,
                    "status": "completed",
                    "message": "No signals matched the selected filters.",
                    "filters": filters.to_dict(),
                    "metrics": compute_core_metrics([]),
                    "analytics": compute_analytics([]),
                    "signalsSaved": 0,
                }

            metrics = compute_core_metrics(signals)
            analytics = compute_analytics(signals)
            signals_saved = persist_backtest_signals(conn, run_id, signals)
            persist_backtest_metrics(conn, run_id, metrics)
            conn.execute(
                "UPDATE backtest_runs SET status = 'completed' WHERE id = ?",
                (run_id,),
            )
            return {
                "ok": True,
                "runId": run_id,
                "status": "completed",
                "createdAt": created_at,
                "name": name,
                "description": description,
                "filters": filters.to_dict(),
                "metrics": metrics,
                "analytics": analytics,
                "signalsSaved": signals_saved,
            }
        except Exception as exc:
            conn.execute(
                "UPDATE backtest_runs SET status = 'failed' WHERE id = ?",
                (run_id,),
            )
            raise RuntimeError(f"Backtest run {run_id} failed: {exc}") from exc


def backtest_run_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "createdAt": row["created_at"],
        "name": row["name"],
        "description": row["description"],
        "engineVersion": row["engine_version"],
        "backtestVersion": row["backtest_version"] if "backtest_version" in row.keys() else None,
        "startDate": row["start_date"],
        "endDate": row["end_date"],
        "filters": json_load(row["filters_json"]) or {},
        "status": row["status"],
    }


def list_backtest_runs(limit: int = 20) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        init_backtest_store(conn)
        rows = conn.execute(
            """
            SELECT br.*, bm.win_rate, bm.avg_return, COUNT(bs.id) AS signal_count
            FROM backtest_runs br
            LEFT JOIN backtest_metrics bm ON bm.run_id = br.id
            LEFT JOIN backtest_signals bs ON bs.run_id = br.id
            GROUP BY br.id
            ORDER BY br.created_at DESC, br.id DESC
            LIMIT ?
            """,
            (max(1, min(int(limit), 100)),),
        ).fetchall()
        return [
            {
                **backtest_run_row(row),
                "signalCount": int(row["signal_count"] or 0),
                "winRate": row["win_rate"],
                "avgReturn": row["avg_return"],
            }
            for row in rows
        ]


def get_backtest_run(run_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        init_backtest_store(conn)
        row = conn.execute("SELECT * FROM backtest_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            return None

        metrics_row = conn.execute(
            "SELECT * FROM backtest_metrics WHERE run_id = ?",
            (run_id,),
        ).fetchone()
        signal_dicts = fetch_signals_for_backtest_run(conn, run_id)

        signals = [
            {
                "recommendationId": signal["recommendation_id"],
                "ticker": signal["ticker"],
                "timestamp": signal["timestamp"],
                "direction": signal["direction"],
                "score": signal["score"],
                "sector": signal["sector"],
                "outcomeLabel": signal["outcome_label"],
                "return5d": signal["return_5d"],
                "return10d": signal["return_10d"],
                "return20d": signal["return_20d"],
            }
            for signal in signal_dicts
        ]

        metrics = None
        if metrics_row is not None:
            core = compute_core_metrics(signal_dicts)
            metrics = {
                "winRate": metrics_row["win_rate"],
                "lossRate": metrics_row["loss_rate"],
                "avgReturn": core["avg_return"],
                "avgStockReturn": core["avg_stock_return"],
                "medianReturn": metrics_row["median_return"],
                "maxDrawdown": metrics_row["max_drawdown"],
                "bestTrade": metrics_row["best_trade"],
                "worstTrade": metrics_row["worst_trade"],
                "bullishWinRate": metrics_row["bullish_win_rate"],
                "bearishWinRate": metrics_row["bearish_win_rate"],
                "actionableWinRate": metrics_row["actionable_win_rate"],
                "expectancy": core["expectancy"],
            }

        uses_legacy_metrics = backtest_run_uses_legacy_metrics(conn, run_id, row)

        return {
            "ok": True,
            "run": backtest_run_row(row),
            "metrics": metrics,
            "analytics": compute_analytics(signal_dicts),
            "signals": signals,
            "legacyMetricsWarning": uses_legacy_metrics,
            "legacyMetricsMessage": LEGACY_METRICS_WARNING if uses_legacy_metrics else None,
        }


def preview_backtest(filters: Optional[BacktestFilters] = None) -> dict[str, Any]:
    """Dry-run counts and headline metrics without persisting a backtest run."""
    init_db()
    filters = filters or BacktestFilters()
    with connect() as conn:
        signals = fetch_backtest_signals(conn, filters)
        metrics = compute_core_metrics(signals)
        analytics = compute_analytics(signals)
        return {
            "ok": True,
            "filters": filters.to_dict(),
            "metrics": metrics,
            "analytics": analytics,
            "signalsMatched": metrics["sample_size"],
        }


def main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Scout Horizon Backtesting Foundation 1.0")
    subparsers = parser.add_subparsers(dest="command")

    run_parser = subparsers.add_parser("run", help="Execute and persist a backtest run")
    run_parser.add_argument("--name", required=True)
    run_parser.add_argument("--description", default="")
    run_parser.add_argument("--start-date")
    run_parser.add_argument("--end-date")
    run_parser.add_argument("--engine-version")
    run_parser.add_argument("--cohort-class")
    run_parser.add_argument("--scan-purpose")
    run_parser.add_argument("--sector")
    run_parser.add_argument("--min-score", type=float)
    run_parser.add_argument("--max-score", type=float)

    preview_parser = subparsers.add_parser("preview", help="Preview filters without saving")
    preview_parser.add_argument("--start-date")
    preview_parser.add_argument("--end-date")
    preview_parser.add_argument("--engine-version")
    preview_parser.add_argument("--cohort-class")
    preview_parser.add_argument("--sector")
    preview_parser.add_argument("--min-score", type=float)
    preview_parser.add_argument("--max-score", type=float)

    list_parser = subparsers.add_parser("list", help="List saved backtest runs")
    list_parser.add_argument("--limit", type=int, default=10)

    show_parser = subparsers.add_parser("show", help="Show one saved backtest run")
    show_parser.add_argument("run_id", type=int)

    args = parser.parse_args()
    if args.command == "run":
        payload = {
            "startDate": args.start_date,
            "endDate": args.end_date,
            "engineVersion": args.engine_version,
            "cohortClass": args.cohort_class,
            "scanPurpose": args.scan_purpose,
            "sector": args.sector,
            "minScore": args.min_score,
            "maxScore": args.max_score,
        }
        result = run_backtest(
            name=args.name,
            description=args.description or None,
            filters=parse_backtest_filters(payload),
        )
        print(json.dumps(result, indent=2))
        return 0
    if args.command == "preview":
        payload = {
            "startDate": args.start_date,
            "endDate": args.end_date,
            "engineVersion": args.engine_version,
            "cohortClass": args.cohort_class,
            "sector": args.sector,
            "minScore": args.min_score,
            "maxScore": args.max_score,
        }
        print(json.dumps(preview_backtest(parse_backtest_filters(payload)), indent=2))
        return 0
    if args.command == "list":
        print(json.dumps({"ok": True, "runs": list_backtest_runs(args.limit)}, indent=2))
        return 0
    if args.command == "show":
        result = get_backtest_run(args.run_id)
        if result is None:
            print(json.dumps({"ok": False, "message": "Backtest run not found."}, indent=2))
            return 1
        print(json.dumps(result, indent=2))
        return 0

    parser.print_help()
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
