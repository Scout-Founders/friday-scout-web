#!/usr/bin/env python3
"""Update local Scout memory records with forward performance outcomes."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Optional

from memory_store import connect, init_db, json_load, log_outcome_update_audit
from option_picker import fmp_api_key


FMP_HISTORICAL_URL = "https://financialmodelingprep.com/stable/historical-price-eod/full"
STOCK_HORIZONS = (1, 3, 5, 10, 20)
OPTION_HORIZONS = (1, 3, 5, 10)
AUDITED_UPDATE_FIELDS = (
    "entry_price",
    "price_after_1d",
    "price_after_3d",
    "price_after_5d",
    "price_after_10d",
    "price_after_20d",
    "return_1d",
    "return_3d",
    "return_5d",
    "return_10d",
    "return_20d",
    "stock_outcome_label_1d",
    "stock_outcome_label_3d",
    "stock_outcome_label_5d",
    "stock_outcome_label_10d",
    "stock_outcome_label_20d",
    "stock_outcome_label",
    "option_entry_price",
    "option_outcome_label",
    "max_favorable_move",
    "max_adverse_move",
    "result_notes",
    "outcome_last_updated_at",
)


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


def fetch_historical_prices(
    ticker: str,
    from_date: date,
    to_date: date,
    timeout: float = 25.0,
) -> tuple[dict[date, float], dict[str, Any]]:
    api_key = fmp_api_key()
    if not api_key:
        raise RuntimeError("FMP API key was not found in the environment.")

    query = urllib.parse.urlencode(
        {
            "symbol": ticker.upper(),
            "from": from_date.isoformat(),
            "to": to_date.isoformat(),
            "apikey": api_key,
        }
    )
    request = urllib.request.Request(
        f"{FMP_HISTORICAL_URL}?{query}",
        headers={"Accept": "application/json", "User-Agent": "scout-gates-sandbox/1.0"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = response.status
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"FMP historical HTTP {exc.code}: {body[:240]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Could not reach FMP historical endpoint: {exc.reason}") from exc

    prices: dict[date, float] = {}
    rows = payload if isinstance(payload, list) else payload.get("historical") or []
    for row in rows:
        if not isinstance(row, dict):
            continue
        row_date = parse_date(row.get("date"))
        close = row.get("close")
        if row_date and isinstance(close, (int, float)):
            prices[row_date] = float(close)
    return prices, {
        "status": status,
        "from": from_date.isoformat(),
        "to": to_date.isoformat(),
        "prices_returned": len(prices),
    }


def first_price_on_or_after(prices: dict[date, float], target: date) -> Optional[float]:
    for available in sorted(prices):
        if available >= target:
            return prices[available]
    return None


def first_price_entry_on_or_after(
    prices: dict[date, float],
    target: date,
) -> tuple[Optional[date], Optional[float]]:
    for available in sorted(prices):
        if available >= target:
            return available, prices[available]
    return None, None


def trading_day_price_after(
    prices: dict[date, float],
    entry_trade_date: date,
    trading_days_after_entry: int,
) -> tuple[Optional[date], Optional[float]]:
    available_dates = sorted(prices)
    try:
        entry_index = available_dates.index(entry_trade_date)
    except ValueError:
        return None, None

    target_index = entry_index + trading_days_after_entry
    if target_index >= len(available_dates):
        return None, None
    target_date = available_dates[target_index]
    return target_date, prices[target_date]


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


def stock_label_for_return(final_direction: str, result_return: float) -> str:
    if abs(result_return) < 1:
        return "FLAT"
    if final_direction == "Bearish":
        return "WIN" if result_return < 0 else "LOSS"
    return "WIN" if result_return > 0 else "LOSS"


def stock_label(final_direction: str, returns: list[float]) -> str:
    if not returns:
        return "PENDING"
    return stock_label_for_return(final_direction, returns[-1])


def old_values_for_update(row: Any, updates: dict[str, Any]) -> dict[str, Any]:
    return {field: row[field] for field in updates if field in AUDITED_UPDATE_FIELDS}


def execute_audited_update(
    conn: Any,
    row: Any,
    updates: dict[str, Any],
    checked_at: str,
    source_endpoint: str,
) -> None:
    assignments = ", ".join(f"{field} = ?" for field in updates)
    conn.execute(
        f"UPDATE scan_results SET {assignments} WHERE id = ?",
        (*updates.values(), row["id"]),
    )
    log_outcome_update_audit(
        conn,
        checked_at,
        row["ticker"],
        int(row["id"]),
        old_values_for_update(row, updates),
        updates,
        source_endpoint,
        row["engine_version"],
    )


def update_outcomes(limit: int = 250, timeout: float = 25.0) -> dict[str, Any]:
    init_db()
    today = date.today()
    checked = 0
    updated = 0
    pending = 0
    errors: list[str] = []
    details: list[dict[str, Any]] = []

    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM scan_results
            WHERE return_1d IS NULL
               OR return_3d IS NULL
               OR return_5d IS NULL
               OR return_10d IS NULL
               OR return_20d IS NULL
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
            saved_entry = raw_entry_price(row)
            detail: dict[str, Any] = {
                "id": row["id"],
                "ticker": ticker,
                "recommendation_timestamp": row["timestamp"],
                "recommendation_date": entry_date.isoformat() if entry_date else None,
                "saved_entry_price": saved_entry,
                "entry_price": None,
                "target_dates_checked": {},
                "fmp_response_status": None,
                "prices_found": {},
                "rows_updated": 0,
                "still_pending_reason": None,
            }
            details.append(detail)
            print(
                f"[outcomes] checking id={row['id']} ticker={ticker} "
                f"timestamp={row['timestamp']} saved_entry={saved_entry}",
                flush=True,
            )

            if not entry_date:
                pending += 1
                detail["still_pending_reason"] = "Recommendation timestamp was not available."
                execute_audited_update(
                    conn,
                    row,
                    {
                        "stock_outcome_label": row["stock_outcome_label"] or "PENDING",
                        "result_notes": row["result_notes"] or "Recommendation timestamp was not available.",
                        "outcome_last_updated_at": row["outcome_last_updated_at"] or checked_at,
                    },
                    checked_at,
                    "not_requested_missing_recommendation_timestamp",
                )
                print(f"[outcomes] pending ticker={ticker}: {detail['still_pending_reason']}", flush=True)
                continue

            try:
                prices, fmp_meta = fetch_historical_prices(
                    ticker,
                    entry_date,
                    today,
                    timeout=timeout,
                )
                detail["fmp_response_status"] = fmp_meta.get("status")
                detail["fmp_prices_returned"] = fmp_meta.get("prices_returned")
            except RuntimeError as exc:
                pending += 1
                errors.append(f"{ticker}: {exc}")
                if "API key was not found" in str(exc):
                    detail["fmp_response_status"] = "not_requested_missing_api_key"
                detail["still_pending_reason"] = f"FMP stock outcome data unavailable: {exc}"
                execute_audited_update(
                    conn,
                    row,
                    {
                        "entry_price": row["entry_price"] if row["entry_price"] is not None else saved_entry,
                        "stock_outcome_label": row["stock_outcome_label"] or "PENDING",
                        "result_notes": f"FMP stock outcome data unavailable: {exc}",
                        "outcome_last_updated_at": checked_at,
                    },
                    checked_at,
                    FMP_HISTORICAL_URL,
                )
                print(f"[outcomes] pending ticker={ticker}: {detail['still_pending_reason']}", flush=True)
                continue

            entry_trade_date, entry = first_price_entry_on_or_after(prices, entry_date)
            if entry_trade_date is None or entry is None:
                pending += 1
                detail["still_pending_reason"] = "No FMP trading price was found on or after the recommendation date."
                execute_audited_update(
                    conn,
                    row,
                    {
                        "stock_outcome_label": row["stock_outcome_label"] or "PENDING",
                        "result_notes": detail["still_pending_reason"],
                        "outcome_last_updated_at": checked_at,
                    },
                    checked_at,
                    FMP_HISTORICAL_URL,
                )
                print(f"[outcomes] pending ticker={ticker}: {detail['still_pending_reason']}", flush=True)
                continue

            detail["entry_trade_date"] = entry_trade_date.isoformat()
            detail["entry_price"] = entry
            for horizon in STOCK_HORIZONS:
                detail["target_dates_checked"][f"{horizon}D"] = {
                    "trading_day_offset": horizon,
                    "from_entry_trading_date": entry_trade_date.isoformat(),
                }
            print(
                f"[outcomes] FMP status={detail['fmp_response_status']} "
                f"prices_returned={detail.get('fmp_prices_returned')} "
                f"entry_date={entry_trade_date.isoformat()} entry_price={entry}",
                flush=True,
            )

            updates: dict[str, Any] = {"entry_price": entry}
            returns: list[float] = []
            available_prices: list[float] = []
            notes: list[str] = []
            direction = row["final_direction"] or ""

            for horizon in STOCK_HORIZONS:
                price_field = f"price_after_{horizon}d"
                return_field = f"return_{horizon}d"
                label_field = f"stock_outcome_label_{horizon}d"
                if row[return_field] is not None:
                    continue
                target_trade_date, target_price = trading_day_price_after(
                    prices,
                    entry_trade_date,
                    horizon,
                )
                if target_price is None:
                    notes.append(f"{horizon}D pending: not enough future trading closes from FMP.")
                    continue
                result_return = (target_price - entry) / entry * 100
                horizon_label = stock_label_for_return(direction, result_return)
                updates[price_field] = target_price
                updates[return_field] = round(result_return, 4)
                updates[label_field] = horizon_label
                detail["prices_found"][f"{horizon}D"] = {
                    "trading_day_offset": horizon,
                    "trading_date": target_trade_date.isoformat() if target_trade_date else None,
                    "price": target_price,
                    "return": round(result_return, 4),
                    "label": horizon_label,
                }
                returns.append(result_return)
                available_prices.append(target_price)

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
                execute_audited_update(conn, row, updates, checked_at, FMP_HISTORICAL_URL)
                updated += 1
                detail["rows_updated"] = 1

            if label == "PENDING":
                pending += 1
                detail["still_pending_reason"] = "; ".join(notes) or "No forward prices were available."
            print(
                f"[outcomes] updated ticker={ticker} row={row['id']} "
                f"label={label} prices_found={detail['prices_found']}",
                flush=True,
            )

    return {
        "ok": True,
        "records_checked": checked,
        "records_updated": updated,
        "records_pending": pending,
        "records_still_pending": pending,
        "errors": errors,
        "details": details,
    }
