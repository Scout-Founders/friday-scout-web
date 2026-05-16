#!/usr/bin/env python3
"""Sandbox option picker for passed Scout gate results.

This module is intentionally beta-only. It uses FMP option-chain data when an
API key is available in the same environment files loaded by run_gates.py.
"""

from __future__ import annotations

import json
import math
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime
from typing import Any, Optional


FMP_OPTION_CHAIN_URL = "https://financialmodelingprep.com/api/v3/option-chain/{ticker}"
OPTION_FIELDS = [
    "ticker",
    "direction",
    "contractSymbol",
    "optionType",
    "strike",
    "expiration",
    "bid",
    "ask",
    "midPrice",
    "lastPrice",
    "volume",
    "openInterest",
    "impliedVolatility",
    "delta",
    "gamma",
    "theta",
    "vega",
    "reasonSelected",
]


def empty_option_pick(ticker: str, direction: str, reason: str) -> dict[str, Any]:
    pick = {field: None for field in OPTION_FIELDS}
    pick["ticker"] = ticker
    pick["direction"] = direction
    pick["reasonSelected"] = reason
    return pick


def fmp_api_key() -> Optional[str]:
    for key in ("FMP_API_KEY", "FINANCIAL_MODELING_PREP_API_KEY", "NEXT_PUBLIC_FMP_API_KEY"):
        value = os.environ.get(key)
        if value:
            return value
    return None


def normalize_direction(direction: str) -> str:
    value = (direction or "").strip().upper()
    if "BEAR" in value or value == "PUT":
        return "BEARISH"
    return "BULLISH"


def desired_option_type(direction: str) -> str:
    return "put" if normalize_direction(direction) == "BEARISH" else "call"


def to_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def to_int(value: Any) -> Optional[int]:
    numeric = to_float(value)
    return int(numeric) if numeric is not None else None


def pick_value(source: dict[str, Any], keys: list[str]) -> Any:
    for key in keys:
        value = source.get(key)
        if value is not None and value != "":
            return value
    return None


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    text = str(value).split("T", 1)[0]
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def normalize_contract(raw: dict[str, Any]) -> dict[str, Any]:
    option_type = pick_value(raw, ["optionType", "type", "call_put", "putCall", "side"])
    if option_type is not None:
        option_type = str(option_type).lower()
        if "put" in option_type:
            option_type = "put"
        elif "call" in option_type:
            option_type = "call"

    bid = to_float(pick_value(raw, ["bid", "bidPrice"]))
    ask = to_float(pick_value(raw, ["ask", "askPrice"]))
    mid_price = (bid + ask) / 2 if bid is not None and ask is not None and ask > 0 else None

    return {
        "ticker": pick_value(raw, ["symbol", "underlying", "underlyingSymbol"]),
        "contractSymbol": pick_value(raw, ["contractSymbol", "optionSymbol", "symbol"]),
        "optionType": option_type,
        "strike": to_float(pick_value(raw, ["strike", "strikePrice"])),
        "expiration": pick_value(raw, ["expirationDate", "expiration", "expiry", "date"]),
        "bid": bid,
        "ask": ask,
        "midPrice": mid_price,
        "lastPrice": to_float(pick_value(raw, ["lastPrice", "last", "price"])),
        "volume": to_int(pick_value(raw, ["volume"])),
        "openInterest": to_int(pick_value(raw, ["openInterest", "open_interest", "oi"])),
        "impliedVolatility": to_float(
            pick_value(raw, ["impliedVolatility", "iv", "volatility"])
        ),
        "delta": to_float(pick_value(raw, ["delta"])),
        "gamma": to_float(pick_value(raw, ["gamma"])),
        "theta": to_float(pick_value(raw, ["theta"])),
        "vega": to_float(pick_value(raw, ["vega"])),
        "_raw": raw,
    }


def flatten_contracts(payload: Any) -> list[dict[str, Any]]:
    contracts: list[dict[str, Any]] = []

    def visit(value: Any) -> None:
        if isinstance(value, list):
            for item in value:
                visit(item)
            return
        if not isinstance(value, dict):
            return

        if any(key in value for key in ("strike", "strikePrice", "contractSymbol", "optionSymbol")):
            contracts.append(normalize_contract(value))

        for key in ("options", "calls", "puts", "data", "chain", "results"):
            if key in value:
                visit(value[key])

    visit(payload)
    return contracts


