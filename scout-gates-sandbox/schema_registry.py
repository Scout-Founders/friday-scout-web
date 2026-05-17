#!/usr/bin/env python3
"""Read-only schema registry for Scout sandbox SQLite memory."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


EXPECTED_SCHEMAS: dict[str, dict[str, Any]] = {
    "scan_runs": {
        "table": "scan_runs",
        "columns": {
            "id": "INTEGER",
            "timestamp": "TEXT",
            "universe_mode": "TEXT",
            "pick_mode": "TEXT",
            "timeout": "REAL",
            "candidates_json": "TEXT",
            "api_url": "TEXT",
            "created_at": "TEXT",
            "universe_snapshot_json": "TEXT",
            "engine_version": "TEXT",
            "git_commit_hash": "TEXT",
            "created_at_utc": "TEXT",
        },
    },
    "scan_results": {
        "table": "scan_results",
        "columns": {
            "id": "INTEGER",
            "run_id": "INTEGER",
            "timestamp": "TEXT",
            "ticker": "TEXT",
            "scout_score": "REAL",
            "bull_score": "REAL",
            "bear_score": "REAL",
            "net_direction": "REAL",
            "final_direction": "TEXT",
            "final_option_pick_json": "TEXT",
            "gates_json": "TEXT",
            "gate_explanations_json": "TEXT",
            "failed_gates_json": "TEXT",
            "failure_reasons_json": "TEXT",
            "raw_fmp_inputs_json": "TEXT",
            "raw_result_json": "TEXT",
            "created_at": "TEXT",
            "is_test_record": "INTEGER",
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
            "engine_version": "TEXT",
            "gate_snapshot_json": "TEXT",
            "feature_vector_json": "TEXT",
            "explanation_json": "TEXT",
        },
    },
    "feature_vectors": {
        "table": "feature_vectors",
        "columns": {
            "id": "INTEGER",
            "recommendation_id": "INTEGER",
            "scan_id": "INTEGER",
            "ticker": "TEXT",
            "timestamp": "TEXT",
            "engine_version": "TEXT",
            "return_1d": "REAL",
            "return_3d": "REAL",
            "return_5d": "REAL",
            "return_20d": "REAL",
            "distance_to_20ema": "REAL",
            "distance_to_50ema": "REAL",
            "distance_to_200ema": "REAL",
            "atr": "REAL",
            "gap_percent": "REAL",
            "rsi": "REAL",
            "macd_state": "TEXT",
            "relative_volume": "REAL",
            "volume_spike_percent": "REAL",
            "options_volume_score": "REAL",
            "call_put_skew": "REAL",
            "open_interest_change": "REAL",
            "iv_percentile": "REAL",
            "beta": "REAL",
            "spy_correlation": "REAL",
            "qqq_correlation": "REAL",
            "spy_trend": "TEXT",
            "qqq_trend": "TEXT",
            "vix_level": "REAL",
            "breadth_score": "REAL",
            "risk_regime": "TEXT",
            "sector_name": "TEXT",
            "sector_rank": "INTEGER",
            "sector_strength": "REAL",
            "market_cap_bucket": "TEXT",
            "revenue_growth": "REAL",
            "earnings_growth": "REAL",
            "piotroski": "REAL",
            "altman": "REAL",
            "debt_profile": "TEXT",
            "earnings_days_away": "REAL",
            "gate_states_json": "TEXT",
            "gate_scores_json": "TEXT",
            "outcome_1d": "TEXT",
            "outcome_3d": "TEXT",
            "outcome_5d": "TEXT",
            "outcome_10d": "TEXT",
            "outcome_20d": "TEXT",
            "final_label": "TEXT",
            "layer_weights_json": "TEXT",
            "raw_feature_json": "TEXT",
            "created_at": "TEXT",
            "updated_at": "TEXT",
        },
    },
    "gate_snapshots": {
        "table": "scan_results",
        "columns": {
            "gate_snapshot_json": "TEXT",
            "engine_version": "TEXT",
        },
    },
    "universe_snapshots": {
        "table": "scan_runs",
        "columns": {
            "universe_snapshot_json": "TEXT",
        },
    },
    "outcome_update_audit": {
        "table": "outcome_update_audit",
        "columns": {
            "id": "INTEGER",
            "timestamp": "TEXT",
            "ticker": "TEXT",
            "row_id": "INTEGER",
            "old_values_json": "TEXT",
            "new_values_json": "TEXT",
            "source_endpoint": "TEXT",
            "engine_version": "TEXT",
            "created_at": "TEXT",
        },
    },
    "engine_versions": {
        "table": "scan_results",
        "columns": {
            "engine_version": "TEXT",
        },
    },
    "pattern_intelligence": {
        "table": "pattern_intelligence",
        "columns": {
            "pattern_id": "TEXT",
            "pattern_signature": "TEXT",
            "sample_size": "INTEGER",
            "win_rate": "REAL",
            "loss_rate": "REAL",
            "avg_1d": "REAL",
            "avg_3d": "REAL",
            "avg_5d": "REAL",
            "avg_10d": "REAL",
            "avg_20d": "REAL",
            "expectancy_score": "REAL",
            "confidence_score": "REAL",
            "created_at_utc": "TEXT",
        },
    },
    "gate_attributions": {
        "table": "gate_attributions",
        "columns": {
            "id": "INTEGER",
            "scan_id": "INTEGER",
            "ticker": "TEXT",
            "gate_name": "TEXT",
            "gate_score": "REAL",
            "gate_weight": "REAL",
            "contribution_pct": "REAL",
            "gate_rank": "INTEGER",
            "regime_tag": "TEXT",
            "created_at_utc": "TEXT",
        },
    },
    "gate_alpha_metrics": {
        "table": "gate_alpha_metrics",
        "columns": {
            "id": "INTEGER",
            "gate_name": "TEXT",
            "sector": "TEXT",
            "market_regime": "TEXT",
            "volatility_regime": "TEXT",
            "sample_count": "INTEGER",
            "wins": "INTEGER",
            "losses": "INTEGER",
            "win_rate": "REAL",
            "avg_return": "REAL",
            "expectancy": "REAL",
            "confidence_score": "REAL",
            "last_updated_utc": "TEXT",
        },
    },
    "regime_snapshots": {
        "table": "regime_snapshots",
        "columns": {
            "id": "INTEGER",
            "scan_id": "INTEGER",
            "ticker": "TEXT",
            "sector": "TEXT",
            "market_trend": "TEXT",
            "volatility_regime": "TEXT",
            "liquidity_regime": "TEXT",
            "earnings_proximity": "TEXT",
            "macro_bias": "TEXT",
            "timestamp": "TEXT",
        },
    },
}


def normalize_type(value: str) -> str:
    upper = (value or "").upper()
    if "INT" in upper:
        return "INTEGER"
    if any(token in upper for token in ("REAL", "FLOA", "DOUB")):
        return "REAL"
    if any(token in upper for token in ("CHAR", "CLOB", "TEXT")):
        return "TEXT"
    if not upper:
        return ""
    return upper


def table_columns(conn: sqlite3.Connection, table: str) -> dict[str, str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {str(row[1]): normalize_type(str(row[2])) for row in rows}


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return (
        conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table,),
        ).fetchone()
        is not None
    )


def validate_schema(db_path: Path) -> dict[str, Any]:
    checks = []
    warnings = []
    drift = []
    if not db_path.exists():
        return {
            "status": "WARNING",
            "checks": [],
            "warnings": [f"SQLite database does not exist yet: {db_path}"],
            "drift": [],
        }

    conn = sqlite3.connect(db_path)
    try:
        for name, expected in EXPECTED_SCHEMAS.items():
            table = expected["table"]
            expected_columns = expected["columns"]
            if not table_exists(conn, table):
                message = f"{name}: table {table} is missing."
                warnings.append(message)
                checks.append({"name": name, "status": "WARNING", "message": message})
                continue

            live_columns = table_columns(conn, table)
            missing = [column for column in expected_columns if column not in live_columns]
            mismatched = [
                {
                    "column": column,
                    "expected": normalize_type(expected_type),
                    "actual": live_columns.get(column),
                }
                for column, expected_type in expected_columns.items()
                if column in live_columns
                and normalize_type(expected_type)
                and live_columns.get(column) != normalize_type(expected_type)
            ]
            if mismatched:
                message = f"{name}: schema drift detected in {len(mismatched)} columns."
                drift.append({"schema": name, "columns": mismatched})
                checks.append({"name": name, "status": "DRIFT DETECTED", "message": message})
            elif missing:
                message = f"{name}: missing columns: {', '.join(missing)}."
                warnings.append(message)
                checks.append({"name": name, "status": "WARNING", "message": message})
            else:
                checks.append({"name": name, "status": "PASS", "message": f"{name}: schema matches registry."})
    finally:
        conn.close()

    status = "PASS"
    if warnings:
        status = "WARNING"
    if drift:
        status = "DRIFT DETECTED"
    return {
        "status": status,
        "checks": checks,
        "warnings": warnings,
        "drift": drift,
    }
