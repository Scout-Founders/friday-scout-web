#!/usr/bin/env python3
"""Build structured beta-test explanations for Scout gate results."""

from __future__ import annotations

import re
from typing import Any, Optional


MISSING_DETAIL = "Specific value/threshold was not returned by this gate yet."

GATE_RULES = {
    "sentinel": {
        "display": "Market Filter",
        "required": "Tradable price and sufficient volume",
        "fields": ["price", "volume", "raw_output.gate_1"],
    },
    "atlas": {
        "display": "Core Strength",
        "required": "Healthy core fundamentals and a passing core-strength score",
        "fields": ["piotroski", "z_score", "rsi", "dcf_value", "dcf_gap", "raw_output.gate_2"],
    },
    "oracle": {
        "display": "Forward Vision",
        "required": "Positive forward growth and a passing forward-vision score",
        "fields": ["raw_output.gate_3"],
    },
    "phantom": {
        "display": "Smart Money",
        "required": "Supportive analyst/smart-money signal and a passing smart-money score",
        "fields": ["raw_output.gate_4"],
    },
    "catalyst": {
        "display": "Event Trigger",
        "required": "Positive or sufficient catalyst signal",
        "fields": ["raw_output.gate_5"],
    },
    "specter": {
        "display": "Threat Scan",
        "required": "No material red flags detected",
        "fields": ["raw_output.gate_6"],
    },
    "meridian": {
        "display": "Sector Wind",
        "required": "Neutral or positive sector wind",
        "fields": ["sector", "wind", "raw_output.gate_7"],
    },
    "aegis": {
        "display": "Earnings Shield",
        "required": "No near-term earnings conflict or manually review if date unavailable",
        "fields": ["earnings_days", "raw_output.gate_8"],
    },
    "compass": {
        "display": "Trend Lock",
        "required": "Price confirms trend, typically above the referenced moving average",
        "fields": ["trend", "price", "raw_output.gate_9"],
    },
    "pulse": {
        "display": "Volatility Read",
        "required": "Normal or acceptable volatility for the intended setup",
        "fields": ["iv_elevated", "change", "raw_output.gate_10"],
    },
    "signal": {
        "display": "Intel Feed",
        "required": "Recent news/intel context available for review",
        "fields": ["raw_output.gate_11"],
    },
    "current": {
        "display": "Flow Analysis",
        "required": "Supportive ETF/sector flow context",
        "fields": ["sector", "wind", "raw_output.gate_12"],
    },
    "archer": {
        "display": "Strategy Select",
        "required": "A viable strategy can be selected for the ticker direction",
        "fields": ["direction", "strategies", "raw_output.gate_13"],
    },
    "fortress": {
        "display": "Risk Gate",
        "required": "Position size and risk guidance are acceptable",
        "fields": ["scout_score", "raw_output.gate_14"],
    },
}


def clean_line(line: str) -> str:
    cleaned = re.sub(r"[✅❌⚠ℹ📰🔴🚨→─]+", " ", line)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    return cleaned


def split_gate_blocks(raw_output: str) -> dict[int, str]:
    blocks: dict[int, str] = {}
    pattern = re.compile(
        r"GATE\s+(\d+)\s+[—-]\s+.*?(?=\n[^\n]*GATE\s+\d+\s+[—-]|\n=+|\Z)",
        re.S,
    )
    for match in pattern.finditer(raw_output or ""):
        blocks[int(match.group(1))] = match.group(0).strip()
    return blocks


def gate_detail_lines(block: str) -> list[str]:
    lines: list[str] = []
    for raw_line in block.splitlines()[1:]:
        line = clean_line(raw_line)
        if not line:
            continue
        upper = line.upper()
        if upper in ("PASS", "FAIL") or "RED FLAGS DETECTED" in upper:
            continue
        lines.append(line)
    return lines


def extract_score(lines: list[str]) -> Optional[str]:
    for line in lines:
        if line.lower().startswith("score:"):
            return line.split(":", 1)[1].strip()
    return None


def most_relevant_lines(lines: list[str], passed: bool) -> list[str]:
    if not lines:
        return []

    if not passed:
        priority_terms = (
            "score:",
            "negative:",
            "warning:",
            "insider",
            "legal",
            "regulatory",
            "headwind",
            "moved",
            "below",
            "overbought",
            "overvalued",
            "revenue growth",
            "eps growth",
        )
        prioritized = [
            line for line in lines if any(term in line.lower() for term in priority_terms)
        ]
        return prioritized[:5] or lines[:5]

    return lines[:4]


