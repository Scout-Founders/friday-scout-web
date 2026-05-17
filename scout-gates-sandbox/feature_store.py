#!/usr/bin/env python3
"""Institutional feature store for Scout Horizon-1 sandbox memory."""

from __future__ import annotations

import json
import sqlite3
from typing import Any, Optional


TECHNICAL_FLOW_WEIGHT = 0.60
MACRO_SECTOR_WEIGHT = 0.25
FUNDAMENTALS_WEIGHT = 0.15


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_load(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def market_cap_bucket(value: Any) -> Optional[str]:
    market_cap = to_float(value)
    if market_cap is None:
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


def init_feature_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS feature_vectors (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recommendation_id INTEGER UNIQUE,
            scan_id INTEGER NOT NULL,
            ticker TEXT NOT NULL,
            timestamp TEXT,
            engine_version TEXT,

            return_1d REAL,
            return_3d REAL,
            return_5d REAL,
            return_20d REAL,
            distance_to_20ema REAL,
            distance_to_50ema REAL,
            distance_to_200ema REAL,
            atr REAL,
            gap_percent REAL,
            rsi REAL,
            macd_state TEXT,

            relative_volume REAL,
            volume_spike_percent REAL,
            options_volume_score REAL,
            call_put_skew REAL,
            open_interest_change REAL,
            iv_percentile REAL,

            beta REAL,
            spy_correlation REAL,
            qqq_correlation REAL,

            spy_trend TEXT,
            qqq_trend TEXT,
            vix_level REAL,
            breadth_score REAL,
            risk_regime TEXT,
            sector_name TEXT,
            sector_rank INTEGER,
            sector_strength REAL,

            market_cap_bucket TEXT,
            revenue_growth REAL,
            earnings_growth REAL,
            piotroski REAL,
            altman REAL,
            debt_profile TEXT,
            earnings_days_away REAL,

            gate_states_json TEXT,
            gate_scores_json TEXT,

            outcome_1d TEXT,
            outcome_3d TEXT,
            outcome_5d TEXT,
            outcome_10d TEXT,
            outcome_20d TEXT,
            final_label TEXT,

            layer_weights_json TEXT,
            raw_feature_json TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_feature_vectors_scan
            ON feature_vectors(scan_id, ticker);
        CREATE INDEX IF NOT EXISTS idx_feature_vectors_ticker
            ON feature_vectors(ticker, timestamp DESC);
        CREATE INDEX IF NOT EXISTS idx_feature_vectors_engine
            ON feature_vectors(engine_version);
        """
    )


def gate_state_maps(gate_snapshot: dict[str, Any]) -> tuple[dict[str, Optional[bool]], dict[str, Any]]:
    states: dict[str, Optional[bool]] = {}
    scores: dict[str, Any] = {}
    for gate in gate_snapshot.get("gates", []) if isinstance(gate_snapshot, dict) else []:
        if not isinstance(gate, dict):
            continue
        key = str(gate.get("key") or gate.get("code") or gate.get("name"))
        states[key] = gate.get("passed") if gate.get("passed") in (True, False) else None
        scores[key] = gate.get("raw_score")
    return states, scores


def build_feature_vector(
    result: dict[str, Any],
    payload: dict[str, Any],
    gate_snapshot: dict[str, Any],
    engine_version: str,
    scan_id: int,
    recommendation_id: int,
    sector_rank: Optional[int] = None,
) -> dict[str, Any]:
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    direction = result.get("directionBreakdown") if isinstance(result.get("directionBreakdown"), dict) else {}
    source = {**raw, **result, **direction}
    gate_states, gate_scores = gate_state_maps(gate_snapshot)
    market_cap = first_present(source, ("market_cap", "marketCap", "mktCap"))
    sector_name = first_present(source, ("sector", "sector_name", "sectorName"))
    return {
        "recommendation_id": recommendation_id,
        "scan_id": scan_id,
        "ticker": result.get("ticker") or raw.get("ticker"),
        "timestamp": payload.get("runTimestamp"),
        "engine_version": engine_version,
        "return_1d": None,
        "return_3d": None,
        "return_5d": None,
        "return_20d": None,
        "distance_to_20ema": to_float(first_present(source, ("distance_to_20ema", "distanceTo20Ema", "dist20ema"))),
        "distance_to_50ema": to_float(first_present(source, ("distance_to_50ema", "distanceTo50Ema", "dist50ema"))),
        "distance_to_200ema": to_float(first_present(source, ("distance_to_200ema", "distanceTo200Ema", "dist200ema"))),
        "atr": to_float(first_present(source, ("atr", "ATR"))),
        "gap_percent": to_float(first_present(source, ("gap_percent", "gapPercent", "gap"))),
        "rsi": to_float(first_present(source, ("rsi", "RSI"))),
        "macd_state": first_present(source, ("macd_state", "macdState", "macd")),
        "relative_volume": to_float(first_present(source, ("relative_volume", "relativeVolume", "volume_ratio", "volumeRatio"))),
        "volume_spike_percent": to_float(first_present(source, ("volume_spike_percent", "volumeSpikePercent"))),
        "options_volume_score": to_float(first_present(source, ("options_volume_score", "optionsVolumeScore"))),
        "call_put_skew": to_float(first_present(source, ("call_put_skew", "callPutSkew", "putCallSkew"))),
        "open_interest_change": to_float(first_present(source, ("open_interest_change", "openInterestChange"))),
        "iv_percentile": to_float(first_present(source, ("iv_percentile", "ivPercentile", "iv_rank", "ivRank"))),
        "beta": to_float(first_present(source, ("beta",))),
        "spy_correlation": to_float(first_present(source, ("spy_correlation", "spyCorrelation"))),
        "qqq_correlation": to_float(first_present(source, ("qqq_correlation", "qqqCorrelation"))),
        "spy_trend": first_present(source, ("spy_trend", "spyTrend")),
        "qqq_trend": first_present(source, ("qqq_trend", "qqqTrend")),
        "vix_level": to_float(first_present(source, ("vix_level", "vixLevel", "vix"))),
        "breadth_score": to_float(first_present(source, ("breadth_score", "breadthScore"))),
        "risk_regime": first_present(source, ("risk_regime", "riskRegime")),
        "sector_name": sector_name,
        "sector_rank": sector_rank,
        "sector_strength": to_float(first_present(source, ("sector_strength", "sectorStrength"))),
        "market_cap_bucket": market_cap_bucket(market_cap),
        "revenue_growth": to_float(first_present(source, ("revenue_growth", "revenueGrowth"))),
        "earnings_growth": to_float(first_present(source, ("earnings_growth", "earningsGrowth"))),
        "piotroski": to_float(first_present(source, ("piotroski", "piotroskiScore"))),
        "altman": to_float(first_present(source, ("altman", "altmanZScore"))),
        "debt_profile": first_present(source, ("debt_profile", "debtProfile")),
        "earnings_days_away": to_float(first_present(source, ("earnings_days_away", "earnings_days", "earningsDays", "days_to_earnings"))),
        "gate_states": gate_states,
        "gate_scores": gate_scores,
        "outcome_1d": None,
        "outcome_3d": None,
        "outcome_5d": None,
        "outcome_10d": None,
        "outcome_20d": None,
        "final_label": None,
        "layer_weights": {
            "technical_flow": TECHNICAL_FLOW_WEIGHT,
            "macro_sector": MACRO_SECTOR_WEIGHT,
            "fundamentals": FUNDAMENTALS_WEIGHT,
        },
    }


def save_feature_vector(conn: sqlite3.Connection, vector: dict[str, Any]) -> None:
    init_feature_store(conn)
    conn.execute(
        """
        INSERT OR REPLACE INTO feature_vectors (
            recommendation_id, scan_id, ticker, timestamp, engine_version,
            return_1d, return_3d, return_5d, return_20d,
            distance_to_20ema, distance_to_50ema, distance_to_200ema,
            atr, gap_percent, rsi, macd_state,
            relative_volume, volume_spike_percent, options_volume_score,
            call_put_skew, open_interest_change, iv_percentile,
            beta, spy_correlation, qqq_correlation,
            spy_trend, qqq_trend, vix_level, breadth_score, risk_regime,
            sector_name, sector_rank, sector_strength,
            market_cap_bucket, revenue_growth, earnings_growth, piotroski,
            altman, debt_profile, earnings_days_away,
            gate_states_json, gate_scores_json,
            outcome_1d, outcome_3d, outcome_5d, outcome_10d, outcome_20d,
            final_label, layer_weights_json, raw_feature_json, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """,
        (
            vector.get("recommendation_id"),
            vector.get("scan_id"),
            vector.get("ticker"),
            vector.get("timestamp"),
            vector.get("engine_version"),
            vector.get("return_1d"),
            vector.get("return_3d"),
            vector.get("return_5d"),
            vector.get("return_20d"),
            vector.get("distance_to_20ema"),
            vector.get("distance_to_50ema"),
            vector.get("distance_to_200ema"),
            vector.get("atr"),
            vector.get("gap_percent"),
            vector.get("rsi"),
            vector.get("macd_state"),
            vector.get("relative_volume"),
            vector.get("volume_spike_percent"),
            vector.get("options_volume_score"),
            vector.get("call_put_skew"),
            vector.get("open_interest_change"),
            vector.get("iv_percentile"),
            vector.get("beta"),
            vector.get("spy_correlation"),
            vector.get("qqq_correlation"),
            vector.get("spy_trend"),
            vector.get("qqq_trend"),
            vector.get("vix_level"),
            vector.get("breadth_score"),
            vector.get("risk_regime"),
            vector.get("sector_name"),
            vector.get("sector_rank"),
            vector.get("sector_strength"),
            vector.get("market_cap_bucket"),
            vector.get("revenue_growth"),
            vector.get("earnings_growth"),
            vector.get("piotroski"),
            vector.get("altman"),
            vector.get("debt_profile"),
            vector.get("earnings_days_away"),
            json_dump(vector.get("gate_states") or {}),
            json_dump(vector.get("gate_scores") or {}),
            vector.get("outcome_1d"),
            vector.get("outcome_3d"),
            vector.get("outcome_5d"),
            vector.get("outcome_10d"),
            vector.get("outcome_20d"),
            vector.get("final_label"),
            json_dump(vector.get("layer_weights") or {}),
            json_dump(vector),
        ),
    )


def refresh_feature_vector_labels(conn: sqlite3.Connection, recommendation_id: Optional[int] = None) -> int:
    init_feature_store(conn)
    where = "WHERE id = ?" if recommendation_id is not None else ""
    params = (recommendation_id,) if recommendation_id is not None else ()
    rows = conn.execute(
        f"""
        SELECT id, return_1d, return_3d, return_5d, return_10d, return_20d,
               stock_outcome_label_1d, stock_outcome_label_3d,
               stock_outcome_label_5d, stock_outcome_label_10d,
               stock_outcome_label_20d, stock_outcome_label
        FROM scan_results
        {where}
        """,
        params,
    ).fetchall()
    updated = 0
    for row in rows:
        cursor = conn.execute(
            """
            UPDATE feature_vectors
            SET return_1d = ?, return_3d = ?, return_5d = ?, return_20d = ?,
                outcome_1d = ?, outcome_3d = ?, outcome_5d = ?,
                outcome_10d = ?, outcome_20d = ?, final_label = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE recommendation_id = ?
            """,
            (
                row["return_1d"],
                row["return_3d"],
                row["return_5d"],
                row["return_20d"],
                row["stock_outcome_label_1d"],
                row["stock_outcome_label_3d"],
                row["stock_outcome_label_5d"],
                row["stock_outcome_label_10d"],
                row["stock_outcome_label_20d"],
                row["stock_outcome_label"],
                row["id"],
            ),
        )
        updated += cursor.rowcount
    return updated


def get_feature_intelligence_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    init_feature_store(conn)
    total = int(conn.execute("SELECT COUNT(*) FROM feature_vectors").fetchone()[0] or 0)
    labeled = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM feature_vectors
            WHERE final_label IN ('WIN', 'LOSS', 'FLAT')
               OR outcome_1d IN ('WIN', 'LOSS', 'FLAT')
               OR outcome_3d IN ('WIN', 'LOSS', 'FLAT')
               OR outcome_5d IN ('WIN', 'LOSS', 'FLAT')
               OR outcome_10d IN ('WIN', 'LOSS', 'FLAT')
               OR outcome_20d IN ('WIN', 'LOSS', 'FLAT')
            """
        ).fetchone()[0]
        or 0
    )
    unique_tickers = int(conn.execute("SELECT COUNT(DISTINCT ticker) FROM feature_vectors").fetchone()[0] or 0)
    unique_sectors = int(
        conn.execute(
            "SELECT COUNT(DISTINCT sector_name) FROM feature_vectors WHERE sector_name IS NOT NULL AND sector_name != ''"
        ).fetchone()[0]
        or 0
    )
    engine_versions = [
        row["engine_version"]
        for row in conn.execute(
            """
            SELECT DISTINCT engine_version FROM feature_vectors
            WHERE engine_version IS NOT NULL AND engine_version != ''
            ORDER BY engine_version
            """
        ).fetchall()
    ]
    readiness = 0
    if total:
        labeled_score = min(labeled / max(total, 1), 1.0) * 45
        coverage_score = min(total / 1000, 1.0) * 35
        diversity_score = min((unique_tickers + unique_sectors) / 100, 1.0) * 20
        readiness = round(labeled_score + coverage_score + diversity_score, 1)
    return {
        "total_feature_vectors": total,
        "labeled_vectors": labeled,
        "unlabeled_vectors": max(total - labeled, 0),
        "unique_tickers": unique_tickers,
        "unique_sectors": unique_sectors,
        "engine_versions": engine_versions,
        "feature_readiness_score": readiness,
    }
