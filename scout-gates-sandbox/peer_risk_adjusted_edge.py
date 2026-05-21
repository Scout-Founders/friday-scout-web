#!/usr/bin/env python3
"""Peer Risk-Adjusted Edge (PRAE) — P0: scoringBreakdown only (no score adjustment).

Architecture: docs/peer-risk-adjusted-edge.md
"""

from __future__ import annotations

import math
from typing import Any, Optional

from run_gates import CandidateResult

# --- Peer cohort ---
MIN_PEER_COUNT = 3
MIN_UNIVERSE_SIZE = 2
PRIMARY_GROUP_SECTOR = "sector"
PRIMARY_GROUP_UNIVERSE = "universe"

# --- Influence caps (P1; P0 keeps adjustment at 0) ---
PEER_CONVICTION_CAP = 5
PEER_CONVICTION_CAP_WHEN_EI_ACTIVE = 3
PEER_REFERENCE_WEIGHT = 0.25
MAX_EDGE_MAGNITUDE = 2.0
EDGE_TO_POINTS_MULTIPLIER = 2.0

FEATURE_WEIGHTS: dict[str, float] = {
    "scout_score": 0.40,
    "rsi": 0.15,
    "change": 0.15,
    "wind": 0.15,
    "dcf_gap": 0.15,
}
Z_SCORE_WINSOR = 2.5
MIN_RETURN_SAMPLES = 20

MODE_UNAVAILABLE = "unavailable"
MODE_INSUFFICIENT_PEERS = "insufficient_peers"
MODE_PARTIAL_FEATURES = "partial_features"
MODE_AWAITING_RETURNS = "awaiting_returns"
MODE_SCORED = "scored"

FEATURE_SOURCE_KEYS: dict[str, tuple[str, ...]] = {
    "scout_score": ("scout_score",),
    "rsi": ("rsi", "RSI"),
    "change": ("change",),
    "wind": ("wind",),
    "dcf_gap": ("dcf_gap", "dcfGap"),
    "beta": ("beta",),
    "iv_percentile": ("iv_percentile", "ivPercentile", "iv_rank", "ivRank"),
    "relative_volume": ("relative_volume", "relativeVolume", "volume_ratio", "volumeRatio"),
}


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def round_optional(value: Optional[float], digits: int = 4) -> Optional[float]:
    if value is None:
        return None
    return round(value, digits)


def extract_features(data: dict[str, Any]) -> dict[str, Optional[float]]:
    return {
        key: to_float(first_present(data, FEATURE_SOURCE_KEYS[key]))
        for key in FEATURE_WEIGHTS
    }


def cohort_values(rows: list[dict[str, Any]], feature: str) -> list[float]:
    values: list[float] = []
    for row in rows:
        value = row["features"].get(feature)
        if value is not None:
            values.append(float(value))
    return values


def cohort_mean_std(values: list[float]) -> tuple[Optional[float], Optional[float]]:
    if not values:
        return None, None
    mean = sum(values) / len(values)
    if len(values) < 2:
        return mean, None
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    std = math.sqrt(variance)
    return mean, std if std > 0 else None


def percentile_rank(value: float, peers: list[float]) -> Optional[float]:
    if len(peers) < 2:
        return None
    less = sum(1 for peer in peers if peer < value)
    return round(100 * less / (len(peers) - 1), 2)


def z_score(value: float, mean: Optional[float], std: Optional[float]) -> Optional[float]:
    if mean is None or std is None or std <= 0:
        return None
    return clamp((value - mean) / std, -Z_SCORE_WINSOR, Z_SCORE_WINSOR)


def composite_z(features: dict[str, Optional[float]], stats: dict[str, dict[str, Optional[float]]]) -> Optional[float]:
    weighted_sum = 0.0
    weight_total = 0.0
    for key, weight in FEATURE_WEIGHTS.items():
        value = features.get(key)
        if value is None:
            continue
        cohort = stats.get(key, {})
        z = z_score(value, cohort.get("mean"), cohort.get("std"))
        if z is None:
            continue
        weighted_sum += weight * z
        weight_total += weight
    if weight_total <= 0:
        return None
    return weighted_sum / weight_total


def build_group_stats(rows: list[dict[str, Any]]) -> dict[str, dict[str, Optional[float]]]:
    stats: dict[str, dict[str, Optional[float]]] = {}
    for feature in FEATURE_WEIGHTS:
        values = cohort_values(rows, feature)
        mean, std = cohort_mean_std(values)
        stats[feature] = {"mean": mean, "std": std, "values": values}
    return stats


