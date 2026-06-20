#!/usr/bin/env python3
"""Fetch post-earnings result data from FMP for Earnings Intelligence."""

from __future__ import annotations

import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from option_picker import fmp_api_key
from performance_tracker import fetch_historical_prices, parse_date


FMP_STABLE_EARNINGS_URL = "https://financialmodelingprep.com/stable/earnings"
FMP_STABLE_QUOTE_URL = "https://financialmodelingprep.com/stable/quote"


def log_earnings_debug(message: str) -> None:
    print(f"[earnings-ingestion] {message}", file=sys.stderr, flush=True)


def to_optional_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def surprise_percent(actual: Optional[float], estimate: Optional[float]) -> Optional[float]:
    if actual is None or estimate is None:
        return None
    if estimate == 0:
        return None
    return (actual - estimate) / abs(estimate) * 100.0


def fetch_json(url: str, params: dict[str, Any], timeout: float) -> tuple[Any, dict[str, Any]]:
    api_key = fmp_api_key()
    if not api_key:
        raise RuntimeError("FMP API key was not found in the environment.")
    query = urllib.parse.urlencode({**params, "apikey": api_key})
    request = urllib.request.Request(
        f"{url}?{query}",
        headers={"Accept": "application/json", "User-Agent": "scout-gates-sandbox/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"FMP HTTP {exc.code}: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach FMP: {exc.reason}") from exc
    return payload, {"status": status, "url": url, "params": params}


def fetch_fmp_earnings_rows(ticker: str, timeout: float = 20.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload, meta = fetch_json(
        FMP_STABLE_EARNINGS_URL,
        {"symbol": ticker.upper()},
        timeout=timeout,
    )
    rows = payload if isinstance(payload, list) else []
    meta["rows_returned"] = len(rows)
    return [row for row in rows if isinstance(row, dict)], meta


def fetch_fmp_quote(ticker: str, timeout: float = 12.0) -> tuple[Optional[dict[str, Any]], dict[str, Any]]:
    payload, meta = fetch_json(
        FMP_STABLE_QUOTE_URL,
        {"symbol": ticker.upper()},
        timeout=timeout,
    )
    if isinstance(payload, list) and payload:
        return payload[0], meta
    if isinstance(payload, dict):
        return payload, meta
    return None, meta


def parse_earnings_row_date(row: dict[str, Any]) -> Optional[date]:
    return parse_date(row.get("date"))


def has_reported_actuals(row: dict[str, Any]) -> bool:
    return to_optional_float(row.get("epsActual")) is not None or to_optional_float(
        row.get("revenueActual")
    ) is not None


def select_earnings_event(
    rows: list[dict[str, Any]],
    today: Optional[date] = None,
    gate_days_until: Optional[float] = None,
) -> tuple[Optional[dict[str, Any]], str]:
    """Pick the earnings row for the current cycle (imminent/upcoming or just reported)."""
    today = today or date.today()
    dated_rows = [(parse_earnings_row_date(row), row) for row in rows]
    dated_rows = [(row_date, row) for row_date, row in dated_rows if row_date is not None]
    dated_rows.sort(key=lambda item: item[0])

    upcoming = [
        (row_date, row)
        for row_date, row in dated_rows
        if row_date > today and (row_date - today).days <= 14
    ]
    recent_past = [
        (row_date, row)
        for row_date, row in dated_rows
        if row_date <= today and (today - row_date).days <= 14
    ]

    imminent = gate_days_until is not None and gate_days_until <= 7
    if imminent and upcoming:
        return upcoming[0][1], "imminent_upcoming_from_gate"

    if recent_past:
        for row_date, row in reversed(recent_past):
            if not has_reported_actuals(row):
                return row, "recent_report_awaiting_provider_actuals"
        for row_date, row in reversed(recent_past):
            if has_reported_actuals(row):
                return row, "recent_report_with_actuals"

    if upcoming:
        return upcoming[0][1], "upcoming_within_14_days"

    past_with_actuals = [
        (row_date, row)
        for row_date, row in reversed(dated_rows)
        if row_date <= today and has_reported_actuals(row)
    ]
    if past_with_actuals:
        return past_with_actuals[0][1], "latest_historical_with_actuals"

    if dated_rows:
        return dated_rows[-1][1], "latest_row_fallback"
    return None, "no_earnings_rows"


def reaction_percent_from_prices(
    ticker: str,
    report_date: date,
    timeout: float,
) -> tuple[Optional[float], dict[str, Any]]:
    meta: dict[str, Any] = {"report_date": report_date.isoformat()}
    try:
        prices, price_meta = fetch_historical_prices(
            ticker,
            report_date - timedelta(days=10),
            report_date + timedelta(days=3),
            timeout=timeout,
        )
        meta.update(price_meta)
    except RuntimeError as exc:
        meta["error"] = str(exc)
        return None, meta

    if report_date not in prices:
        prior_dates = [row_date for row_date in prices if row_date < report_date]
        if not prior_dates:
            meta["reason"] = "no_prices_before_report_date"
            return None, meta
        report_date_use = max(prior_dates)
    else:
        report_date_use = report_date

    prior_dates = [row_date for row_date in prices if row_date < report_date_use]
    if not prior_dates:
        meta["reason"] = "no_prior_close_for_reaction"
        return None, meta
    prior_date = max(prior_dates)
    prior_close = prices.get(prior_date)
    report_close = prices.get(report_date_use)
    if prior_close in (None, 0) or report_close is None:
        meta["reason"] = "missing_close_values"
        return None, meta
    reaction = (report_close - prior_close) / prior_close * 100.0
    meta["prior_date"] = prior_date.isoformat()
    meta["report_close_date"] = report_date_use.isoformat()
    meta["prior_close"] = prior_close
    meta["report_close"] = report_close
    return reaction, meta


def build_market_reaction(
    ticker: str,
    report_date: Optional[date],
    quote: Optional[dict[str, Any]],
    timeout: float,
) -> tuple[Optional[float], dict[str, Any]]:
    debug: dict[str, Any] = {}
    if quote:
        debug["quote_change_percent"] = quote.get("changePercentage")
        debug["quote_timestamp"] = quote.get("timestamp")
        change_pct = to_optional_float(quote.get("changePercentage"))
        if change_pct is not None:
            return change_pct, {"source": "fmp_quote_intraday", **debug}

    if report_date is None:
        return None, {"source": "unavailable", **debug}
    reaction, price_meta = reaction_percent_from_prices(ticker, report_date, timeout=timeout)
    return reaction, {"source": "fmp_historical_close_to_close", **price_meta, **debug}


def merge_fmp_earnings_into_source(
    source: dict[str, Any],
    timeout: float = 20.0,
) -> tuple[dict[str, Any], dict[str, Any]]:
    ticker = str(source.get("ticker") or "").upper()
    today = date.today()
    ingestion: dict[str, Any] = {
        "provider": "fmp",
        "ticker": ticker,
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(),
        "endpoints": {
            "earnings": FMP_STABLE_EARNINGS_URL,
            "quote": FMP_STABLE_QUOTE_URL,
        },
        "raw_api_response": None,
        "parsed_fields": {},
        "missing_fields": [],
        "timestamp_handling": {},
        "classification_reason": None,
        "error": None,
    }

    if not ticker:
        ingestion["error"] = "missing_ticker"
        return source, ingestion

    try:
        rows, earnings_meta = fetch_fmp_earnings_rows(ticker, timeout=timeout)
        ingestion["raw_api_response"] = rows[:5]
        ingestion["earnings_meta"] = earnings_meta
        log_earnings_debug(
            f"{ticker} FMP earnings rows={len(rows)} status={earnings_meta.get('status')}"
        )
    except RuntimeError as exc:
        ingestion["error"] = str(exc)
        log_earnings_debug(f"{ticker} FMP earnings fetch failed: {exc}")
        return source, ingestion

    gate_days_until = to_optional_float(source.get("earnings_days"))
    event, selection_reason = select_earnings_event(
        rows,
        today=today,
        gate_days_until=gate_days_until,
    )
    ingestion["classification_reason"] = selection_reason
    if not event:
        ingestion["missing_fields"] = [
            "eps_actual",
            "eps_estimate",
            "eps_surprise_pct",
            "revenue_actual",
            "revenue_estimate",
            "revenue_surprise_pct",
            "guidance",
            "market_reaction_pct",
        ]
        return source, ingestion

    report_date = parse_earnings_row_date(event)
    eps_actual = to_optional_float(event.get("epsActual"))
    eps_estimate = to_optional_float(event.get("epsEstimated"))
    revenue_actual = to_optional_float(event.get("revenueActual"))
    revenue_estimate = to_optional_float(event.get("revenueEstimated"))

    quote = None
    quote_meta: dict[str, Any] = {}
    try:
        quote, quote_meta = fetch_fmp_quote(ticker, timeout=timeout)
        ingestion["quote_meta"] = quote_meta
    except RuntimeError as exc:
        ingestion["quote_error"] = str(exc)

    days_until = None
    days_since = None
    if report_date:
        delta_days = (report_date - today).days
        if delta_days > 0:
            days_until = float(delta_days)
        else:
            days_since = float((today - report_date).days)

    market_reaction = None
    reaction_meta: dict[str, Any] = {"skipped": "report_not_yet_occurred"}
    if days_until is None or days_until <= 0:
        market_reaction, reaction_meta = build_market_reaction(
            ticker,
            report_date,
            quote,
            timeout=timeout,
        )

    eps_surprise = surprise_percent(eps_actual, eps_estimate)
    revenue_surprise = surprise_percent(revenue_actual, revenue_estimate)

    parsed = {
        "report_date": report_date.isoformat() if report_date else None,
        "eps_actual": eps_actual,
        "eps_estimate": eps_estimate,
        "eps_surprise_pct": eps_surprise,
        "revenue_actual": revenue_actual,
        "revenue_estimate": revenue_estimate,
        "revenue_surprise_pct": revenue_surprise,
        "guidance": None,
        "market_reaction_pct": market_reaction,
        "earnings_days_until": days_until,
        "earnings_days_since": days_since,
        "selection_reason": selection_reason,
        "reaction_meta": reaction_meta,
        "last_updated": event.get("lastUpdated"),
    }
    ingestion["parsed_fields"] = parsed
    ingestion["timestamp_handling"] = {
        "today": today.isoformat(),
        "report_date": parsed["report_date"],
        "days_until": days_until,
        "days_since": days_since,
        "gate_earnings_days": source.get("earnings_days"),
    }

    missing = []
    for field in (
        "eps_actual",
        "eps_estimate",
        "eps_surprise_pct",
        "revenue_actual",
        "revenue_estimate",
        "revenue_surprise_pct",
        "guidance",
        "market_reaction_pct",
    ):
        if parsed.get(field) in (None, ""):
            missing.append(field)
    ingestion["missing_fields"] = missing

    merged = {**source}
    if eps_surprise is not None:
        merged["eps_surprise_pct"] = eps_surprise
    if revenue_surprise is not None:
        merged["revenue_surprise_pct"] = revenue_surprise
    if market_reaction is not None:
        merged["market_reaction_pct"] = market_reaction
    if days_until is not None:
        merged["earnings_days"] = days_until
    if days_since is not None:
        merged["earnings_days_since"] = days_since
    merged["eps_actual"] = eps_actual
    merged["eps_estimate"] = eps_estimate
    merged["revenue_actual"] = revenue_actual
    merged["revenue_estimate"] = revenue_estimate
    merged["earnings_report_date"] = parsed["report_date"]
    merged["earnings_data_source"] = "fmp_stable_earnings"

    log_earnings_debug(
        f"{ticker} parsed report={parsed['report_date']} eps_surprise={eps_surprise} "
        f"rev_surprise={revenue_surprise} reaction={market_reaction} missing={missing}"
    )
    return merged, ingestion
