#!/usr/bin/env python3
"""Tiered Earnings Intelligence scoring for Scout sandbox results.

Standalone module — not coupled to numbered gate pass/fail logic.
Designed for future extensions (transcript sentiment, analyst revisions, etc.).
"""

from __future__ import annotations

import re
from typing import Any, Optional

from earnings_data import merge_fmp_earnings_into_source
from explainability import split_gate_blocks


BASELINE_SCORE = 50
BIG_MOVE_THRESHOLD = 8.0
MODERATE_MOVE_THRESHOLD = 4.0

DEFAULT_CONVICTION_CAP = 8
MIN_CONVICTION_CAP = 6
MAX_CONVICTION_CAP = 10

# When Earnings Intelligence is the primary interpreter, secondary gates reference lightly.
SECONDARY_GATE_DISPLAY_NAMES = frozenset(
    {"Event Trigger", "Intel Feed", "Volatility Read"}
)
SECONDARY_GATE_REFERENCE_WEIGHT = 0.35

GUIDANCE_STRONGLY_RAISED = "strongly_raised"
GUIDANCE_RAISED = "raised"
GUIDANCE_INLINE = "inline"
GUIDANCE_MIXED = "mixed"
GUIDANCE_LOWERED = "lowered"
GUIDANCE_STRONGLY_LOWERED = "strongly_lowered"
GUIDANCE_UNKNOWN = "unknown"

RAISED_GUIDANCE = {GUIDANCE_RAISED, GUIDANCE_STRONGLY_RAISED}
LOWERED_GUIDANCE = {GUIDANCE_LOWERED, GUIDANCE_STRONGLY_LOWERED}

# Reserved extension slots for future subsystems (not wired yet).
EXTENSION_SLOTS: dict[str, Optional[str]] = {
    "transcript_sentiment": None,
    "analyst_revisions": None,
    "implied_vs_actual_move": None,
    "sector_sympathy": None,
    "continuation_probability": None,
}

