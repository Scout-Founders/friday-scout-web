#!/usr/bin/env python3
"""Directional transparency for Scout gate sandbox results.

The deployed gate response exposes a single Scout score plus a direction. This
module separates beta-facing directional conviction from the overall score by
deriving bull/bear evidence from returned fields and raw gate text.
"""

from __future__ import annotations

import re
from typing import Any

from explainability import clean_line, split_gate_blocks


GATE_NAME_TO_CODE: dict[str, str] = {
    "Market Filter": "SENTINEL",
    "Core Strength": "ATLAS",
    "Forward Vision": "ORACLE",
    "Smart Money": "PHANTOM",
    "Event Trigger": "CATALYST",
    "Threat Scan": "SPECTER",
    "Sector Wind": "MERIDIAN",
    "Trend Lock": "COMPASS",
    "Volatility Read": "PULSE",
    "Strategy Select": "ARCHER",
}


def gate_code(gate_name: str) -> str:
    return GATE_NAME_TO_CODE.get(gate_name, gate_name.upper().replace(" ", "_"))


def clamp(value: float, low: int = 0, high: int = 100) -> int:
    return max(low, min(high, int(round(value))))


def add_signal(
    signals: list[dict[str, Any]],
    side: str,
    gate: str,
    description: str,
    value: Any,
    weight: float,
    source_field: str,
) -> None:
    if value is None or value == "":
        return
    signals.append(
        {
            "side": side,
            "gate": gate,
            "description": description,
            "value": value,
            "weight": weight,
            "source_field": source_field,
        }
    )


def block_lines(result_data: dict[str, Any], gate_number: int) -> list[str]:
    raw_output = str(result_data.get("raw_output") or "")
    block = split_gate_blocks(raw_output).get(gate_number, "")
    return [clean_line(line) for line in block.splitlines()[1:] if clean_line(line)]


def find_line(lines: list[str], *needles: str) -> str | None:
    for line in lines:
        lower = line.lower()
        if all(needle.lower() in lower for needle in needles):
            return line
    return None


def parse_float(pattern: str, text: str) -> float | None:
    match = re.search(pattern, text)
    if not match:
        return None
    try:
        return float(match.group(1).replace(",", ""))
    except ValueError:
        return None


def direction_label(direction: str) -> str:
    normalized = (direction or "").strip().upper()
    if "BULL" in normalized:
        return "Bullish"
    if "BEAR" in normalized:
        return "Bearish"
    return "Neutral"


def reconcile_contributor_sum(
    contributors: list[dict[str, Any]],
    target: int,
) -> list[dict[str, Any]]:
    if target <= 0:
        return []
    if not contributors:
        return contributors
    current = sum(item["points"] for item in contributors)
    if current == target:
        return contributors
    if current <= 0:
        return contributors
    factor = target / current
    scaled = [
        {**item, "points": int(round(item["points"] * factor))}
        for item in contributors
    ]
    drift = target - sum(item["points"] for item in scaled)
    if drift:
        index = max(range(len(scaled)), key=lambda idx: scaled[idx]["points"])
        scaled[index] = {
            **scaled[index],
            "points": max(0, scaled[index]["points"] + drift),
        }
    return scaled


def scale_contributors(
    contributors: list[dict[str, Any]],
    factor: float,
) -> list[dict[str, Any]]:
    if not contributors or factor == 1.0:
        return contributors
    target = int(round(sum(item["points"] for item in contributors) * factor))
    scaled = [
        {**item, "points": int(round(item["points"] * factor))}
        for item in contributors
    ]
    return reconcile_contributor_sum(scaled, target)


def contributors_from_signals(
    signals: list[dict[str, Any]],
    side: str,
) -> list[dict[str, Any]]:
    contributors: list[dict[str, Any]] = []
    for signal in signals:
        if signal.get("side") != side:
            continue
        points = int(round(float(signal.get("weight") or 0)))
        if points <= 0:
            continue
        contributors.append(
            {
                "gate": gate_code(str(signal.get("gate") or "UNKNOWN")),
                "points": points,
                "reason": str(signal.get("description") or "No reason returned."),
            }
        )
    return contributors


