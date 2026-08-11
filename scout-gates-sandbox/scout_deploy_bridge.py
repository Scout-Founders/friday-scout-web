#!/usr/bin/env python3
"""Bridge Scout-Deploy output emails into Sandbox Research Memory tracking."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Iterable, Optional

from memory_store import save_scan_result_once


OUTPUT_EMAIL_SOURCE = "scout_deploy_output_email"
OUTPUT_EMAIL_API_URL = "scout-deploy://output-email"
OUTPUT_EMAIL_UNIVERSE_MODE = "scout_deploy_output_email"
OUTPUT_EMAIL_PICK_MODE = "output_email"
SUPPORTED_FILE_SUFFIXES = {".json", ".txt", ".eml", ".md", ".html", ".htm"}

EXPLICIT_TICKER_PATTERNS = (
    re.compile(r"\b(?:ticker|symbol|stock)\s*[:=#-]\s*\$?([A-Z][A-Z0-9.-]{0,9})\b", re.I),
    re.compile(r"\b(?:recommendation|pick|alert)\s*[:=#-]\s*\$?([A-Z][A-Z0-9.-]{0,9})\b", re.I),
)
CASE_TICKER_PATTERN = re.compile(r"\$([A-Z][A-Z0-9.-]{0,9})\b")
DATE_HEADER_PATTERN = re.compile(r"^date\s*:\s*(.+)$", re.I | re.M)
SCORE_PATTERN = re.compile(r"\b(?:scout\s*)?score\s*[:=#-]\s*(-?\d+(?:\.\d+)?)\b", re.I)
PRICE_PATTERN = re.compile(r"\b(?:entry\s*)?price\s*[:=#-]\s*\$?(\d+(?:\.\d+)?)\b", re.I)
EMAIL_TEXT_KEYS = (
    "subject",
    "body",
    "text",
    "html",
    "email",
    "outputEmail",
    "output_email",
    "message",
    "content",
)
TIMESTAMP_KEYS = (
    "generatedAt",
    "generated_at",
    "createdAt",
    "created_at",
    "sentAt",
    "sent_at",
    "timestamp",
    "runTimestamp",
    "date",
)
TICKER_KEYS = ("ticker", "symbol", "stock", "underlying", "underlyingSymbol")
DIRECTION_KEYS = ("direction", "bias", "side", "recommendationDirection", "finalDirection")
SCORE_KEYS = ("score", "scoutScore", "scout_score", "finalScore")
PRICE_KEYS = ("price", "entryPrice", "entry_price", "lastPrice", "last_price")


def _nested_values(payload: Any, keys: Iterable[str]) -> Iterable[Any]:
    if not isinstance(payload, dict):
        return []
    values: list[Any] = []
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            values.append(value)
    for nested_key in ("email", "outputEmail", "output_email", "payload", "data"):
        nested = payload.get(nested_key)
        if isinstance(nested, dict):
            values.extend(_nested_values(nested, keys))
    return values


def _text_from_payload(payload: Any) -> str:
    if isinstance(payload, str):
        return payload
    if not isinstance(payload, dict):
        return str(payload or "")
    parts: list[str] = []
    for value in _nested_values(payload, EMAIL_TEXT_KEYS):
        if isinstance(value, dict):
            parts.append(_text_from_payload(value))
        elif isinstance(value, list):
            parts.extend(str(item) for item in value if item not in (None, ""))
        else:
            parts.append(str(value))
    if not parts:
        parts.append(json.dumps(payload, sort_keys=True))
    return "\n".join(part for part in parts if part)


def _clean_ticker(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip().upper().lstrip("$")
    text = re.sub(r"[^A-Z0-9.-]", "", text)
    if not re.fullmatch(r"[A-Z][A-Z0-9.-]{0,9}", text):
        return None
    return text


def extract_ticker(payload: Any) -> Optional[str]:
    for value in _nested_values(payload, TICKER_KEYS):
        ticker = _clean_ticker(value)
        if ticker:
            return ticker

    text = _text_from_payload(payload)
    for pattern in EXPLICIT_TICKER_PATTERNS:
        match = pattern.search(text)
        if match:
            ticker = _clean_ticker(match.group(1))
            if ticker:
                return ticker

    match = CASE_TICKER_PATTERN.search(text)
    if match:
        return _clean_ticker(match.group(1))
    return None


def normalize_direction(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip().lower()
    if text in {"bull", "bullish", "long", "buy", "call", "calls"}:
        return "Bullish"
    if text in {"bear", "bearish", "short", "sell", "put", "puts"}:
        return "Bearish"
    if text in {"neutral", "hold", "watch", "watchlist"}:
        return "Neutral"
    return None


def extract_direction(payload: Any) -> str:
    for value in _nested_values(payload, DIRECTION_KEYS):
        direction = normalize_direction(value)
        if direction:
            return direction

    text = _text_from_payload(payload).lower()
    for value in ("bullish", "bearish", "neutral", "long", "short", "buy", "sell"):
        direction = normalize_direction(value)
        if direction and re.search(rf"\b{re.escape(value)}\b", text):
            return direction
    return "Neutral"


def _to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"-?\d+(?:\.\d+)?", str(value).replace(",", ""))
    return float(match.group(0)) if match else None


def extract_score(payload: Any) -> Optional[float]:
    for value in _nested_values(payload, SCORE_KEYS):
        score = _to_float(value)
        if score is not None:
            return score
    match = SCORE_PATTERN.search(_text_from_payload(payload))
    return float(match.group(1)) if match else None


def extract_price(payload: Any) -> Optional[float]:
    for value in _nested_values(payload, PRICE_KEYS):
        price = _to_float(value)
        if price is not None:
            return price
    match = PRICE_PATTERN.search(_text_from_payload(payload))
    return float(match.group(1)) if match else None


def _normalize_timestamp(value: Any) -> Optional[str]:
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    normalized = text.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(normalized).astimezone(timezone.utc).isoformat()
    except ValueError:
        try:
            parsed = parsedate_to_datetime(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed.astimezone(timezone.utc).isoformat()
        except (TypeError, ValueError):
            return text


def extract_timestamp(payload: Any, *, fallback: Optional[str] = None) -> str:
    for value in _nested_values(payload, TIMESTAMP_KEYS):
        timestamp = _normalize_timestamp(value)
        if timestamp:
            return timestamp

    match = DATE_HEADER_PATTERN.search(_text_from_payload(payload))
    if match:
        timestamp = _normalize_timestamp(match.group(1))
        if timestamp:
            return timestamp

    if fallback:
        return fallback
    return datetime.now(timezone.utc).isoformat()


def _direction_breakdown(direction: str) -> dict[str, Any]:
    if direction == "Bullish":
        return {"direction": direction, "bullConviction": 1.0, "bearConviction": 0.0, "netDirectionalEdge": 1.0}
    if direction == "Bearish":
        return {"direction": direction, "bullConviction": 0.0, "bearConviction": 1.0, "netDirectionalEdge": -1.0}
    return {"direction": direction, "bullConviction": 0.0, "bearConviction": 0.0, "netDirectionalEdge": 0.0}


def build_tracking_payload(
    output_email: Any,
    *,
    source_name: Optional[str] = None,
    fallback_timestamp: Optional[str] = None,
) -> dict[str, Any]:
    ticker = extract_ticker(output_email)
    if not ticker:
        raise ValueError("Scout-Deploy output email did not include a recognizable ticker.")

    direction = extract_direction(output_email)
    score = extract_score(output_email)
    price = extract_price(output_email)
    timestamp = extract_timestamp(output_email, fallback=fallback_timestamp)
    text = _text_from_payload(output_email)
    raw_payload = output_email if isinstance(output_email, dict) else {"body": text}
    raw = {
        "source": OUTPUT_EMAIL_SOURCE,
        "source_name": source_name,
        "output_email": raw_payload,
    }

    result: dict[str, Any] = {
        "ticker": ticker,
        "score": score,
        "scout_score": score,
        "direction": direction,
        "directionBreakdown": _direction_breakdown(direction),
        "price": price,
        "gates": [],
        "explanation": {
            "source": OUTPUT_EMAIL_SOURCE,
            "summary": f"Tracked from Scout-Deploy output email for {ticker}.",
            "failed_gates": [],
            "gates": [],
        },
        "raw": raw,
        "source": OUTPUT_EMAIL_SOURCE,
    }
    final_pick = {"ticker": ticker, "score": score, "direction": direction}
    return {
        "ok": True,
        "source": OUTPUT_EMAIL_SOURCE,
        "sourceName": source_name,
        "runTimestamp": timestamp,
        "universeMode": OUTPUT_EMAIL_UNIVERSE_MODE,
        "pickMode": OUTPUT_EMAIL_PICK_MODE,
        "timeout": None,
        "candidates": [ticker],
        "apiUrl": OUTPUT_EMAIL_API_URL,
        "results": [result],
        "finalPick": final_pick,
        "message": f"Scout-Deploy output email queued {ticker} for Sandbox tracking.",
    }


def ingest_output_email_payload(
    output_email: Any,
    *,
    source_name: Optional[str] = None,
    fallback_timestamp: Optional[str] = None,
) -> dict[str, Any]:
    tracking_payload = build_tracking_payload(
        output_email,
        source_name=source_name,
        fallback_timestamp=fallback_timestamp,
    )
    saved = save_scan_result_once(tracking_payload)
    return {
        **saved,
        "source": OUTPUT_EMAIL_SOURCE,
        "ticker": tracking_payload["results"][0]["ticker"],
        "direction": tracking_payload["results"][0]["direction"],
        "runTimestamp": tracking_payload["runTimestamp"],
    }


def _read_output_email_file(path: Path) -> Any:
    text = path.read_text(encoding="utf-8")
    if path.suffix.lower() == ".json":
        return json.loads(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def ingest_output_email_file(path: Path | str) -> dict[str, Any]:
    target = Path(path)
    stat = target.stat()
    fallback_timestamp = datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat()
    return ingest_output_email_payload(
        _read_output_email_file(target),
        source_name=str(target),
        fallback_timestamp=fallback_timestamp,
    )


def iter_output_email_files(path: Path | str) -> Iterable[Path]:
    target = Path(path)
    if target.is_file():
        yield target
        return
    for item in sorted(target.iterdir()):
        if item.is_file() and item.suffix.lower() in SUPPORTED_FILE_SUFFIXES:
            yield item


def ingest_output_email_path(path: Path | str) -> dict[str, Any]:
    results = [ingest_output_email_file(item) for item in iter_output_email_files(path)]
    return {
        "ok": True,
        "source": OUTPUT_EMAIL_SOURCE,
        "filesProcessed": len(results),
        "tracked": sum(1 for result in results if result.get("ok")),
        "results": results,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Import Scout-Deploy output email files into Sandbox Research Memory tracking."
    )
    parser.add_argument("paths", nargs="+", help="Output email file or directory paths to import.")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    payload = {"ok": True, "results": []}
    for path in args.paths:
        payload["results"].append(ingest_output_email_path(path))
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
