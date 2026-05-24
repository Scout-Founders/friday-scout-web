#!/usr/bin/env python3
"""Update local Scout memory records with forward performance outcomes."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date, datetime, timezone
from typing import Any, Optional

from memory_store import (
    connect,
    init_db,
    json_load,
    log_memory_load,
    log_outcome_update_audit,
    log_timing,
    refresh_gate_intelligence_metrics,
    refresh_feature_vector_labels,
    refresh_gate_alpha_metrics,
)
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
    "outcome_horizon_debug_json",
)


def parse_date(value: Any) -> Optional[date]:
    """Parse an ISO date or datetime into a calendar date (UTC for aware values)."""
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).date()
        return parsed.date()
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


def first_price_entry_on_or_after(
    prices: dict[date, float],
    target: date,
) -> tuple[Optional[date], Optional[float]]:
    for available in sorted(prices):
        if available >= target:
            return available, prices[available]
    return None, None


def trading_dates_through(prices: dict[date, float], as_of: date) -> list[date]:
    return [row_date for row_date in sorted(prices) if row_date <= as_of]


def trading_day_price_after(
    prices: dict[date, float],
    entry_trade_date: date,
    trading_days_after_entry: int,
    as_of: Optional[date] = None,
) -> tuple[Optional[date], Optional[float], dict[str, Any]]:
    """Price at N trading sessions after the entry close (not calendar days)."""
    as_of = as_of or date.today()
    available_dates = trading_dates_through(prices, as_of)
    meta: dict[str, Any] = {
        "trading_day_offset": trading_days_after_entry,
        "entry_trade_date": entry_trade_date.isoformat(),
        "as_of": as_of.isoformat(),
        "available_sessions": len(available_dates),
    }
    try:
        entry_index = available_dates.index(entry_trade_date)
    except ValueError:
        meta["status"] = "pending"
        meta["reason"] = "Entry trading date was not present in the FMP close series."
        return None, None, meta

    target_index = entry_index + trading_days_after_entry
    sessions_after_entry = max(0, len(available_dates) - entry_index - 1)
    meta["sessions_after_entry"] = sessions_after_entry
    if target_index >= len(available_dates):
        meta["status"] = "pending"
        meta["reason"] = (
            f"Pending: need {trading_days_after_entry} trading day(s) after entry; "
            f"only {sessions_after_entry} trading session(s) have elapsed so far."
        )
        return None, None, meta

    target_date = available_dates[target_index]
    meta["status"] = "resolved"
    meta["reason"] = "Resolved from FMP EOD close."
    meta["target_trade_date"] = target_date.isoformat()
    return target_date, prices[target_date], meta


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


def stock_label(final_direction: str, returns_by_horizon: dict[int, float]) -> str:
    for horizon in reversed(STOCK_HORIZONS):
        if horizon in returns_by_horizon:
            return stock_label_for_return(final_direction, returns_by_horizon[horizon])
    return "PENDING"


def horizon_return_value(row: Any, horizon: int, updates: dict[str, Any]) -> Optional[float]:
    field = f"return_{horizon}d"
    if updates.get(field) is not None:
        return float(updates[field])
    if row[field] is not None:
        return float(row[field])
    return None


def collect_returns_by_horizon(row: Any, updates: dict[str, Any]) -> dict[int, float]:
    returns: dict[int, float] = {}
    for horizon in STOCK_HORIZONS:
        value = horizon_return_value(row, horizon, updates)
        if value is not None:
            returns[horizon] = value
    return returns


def build_horizon_debug(
    row: Any,
    entry_trade_date: date,
    entry_price: float,
    prices: dict[date, float],
    direction: str,
    updates: dict[str, Any],
    as_of: date,
) -> dict[str, Any]:
    debug: dict[str, Any] = {}
    for horizon in STOCK_HORIZONS:
        key = f"{horizon}D"
        price_field = f"price_after_{horizon}d"
        return_field = f"return_{horizon}d"
        label_field = f"stock_outcome_label_{horizon}d"
        existing_return = row[return_field]
        if existing_return is not None:
            debug[key] = {
                "status": "resolved",
                "reason": "Stored from a previous outcome refresh.",
                "return_pct": float(existing_return),
                "price": row[price_field],
                "label": row[label_field],
                "target_trade_date": None,
            }
            continue

        target_trade_date, target_price, meta = trading_day_price_after(
            prices,
            entry_trade_date,
            horizon,
            as_of=as_of,
        )
        if target_price is None:
            debug[key] = {
                "status": "pending",
                "reason": meta.get("reason"),
                "sessions_after_entry": meta.get("sessions_after_entry"),
                "trading_day_offset": horizon,
            }
            continue

        result_return = (target_price - entry_price) / entry_price * 100
        debug[key] = {
            "status": "resolved",
            "reason": meta.get("reason"),
            "return_pct": round(result_return, 4),
            "price": target_price,
            "label": stock_label_for_return(direction, result_return),
            "target_trade_date": target_trade_date.isoformat() if target_trade_date else None,
            "sessions_after_entry": meta.get("sessions_after_entry"),
            "trading_day_offset": horizon,
        }
    return debug


def apply_horizon_updates(
    row: Any,
    entry_trade_date: date,
    entry_price: float,
    prices: dict[date, float],
    direction: str,
    as_of: date,
) -> tuple[dict[str, Any], list[str], int]:
    updates: dict[str, Any] = {"entry_price": entry_price}
    notes: list[str] = []
    filled = 0

    for horizon in STOCK_HORIZONS:
        price_field = f"price_after_{horizon}d"
        return_field = f"return_{horizon}d"
        label_field = f"stock_outcome_label_{horizon}d"
        if row[return_field] is not None:
            continue

        target_trade_date, target_price, meta = trading_day_price_after(
            prices,
            entry_trade_date,
            horizon,
            as_of=as_of,
        )
        if target_price is None:
            reason = meta.get("reason") or f"{horizon}D pending."
            notes.append(reason)
            continue

        result_return = (target_price - entry_price) / entry_price * 100
        updates[price_field] = target_price
        updates[return_field] = round(result_return, 4)
        updates[label_field] = stock_label_for_return(direction, result_return)
        filled += 1
        print(
            f"[outcomes] resolved {horizon}D id={row['id']} ticker={row['ticker']} "
            f"date={target_trade_date} return={result_return:.4f}%",
            flush=True,
        )

    return updates, notes, filled


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
    refresh_timings: dict[str, float] = {}
    refresh_started = time.perf_counter()
    with log_timing(refresh_timings, "init_db_ms"):
        init_db()
    today = date.today()
    checked = 0
    updated = 0
    pending = 0
    outcomes_updated = 0
    still_pending_not_old_enough = 0
    missing_price_data = 0
    label_refreshed = 0
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
               OR stock_outcome_label IS NULL
               OR stock_outcome_label = 'PENDING'
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
                "entry_trade_date": None,
                "fmp_response_status": None,
                "prices_found": {},
                "horizons": {},
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
                missing_price_data += 1
                detail["still_pending_reason"] = "Recommendation timestamp was not available."
                execute_audited_update(
                    conn,
                    row,
                    {
                        "stock_outcome_label": row["stock_outcome_label"] or "PENDING",
                        "result_notes": row["result_notes"] or detail["still_pending_reason"],
                        "outcome_last_updated_at": row["outcome_last_updated_at"] or checked_at,
                        "outcome_horizon_debug_json": json.dumps(
                            {f"{horizon}D": {"status": "pending", "reason": detail["still_pending_reason"]}
                             for horizon in STOCK_HORIZONS}
                        ),
                    },
                    checked_at,
                    "not_requested_missing_recommendation_timestamp",
                )
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
                missing_price_data += 1
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
                        "result_notes": detail["still_pending_reason"],
                        "outcome_last_updated_at": checked_at,
                        "outcome_horizon_debug_json": json.dumps(
                            {f"{horizon}D": {"status": "pending", "reason": detail["still_pending_reason"]}
                             for horizon in STOCK_HORIZONS}
                        ),
                    },
                    checked_at,
                    FMP_HISTORICAL_URL,
                )
                continue

            entry_trade_date, entry = first_price_entry_on_or_after(prices, entry_date)
            if entry_trade_date is None or entry is None:
                pending += 1
                missing_price_data += 1
                detail["still_pending_reason"] = "No FMP trading price was found on or after the recommendation date."
                execute_audited_update(
                    conn,
                    row,
                    {
                        "stock_outcome_label": row["stock_outcome_label"] or "PENDING",
                        "result_notes": detail["still_pending_reason"],
                        "outcome_last_updated_at": checked_at,
                        "outcome_horizon_debug_json": json.dumps(
                            {f"{horizon}D": {"status": "pending", "reason": detail["still_pending_reason"]}
                             for horizon in STOCK_HORIZONS}
                        ),
                    },
                    checked_at,
                    FMP_HISTORICAL_URL,
                )
                continue

            detail["entry_trade_date"] = entry_trade_date.isoformat()
            detail["entry_price"] = entry
            direction = row["final_direction"] or ""

            updates, notes, filled = apply_horizon_updates(
                row,
                entry_trade_date,
                entry,
                prices,
                direction,
                today,
            )
            outcomes_updated += filled

            horizon_debug = build_horizon_debug(
                row,
                entry_trade_date,
                entry,
                prices,
                direction,
                updates,
                today,
            )
            detail["horizons"] = horizon_debug
            for horizon in STOCK_HORIZONS:
                key = f"{horizon}D"
                info = horizon_debug.get(key, {})
                if info.get("status") == "resolved":
                    detail["prices_found"][key] = {
                        "trading_date": info.get("target_trade_date"),
                        "price": info.get("price"),
                        "return": info.get("return_pct"),
                        "label": info.get("label"),
                    }

            returns_by_horizon = collect_returns_by_horizon(row, updates)
            label = stock_label(direction, returns_by_horizon)
            if label != (row["stock_outcome_label"] or "PENDING"):
                label_refreshed += 1
            if row["stock_outcome_label"] in (None, "PENDING") or label != "PENDING":
                updates["stock_outcome_label"] = label

            directional_moves = [
                (-move if direction == "Bearish" else move) for move in returns_by_horizon.values()
            ]
            if directional_moves:
                updates["max_favorable_move"] = round(max(directional_moves), 4)
                updates["max_adverse_move"] = round(min(directional_moves), 4)

            opt_entry = option_entry_price(row)
            if opt_entry is not None:
                updates["option_entry_price"] = opt_entry
            if row["option_outcome_label"] is None:
                updates["option_outcome_label"] = "PENDING"
                notes.append("Option historical pricing is not available from the current sandbox FMP setup.")

            pending_horizons = [
                key for key, info in horizon_debug.items() if info.get("status") == "pending"
            ]
            if notes:
                updates["result_notes"] = "; ".join(dict.fromkeys(notes))
            elif pending_horizons:
                updates["result_notes"] = (
                    f"Outcome refresh completed. Still pending horizons: {', '.join(pending_horizons)}."
                )
            else:
                updates["result_notes"] = "Outcome update completed with available FMP stock prices."

            updates["outcome_horizon_debug_json"] = json.dumps(horizon_debug)
            updates["outcome_last_updated_at"] = checked_at

            if updates:
                execute_audited_update(conn, row, updates, checked_at, FMP_HISTORICAL_URL)
                updated += 1
                detail["rows_updated"] = 1

            if label == "PENDING" or pending_horizons:
                pending += 1
                detail["still_pending_reason"] = "; ".join(
                    info.get("reason", "")
                    for info in horizon_debug.values()
                    if info.get("status") == "pending" and info.get("reason")
                ) or "No forward prices were available."
                if any("trading session" in (info.get("reason") or "") for info in horizon_debug.values()):
                    still_pending_not_old_enough += 1

            print(
                f"[outcomes] row={row['id']} ticker={ticker} label={label} "
                f"resolved={[k for k, v in horizon_debug.items() if v.get('status') == 'resolved']} "
                f"pending={[k for k, v in horizon_debug.items() if v.get('status') == 'pending']}",
                flush=True,
            )

        with log_timing(refresh_timings, "gate_intelligence_ms"):
            gate_intelligence = refresh_gate_intelligence_metrics(conn)
        with log_timing(refresh_timings, "feature_vector_labels_ms"):
            feature_vector_labels_updated = refresh_feature_vector_labels(conn)
        with log_timing(refresh_timings, "gate_alpha_ms"):
            gate_alpha = refresh_gate_alpha_metrics(conn)

    refresh_timings["total_ms"] = round((time.perf_counter() - refresh_started) * 1000, 2)
    log_memory_load(refresh_timings, "outcome_refresh")

    return {
        "ok": True,
        "records_checked": checked,
        "records_updated": updated,
        "records_pending": pending,
        "records_still_pending": pending,
        "outcomes_updated": outcomes_updated,
        "still_pending_not_old_enough": still_pending_not_old_enough,
        "missing_price_data": missing_price_data,
        "label_refreshed": label_refreshed,
        "errors": errors,
        "details": details,
        "gate_intelligence_updated": len(gate_intelligence),
        "feature_vector_labels_updated": feature_vector_labels_updated,
        "gate_alpha_metrics_updated": gate_alpha["metric_rows"],
        "timings": refresh_timings,
    }