def build_net_attribution(
    signals: list[dict[str, Any]],
    direction: str,
    bull_conviction: int,
    bear_conviction: int,
) -> dict[str, Any]:
    bull_contributors = contributors_from_signals(signals, "bull")
    bear_contributors = contributors_from_signals(signals, "bear")

    if direction == "Bullish":
        bull_contributors.append(
            {
                "gate": "DIRECTION",
                "points": 25,
                "reason": "Scout's returned direction was bullish.",
            }
        )
        bear_contributors = scale_contributors(bear_contributors, 0.65)
    elif direction == "Bearish":
        bear_contributors.append(
            {
                "gate": "DIRECTION",
                "points": 25,
                "reason": "Scout's returned direction was bearish.",
            }
        )
        bull_contributors = scale_contributors(bull_contributors, 0.65)

    bull_contributors = reconcile_contributor_sum(bull_contributors, bull_conviction)
    bear_contributors = reconcile_contributor_sum(bear_contributors, bear_conviction)

    return {
        "bullTotal": bull_conviction,
        "bearTotal": bear_conviction,
        "net": bull_conviction - bear_conviction,
        "bullContributors": bull_contributors,
        "bearContributors": bear_contributors,
    }


def build_directional_breakdown(result_data: dict[str, Any]) -> dict[str, Any]:
    ticker = str(result_data.get("ticker") or "")
    direction = direction_label(str(result_data.get("direction") or ""))
    signals: list[dict[str, Any]] = []

    rsi = result_data.get("rsi")
    if isinstance(rsi, (int, float)):
        if rsi >= 70:
            add_signal(
                signals,
                "bear",
                "Core Strength",
                "RSI is overbought, which can mean the stock is extended short term.",
                round(rsi, 2),
                min(22, (rsi - 70) * 3 + 10),
                "rsi",
            )
        elif rsi <= 35:
            add_signal(
                signals,
                "bull",
                "Core Strength",
                "RSI is oversold, which can support a rebound setup.",
                round(rsi, 2),
                min(18, (35 - rsi) * 2 + 8),
                "rsi",
            )
        else:
            add_signal(
                signals,
                "bull",
                "Core Strength",
                "RSI is neutral, so momentum is not showing an overbought warning.",
                round(rsi, 2),
                5,
                "rsi",
            )

    dcf_gap = result_data.get("dcf_gap")
    if isinstance(dcf_gap, (int, float)):
        if dcf_gap > 0:
            add_signal(
                signals,
                "bull",
                "Core Strength",
                "DCF gap is positive, suggesting upside versus current price.",
                f"{dcf_gap:.1f}%",
                min(20, dcf_gap / 3),
                "dcf_gap",
            )
        elif dcf_gap < -20:
            add_signal(
                signals,
                "bear",
                "Core Strength",
                "DCF gap is deeply negative, suggesting overvaluation versus DCF.",
                f"{dcf_gap:.1f}%",
                min(22, abs(dcf_gap) / 4),
                "dcf_gap",
            )

    change = result_data.get("change")
    if isinstance(change, (int, float)):
        if change <= -2:
            add_signal(
                signals,
                "bear",
                "Market Filter",
                "The stock is down materially today, adding bearish near-term pressure.",
                f"{change:.2f}%",
                min(16, abs(change) * 2),
                "change",
            )
        elif change >= 2:
            add_signal(
                signals,
                "bull",
                "Market Filter",
                "The stock is up materially today, adding bullish near-term momentum.",
                f"{change:.2f}%",
                min(14, change * 2),
                "change",
            )

    wind = result_data.get("wind")
    sector = result_data.get("sector")
    if isinstance(wind, (int, float)):
        if wind > 0:
            add_signal(
                signals,
                "bull",
                "Sector Wind",
                "Sector wind is positive, indicating money is flowing into the group.",
                f"{sector} wind {wind:+}",
                min(18, wind * 6),
                "wind",
            )
        elif wind < 0:
            add_signal(
                signals,
                "bear",
                "Sector Wind",
                "Sector wind is negative, creating a directional headwind.",
                f"{sector} wind {wind:+}",
                min(16, abs(wind) * 6),
                "wind",
            )

    trend_lines = block_lines(result_data, 9)
    trend_line = find_line(trend_lines, "trend")
    if trend_line:
        side = "bull" if "uptrend" in trend_line.lower() or "above" in trend_line.lower() else "bear"
        add_signal(
            signals,
            side,
            "Trend Lock",
            "Trend confirmation contributed to the directional read.",
            trend_line,
            18,
            "raw_output.gate_9",
        )

    oracle_lines = block_lines(result_data, 3)
    revenue_line = find_line(oracle_lines, "revenue growth")
    eps_line = find_line(oracle_lines, "eps growth")
    for line, label in ((revenue_line, "revenue growth"), (eps_line, "EPS growth")):
        if not line:
            continue
        value = parse_float(r":\s*([-+]?\d+(?:\.\d+)?)%", line)
        if value is None:
            continue
        add_signal(
            signals,
            "bull" if value > 0 else "bear",
            "Forward Vision",
            f"{label} {'supports expansion' if value > 0 else 'is negative'}.",
            line,
            min(16, abs(value) / 4 + 4),
            "raw_output.gate_3",
        )

    phantom_lines = block_lines(result_data, 4)
    consensus_line = find_line(phantom_lines, "bullish")
    if consensus_line:
        add_signal(
            signals,
            "bull",
            "Smart Money",
            "Analyst consensus supports a bullish setup.",
            consensus_line,
            16,
            "raw_output.gate_4",
        )
    insider_line = find_line(phantom_lines, "insider selling")
    if insider_line:
        add_signal(
            signals,
            "bear",
            "Smart Money",
            "Insider selling reduced directional confidence.",
            insider_line,
            12,
            "raw_output.gate_4",
        )
    upgrades_line = find_line(phantom_lines, "upgrades")
    if upgrades_line:
        add_signal(
            signals,
            "bull",
            "Smart Money",
            "Recent analyst upgrades added bullish support.",
            upgrades_line,
            10,
            "raw_output.gate_4",
        )

    catalyst_lines = block_lines(result_data, 5)
    positive_line = find_line(catalyst_lines, "positive:")
    negative_line = find_line(catalyst_lines, "negative:")
    if positive_line:
        add_signal(
            signals,
            "bull",
            "Event Trigger",
            "Positive catalyst language supported the bullish side.",
            positive_line,
            12,
            "raw_output.gate_5",
        )
    if negative_line:
        add_signal(
            signals,
            "bear",
            "Event Trigger",
            "Negative catalyst language supported the bearish side.",
            negative_line,
            14,
            "raw_output.gate_5",
        )
    multi_negative = find_line(catalyst_lines, "multiple negative")
    if multi_negative:
        add_signal(
            signals,
            "bear",
            "Event Trigger",
            "Multiple negative headlines added bearish pressure.",
            multi_negative,
            12,
            "raw_output.gate_5",
        )

    specter_lines = block_lines(result_data, 6)
    for line in specter_lines:
        lower = line.lower()
        if any(term in lower for term in ("insider selling", "legal", "regulatory", "priced in", "threat")):
            add_signal(
                signals,
                "bear",
                "Threat Scan",
                "Risk/threat evidence contributed bearish pressure.",
                line,
                18 if "legal" in lower or "regulatory" in lower else 14,
                "raw_output.gate_6",
            )

    pulse_lines = block_lines(result_data, 10)
    pulse_line = pulse_lines[0] if pulse_lines else None
    if pulse_line and ("moved" in pulse_line.lower() or "elevated" in pulse_line.lower()):
        add_signal(
            signals,
            "bear",
            "Volatility Read",
            "Elevated volatility increased downside or caution pressure.",
            pulse_line,
            12,
            "raw_output.gate_10",
        )

    archer_lines = block_lines(result_data, 13)
    for line in archer_lines:
        lower = line.lower()
        if "bull call" in lower or "leaps candidate" in lower or "long-term buy candidate" in lower:
            add_signal(
                signals,
                "bull",
                "Strategy Select",
                "Strategy selection included bullish/long-term buy evidence.",
                line,
                16,
                "raw_output.gate_13",
            )
        if "bear call" in lower or "bearish direction" in lower or "not recommended" in lower:
            add_signal(
                signals,
                "bear",
                "Strategy Select",
                "Strategy selection included bearish or caution evidence.",
                line,
                16 if "bear call" in lower else 10,
                "raw_output.gate_13",
            )

    direction_upper = str(result_data.get("direction") or "").upper()
    if "BULL" in direction_upper:
        add_signal(
            signals,
            "bull",
            "Strategy Select",
            "Scout's returned direction was bullish.",
            result_data.get("direction"),
            22,
            "direction",
        )
    elif "BEAR" in direction_upper:
        add_signal(
            signals,
            "bear",
            "Strategy Select",
            "Scout's returned direction was bearish.",
            result_data.get("direction"),
            22,
            "direction",
        )

    bull_raw = sum(signal["weight"] for signal in signals if signal["side"] == "bull")
    bear_raw = sum(signal["weight"] for signal in signals if signal["side"] == "bear")
    # The deployed system returns trade direction separately from broad quality.
    # Anchor the conviction read to that returned direction while still showing
    # opposing quality signals in the modal/report.
    if direction == "Bullish":
        bull_raw += 25
        bear_raw *= 0.65
    elif direction == "Bearish":
        bear_raw += 25
        bull_raw *= 0.65
    bull_conviction = clamp(bull_raw)
    bear_conviction = clamp(bear_raw)
    net_edge = bull_conviction - bear_conviction

    top_bull = sorted(
        [signal for signal in signals if signal["side"] == "bull"],
        key=lambda signal: signal["weight"],
        reverse=True,
    )
    top_bear = sorted(
        [signal for signal in signals if signal["side"] == "bear"],
        key=lambda signal: signal["weight"],
        reverse=True,
    )

    if direction == "Bullish":
        summary = (
            f"{ticker} was labeled bullish because bull conviction "
            f"({bull_conviction}) exceeded bear conviction ({bear_conviction})."
        )
    elif direction == "Bearish":
        summary = (
            f"{ticker} was labeled bearish because bear conviction "
            f"({bear_conviction}) exceeded bull conviction ({bull_conviction}) in the beta directional read."
        )
    else:
        summary = (
            f"{ticker} was labeled neutral because bull conviction ({bull_conviction}) "
            f"and bear conviction ({bear_conviction}) were close or direction was not returned."
        )

    net_attribution = build_net_attribution(
        signals,
        direction,
        bull_conviction,
        bear_conviction,
    )

    return {
        "ticker": ticker,
        "scoutScore": result_data.get("scout_score"),
        "scoreInterpretation": (
            "The returned Scout Score is treated as an overall/blended Scout conviction score. "
            "Directional conviction is derived separately in this sandbox from returned fields and gate text."
        ),
        "direction": direction,
        "bullConviction": bull_conviction,
        "bearConviction": bear_conviction,
        "netDirectionalEdge": net_edge,
        "summary": summary,
        "bullishSignals": top_bull,
        "bearishSignals": top_bear,
        "allSignals": signals,
        "netAttribution": net_attribution,
    }
