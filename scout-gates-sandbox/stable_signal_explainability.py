#!/usr/bin/env python3
"""Stable Signal S2a — explainability metadata (read-only, no ranking impact).

Builds stableSignal.explainability from existing serialized scan output and S1
layers. Not attached in production until S2b (feature flag).

Architecture: docs/stable-signal-s2-explainability-plan.md
"""

from __future__ import annotations

import copy
import os
from dataclasses import dataclass
from typing import Any, Optional

from stable_signal_layers import (
    LAYER_KEYS,
    LAYER_PRIMARY_GATE,
    RANKING_SCORE_FIELD,
)


EXPLAINABILITY_VERSION = "1"
EXPLAINABILITY_TIER = "explainability"
MAX_DRIVERS = 8

BAND_STRONG = "strong"
BAND_NEUTRAL = "neutral"
BAND_WEAK = "weak"
BAND_UNKNOWN = "unknown"

STRONGEST_LAYER_PRIORITY = (
    "fundamentalQuality",
    "momentum",
    "risk",
    "liquidity",
    "volatility",
    "regime",
    "breadth",
)

PICK_ROLE_UNKNOWN = "unknown"
PICK_ROLE_FINAL_PICK = "final_pick"
PICK_ROLE_REJECTED = "rejected"
PICK_ROLE_ALSO_RAN = "also_ran"

FORBIDDEN_EXPLAINABILITY_KEYS = frozenset(
    {
        "layerScore",
        "layer_score",
        "composite",
        "compositeScore",
        "rank",  # use universeRankByScore / passingPoolRankByScore instead
    }
)


@dataclass(frozen=True)
class ScanExplainContext:
    """Read-only scan snapshot for rankingExplanation (S2b wires this)."""

    pick_mode: str
    final_pick_ticker: str
    run_timestamp: str
    universe: tuple[tuple[str, float, bool], ...]
    rejected_tickers: frozenset[str] = frozenset()

    @classmethod
    def from_universe(
        cls,
        *,
        pick_mode: str,
        final_pick_ticker: str,
        run_timestamp: str,
        universe: list[tuple[str, float, bool]],
        rejected_tickers: Optional[set[str]] = None,
    ) -> ScanExplainContext:
        return cls(
            pick_mode=str(pick_mode or "gate_runner"),
            final_pick_ticker=str(final_pick_ticker).upper(),
            run_timestamp=str(run_timestamp),
            universe=tuple(
                (str(ticker).upper(), float(score), bool(passed)) for ticker, score, passed in universe
            ),
            rejected_tickers=frozenset(str(t).upper() for t in (rejected_tickers or set())),
        )


