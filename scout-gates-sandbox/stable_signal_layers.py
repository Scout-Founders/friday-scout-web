#!/usr/bin/env python3
"""Stable Signal State v1 — institutional layer metadata (S1).

Maps existing Horizon-1 API fields and gate passes to seven layers for
visibility and logging only. Does not alter scout_score, gates, or rankings.

Architecture: docs/stable-signal-state-v1.md
"""

from __future__ import annotations

from typing import Any, Optional

from run_gates import GATES, CandidateResult


STABLE_SIGNAL_VERSION = "1"
RANKING_SCORE_FIELD = "scout_score"
TIER_AUTHORITATIVE = "authoritative"
TIER_SHADOW = "shadow"

LAYER_KEYS = (
    "momentum",
    "risk",
    "liquidity",
    "volatility",
    "fundamentalQuality",
    "regime",
    "breadth",
)

# Primary gate anchor per layer (14-gate architecture unchanged).
LAYER_PRIMARY_GATE: dict[str, str] = {
    "momentum": "compass",
    "risk": "fortress",
    "liquidity": "sentinel",
    "volatility": "pulse",
    "fundamentalQuality": "atlas",
    "regime": "meridian",
    "breadth": "current",
}

# Gates associated with each layer (documentation / orchestration only).
LAYER_GATES: dict[str, tuple[str, ...]] = {
    "momentum": ("compass", "archer"),
    "risk": ("fortress", "specter", "aegis"),
    "liquidity": ("sentinel",),
    "volatility": ("pulse",),
    "fundamentalQuality": ("atlas", "oracle", "phantom"),
    "regime": ("meridian", "catalyst", "signal"),
    "breadth": ("current",),
}

SHADOW_FIELD_NAMES: dict[str, tuple[str, ...]] = {
    "momentum": (
        "rsi",
        "change",
        "distance_to_20ema",
        "distance_to_50ema",
        "distance_to_200ema",
        "macd_state",
        "gap_percent",
    ),
    "risk": ("beta", "earnings_days", "short_interest", "earningsConvictionAdjustment"),
    "liquidity": ("relative_volume", "volume_ratio", "options_volume_score"),
    "volatility": ("iv_percentile", "iv_rank", "change", "atr", "vix", "vix_level"),
    "fundamentalQuality": (
        "dcf_gap",
        "dcf_value",
        "revenue_growth",
        "earnings_growth",
        "earningsIntelligence",
    ),
    "regime": ("sector", "risk_regime", "spy_trend", "qqq_trend", "macro_bias"),
    "breadth": ("wind", "netDirectionalEdge"),
}

FIELD_ALIASES: dict[str, tuple[str, ...]] = {
    "scout_score": ("scout_score", "score"),
    "volume": ("volume",),
    "trend": ("trend", "trend_state", "trendState"),
    "wind": ("wind",),
    "sector": ("sector", "sector_name", "sectorName"),
    "piotroski": ("piotroski", "piotroskiScore"),
    "z_score": ("z_score", "zScore", "altman", "altmanZScore"),
    "dcf_gap": ("dcf_gap", "dcfGap"),
    "iv_elevated": ("iv_elevated", "ivElevated"),
    "iv_percentile": ("iv_percentile", "ivPercentile", "iv_rank", "ivRank"),
    "relative_volume": ("relative_volume", "relativeVolume", "volume_ratio", "volumeRatio"),
    "breadth_score": ("breadth_score", "breadthScore"),
    "spy_trend": ("spy_trend", "spyTrend"),
    "qqq_trend": ("qqq_trend", "qqqTrend"),
    "earnings_days": ("earnings_days", "earningsDays", "days_to_earnings", "earnings_days_away"),
    "beta": ("beta",),
    "change": ("change",),
    "rsi": ("rsi", "RSI"),
}


def first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def gate_passed(gates: dict[str, Any], gate_key: str) -> Optional[bool]:
    value = gates.get(gate_key)
    if value is True:
        return True
    if value is False:
        return False
    return None


def gate_entries_for_layer(gates: dict[str, Any], layer: str) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for key in LAYER_GATES.get(layer, ()):
        for index, (gate_key, code, name) in enumerate(GATES, start=1):
            if gate_key != key:
                continue
            passed = gate_passed(gates, gate_key)
            entries.append(
                {
                    "index": index,
                    "key": gate_key,
                    "code": code,
                    "name": name,
                    "passed": passed,
                }
            )
            break
    return entries