def select_primary_group(
    row: dict[str, Any],
    universe_rows: list[dict[str, Any]],
    sector_rows: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    if len(sector_rows) >= MIN_PEER_COUNT:
        return PRIMARY_GROUP_SECTOR, sector_rows
    if len(universe_rows) >= MIN_PEER_COUNT:
        return PRIMARY_GROUP_UNIVERSE, universe_rows
    return PRIMARY_GROUP_UNIVERSE, universe_rows


def compute_edge_components(
    row: dict[str, Any],
    primary_stats: dict[str, dict[str, Optional[float]]],
    score_percentile: Optional[float],
) -> dict[str, Optional[float]]:
    features = row["features"]
    data = row["data"]
    raw_edge = composite_z(features, primary_stats)

    beta = to_float(first_present(data, FEATURE_SOURCE_KEYS["beta"]))
    risk_adjustment = None
    if beta is not None:
        risk_adjustment = round(-0.15 * max(0.0, beta - 1.2), 4)

    peer_adjustment = None
    if score_percentile is not None:
        peer_adjustment = round((score_percentile - 50) / 50, 4)

    iv = to_float(first_present(data, FEATURE_SOURCE_KEYS["iv_percentile"]))
    volatility_penalty = None
    if iv is not None:
        if iv >= 80:
            volatility_penalty = -0.05
        elif iv <= 20:
            volatility_penalty = 0.05
        else:
            volatility_penalty = 0.0

    rel_vol = to_float(first_present(data, FEATURE_SOURCE_KEYS["relative_volume"]))
    liquidity_penalty = None
    if rel_vol is not None and rel_vol < 0.7:
        liquidity_penalty = -0.1

    parts = [
        raw_edge if raw_edge is not None else 0.0,
        risk_adjustment or 0.0,
        peer_adjustment or 0.0,
        0.0,
        volatility_penalty or 0.0,
        liquidity_penalty or 0.0,
    ]
    if raw_edge is None and risk_adjustment is None and peer_adjustment is None:
        final_edge = None
    else:
        final_edge = clamp(sum(parts), -MAX_EDGE_MAGNITUDE, MAX_EDGE_MAGNITUDE)

    return {
        "rawEdge": round_optional(raw_edge),
        "riskAdjustment": risk_adjustment,
        "peerAdjustment": peer_adjustment,
        "sectorAdjustment": 0.0,
        "volatilityPenalty": volatility_penalty,
        "liquidityPenalty": liquidity_penalty,
        "finalEdge": round_optional(final_edge),
    }


def resolve_mode(
    universe_size: int,
    primary_size: int,
    present_feature_count: int,
) -> str:
    if universe_size < MIN_UNIVERSE_SIZE:
        return MODE_INSUFFICIENT_PEERS
    if primary_size < MIN_PEER_COUNT:
        return MODE_INSUFFICIENT_PEERS
    if present_feature_count < 3:
        return MODE_PARTIAL_FEATURES
    return MODE_SCORED


def status_message_for_mode(
    mode: str,
    *,
    primary_group: str,
    peer_count: int,
    sector: Optional[str],
) -> str:
    if mode == MODE_INSUFFICIENT_PEERS:
        if peer_count < MIN_UNIVERSE_SIZE:
            return "Peer edge unavailable: fewer than two tickers in this scan."
        return (
            f"Peer edge unavailable: only {peer_count} name(s) in "
            f"{primary_group} cohort (minimum {MIN_PEER_COUNT}). Universe percentiles may still apply."
        )
    if mode == MODE_PARTIAL_FEATURES:
        return "Peer edge computed from a partial feature set; interpret with caution."
    return (
        "Peer Risk-Adjusted Edge is informational (P0). Score adjustment is disabled. "
        "Sharpe and t-stat populate after outcome history is available."
    )


def build_peer_bundle_for_run(
    results: list[CandidateResult],
    *,
    run_timestamp: str,
) -> dict[str, dict[str, Any]]:
    """Build per-ticker peer context for an entire scan run (in-memory only)."""
    del run_timestamp  # reserved for future snapshot keys
    rows = [
        {
            "ticker": str(item.data.get("ticker") or item.ticker).upper(),
            "data": item.data,
            "sector": str(item.data.get("sector") or "").strip() or None,
            "features": extract_features(item.data),
        }
        for item in results
    ]
    universe_rows = list(rows)
    sector_index: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row["sector"]:
            sector_index.setdefault(row["sector"], []).append(row)

    universe_stats = build_group_stats(universe_rows)
    bundle: dict[str, dict[str, Any]] = {}

    for row in rows:
        ticker = row["ticker"]
        sector = row["sector"]
        sector_rows = sector_index.get(sector or "", [])
        primary_group, primary_rows = select_primary_group(row, universe_rows, sector_rows)
        primary_stats = build_group_stats(primary_rows)

        score_values = cohort_values(primary_rows, "scout_score")
        score_percentile = None
        if row["features"].get("scout_score") is not None and score_values:
            score_percentile = percentile_rank(float(row["features"]["scout_score"]), score_values)

        universe_score_values = cohort_values(universe_rows, "scout_score")
        universe_percentile = None
        if row["features"].get("scout_score") is not None and universe_score_values:
            universe_percentile = percentile_rank(
                float(row["features"]["scout_score"]),
                universe_score_values,
            )

        present_features = sum(1 for key in FEATURE_WEIGHTS if row["features"].get(key) is not None)
        mode = resolve_mode(len(universe_rows), len(primary_rows), present_features)
        edge_components = compute_edge_components(row, primary_stats, score_percentile)
        final_edge = edge_components["finalEdge"]
        if mode != MODE_SCORED:
            final_edge = None
            edge_components = {
                key: None
                for key in (
                    "rawEdge",
                    "riskAdjustment",
                    "peerAdjustment",
                    "sectorAdjustment",
                    "volatilityPenalty",
                    "liquidityPenalty",
                    "finalEdge",
                )
            }

        bundle[ticker] = {
            "mode": mode,
            "active": mode == MODE_SCORED and final_edge is not None,
            "primaryGroup": primary_group,
            "primaryGroupKey": sector if primary_group == PRIMARY_GROUP_SECTOR else "scan_universe",
            "peerCount": len(primary_rows),
            "sector": sector,
            "universeSize": len(universe_rows),
            "presentFeatureCount": present_features,
            "percentiles": {
                "scoutScore": score_percentile,
                "universeScore": universe_percentile,
            },
            "edgeComponents": edge_components,
            "peerRiskAdjustedEdge": final_edge,
            "sharpeRatio": None,
            "tStat": None,
            "returnsMode": MODE_AWAITING_RETURNS,
            "statusMessage": status_message_for_mode(
                mode,
                primary_group=primary_group,
                peer_count=len(primary_rows),
                sector=sector,
            ),
        }
    return bundle


def build_scoring_breakdown(
    ticker: str,
    result_data: dict[str, Any],
    peer_bundle: dict[str, dict[str, Any]],
    *,
    earnings_intelligence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Build scoringBreakdown for one ticker from a precomputed run bundle."""
    del result_data
    entry = peer_bundle.get(str(ticker).upper(), {})
    mode = str(entry.get("mode") or MODE_UNAVAILABLE)
    edge = entry.get("peerRiskAdjustedEdge")
    components = entry.get("edgeComponents") if isinstance(entry.get("edgeComponents"), dict) else {}
    percentiles = entry.get("percentiles") if isinstance(entry.get("percentiles"), dict) else {}

    return {
        "active": bool(entry.get("active")),
        "mode": mode,
        "peerRiskAdjustedEdge": edge,
        "sharpeRatio": entry.get("sharpeRatio"),
        "tStat": entry.get("tStat"),
        "returnsMode": entry.get("returnsMode", MODE_AWAITING_RETURNS),
        "peerRiskAdjustedEdgeBreakdown": {
            "rawEdge": components.get("rawEdge"),
            "riskAdjustment": components.get("riskAdjustment"),
            "peerAdjustment": components.get("peerAdjustment"),
            "sectorAdjustment": components.get("sectorAdjustment"),
            "volatilityPenalty": components.get("volatilityPenalty"),
            "liquidityPenalty": components.get("liquidityPenalty"),
            "finalEdge": components.get("finalEdge"),
        },
        "peerContext": {
            "primaryGroup": entry.get("primaryGroup"),
            "primaryGroupKey": entry.get("primaryGroupKey"),
            "peerCount": entry.get("peerCount"),
            "universeSize": entry.get("universeSize"),
            "sector": entry.get("sector"),
            "universePercentile": percentiles.get("universeScore"),
            "sectorPercentile": percentiles.get("scoutScore")
            if entry.get("primaryGroup") == PRIMARY_GROUP_SECTOR
            else None,
            "scorePercentile": percentiles.get("scoutScore"),
        },
        "convictionAdjustment": 0,
        "convictionCap": peer_conviction_cap(earnings_intelligence),
        "antiDoubleCount": {
            "primaryInterpreter": False,
            "peerReferenceWeight": PEER_REFERENCE_WEIGHT,
            "message": "P0: peer edge is informational only; gates and final score are unchanged.",
        },
        "status_message": entry.get("statusMessage"),
    }


def attach_peer_scoring(
    serialized: dict[str, Any],
    breakdown: dict[str, Any],
) -> dict[str, Any]:
    """Attach scoringBreakdown without mutating score, gates, or EI adjustments."""
    serialized["scoringBreakdown"] = breakdown
    serialized["peerConvictionAdjustment"] = 0
    return serialized


def peer_conviction_cap(earnings_intelligence: Optional[dict[str, Any]]) -> int:
    if isinstance(earnings_intelligence, dict) and earnings_intelligence.get("active"):
        return PEER_CONVICTION_CAP_WHEN_EI_ACTIVE
    return PEER_CONVICTION_CAP