EARNINGS_TEXT_KEYWORDS = (
    "earnings",
    "eps",
    "revenue",
    "guidance",
    "quarter",
    "q1",
    "q2",
    "q3",
    "q4",
    "beat",
    "miss",
    "report",
    "call",
    "outlook",
    "forecast",
)


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def first_present(source: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = source.get(key)
        if value not in (None, ""):
            return value
    return None


def to_optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_guidance(value: Any) -> str:
    if value in (None, ""):
        return GUIDANCE_UNKNOWN
    text = str(value).strip().lower().replace("-", "_").replace(" ", "_")
    aliases = {
        "strongly_raised": GUIDANCE_STRONGLY_RAISED,
        "strong_raise": GUIDANCE_STRONGLY_RAISED,
        "raised": GUIDANCE_RAISED,
        "raise": GUIDANCE_RAISED,
        "up": GUIDANCE_RAISED,
        "inline": GUIDANCE_INLINE,
        "reaffirmed": GUIDANCE_INLINE,
        "maintained": GUIDANCE_INLINE,
        "unchanged": GUIDANCE_INLINE,
        "in_line": GUIDANCE_INLINE,
        "neutral": GUIDANCE_INLINE,
        "mixed": GUIDANCE_MIXED,
        "lowered": GUIDANCE_LOWERED,
        "lower": GUIDANCE_LOWERED,
        "down": GUIDANCE_LOWERED,
        "strongly_lowered": GUIDANCE_STRONGLY_LOWERED,
        "strong_lower": GUIDANCE_STRONGLY_LOWERED,
        "unknown": GUIDANCE_UNKNOWN,
        "n/a": GUIDANCE_UNKNOWN,
        "na": GUIDANCE_UNKNOWN,
    }
    return aliases.get(text, GUIDANCE_UNKNOWN)


def earnings_days_until(source: dict[str, Any]) -> Optional[float]:
    return to_optional_float(
        first_present(source, ("earnings_days", "earningsDays", "days_to_earnings"))
    )


def earnings_days_since(source: dict[str, Any]) -> Optional[float]:
    """Days since the last report. Do not infer from earnings_days (that is usually days until)."""
    explicit = to_optional_float(
        first_present(
            source,
            (
                "earnings_days_since",
                "days_since_earnings",
                "daysSinceEarnings",
                "days_since_report",
            ),
        )
    )
    if explicit is not None:
        return max(0.0, explicit)
    return None


def parse_surprise_percent(text: str, labels: tuple[str, ...]) -> Optional[float]:
    for line in text.splitlines():
        lower = line.lower()
        if not any(label in lower for label in labels):
            continue
        if not any(term in lower for term in ("surprise", "beat", "miss", "estimate", "consensus", "vs")):
            continue
        match = re.search(r"([-+]?\d+(?:\.\d+)?)\s*%", lower)
        if match:
            return to_optional_float(match.group(1))
    return None


def parse_guidance_from_text(text: str) -> Optional[str]:
    lower = text.lower()
    if any(term in lower for term in ("strongly raised", "significantly raised", "guidance up")):
        return GUIDANCE_STRONGLY_RAISED
    if any(term in lower for term in ("raised guidance", "guidance raised", "outlook raised")):
        return GUIDANCE_RAISED
    if any(term in lower for term in ("strongly lowered", "significantly lowered")):
        return GUIDANCE_STRONGLY_LOWERED
    if any(term in lower for term in ("lowered guidance", "guidance lowered", "outlook cut")):
        return GUIDANCE_LOWERED
    if any(term in lower for term in ("mixed guidance", "guidance mixed")):
        return GUIDANCE_MIXED
    if any(term in lower for term in ("inline", "reaffirmed", "maintained guidance", "in line")):
        return GUIDANCE_INLINE
    return None


def enrich_earnings_source(source: dict[str, Any]) -> dict[str, Any]:
    enriched = dict(source)
    raw_output = str(source.get("raw_output") or "")
    if raw_output:
        blocks = split_gate_blocks(raw_output)
        gate8 = blocks.get(8, "")
        if enriched.get("earnings_days") in (None, "") and gate8:
            match = re.search(r"earnings in (\d+)\s+days?", gate8, re.I)
            if match:
                enriched["earnings_days"] = int(match.group(1))

        if enriched.get("eps_surprise_pct") in (None, "") and gate8:
            eps = parse_surprise_percent(gate8, ("eps",))
            if eps is not None:
                enriched["eps_surprise_pct"] = eps
        if enriched.get("revenue_surprise_pct") in (None, "") and gate8:
            revenue = parse_surprise_percent(gate8, ("revenue", "sales"))
            if revenue is not None:
                enriched["revenue_surprise_pct"] = revenue
        if enriched.get("guidance") in (None, "") and gate8:
            guidance = parse_guidance_from_text(gate8)
            if guidance is not None:
                enriched["guidance"] = guidance

    days_since = earnings_days_since(enriched)
    if (
        enriched.get("market_reaction_pct") in (None, "")
        and days_since is not None
        and enriched.get("change") is not None
    ):
        enriched["market_reaction_pct"] = enriched.get("change")
    return enriched


def has_scoring_inputs(source: dict[str, Any], inputs: dict[str, Any]) -> bool:
    days_until = inputs.get("earnings_days_until")
    if days_until is not None and days_until > 0:
        return False
    has_surprise = (
        inputs.get("eps_surprise_pct") is not None
        or inputs.get("revenue_surprise_pct") is not None
    )
    has_actuals = (
        to_optional_float(source.get("eps_actual")) is not None
        or to_optional_float(source.get("revenue_actual")) is not None
    )
    if has_surprise:
        return True
    if inputs.get("guidance") not in (None, "", GUIDANCE_UNKNOWN):
        return True
    if inputs.get("market_reaction_pct") is not None and (has_surprise or has_actuals):
        return True
    return False


def report_passed_without_actuals(source: dict[str, Any], inputs: dict[str, Any]) -> bool:
    days_since = inputs.get("earnings_days_since")
    if days_since is None:
        return False
    eps_actual = to_optional_float(source.get("eps_actual"))
    revenue_actual = to_optional_float(source.get("revenue_actual"))
    if eps_actual is not None or revenue_actual is not None:
        return False
    if has_scoring_inputs(source, inputs):
        return False
    return days_since >= 0


def earnings_mode(source: dict[str, Any], inputs: dict[str, Any]) -> str:
    if has_scoring_inputs(source, inputs):
        return "scored"
    if report_passed_without_actuals(source, inputs):
        return "awaiting_provider"
    days_until = earnings_days_until(source)
    if days_until is not None and days_until <= 7 and inputs.get("earnings_days_since") is None:
        return "pre_earnings"
    if days_until is not None and days_until <= 7:
        return "pre_earnings"
    raw_output = str(source.get("raw_output") or "")
    if raw_output and re.search(r"earnings in \d+\s+days?", raw_output, re.I):
        return "pre_earnings"
    if inputs.get("earnings_days_since") is not None:
        return "awaiting_provider" if report_passed_without_actuals(source, inputs) else "unavailable"
    return "unavailable"


def recency_multiplier(days_since: Optional[float]) -> float:
    if days_since is None:
        return 1.0
    if days_since <= 1:
        return 1.0
    if days_since <= 3:
        return 0.8
    if days_since <= 7:
        return 0.5
    return 0.25


def conviction_cap(source: dict[str, Any]) -> int:
    override = to_optional_float(
        first_present(source, ("earnings_conviction_cap_override", "earningsConvictionCapOverride"))
    )
    if override is not None:
        return int(clamp(override, MIN_CONVICTION_CAP, MAX_CONVICTION_CAP))
    return DEFAULT_CONVICTION_CAP


def line_mentions_earnings(text: Any) -> bool:
    if text in (None, ""):
        return False
    lower = str(text).lower()
    return any(keyword in lower for keyword in EARNINGS_TEXT_KEYWORDS)


def secondary_gate_weight(
    gate_display_name: str,
    line_text: Any,
    earnings_context: dict[str, Any],
) -> float:
    if not earnings_context.get("primary_interpreter_active"):
        return 1.0
    if gate_display_name not in SECONDARY_GATE_DISPLAY_NAMES:
        return 1.0
    if line_mentions_earnings(line_text):
        return float(earnings_context.get("secondary_gate_multiplier", SECONDARY_GATE_REFERENCE_WEIGHT))
    if (
        gate_display_name == "Volatility Read"
        and earnings_context.get("recent_post_earnings_window")
    ):
        return float(earnings_context.get("secondary_gate_multiplier", SECONDARY_GATE_REFERENCE_WEIGHT))
    return 1.0


def earnings_context_for_gates(
    source: dict[str, Any],
    intelligence: dict[str, Any],
) -> dict[str, Any]:
    days_since = intelligence.get("inputs", {}).get("earnings_days_since")
    recent_window = days_since is None or days_since <= 7
    return {
        "primary_interpreter_active": bool(intelligence.get("active")),
        "secondary_gate_multiplier": SECONDARY_GATE_REFERENCE_WEIGHT,
        "catalyst_weighting": intelligence.get("catalyst_weighting", 1.0),
        "recent_post_earnings_window": recent_window,
        "earnings_days_since": days_since,
    }


def score_eps_surprise(pct: Optional[float]) -> int:
    if pct is None:
        return 0
    if pct > 20:
        return 15
    if pct > 10:
        return 12
    if pct > 3:
        return 8
    if pct >= 0:
        return 4
    if pct >= -3:
        return -4
    if pct > -10:
        return -8
    if pct > -20:
        return -12
    return -15


def score_revenue_surprise(pct: Optional[float]) -> int:
    if pct is None:
        return 0
    if pct > 10:
        return 15
    if pct > 5:
        return 12
    if pct > 2:
        return 8
    if pct >= 0:
        return 4
    if pct >= -2:
        return -4
    if pct > -5:
        return -8
    if pct > -10:
        return -12
    return -15


def score_guidance(guidance: str) -> int:
    return {
        GUIDANCE_STRONGLY_RAISED: 25,
        GUIDANCE_RAISED: 18,
        GUIDANCE_INLINE: 5,
        GUIDANCE_MIXED: -5,
        GUIDANCE_LOWERED: -20,
        GUIDANCE_STRONGLY_LOWERED: -30,
        GUIDANCE_UNKNOWN: 0,
    }.get(guidance, 0)


def score_market_reaction(pct: Optional[float]) -> int:
    if pct is None:
        return 0
    if pct > BIG_MOVE_THRESHOLD:
        return 15
    if pct > MODERATE_MOVE_THRESHOLD:
        return 10
    if pct > 1.5:
        return 5
    if pct >= -1.5:
        return 0
    if pct >= -MODERATE_MOVE_THRESHOLD:
        return -5
    if pct >= -BIG_MOVE_THRESHOLD:
        return -10
    return -15


def is_beat(pct: Optional[float]) -> bool:
    return pct is not None and pct > 0


def is_miss(pct: Optional[float]) -> bool:
    return pct is not None and pct < 0


def score_quality_modifier(
    eps_surprise_pct: Optional[float],
    revenue_surprise_pct: Optional[float],
    guidance: str,
    market_reaction_pct: Optional[float],
) -> int:
    modifier = 0
    eps_beat = is_beat(eps_surprise_pct)
    revenue_beat = is_beat(revenue_surprise_pct)
    eps_miss = is_miss(eps_surprise_pct)
    revenue_miss = is_miss(revenue_surprise_pct)
    numbers_bullish = eps_beat and revenue_beat
    numbers_bearish = eps_miss and revenue_miss
    guidance_raised = guidance in RAISED_GUIDANCE
    guidance_lowered = guidance in LOWERED_GUIDANCE

    if numbers_bullish and guidance_raised:
        modifier += 10
    if numbers_bullish and guidance_lowered:
        modifier -= 15
    if numbers_bearish and guidance_lowered:
        modifier -= 12
    if (eps_beat or revenue_beat) and guidance_lowered and not numbers_bullish:
        modifier -= 8

    if market_reaction_pct is not None:
        if numbers_bullish and market_reaction_pct <= -BIG_MOVE_THRESHOLD:
            modifier -= 18
        elif numbers_bullish and market_reaction_pct <= -MODERATE_MOVE_THRESHOLD:
            modifier -= 12
        elif numbers_bullish and market_reaction_pct < 0:
            modifier -= 6

        if numbers_bearish and market_reaction_pct >= BIG_MOVE_THRESHOLD:
            modifier += 12
        elif numbers_bearish and market_reaction_pct >= MODERATE_MOVE_THRESHOLD:
            modifier += 8
        elif numbers_bearish and market_reaction_pct > 0:
            modifier += 4

        if eps_beat and revenue_miss and market_reaction_pct <= -MODERATE_MOVE_THRESHOLD:
            modifier -= 10
        if eps_miss and revenue_beat and market_reaction_pct >= MODERATE_MOVE_THRESHOLD:
            modifier += 6

        if numbers_bullish and guidance_lowered and market_reaction_pct <= -MODERATE_MOVE_THRESHOLD:
            modifier -= 8
        if numbers_bearish and guidance_raised and market_reaction_pct >= MODERATE_MOVE_THRESHOLD:
            modifier += 5

    return modifier


def earnings_label(score: int) -> str:
    if score >= 85:
        return "Elite bullish earnings outcome"
    if score >= 70:
        return "Strong bullish earnings outcome"
    if score >= 58:
        return "Moderately bullish earnings outcome"
    if score >= 45:
        return "Mixed/neutral earnings outcome"
    if score >= 30:
        return "Weak earnings outcome"
    if score >= 15:
        return "Severe bearish earnings outcome"
    return "Crisis-level bearish earnings outcome"


def earnings_intelligence_active(source: dict[str, Any]) -> bool:
    explicit = source.get("earnings_intelligence_active")
    if explicit is True:
        return True
    if explicit is False:
        return False
    return any(
        first_present(source, keys) not in (None, "")
        for keys in (
            ("eps_surprise_pct", "eps_surprise", "epsSurprise", "eps_surprise_percent"),
            ("revenue_surprise_pct", "revenue_surprise", "revenueSurprise", "revenue_surprise_percent"),
            ("guidance", "guidance_tone", "earnings_guidance", "guidanceSignal"),
            ("market_reaction_pct", "market_reaction", "earnings_reaction", "post_earnings_move"),
        )
    )


def extract_earnings_inputs(source: dict[str, Any]) -> dict[str, Any]:
    guidance_raw = first_present(
        source,
        ("guidance", "guidance_tone", "earnings_guidance", "guidanceSignal"),
    )
    inputs = {
        "eps_surprise_pct": to_optional_float(
            first_present(
                source,
                ("eps_surprise_pct", "eps_surprise", "epsSurprise", "eps_surprise_percent"),
            )
        ),
        "revenue_surprise_pct": to_optional_float(
            first_present(
                source,
                (
                    "revenue_surprise_pct",
                    "revenue_surprise",
                    "revenueSurprise",
                    "revenue_surprise_percent",
                ),
            )
        ),
        "guidance": normalize_guidance(guidance_raw),
        "market_reaction_pct": to_optional_float(
            first_present(
                source,
                (
                    "market_reaction_pct",
                    "market_reaction",
                    "earnings_reaction",
                    "post_earnings_move",
                    "earnings_move_pct",
                ),
            )
        ),
        "earnings_days_since": earnings_days_since(source),
        "earnings_days_until": earnings_days_until(source),
        "eps_actual": to_optional_float(source.get("eps_actual")),
        "eps_estimate": to_optional_float(source.get("eps_estimate")),
        "revenue_actual": to_optional_float(source.get("revenue_actual")),
        "revenue_estimate": to_optional_float(source.get("revenue_estimate")),
        "earnings_report_date": source.get("earnings_report_date"),
        "earnings_data_source": source.get("earnings_data_source"),
    }
    mode = earnings_mode(source, inputs)
    inputs["active"] = mode == "scored"
    inputs["mode"] = mode
    return inputs


def compute_conviction_adjustment(
    earnings_score: int,
    recency: float,
    cap: int,
) -> int:
    delta = earnings_score - BASELINE_SCORE
    scaled = delta * recency * (cap / max(BASELINE_SCORE, 1))
    return int(round(max(-cap, min(cap, scaled))))


def apply_scout_score_adjustment(base_score: float, conviction_adjustment: int) -> int:
    return clamp(float(base_score) + conviction_adjustment)


def build_earnings_intelligence(source: dict[str, Any]) -> dict[str, Any]:
    enriched_source = enrich_earnings_source(source)
    inputs = extract_earnings_inputs(enriched_source)
    mode = inputs.get("mode", "unavailable")
    cap = conviction_cap(enriched_source)

    if mode == "awaiting_provider":
        days_since = inputs.get("earnings_days_since")
        report_date = inputs.get("earnings_report_date") or "recent report"
        return {
            "active": False,
            "mode": mode,
            "visible": True,
            "earnings_score": None,
            "earnings_score_raw": None,
            "label": "Awaiting provider results",
            "status_message": (
                f"Earnings reported but results not yet available from provider "
                f"({report_date}, {int(days_since or 0)} day(s) ago). "
                "EPS/revenue actuals will populate tiered scoring when FMP updates."
            ),
            "components": {
                "eps_surprise": 0,
                "revenue_surprise": 0,
                "guidance": 0,
                "market_reaction": 0,
                "quality_modifier": 0,
            },
            "conviction_adjustment": 0,
            "conviction_cap": cap,
            "catalyst_weighting": 1.0,
            "recency_multiplier": 1.0,
            "anti_double_count": {
                "primary_interpreter": False,
                "secondary_gate_reference_weight": SECONDARY_GATE_REFERENCE_WEIGHT,
            },
            "extensions": dict(EXTENSION_SLOTS),
            "inputs": inputs,
        }

    if mode == "pre_earnings":
        days_until = inputs.get("earnings_days_until")
        return {
            "active": False,
            "mode": mode,
            "visible": True,
            "earnings_score": None,
            "earnings_score_raw": None,
            "label": "Pre-earnings watch",
            "status_message": (
                f"Earnings report is imminent ({int(days_until or 0)} day(s) away). "
                "EPS/revenue surprise, guidance, and reaction fields were not returned yet, "
                "so tiered scoring is pending."
            ),
            "components": {
                "eps_surprise": 0,
                "revenue_surprise": 0,
                "guidance": 0,
                "market_reaction": 0,
                "quality_modifier": 0,
            },
            "conviction_adjustment": 0,
            "conviction_cap": cap,
            "catalyst_weighting": 1.0,
            "recency_multiplier": 1.0,
            "anti_double_count": {
                "primary_interpreter": False,
                "secondary_gate_reference_weight": SECONDARY_GATE_REFERENCE_WEIGHT,
            },
            "extensions": dict(EXTENSION_SLOTS),
            "inputs": inputs,
        }

    inactive_payload = {
        "active": False,
        "mode": mode,
        "visible": True,
        "earnings_score": None,
        "earnings_score_raw": None,
        "label": None,
        "status_message": (
            "No post-earnings surprise, guidance, or reaction fields were returned for this scan. "
            "Earnings Intelligence is attached but not scoring yet."
        ),
        "components": {
            "eps_surprise": 0,
            "revenue_surprise": 0,
            "guidance": 0,
            "market_reaction": 0,
            "quality_modifier": 0,
        },
        "conviction_adjustment": 0,
        "conviction_cap": cap,
        "catalyst_weighting": 1.0,
        "recency_multiplier": 1.0,
        "anti_double_count": {
            "primary_interpreter": False,
            "secondary_gate_reference_weight": SECONDARY_GATE_REFERENCE_WEIGHT,
        },
        "extensions": dict(EXTENSION_SLOTS),
        "inputs": inputs,
    }
    if not inputs["active"]:
        return inactive_payload

    recency = recency_multiplier(inputs.get("earnings_days_since"))

    components = {
        "eps_surprise": score_eps_surprise(inputs["eps_surprise_pct"]),
        "revenue_surprise": score_revenue_surprise(inputs["revenue_surprise_pct"]),
        "guidance": score_guidance(inputs["guidance"]),
        "market_reaction": score_market_reaction(inputs["market_reaction_pct"]),
        "quality_modifier": score_quality_modifier(
            inputs["eps_surprise_pct"],
            inputs["revenue_surprise_pct"],
            inputs["guidance"],
            inputs["market_reaction_pct"],
        ),
    }
    raw_delta = sum(components.values())
    earnings_score_raw = clamp(BASELINE_SCORE + raw_delta)
    decayed_delta = raw_delta * recency
    earnings_score = clamp(BASELINE_SCORE + decayed_delta)
    conviction_adjustment = compute_conviction_adjustment(earnings_score, 1.0, cap)

    return {
        "active": True,
        "mode": "scored",
        "visible": True,
        "status_message": "Post-earnings fields detected. Tiered Earnings Intelligence scoring is active.",
        "earnings_score": earnings_score,
        "earnings_score_raw": earnings_score_raw,
        "label": earnings_label(earnings_score),
        "components": components,
        "conviction_adjustment": conviction_adjustment,
        "conviction_cap": cap,
        "catalyst_weighting": recency,
        "recency_multiplier": recency,
        "anti_double_count": {
            "primary_interpreter": True,
            "secondary_gate_reference_weight": SECONDARY_GATE_REFERENCE_WEIGHT,
            "message": (
                "Event Trigger, Intel Feed, and Volatility Read reference earnings lightly "
                f"at {int(SECONDARY_GATE_REFERENCE_WEIGHT * 100)}% weight when Earnings Intelligence is active."
            ),
        },
        "extensions": dict(EXTENSION_SLOTS),
        "inputs": inputs,
    }


def build_earnings_intelligence_for_result(
    result_data: dict[str, Any],
    timeout: float = 20.0,
) -> dict[str, Any]:
    merged, ingestion = merge_fmp_earnings_into_source(result_data, timeout=timeout)
    intelligence = build_earnings_intelligence(merged)
    intelligence["ingestion"] = ingestion
    return intelligence


def attach_adjusted_scout_score(
    serialized: dict[str, Any],
    intelligence: dict[str, Any],
) -> dict[str, Any]:
    base_score = float(serialized.get("score") or 0)
    adjustment = int(intelligence.get("conviction_adjustment") or 0)
    serialized["scoutScoreBase"] = base_score
    serialized["earningsConvictionAdjustment"] = adjustment
    if intelligence.get("active"):
        serialized["adjustedScoutScore"] = apply_scout_score_adjustment(base_score, adjustment)
    else:
        serialized["adjustedScoutScore"] = int(round(base_score))
    serialized["earningsIntelligenceMode"] = intelligence.get("mode", "unavailable")
    return serialized