def shadow_values_for_layer(source: dict[str, Any], layer: str) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for canonical in SHADOW_FIELD_NAMES.get(layer, ()):
        keys = FIELD_ALIASES.get(canonical, (canonical,))
        value = first_present(source, keys)
        if value not in (None, ""):
            values[canonical] = value
    return values


def build_primary_block(
    *,
    field: Optional[str] = None,
    fields: Optional[list[str]] = None,
    values: Optional[dict[str, Any]] = None,
    gate: str,
    passed: Optional[bool],
    fallback_fields: Optional[list[str]] = None,
) -> dict[str, Any]:
    block: dict[str, Any] = {
        "gate": gate,
        "passed": passed,
        "tier": TIER_AUTHORITATIVE,
    }
    if field is not None:
        block["field"] = field
    if fields:
        block["fields"] = fields
    if values:
        block["values"] = values
    if fallback_fields:
        block["fallbackFields"] = fallback_fields
    return block


def build_momentum_layer(source: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    gate = LAYER_PRIMARY_GATE["momentum"]
    return {
        "primary": build_primary_block(
            field="trend",
            values={"trend": first_present(source, FIELD_ALIASES["trend"])},
            gate=gate,
            passed=gate_passed(gates, gate),
        ),
        "shadow": list(SHADOW_FIELD_NAMES["momentum"]),
        "shadowValues": shadow_values_for_layer(source, "momentum"),
        "gates": gate_entries_for_layer(gates, "momentum"),
    }


def build_risk_layer(source: dict[str, Any], gates: dict[str, Any], ranking_score: float) -> dict[str, Any]:
    gate = LAYER_PRIMARY_GATE["risk"]
    return {
        "primary": build_primary_block(
            field=RANKING_SCORE_FIELD,
            values={RANKING_SCORE_FIELD: ranking_score},
            gate=gate,
            passed=gate_passed(gates, gate),
        ),
        "shadow": list(SHADOW_FIELD_NAMES["risk"]),
        "shadowValues": shadow_values_for_layer(source, "risk"),
        "gates": gate_entries_for_layer(gates, "risk"),
    }


def build_liquidity_layer(source: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    gate = LAYER_PRIMARY_GATE["liquidity"]
    return {
        "primary": build_primary_block(
            field="volume",
            values={"volume": first_present(source, FIELD_ALIASES["volume"])},
            gate=gate,
            passed=gate_passed(gates, gate),
        ),
        "shadow": list(SHADOW_FIELD_NAMES["liquidity"]),
        "shadowValues": shadow_values_for_layer(source, "liquidity"),
        "gates": gate_entries_for_layer(gates, "liquidity"),
    }


def build_volatility_layer(source: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    gate = LAYER_PRIMARY_GATE["volatility"]
    return {
        "primary": build_primary_block(
            field="iv_elevated",
            values={"iv_elevated": first_present(source, FIELD_ALIASES["iv_elevated"])},
            gate=gate,
            passed=gate_passed(gates, gate),
        ),
        "shadow": list(SHADOW_FIELD_NAMES["volatility"]),
        "shadowValues": shadow_values_for_layer(source, "volatility"),
        "gates": gate_entries_for_layer(gates, "volatility"),
    }


def build_fundamental_quality_layer(source: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    gate = LAYER_PRIMARY_GATE["fundamentalQuality"]
    piotroski = first_present(source, FIELD_ALIASES["piotroski"])
    z_score = first_present(source, FIELD_ALIASES["z_score"])
    return {
        "primary": build_primary_block(
            fields=["piotroski", "z_score"],
            values={"piotroski": piotroski, "z_score": z_score},
            gate=gate,
            passed=gate_passed(gates, gate),
        ),
        "shadow": list(SHADOW_FIELD_NAMES["fundamentalQuality"]),
        "shadowValues": shadow_values_for_layer(source, "fundamentalQuality"),
        "gates": gate_entries_for_layer(gates, "fundamentalQuality"),
    }


def build_regime_layer(source: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    gate = LAYER_PRIMARY_GATE["regime"]
    return {
        "primary": build_primary_block(
            field="wind",
            values={
                "wind": first_present(source, FIELD_ALIASES["wind"]),
                "sector": first_present(source, FIELD_ALIASES["sector"]),
            },
            gate=gate,
            passed=gate_passed(gates, gate),
        ),
        "shadow": list(SHADOW_FIELD_NAMES["regime"]),
        "shadowValues": shadow_values_for_layer(source, "regime"),
        "gates": gate_entries_for_layer(gates, "regime"),
    }


def build_breadth_layer(source: dict[str, Any], gates: dict[str, Any]) -> dict[str, Any]:
    gate = LAYER_PRIMARY_GATE["breadth"]
    breadth = first_present(source, FIELD_ALIASES["breadth_score"])
    spy = first_present(source, FIELD_ALIASES["spy_trend"])
    qqq = first_present(source, FIELD_ALIASES["qqq_trend"])

    if breadth is not None:
        primary = build_primary_block(
            field="breadth_score",
            values={"breadth_score": breadth},
            gate=gate,
            passed=gate_passed(gates, gate),
        )
    elif spy is not None or qqq is not None:
        primary = build_primary_block(
            fields=["spy_trend", "qqq_trend"],
            values={"spy_trend": spy, "qqq_trend": qqq},
            gate=gate,
            passed=gate_passed(gates, gate),
            fallback_fields=["spy_trend", "qqq_trend"],
        )
    else:
        primary = build_primary_block(
            field="breadth_score",
            values={},
            gate=gate,
            passed=gate_passed(gates, gate),
            fallback_fields=["spy_trend", "qqq_trend"],
        )

    return {
        "primary": primary,
        "shadow": list(SHADOW_FIELD_NAMES["breadth"]),
        "shadowValues": shadow_values_for_layer(source, "breadth"),
        "gates": gate_entries_for_layer(gates, "breadth"),
    }


def detect_redundancy_flags(source: dict[str, Any]) -> list[str]:
    flags: list[str] = []
    if first_present(source, FIELD_ALIASES["rsi"]) is not None:
        flags.append("rsi_multi_use")
    if first_present(source, FIELD_ALIASES["change"]) is not None:
        flags.append("change_multi_use")
    if first_present(source, FIELD_ALIASES["wind"]) is not None:
        flags.append("wind_multi_use")
    if first_present(source, FIELD_ALIASES["dcf_gap"]) is not None:
        flags.append("dcf_gap_multi_use")
    if first_present(source, FIELD_ALIASES["earnings_days"]) is not None:
        flags.append("earnings_multi_use")
    if first_present(source, FIELD_ALIASES["iv_percentile"]) is not None and first_present(
        source, FIELD_ALIASES["iv_elevated"]
    ) is not None:
        flags.append("iv_multi_use")
    if first_present(source, FIELD_ALIASES["relative_volume"]) is not None and first_present(
        source, FIELD_ALIASES["volume"]
    ) is not None:
        flags.append("volume_multi_use")
    return flags


def build_stable_signal(result: CandidateResult) -> dict[str, Any]:
    """Build layer metadata from gate API payload only (no recomputed scores)."""
    source = result.data if isinstance(result.data, dict) else {}
    gates = result.gates
    ranking_score = result.score

    layers = {
        "momentum": build_momentum_layer(source, gates),
        "risk": build_risk_layer(source, gates, ranking_score),
        "liquidity": build_liquidity_layer(source, gates),
        "volatility": build_volatility_layer(source, gates),
        "fundamentalQuality": build_fundamental_quality_layer(source, gates),
        "regime": build_regime_layer(source, gates),
        "breadth": build_breadth_layer(source, gates),
    }

    return {
        "version": STABLE_SIGNAL_VERSION,
        "rankingScoreField": RANKING_SCORE_FIELD,
        "rankingScore": ranking_score,
        "rankingTier": TIER_AUTHORITATIVE,
        "overlayTier": TIER_SHADOW,
        "layers": layers,
        "redundancyFlags": detect_redundancy_flags(source),
        "gateLayerMap": {key: list(LAYER_GATES[key]) for key in LAYER_KEYS},
    }


def attach_stable_signal(
    serialized: dict[str, Any],
    stable_signal: dict[str, Any],
) -> dict[str, Any]:
    """Attach stableSignal without mutating score, gates, or overlays."""
    serialized["stableSignal"] = stable_signal
    return serialized


def build_and_attach_stable_signal(result: CandidateResult, serialized: dict[str, Any]) -> dict[str, Any]:
    return attach_stable_signal(serialized, build_stable_signal(result))