def fetch_option_chain(ticker: str, timeout: float = 25.0) -> list[dict[str, Any]]:
    api_key = fmp_api_key()
    if not api_key:
        raise RuntimeError("FMP API key was not found in the environment.")

    url_template = os.environ.get("FMP_OPTION_CHAIN_URL", FMP_OPTION_CHAIN_URL)
    base_url = url_template.format(ticker=urllib.parse.quote(ticker.upper()))
    separator = "&" if "?" in base_url else "?"
    url = f"{base_url}{separator}{urllib.parse.urlencode({'apikey': api_key})}"

    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "User-Agent": "scout-gates-sandbox/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"FMP option chain HTTP {exc.code}: {body[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach FMP option chain endpoint: {exc.reason}") from exc

    payload = json.loads(body)
    return flatten_contracts(payload)


def contract_score(contract: dict[str, Any], direction: str) -> Optional[tuple[float, list[str]]]:
    option_type = desired_option_type(direction)
    if contract.get("optionType") != option_type:
        return None

    bid = contract.get("bid")
    ask = contract.get("ask")
    volume = contract.get("volume")
    open_interest = contract.get("openInterest")
    expiration = parse_date(contract.get("expiration"))
    if bid is not None and bid <= 0:
        return None
    if volume is not None and volume <= 0:
        return None
    if expiration is None:
        return None

    days_out = (expiration - date.today()).days
    if days_out <= 0:
        return None

    score = 0.0
    reasons: list[str] = []

    if 14 <= days_out <= 45:
        score += 45
        reasons.append(f"expiration is {days_out} days out")
    else:
        score += max(0, 25 - min(abs(days_out - 30), 25))

    delta = contract.get("delta")
    abs_delta = abs(delta) if delta is not None else None
    if abs_delta is not None:
        delta_gap = abs(abs_delta - 0.425)
        score += max(0, 30 - delta_gap * 120)
        if 0.30 <= abs_delta <= 0.55:
            reasons.append(f"delta {abs_delta:.2f} is in target range")

    if bid is not None and ask is not None and ask > 0:
        spread_pct = (ask - bid) / ask
        score += max(0, 20 - spread_pct * 100)
        if spread_pct <= 0.15:
            reasons.append("spread is relatively tight")

    score += min((open_interest or 0) / 100, 15)
    score += min((volume or 0) / 50, 10)
    if open_interest:
        reasons.append(f"open interest {open_interest}")
    if volume:
        reasons.append(f"volume {volume}")

    if bid is None or ask is None:
        reasons.append("bid/ask was not fully returned")
    if delta is None:
        reasons.append("Greeks were not returned")

    return score, reasons


def choose_option_contract(
    ticker: str,
    direction: str,
    timeout: float = 25.0,
) -> dict[str, Any]:
    try:
        contracts = fetch_option_chain(ticker, timeout=timeout)
    except (RuntimeError, json.JSONDecodeError) as exc:
        return empty_option_pick(ticker, direction, str(exc))

    ranked: list[tuple[float, dict[str, Any], list[str]]] = []
    for contract in contracts:
        scored = contract_score(contract, direction)
        if scored is None:
            continue
        score, reasons = scored
        ranked.append((score, contract, reasons))

    if not ranked:
        return empty_option_pick(
            ticker,
            direction,
            "No option contract matched the beta liquidity, spread, expiration, and direction filters.",
        )

    score, selected, reasons = max(ranked, key=lambda item: item[0])
    return {
        "ticker": ticker,
        "direction": normalize_direction(direction),
        "contractSymbol": selected.get("contractSymbol"),
        "optionType": selected.get("optionType"),
        "strike": selected.get("strike"),
        "expiration": selected.get("expiration"),
        "bid": selected.get("bid"),
        "ask": selected.get("ask"),
        "midPrice": selected.get("midPrice"),
        "lastPrice": selected.get("lastPrice"),
        "volume": selected.get("volume"),
        "openInterest": selected.get("openInterest"),
        "impliedVolatility": selected.get("impliedVolatility"),
        "delta": selected.get("delta"),
        "gamma": selected.get("gamma"),
        "theta": selected.get("theta"),
        "vega": selected.get("vega"),
        "reasonSelected": "; ".join(reasons) or f"highest beta score {score:.1f}",
    }