def explainability_enabled() -> bool:
    """Feature flag for S2b attachment (default off)."""
    return os.environ.get("SCOUT_STABLE_SIGNAL_EXPLAINABILITY", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def format_score(value: Any) -> str:
    if not isinstance(value, (int, float)):
        return str(value)
    if float(value).is_integer():
        return str(int(value))
    return f"{float(value):.1f}"


def pick_role_for_ticker(ticker: str, context: Optional[ScanExplainContext]) -> str:
    if context is None:
        return PICK_ROLE_UNKNOWN
    upper = str(ticker).upper()
    if upper == context.final_pick_ticker:
        return PICK_ROLE_FINAL_PICK
    if upper in context.rejected_tickers:
        return PICK_ROLE_REJECTED
    return PICK_ROLE_ALSO_RAN


def dense_rank_by_score(
    ticker: str,
    universe: tuple[tuple[str, float, bool], ...],
) -> tuple[Optional[int], int]:
    if not universe:
        return None, 0
    ordered = sorted(universe, key=lambda row: (-row[1], row[0]))
    size = len(ordered)
    for index, (symbol, _, _) in enumerate(ordered, start=1):
        if symbol == str(ticker).upper():
            return index, size
    return None, size


def passing_pool_rank(
    ticker: str,
    universe: tuple[tuple[str, float, bool], ...],
) -> tuple[Optional[int], int]:
    pool = [(symbol, score, passed) for symbol, score, passed in universe if passed]
    if not pool:
        return None, 0
    ordered = sorted(pool, key=lambda row: (-row[1], row[0]))
    for index, (symbol, _, _) in enumerate(ordered, start=1):
        if symbol == str(ticker).upper():
            return index, len(ordered)
    return None, len(ordered)


def primary_values_present(primary: dict[str, Any]) -> bool:
    values = primary.get("values")
    if not isinstance(values, dict):
        return False
    return any(value not in (None, "") for value in values.values())


def anchor_gate_passed(layer: dict[str, Any]) -> Optional[bool]:
    primary = layer.get("primary") if isinstance(layer.get("primary"), dict) else {}
    anchor = str(primary.get("gate") or "")
    for gate in layer.get("gates") or []:
        if not isinstance(gate, dict):
            continue
        if str(gate.get("key") or "") == anchor:
            passed = gate.get("passed")
            if passed is True:
                return True
            if passed is False:
                return False
    return None


def gate_pass_rate(layer: dict[str, Any]) -> float:
    gates = layer.get("gates") or []
    if not gates:
        return 0.0
    passed_count = sum(1 for gate in gates if isinstance(gate, dict) and gate.get("passed") is True)
    return round(passed_count / len(gates), 4)


def build_layer_strength(layer_key: str, layer: dict[str, Any], redundancy_flags: list[str]) -> dict[str, Any]:
    primary = layer.get("primary") if isinstance(layer.get("primary"), dict) else {}
    primary_present = primary_values_present(primary)
    pass_rate = gate_pass_rate(layer)
    anchor_passed = anchor_gate_passed(layer)
    notes: list[str] = []

    if pass_rate < 1.0:
        for gate in layer.get("gates") or []:
            if isinstance(gate, dict) and gate.get("passed") is False:
                notes.append(f"{gate.get('code') or gate.get('key')} gate failed")

    if not primary_present:
        notes.append("primary field values unavailable")

    if anchor_passed is False:
        anchor = str(primary.get("gate") or LAYER_PRIMARY_GATE.get(layer_key, ""))
        notes.append(f"{anchor} anchor gate failed")

    shadow_redundant = _layer_redundancy_note(layer_key, redundancy_flags)
    if shadow_redundant:
        notes.append(shadow_redundant)

    if not primary_present:
        band = BAND_UNKNOWN
    elif pass_rate < 1.0 or anchor_passed is False:
        band = BAND_WEAK
    elif notes:
        band = BAND_NEUTRAL
    else:
        band = BAND_STRONG

    return {
        "band": band,
        "gatePassRate": pass_rate,
        "primaryPresent": primary_present,
        "notes": notes,
    }


def _layer_redundancy_note(layer_key: str, flags: list[str]) -> Optional[str]:
    mapping = {
        "rsi_multi_use": ("momentum", "fundamentalQuality"),
        "change_multi_use": ("momentum", "volatility"),
        "wind_multi_use": ("regime", "breadth"),
        "dcf_gap_multi_use": ("fundamentalQuality",),
    }
    for flag in flags:
        layers = mapping.get(flag)
        if layers and layer_key in layers:
            return f"shadow overlap ({flag})"
    return None


def pick_strongest_weakest(layer_strengths: dict[str, dict[str, Any]]) -> tuple[str, str]:
    strongest = STRONGEST_LAYER_PRIORITY[0]
    weakest = STRONGEST_LAYER_PRIORITY[-1]
    strong_candidates = [
        key for key in STRONGEST_LAYER_PRIORITY if layer_strengths[key]["band"] == BAND_STRONG
    ]
    if strong_candidates:
        strongest = strong_candidates[0]
    weak_candidates = [
        key
        for key in reversed(STRONGEST_LAYER_PRIORITY)
        if layer_strengths[key]["band"] in (BAND_WEAK, BAND_UNKNOWN)
    ]
    if weak_candidates:
        weakest = weak_candidates[0]
    else:
        weakest = max(
            STRONGEST_LAYER_PRIORITY,
            key=lambda key: len(layer_strengths[key].get("notes") or []),
        )
    return strongest, weakest


def build_ranking_explanation(
    serialized: dict[str, Any],
    stable_signal: dict[str, Any],
    context: Optional[ScanExplainContext],
) -> dict[str, Any]:
    ticker = str(serialized.get("ticker") or "")
    ranking_score = stable_signal.get("rankingScore", serialized.get("score"))
    passed_all = bool(serialized.get("passedAllGates"))
    pick_role = pick_role_for_ticker(ticker, context)
    universe_rank: Optional[int] = None
    universe_size = 0
    passing_rank: Optional[int] = None
    passing_size = 0

    if context is not None:
        universe_rank, universe_size = dense_rank_by_score(ticker, context.universe)
        passing_rank, passing_size = passing_pool_rank(ticker, context.universe)

    first_failed = serialized.get("firstFailedGate")
    failed_name = ""
    if isinstance(first_failed, dict):
        failed_name = str(first_failed.get("name") or first_failed.get("code") or "")

    score_text = format_score(ranking_score)
    summary = _ranking_summary(
        ticker=ticker,
        score_text=score_text,
        passed_all=passed_all,
        pick_role=pick_role,
        pick_mode=context.pick_mode if context else "unknown",
        universe_rank=universe_rank,
        universe_size=universe_size,
        passing_rank=passing_rank,
        passing_size=passing_size,
        failed_name=failed_name,
    )

    return {
        "rankingScoreField": RANKING_SCORE_FIELD,
        "rankingScore": ranking_score,
        "passedAllGates": passed_all,
        "pickRole": pick_role,
        "universeRankByScore": universe_rank,
        "universeSize": universe_size,
        "passingPoolRankByScore": passing_rank,
        "passingPoolSize": passing_size,
        "summary": summary,
        "tier": EXPLAINABILITY_TIER,
    }


def _ranking_summary(
    *,
    ticker: str,
    score_text: str,
    passed_all: bool,
    pick_role: str,
    pick_mode: str,
    universe_rank: Optional[int],
    universe_size: int,
    passing_rank: Optional[int],
    passing_size: int,
    failed_name: str,
) -> str:
    if pick_role == PICK_ROLE_FINAL_PICK and pick_mode == "gate_runner" and passed_all:
        return (
            f"{ticker} is the gate-runner pick: highest scout_score ({score_text}) among "
            f"{passing_size} ticker(s) that passed all 14 gates."
        )
    if pick_role == PICK_ROLE_FINAL_PICK and pick_mode == "score_only":
        return (
            f"{ticker} leads by scout_score ({score_text}) in score-only mode "
            f"(gate filter not applied to pick)."
        )
    if not passed_all and universe_rank is not None:
        fail_clause = f"; first failure: {failed_name}" if failed_name else ""
        return (
            f"{ticker} has scout_score {score_text} (rank {universe_rank}/{universe_size}) "
            f"but did not pass all gates{fail_clause}."
        )
    if universe_rank is not None and universe_size:
        return (
            f"{ticker} has scout_score {score_text} (rank {universe_rank}/{universe_size}) "
            f"in this scan."
        )
    return f"{ticker} has scout_score {score_text} (ranking context unavailable)."


def _driver(
    *,
    layer: str,
    source: str,
    label: str,
    gate: str = "",
    passed: Optional[bool] = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "layer": layer,
        "source": source,
        "label": label,
        "tier": EXPLAINABILITY_TIER,
    }
    if gate:
        entry["gate"] = gate
    if passed is not None:
        entry["passed"] = passed
    return entry


def build_confidence_drivers(
    serialized: dict[str, Any],
    stable_signal: dict[str, Any],
    layer_strengths: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    layers = stable_signal.get("layers") if isinstance(stable_signal.get("layers"), dict) else {}

    if serialized.get("passedAllGates") is True:
        drivers.append(
            _driver(layer="scan", source="scan", label="All 14 gates passed", gate="", passed=True)
        )

    for layer_key in LAYER_KEYS:
        if layer_strengths[layer_key]["band"] != BAND_STRONG:
            continue
        layer = layers.get(layer_key) if isinstance(layers.get(layer_key), dict) else {}
        primary = layer.get("primary") if isinstance(layer.get("primary"), dict) else {}
        values = primary.get("values") if isinstance(primary.get("values"), dict) else {}
        if not values:
            continue
        parts = [f"{key} {values[key]}" for key in sorted(values.keys()) if values[key] not in (None, "")]
        if not parts:
            continue
        gate = str(primary.get("gate") or LAYER_PRIMARY_GATE.get(layer_key, ""))
        drivers.append(
            _driver(
                layer=layer_key,
                source="primary",
                label=", ".join(parts),
                gate=gate,
                passed=anchor_gate_passed(layer),
            )
        )

    explanation = serialized.get("explanation") if isinstance(serialized.get("explanation"), dict) else {}
    for gate in (explanation.get("gates") or [])[:3]:
        if not isinstance(gate, dict):
            continue
        if str(gate.get("status") or "").upper() != "PASS":
            continue
        drivers.append(
            _driver(
                layer="scan",
                source="gate",
                label=str(gate.get("gate_name") or gate.get("gate_key") or "Gate") + " passed",
                gate=str(gate.get("gate_key") or ""),
                passed=True,
            )
        )

    direction = serialized.get("directionBreakdown")
    if isinstance(direction, dict):
        bull = direction.get("bullConviction")
        bear = direction.get("bearConviction")
        if bull is not None and bear is not None:
            drivers.append(
                _driver(
                    layer="momentum",
                    source="overlay",
                    label=(
                        f"Directional overlay (non-ranking): bull conviction {bull} vs bear {bear}"
                    ),
                )
            )

    return drivers[:MAX_DRIVERS]


def build_penalty_drivers(
    serialized: dict[str, Any],
    stable_signal: dict[str, Any],
) -> list[dict[str, Any]]:
    drivers: list[dict[str, Any]] = []
    layers = stable_signal.get("layers") if isinstance(stable_signal.get("layers"), dict) else {}

    first_failed = serialized.get("firstFailedGate")
    if isinstance(first_failed, dict):
        drivers.append(
            _driver(
                layer="scan",
                source="gate",
                label=(
                    f"Failed {first_failed.get('code') or first_failed.get('key')} "
                    f"({first_failed.get('name')})"
                ),
                gate=str(first_failed.get("key") or ""),
                passed=False,
            )
        )

    for gate in serialized.get("gates") or []:
        if not isinstance(gate, dict) or gate.get("passed") is not False:
            continue
        drivers.append(
            _driver(
                layer="scan",
                source="gate",
                label=f"{gate.get('code') or gate.get('key')} gate failed",
                gate=str(gate.get("key") or ""),
                passed=False,
            )
        )

    for layer_key in LAYER_KEYS:
        layer = layers.get(layer_key) if isinstance(layers.get(layer_key), dict) else {}
        for gate in layer.get("gates") or []:
            if not isinstance(gate, dict) or gate.get("passed") is not False:
                continue
            drivers.append(
                _driver(
                    layer=layer_key,
                    source="gate",
                    label=f"{gate.get('code') or gate.get('key')} failed ({layer_key} layer)",
                    gate=str(gate.get("key") or ""),
                    passed=False,
                )
            )

    ei = serialized.get("earningsIntelligence")
    if isinstance(ei, dict):
        mode = str(ei.get("mode") or "")
        if mode in ("pre_earnings", "awaiting_provider", "unavailable"):
            drivers.append(
                _driver(
                    layer="fundamentalQuality",
                    source="overlay",
                    label=f"Earnings Intelligence mode: {mode} (overlay, non-ranking)",
                )
            )

    breakdown = serialized.get("scoringBreakdown")
    if isinstance(breakdown, dict):
        mode = str(breakdown.get("mode") or "")
        if mode not in ("", "scored"):
            drivers.append(
                _driver(
                    layer="scan",
                    source="overlay",
                    label=f"Peer edge mode: {mode} (informational only)",
                )
            )

    direction = serialized.get("directionBreakdown")
    if isinstance(direction, dict):
        bear_signals = direction.get("bearishSignals")
        if isinstance(bear_signals, list) and bear_signals:
            top = bear_signals[0]
            if isinstance(top, dict):
                drivers.append(
                    _driver(
                        layer="momentum",
                        source="overlay",
                        label=f"Bearish overlay signal: {top.get('description', 'present')}",
                    )
                )

    return drivers[:MAX_DRIVERS]


def build_warning_flags(
    serialized: dict[str, Any],
    stable_signal: dict[str, Any],
    ranking: dict[str, Any],
) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    redundancy = list(stable_signal.get("redundancyFlags") or [])

    for code in redundancy:
        flags.append(
            {
                "code": f"redundancy_{code}",
                "severity": "info",
                "message": f"{code.replace('_', ' ')} (shadow paths only).",
                "tier": EXPLAINABILITY_TIER,
            }
        )

    breadth_layer = (stable_signal.get("layers") or {}).get("breadth") or {}
    breadth_primary = breadth_layer.get("primary") if isinstance(breadth_layer, dict) else {}
    if not primary_values_present(breadth_primary if isinstance(breadth_primary, dict) else {}):
        flags.append(
            {
                "code": "missing_breadth_primary",
                "severity": "info",
                "message": "Market breadth primary fields were not returned.",
                "tier": EXPLAINABILITY_TIER,
            }
        )

    ei = serialized.get("earningsIntelligence")
    if isinstance(ei, dict):
        if not ei.get("active"):
            flags.append(
                {
                    "code": "ei_inactive",
                    "severity": "info",
                    "message": "Earnings Intelligence overlay is inactive.",
                    "tier": EXPLAINABILITY_TIER,
                }
            )
        elif str(ei.get("mode") or "") == "pre_earnings":
            flags.append(
                {
                    "code": "ei_pre_earnings",
                    "severity": "warn",
                    "message": "Earnings Intelligence is in pre-earnings mode.",
                    "tier": EXPLAINABILITY_TIER,
                }
            )

    breakdown = serialized.get("scoringBreakdown")
    if isinstance(breakdown, dict) and str(breakdown.get("mode") or "") not in ("", "scored"):
        flags.append(
            {
                "code": "peer_partial",
                "severity": "info",
                "message": "Peer risk-adjusted edge is informational only.",
                "tier": EXPLAINABILITY_TIER,
            }
        )

    if ranking.get("universeRankByScore") == 1 and not ranking.get("passedAllGates"):
        flags.append(
            {
                "code": "high_score_gate_fail",
                "severity": "info",
                "message": "Highest scout_score in scan but not all gates passed.",
                "tier": EXPLAINABILITY_TIER,
            }
        )

    adjusted = serialized.get("adjustedScoutScore")
    base = serialized.get("score")
    if adjusted not in (None, "") and base not in (None, "") and adjusted != base:
        flags.append(
            {
                "code": "adjusted_score_display",
                "severity": "info",
                "message": "adjustedScoutScore is display-only and does not change ranking.",
                "tier": EXPLAINABILITY_TIER,
            }
        )

    return flags


def build_regime_context(serialized: dict[str, Any], stable_signal: dict[str, Any]) -> dict[str, Any]:
    regime_layer = (stable_signal.get("layers") or {}).get("regime") or {}
    primary = regime_layer.get("primary") if isinstance(regime_layer, dict) else {}
    values = primary.get("values") if isinstance(primary, dict) else {}
    shadow = regime_layer.get("shadowValues") if isinstance(regime_layer, dict) else {}
    sector = serialized.get("sector") or (values.get("sector") if isinstance(values, dict) else None)
    wind = values.get("wind") if isinstance(values, dict) else None
    meridian_passed = anchor_gate_passed(regime_layer if isinstance(regime_layer, dict) else {})
    risk_regime = shadow.get("risk_regime") if isinstance(shadow, dict) else None

    wind_text = "positive" if isinstance(wind, (int, float)) and wind > 0 else (
        "negative" if isinstance(wind, (int, float)) and wind < 0 else "neutral/unknown"
    )
    summary = (
        f"Sector wind is {wind_text}; MERIDIAN "
        f"{'passed' if meridian_passed else 'did not pass' if meridian_passed is False else 'status unknown'}."
    )

    return {
        "sector": sector,
        "wind": wind,
        "primaryGate": LAYER_PRIMARY_GATE["regime"],
        "meridianPassed": meridian_passed,
        "riskRegime": risk_regime,
        "summary": summary,
        "tier": EXPLAINABILITY_TIER,
    }


def build_breadth_context(stable_signal: dict[str, Any]) -> dict[str, Any]:
    breadth_layer = (stable_signal.get("layers") or {}).get("breadth") or {}
    primary = breadth_layer.get("primary") if isinstance(breadth_layer, dict) else {}
    values = primary.get("values") if isinstance(primary, dict) else {}
    fallback = primary.get("fallbackFields") if isinstance(primary, dict) else None

    breadth_score = values.get("breadth_score") if isinstance(values, dict) else None
    spy = values.get("spy_trend") if isinstance(values, dict) else None
    qqq = values.get("qqq_trend") if isinstance(values, dict) else None
    current_passed = anchor_gate_passed(breadth_layer if isinstance(breadth_layer, dict) else {})

    if breadth_score not in (None, ""):
        quality = "complete"
        summary = f"Market breadth_score is {breadth_score}; CURRENT gate context available."
    elif spy not in (None, "") or qqq not in (None, ""):
        quality = "partial"
        summary = "Breadth used index trend fallback fields; breadth_score was not returned."
    else:
        quality = "missing"
        summary = "Market breadth fields were not returned; CURRENT gate may still apply."

    return {
        "breadthScore": breadth_score,
        "spyTrend": spy,
        "qqqTrend": qqq,
        "currentGatePassed": current_passed,
        "dataQuality": quality,
        "fallbackFields": list(fallback) if isinstance(fallback, list) else fallback,
        "summary": summary,
        "tier": EXPLAINABILITY_TIER,
    }


def build_overlays_summary(serialized: dict[str, Any]) -> dict[str, Any]:
    direction = serialized.get("directionBreakdown")
    ei = serialized.get("earningsIntelligence")
    peer = serialized.get("scoringBreakdown")

    overlays: dict[str, Any] = {}
    if isinstance(direction, dict):
        overlays["directionBreakdown"] = {
            "available": True,
            "netDirectionalEdge": direction.get("netDirectionalEdge"),
            "direction": direction.get("direction"),
            "tier": "shadow",
        }
    else:
        overlays["directionBreakdown"] = {"available": False, "tier": "shadow"}

    if isinstance(ei, dict):
        overlays["earningsIntelligence"] = {
            "available": True,
            "active": ei.get("active"),
            "mode": ei.get("mode"),
            "convictionAdjustment": ei.get("conviction_adjustment") or ei.get("convictionAdjustment"),
            "tier": "shadow",
        }
    else:
        overlays["earningsIntelligence"] = {"available": False, "tier": "shadow"}

    if isinstance(peer, dict):
        overlays["peerRiskAdjustedEdge"] = {
            "available": True,
            "active": peer.get("active"),
            "mode": peer.get("mode"),
            "peerRiskAdjustedEdge": peer.get("peerRiskAdjustedEdge"),
            "tier": "shadow",
        }
    else:
        overlays["peerRiskAdjustedEdge"] = {"available": False, "tier": "shadow"}

    return overlays


def build_human_summary(
    serialized: dict[str, Any],
    ranking: dict[str, Any],
    strongest: str,
    weakest: str,
    layer_strengths: dict[str, dict[str, Any]],
) -> str:
    ticker = str(serialized.get("ticker") or "")
    parts = [ranking.get("summary") or f"{ticker} scan result."]
    parts.append(
        f"Strongest layer: {strongest} ({layer_strengths[strongest]['band']}); "
        f"weakest: {weakest} ({layer_strengths[weakest]['band']})."
    )
    if ranking.get("pickRole") != PICK_ROLE_FINAL_PICK and ranking.get("passedAllGates") is False:
        parts.append("Gate failures are listed under penaltyDrivers.")
    text = " ".join(parts)
    return text[:320] if len(text) > 320 else text


def assert_no_forbidden_keys(value: Any, path: str = "") -> None:
    """Raise ValueError if explainability tree contains ranking-like numeric keys."""
    if isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_EXPLAINABILITY_KEYS:
                raise ValueError(f"Forbidden explainability key at {path}.{key}")
            assert_no_forbidden_keys(child, f"{path}.{key}" if path else key)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            assert_no_forbidden_keys(child, f"{path}[{index}]")


def build_explainability(
    serialized: dict[str, Any],
    stable_signal: dict[str, Any],
    context: Optional[ScanExplainContext] = None,
) -> dict[str, Any]:
    """Build explainability dict from serialized ticker output (read-only inputs)."""
    snapshot = copy.deepcopy(serialized)
    signal = copy.deepcopy(stable_signal)
    redundancy_flags = list(signal.get("redundancyFlags") or [])

    layers = signal.get("layers") if isinstance(signal.get("layers"), dict) else {}
    layer_strengths = {
        layer_key: build_layer_strength(
            layer_key,
            layers.get(layer_key) if isinstance(layers.get(layer_key), dict) else {},
            redundancy_flags,
        )
        for layer_key in LAYER_KEYS
    }
    strongest, weakest = pick_strongest_weakest(layer_strengths)
    ranking = build_ranking_explanation(snapshot, signal, context)
    confidence = build_confidence_drivers(snapshot, signal, layer_strengths)
    penalties = build_penalty_drivers(snapshot, signal)
    warnings = build_warning_flags(snapshot, signal, ranking)

    generated_at = context.run_timestamp if context else ""

    explainability = {
        "version": EXPLAINABILITY_VERSION,
        "tier": EXPLAINABILITY_TIER,
        "generatedAt": generated_at,
        "rankingExplanation": ranking,
        "strongestLayer": strongest,
        "weakestLayer": weakest,
        "layerStrengths": layer_strengths,
        "confidenceDrivers": confidence,
        "penaltyDrivers": penalties,
        "warningFlags": warnings,
        "redundancyFlags": redundancy_flags,
        "regimeContext": build_regime_context(snapshot, signal),
        "breadthContext": build_breadth_context(signal),
        "humanSummary": build_human_summary(snapshot, ranking, strongest, weakest, layer_strengths),
        "overlays": build_overlays_summary(snapshot),
        "placeholders": {
            "horizonFlow": None,
            "layerNumericScores": None,
            "outcomeCalibratedConfidence": None,
        },
    }
    assert_no_forbidden_keys(explainability)
    return explainability


def attach_explainability_to_stable_signal(
    stable_signal: dict[str, Any],
    serialized: dict[str, Any],
    context: Optional[ScanExplainContext] = None,
) -> dict[str, Any]:
    """Return new stable_signal dict with explainability attached (does not mutate inputs)."""
    merged = copy.deepcopy(stable_signal)
    merged["explainability"] = build_explainability(serialized, stable_signal, context)
    return merged


def attach_explainability_if_enabled(
    stable_signal: dict[str, Any],
    serialized: dict[str, Any],
    context: Optional[ScanExplainContext] = None,
) -> dict[str, Any]:
    """S2b hook: attach only when SCOUT_STABLE_SIGNAL_EXPLAINABILITY is enabled."""
    if not explainability_enabled():
        return stable_signal
    return attach_explainability_to_stable_signal(stable_signal, serialized, context)
