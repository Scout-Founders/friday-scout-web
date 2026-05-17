#!/usr/bin/env python3
"""Update local Scout memory records with forward performance outcomes."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from memory_store import connect, init_db, json_load
from option_picker import fmp_api_key


FMP_HISTORICAL_URL = "https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
STOCK_HORIZONS = (1, 3, 5, 10, 20)
OPTION_HORIZONS = (1, 3, 5, 10)


def parse_date(value: Any) -> Optional[date]:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(text).date()
    except ValueError:
        try:
            return datetime.strptime(str(value).split("T", 1)[0], "%Y-%m-%d").date()
        except ValueError:
            return None


def fetch_historical_prices(ticker: str, from_date: date, to_date: date, timeout: float = 25.0) -> dict[date, float]:
    api_key = fmp_api_key()
    if not api_key:
        raise RuntimeError("FMP API key was not found in the environment.")

    base_url = FMP_HISTORICAL_URL.format(ticker=urllib.parse.quote(ticker.upper()))
    query = urllib.parse.urlencode(
        {"from": from_date.isoformat(), "to": to_date.isoformat(), "apikey": api_key}
    )
    request = urllib.request.Request(
        f"{base_url}?{query}",
        headers={"Accept": "application/json", "User-Agent": "scout-gates-sandbox/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"FMP historical HTTP {exc.code}: {body[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach FMP historical endpoint: {exc.reason}") from exc

    prices: dict[date, float] = {}
    for row in payload.get("historical") or []:
        row_date = parse_date(row.get("date"))
        close = row.get("close")
        if row_date and isinstance(close, (int, float)):
            prices[row_date] = float(close)
    return prices


def first_price_on_or_after(prices: dict[date, float], target: date) -> Optional[float]:
    for available in sorted(prices):
        if available >= target:
            return prices[available]
    return None


def raw_entry_price(row: Any) -> Optional[float]:
    if row["entry_price"] is not None:
        return float(row["entry_price"])
    raw = json_load(row["raw_result_json"]) or {}
    price = raw.get("price")
    return float(price) if isinstance(price, (int, float)) else None


def option_entry_price(row: Any) -> Optional[float]:
    if row["option_entry_price"] is not None:
        return float(row["option_entry_price"])
    option = json_load(row["final_option_pick_json"])
    if not isinstance(option, dict):
        return None
    for key in ("midPrice", "lastPrice", "ask", "bid"):
        value = option.get(key)
        if isinstance(value, (int, float)) and value > 0:
            return float(value)
    return None


def stock_label(final_direction: str, returns: list[float]) -> str:
    if not returns:
        return "PENDING"
    latest = returns[-1]
    directional = -latest if final_direction == "Bearish" else latest
    if directional > 1:
        return "WIN"
    if directional < -1:
        return "LOSS"
    return "FLAT"


def update_outcomes(limit: int = 250, timeout: float = 25.0) -> dict[str, Any]:
    init_db()
    today = date.today()
    checked = 0
    updated = 0
    pending = 0
    errors: list[str] = []

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM scan_results
            ORDER BY timestamp ASC, id ASC
            LIMIT ?
            """,
            (limit,),
        ).fetchall()

        for row in rows:
            checked += 1
            checked_at = datetime.now(timezone.utc).isoformat()
            ticker = row["ticker"]
            entry_date = parse_date(row["timestamp"])
            entry = raw_entry_price(row)
            if not entry_date or entry is None:
                pending += 1
                conn.execute(
                    """
                    UPDATE scan_results
                    SET stock_outcome_label = COALESCE(stock_outcome_label, 'PENDING'),
                        result_notes = COALESCE(result_notes, ?),
                        outcome_last_updated_at = COALESCE(outcome_last_updated_at, ?)
                    WHERE id = ?
                    """,
                    ("Entry date or entry price was not available.", checked_at, row["id"]),
                )
                continue

            max_horizon = max(STOCK_HORIZONS)
            if (today - entry_date).days < min(STOCK_HORIZONS):
                pending += 1
                conn.execute(
                    """
                    UPDATE scan_results
                    SET entry_price = COALESCE(entry_price, ?),
                        stock_outcome_label = 'PENDING',
                        option_entry_price = COALESCE(option_entry_price, ?),
                        option_outcome_label = COALESCE(option_outcome_label, 'PENDING'),
                        result_notes = COALESCE(result_notes, ?),
                        outcome_last_updated_at = COALESCE(outcome_last_updated_at, ?)
                    WHERE id = ?
                    """,
                    (
                        entry,
                        option_entry_price(row),
                        "Not enough time has passed for a 1D outcome.",
                        checked_at,
                        row["id"],
                    ),
                )
                continue

            try:
                prices = fetch_historical_prices(
                    ticker,
                    entry_date,
                    today,
                    timeout=timeout,
                )
            except RuntimeError as exc:
                pending += 1
                errors.append(f"{ticker}: {exc}")
                conn.execute(
                    """
                    UPDATE scan_results
                    SET entry_price = COALESCE(entry_price, ?),
                        stock_outcome_label = COALESCE(stock_outcome_label, 'PENDING'),
                        result_notes = ?,
                        outcome_last_updated_at = ?
                    WHERE id = ?
                    """,
                    (entry, f"FMP stock outcome data unavailable: {exc}", checked_at, row["id"]),
                )
                continue

            updates: dict[str, Any] = {"entry_price": entry}
            returns: list[float] = []
            available_prices: list[float] = []
            notes: list[str] = []

            for horizon in STOCK_HORIZONS:
                price_field = f"price_after_{horizon}d"
                return_field = f"return_{horizon}d"
                if (today - entry_date).days < horizon:
                    notes.append(f"{horizon}D pending: not enough time has passed.")
                    continue
                target_price = first_price_on_or_after(prices, entry_date + timedelta(days=horizon))
                if target_price is None:
                    notes.append(f"{horizon}D price unavailable from FMP.")
                    continue
                if row[price_field] is None:
                    updates[price_field] = target_price
                if row[return_field] is None:
                    result_return = (target_price - entry) / entry * 100
                    updates[return_field] = round(result_return, 4)
                returns.append((target_price - entry) / entry * 100)
                available_prices.append(target_price)

            direction = row["final_direction"] or ""
            directional_moves = [
                (-move if direction == "Bearish" else move) for move in returns
            ]
            if directional_moves:
                updates["max_favorable_move"] = round(max(directional_moves), 4)
                updates["max_adverse_move"] = round(min(directional_moves), 4)
            label = stock_label(direction, returns)
            if row["stock_outcome_label"] in (None, "PENDING") or label != "PENDING":
                updates["stock_outcome_label"] = label

            opt_entry = option_entry_price(row)
            if opt_entry is not None:
                updates["option_entry_price"] = opt_entry
            if row["option_outcome_label"] is None:
                updates["option_outcome_label"] = "PENDING"
                notes.append("Option historical pricing is not available from the current sandbox FMP setup.")

            updates["result_notes"] = "; ".join(notes) if notes else "Outcome update completed with available FMP stock prices."
            updates["outcome_last_updated_at"] = checked_at

            if len(updates) > 0:
                assignments = ", ".join(f"{field} = ?" for field in updates)
                conn.execute(
                    f"UPDATE scan_results SET {assignments} WHERE id = ?",
                    (*updates.values(), row["id"]),
                )
                updated += 1

            if label == "PENDING":
                pending += 1

    return {
        "ok": True,
        "records_checked": checked,
        "records_updated": updated,
        "records_still_pending": pending,
        "errors": errors,
    }