def summarize_actual_value(result_data: dict[str, Any], gate_key: str, block: str) -> str:
    lines = gate_detail_lines(block)
    relevant = most_relevant_lines(lines, result_data.get("gates", {}).get(gate_key) is True)
    if relevant:
        return "; ".join(relevant)

    rule = GATE_RULES.get(gate_key, {})
    values: list[str] = []
    for field in rule.get("fields", []):
        if field.startswith("raw_output"):
            continue
        value = result_data.get(field)
        if value is not None:
            values.append(f"{field}={value}")
    return "; ".join(values) if values else MISSING_DETAIL


def build_gate_explanation(
    ticker: str,
    gate_name: str,
    gate_key: str,
    passed: bool,
    actual_value: str,
    required_value: str,
) -> str:
    status_word = "passed" if passed else "failed"
    if actual_value == MISSING_DETAIL:
        return f"{ticker} {status_word} {gate_name}. {MISSING_DETAIL}"

    if passed:
        return (
            f"{ticker} passed {gate_name} because Scout observed: {actual_value}. "
            f"Required condition: {required_value}."
        )

    return (
        f"{ticker} failed {gate_name} because Scout observed: {actual_value}. "
        f"Required condition: {required_value}."
    )


def result_status(
    result_data: dict[str, Any],
    winner_ticker: str,
    pick_mode: str,
) -> str:
    ticker = str(result_data.get("ticker") or "")
    gates = result_data.get("gates") if isinstance(result_data.get("gates"), dict) else {}
    passed = all(value is True for value in gates.values()) if gates else False

    if ticker == winner_ticker:
        return "winner"
    if passed:
        return "passed"
    if pick_mode == "score_only":
        return "scored only"
    return "rejected"


def build_summary(ticker: str, status: str, failed_gate_names: list[str]) -> str:
    if status == "winner":
        if failed_gate_names:
            return (
                f"{ticker} was the final winner by score, but failed "
                f"{len(failed_gate_names)} gates: {', '.join(failed_gate_names)}."
            )
        return f"{ticker} was the final winner and passed every gate."

    if not failed_gate_names:
        return f"{ticker} passed every gate."

    return (
        f"{ticker} was rejected because it failed {len(failed_gate_names)} gates: "
        f"{', '.join(failed_gate_names)}."
    )


def build_explanation(
    result_data: dict[str, Any],
    gates_order: list[tuple[str, str, str]],
    winner_ticker: str,
    pick_mode: str,
) -> dict[str, Any]:
    ticker = str(result_data.get("ticker") or "")
    raw_output = str(result_data.get("raw_output") or "")
    blocks = split_gate_blocks(raw_output)
    gates = result_data.get("gates") if isinstance(result_data.get("gates"), dict) else {}

    gate_explanations: list[dict[str, Any]] = []
    failed_gate_names: list[str] = []

    for index, (gate_key, _code, default_name) in enumerate(gates_order, start=1):
        rule = GATE_RULES.get(gate_key, {})
        gate_name = str(rule.get("display") or default_name)
        passed = gates.get(gate_key) is True
        status = "PASS" if passed else "FAIL"
        block = blocks.get(index, "")
        actual_value = summarize_actual_value(result_data, gate_key, block)
        required_value = str(rule.get("required") or MISSING_DETAIL)
        source_fields = rule.get("fields") or [f"raw_output.gate_{index}"]

        if not passed:
            failed_gate_names.append(gate_name)

        gate_explanations.append(
            {
                "gate_name": gate_name,
                "gate_key": gate_key,
                "gate_number": index,
                "status": status,
                "actual_value": actual_value,
                "required_value": required_value,
                "source_field": ", ".join(source_fields),
                "explanation": build_gate_explanation(
                    ticker,
                    gate_name,
                    gate_key,
                    passed,
                    actual_value,
                    required_value,
                ),
            }
        )

    status = result_status(result_data, winner_ticker, pick_mode)
    return {
        "ticker": ticker,
        "status": status,
        "score": result_data.get("scout_score"),
        "direction": result_data.get("direction"),
        "price": result_data.get("price"),
        "summary": build_summary(ticker, status, failed_gate_names),
        "failed_gates": failed_gate_names,
        "gates": gate_explanations,
    }
