#!/usr/bin/env python3
"""Scout-Deploy outbound email policy and macro morning report helpers."""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Iterable, Optional


MACRO_NEWS_REPORT_NAME = "Scout Morning Macro Brief"
MACRO_NEWS_SEND_DAYS = ("Tuesday", "Wednesday", "Thursday")
MACRO_NEWS_SEND_TIME = "8:30 AM America/Chicago"
MACRO_NEWS_REPORT_KIND = "macro_morning_news"

BLOCKED_EMAIL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("MU ticker", re.compile(r"(?<![A-Z0-9])\$?MU(?![A-Z0-9])", re.I)),
    ("Micron reference", re.compile(r"\bMicron(?:\s+Technology)?\b", re.I)),
    ("trading record", re.compile(r"\b(?:our\s+)?trading\s+record\b", re.I)),
    ("track record", re.compile(r"\b(?:our\s+)?track\s+record\b", re.I)),
    ("past trades", re.compile(r"\b(?:our\s+)?past\s+trades?\b", re.I)),
    ("previous trades", re.compile(r"\b(?:our\s+)?previous\s+trades?\b", re.I)),
    ("trade history", re.compile(r"\b(?:our\s+)?trade\s+history\b", re.I)),
    ("win rate", re.compile(r"\bwin\s+rate\b", re.I)),
    ("prior winners or losers", re.compile(r"\b(?:our\s+)?(?:winners|losers)\b", re.I)),
)


@dataclass(frozen=True)
class MacroNewsItem:
    headline: str
    summary: str
    market_relevance: str
    source: Optional[str] = None

    @classmethod
    def from_mapping(cls, payload: dict[str, Any]) -> "MacroNewsItem":
        return cls(
            headline=str(payload.get("headline") or payload.get("title") or "").strip(),
            summary=str(payload.get("summary") or payload.get("body") or "").strip(),
            market_relevance=str(
                payload.get("marketRelevance")
                or payload.get("market_relevance")
                or payload.get("marketImpact")
                or payload.get("market_impact")
                or ""
            ).strip(),
            source=str(payload.get("source") or "").strip() or None,
        )


def python_weekday_for_report(day: str | date | datetime) -> int:
    if isinstance(day, datetime):
        return day.weekday()
    if isinstance(day, date):
        return day.weekday()
    normalized = str(day).strip().lower()
    names = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")
    if normalized not in names:
        raise ValueError(f"Unknown weekday: {day}")
    return names.index(normalized)


def is_macro_news_send_day(day: str | date | datetime) -> bool:
    return python_weekday_for_report(day) in {1, 2, 3}


def normalize_news_items(items: Iterable[MacroNewsItem | dict[str, Any]]) -> list[MacroNewsItem]:
    normalized: list[MacroNewsItem] = []
    for item in items:
        news_item = item if isinstance(item, MacroNewsItem) else MacroNewsItem.from_mapping(item)
        if not news_item.headline:
            raise ValueError("Macro news items must include a headline.")
        if not news_item.summary:
            raise ValueError(f"Macro news item '{news_item.headline}' must include a summary.")
        if not news_item.market_relevance:
            raise ValueError(
                f"Macro news item '{news_item.headline}' must explain market relevance."
            )
        normalized.append(news_item)
    if not normalized:
        raise ValueError("At least one macro news item is required.")
    return normalized


def build_macro_news_report_prompt() -> str:
    return "\n".join(
        [
            f"Create the {MACRO_NEWS_REPORT_NAME}, sent {', '.join(MACRO_NEWS_SEND_DAYS)} mornings at {MACRO_NEWS_SEND_TIME}.",
            "Purpose: give readers a quick, unbiased read on major macro/world news and how it may affect markets.",
            "Tone: neutral, concise, evidence-based, non-partisan, and clear about uncertainty.",
            "Required sections: Macro snapshot, Market lens, What to watch next.",
            "Include: global macro, policy, rates, inflation, labor, geopolitics, energy, credit, FX, and broad equity-market context when relevant.",
            "Do not include trade recommendations, position sizing, options instructions, or urgent hype.",
            "Do not mention MU or Micron in outbound Scout-Deploy emails.",
            "Do not mention our trading record, track record, win rate, previous trades, or past trades.",
        ]
    )


def _format_item(index: int, item: MacroNewsItem) -> list[str]:
    source = f" Source: {item.source}." if item.source else ""
    return [
        f"{index}. {item.headline}",
        f"   What happened: {item.summary}{source}",
        f"   Market lens: {item.market_relevance}",
    ]


def build_macro_news_email(
    items: Iterable[MacroNewsItem | dict[str, Any]],
    *,
    as_of: Optional[date | datetime | str] = None,
) -> dict[str, str]:
    normalized_items = normalize_news_items(items)
    if isinstance(as_of, str):
        as_of_label = as_of
    elif isinstance(as_of, datetime):
        as_of_label = as_of.strftime("%A, %b %d, %Y").replace(" 0", " ")
    elif isinstance(as_of, date):
        as_of_label = as_of.strftime("%A, %b %d, %Y").replace(" 0", " ")
    else:
        as_of_label = datetime.utcnow().strftime("%A, %b %d, %Y").replace(" 0", " ")

    subject = f"{MACRO_NEWS_REPORT_NAME} - {as_of_label}"
    lines = [
        MACRO_NEWS_REPORT_NAME,
        as_of_label,
        "",
        "A quick, neutral read on macro news and possible market implications.",
        "This is market context, not a trade recommendation.",
        "",
        "Macro snapshot",
    ]
    for index, item in enumerate(normalized_items, start=1):
        lines.extend(_format_item(index, item))
        lines.append("")
    lines.extend(
        [
            "What to watch next",
            "- Rates, inflation expectations, credit spreads, energy prices, FX, and index breadth.",
            "- Whether news changes broad risk appetite or only affects a narrow sector.",
            "- Confirmation from market pricing instead of assuming a single headline sets the trend.",
        ]
    )
    body = "\n".join(lines).strip()
    validation = validate_outbound_email_content(subject=subject, body=body)
    if not validation["ok"]:
        raise ValueError("; ".join(validation["violations"]))
    return {"subject": subject, "body": body}


def validate_outbound_email_content(*, subject: str = "", body: str = "") -> dict[str, Any]:
    text = f"{subject}\n{body}"
    violations = [
        {"name": name, "match": match.group(0)}
        for name, pattern in BLOCKED_EMAIL_PATTERNS
        if (match := pattern.search(text))
    ]
    return {"ok": not violations, "violations": violations}


def assert_outbound_email_allowed(*, subject: str = "", body: str = "") -> None:
    result = validate_outbound_email_content(subject=subject, body=body)
    if not result["ok"]:
        labels = ", ".join(row["name"] for row in result["violations"])
        raise ValueError(f"Outbound Scout-Deploy email violates policy: {labels}")


def email_contract() -> dict[str, Any]:
    return {
        "reportKind": MACRO_NEWS_REPORT_KIND,
        "name": MACRO_NEWS_REPORT_NAME,
        "sendDays": list(MACRO_NEWS_SEND_DAYS),
        "sendTime": MACRO_NEWS_SEND_TIME,
        "prompt": build_macro_news_report_prompt(),
        "blockedContent": [name for name, _ in BLOCKED_EMAIL_PATTERNS],
        "requiredItemFields": ["headline", "summary", "marketRelevance"],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Scout-Deploy email policy helpers.")
    parser.add_argument(
        "--contract",
        action="store_true",
        help="Print the macro-news email contract as JSON.",
    )
    parser.add_argument(
        "--validate",
        help="Validate a text file as outbound email body content.",
    )
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.contract:
        print(json.dumps(email_contract(), indent=2, sort_keys=True))
        return 0
    if args.validate:
        from pathlib import Path

        body = Path(args.validate).read_text(encoding="utf-8")
        result = validate_outbound_email_content(body=body)
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0 if result["ok"] else 1
    print(build_macro_news_report_prompt())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
