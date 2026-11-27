#!/usr/bin/env python3
"""Deterministic Scout v6 emailed-pick parser (no LLM).

Parses Claude editorial sections from `raw_report_text` (the SMTP email body):

  TOP PICK / SECONDARY PICK / WATCH LIST

Section headings may use markdown hashes, bold markers, emoji or other
symbol prefixes, numbering, and varied capitalization. The keyword must be
the first words after that decoration. A sentence that merely mentions
"top pick" or "watch list" is not a section.

Strategy text has surrounding markdown emphasis removed (`Bull Call Spread**`
becomes `Bull Call Spread`). The words themselves are not rewritten.
Direction, strike, expiration, score, and contract fields are taken only
from the editorial section. Missing fields stay missing.

`parser_version` is stored on each pick dict. Ingest persists that dict in
the existing `pick_json` column. There is no separate parser-version column.

Distinguishes editorial email bodies from raw-scan fallback dumps so
deterministic FINAL RANKINGS are never treated as Claude classifications.
"""

from __future__ import annotations

import re
from typing import Any, Optional


PARSE_MODE_EDITORIAL = "editorial"
PARSE_MODE_RAW_SCAN_FALLBACK = "raw_scan_fallback"
PARSE_MODE_UNRECOGNIZED = "unrecognized"

# Bumped when heading, watch-list, or expiration-boundary recognition changes.
# Recorded on each pick and persisted through the existing pick_json field.
PARSER_VERSION = "sent-pick-parser-3"

EMAIL_CLASSIFICATION_TOP = "top"
EMAIL_CLASSIFICATION_SECONDARY = "secondary"
EMAIL_CLASSIFICATION_WATCH = "watch"

# Words that look ticker-like but are not stock symbols in Scout email prose.
_TICKER_BLOCKLIST = frozenset(
    {
        "A",
        "I",
        "AI",
        "ALL",
        "AND",
        "API",
        "ATH",
        "ATM",
        "CEO",
        "CFO",
        "CPI",
        "DNA",
        "EPS",
        "ETF",
        "FOR",
        "GDP",
        "HIGH",
        "II",
        "III",
        "IPO",
        "IT",
        "IV",
        "LOW",
        "MACRO",
        "MAX",
        "NAV",
        "NOT",
        "OTM",
        "PE",
        "PUT",
        "CALL",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "RSI",
        "SEC",
        "THE",
        "TOP",
        "USA",
        "USD",
        "VIP",
        "YOY",
        "WATCH",
        "LIST",
        "BRIEF",
        "SECTOR",
        "TABLE",
        "SCOUT",
        "STRONG",
        "WEAK",
        "NONE",
        "NOTE",
        "DATA",
        "RISK",
    }
)

# Heading decoration is stripped before the keyword is tested, so these
# patterns see the words themselves. A keyword that is not at the start of
# the stripped line does not match.
_SECTION_KEYWORD = re.compile(
    r"^(TOP\s+PICK|SECONDARY\s+PICK|WATCH\s+LIST)\b[ \t]*(?:[—\-:]|\*\*)?[ \t]*(.*)$",
    re.IGNORECASE,
)
_SECTION_CLASS = {
    "TOP PICK": EMAIL_CLASSIFICATION_TOP,
    "SECONDARY PICK": EMAIL_CLASSIFICATION_SECONDARY,
    "WATCH LIST": EMAIL_CLASSIFICATION_WATCH,
}
_OTHER_SECTION_KEYWORD = re.compile(
    r"^(?:MACRO\s+BRIEF|SECTOR\s+TABLE|ACTIVE\s+TRADE|DISCLAIMER|FINAL\s+RANKINGS|"
    r"CALL\s+CANDIDATES|PUT\s+CANDIDATES|VERDICT)\b",
    re.IGNORECASE,
)
_HEADING_DECORATIONS = (
    re.compile(r"^#{1,6}[ \t]*"),
    re.compile(r"^\*\*[ \t]*"),
    re.compile(r"^\d+[.)][ \t]+"),
    # Emoji and other symbols. Asterisks and hashes are handled above so a
    # bullet or bold marker is not eaten as decoration.
    re.compile(r"^[^\w\s#*]+[ \t]*"),
)

_RAW_SCAN_MARKERS = (
    re.compile(r"\bFINAL\s+RANKINGS\b", re.IGNORECASE),
    re.compile(r"\bCALL\s+CANDIDATES\b", re.IGNORECASE),
    re.compile(r"\bPUT\s+CANDIDATES\b", re.IGNORECASE),
    re.compile(r"\bpassed all (?:14 )?gates\b", re.IGNORECASE),
    re.compile(r"\bNo stocks passed all 14 gates\b", re.IGNORECASE),
)

_EDITORIAL_MARKERS = (
    re.compile(r"\bMACRO\s+BRIEF\b", re.IGNORECASE),
    re.compile(r"\bSECTOR\s+TABLE\b", re.IGNORECASE),
    re.compile(r"\bTOP\s+PICK\b", re.IGNORECASE),
    re.compile(r"\bSECONDARY\s+PICK\b", re.IGNORECASE),
    re.compile(r"\bWATCH\s+LIST\b", re.IGNORECASE),
    re.compile(r"educational purposes only", re.IGNORECASE),
    re.compile(r"Active trade updates available", re.IGNORECASE),
)

_TICKER_TOKEN = re.compile(r"\b([A-Z]{1,5})\b")
_TICKER_IN_PARENS = re.compile(r"\(\s*([A-Z]{1,5})\s*\)")
_BOLD_TICKER = re.compile(r"\*\*\s*([A-Z]{1,5})\s*\*\*")

_DIRECTION_PAREN = re.compile(r"\(\s*(CALL|PUT)S?\s*\)", re.IGNORECASE)
_DIRECTION_LABELED = re.compile(
    r"\b(?:direction|side|bias)\s*[:=\-]\s*(CALL|PUT|bullish|bearish)\b",
    re.IGNORECASE,
)
_DIRECTION_NEAR_TICKER = re.compile(
    r"\b([A-Z]{1,5})\s*[—(,\-:]+\s*(CALL|PUT|bullish|bearish)\b",
    re.IGNORECASE,
)
_DIRECTION_BULL_BEAR = re.compile(r"\b(bullish|bearish)\b", re.IGNORECASE)

_STRIKE = re.compile(
    r"(?:strike(?:s)?\s*(?:at|:)?\s*|@\s*)\$?\s*(\d{2,5}(?:\.\d{1,2})?)"
    r"|\b\$(\d{2,5}(?:\.\d{1,2})?)\s*(?:strike|/)\b",
    re.IGNORECASE,
)
# Expiration values stay on the labeled line. Horizontal whitespace only, so a
# following bullet or the next labeled field cannot become part of the date.
_EXPIRATION = re.compile(
    r"(?:exp(?:iration|iry)?(?:\s*rec)?\s*(?:date)?\s*[:=\-]\s*)"
    r"([A-Za-z0-9][A-Za-z0-9/\-]{0,20}(?:[ \t]+[A-Za-z0-9/\-]{1,20}){0,4})"
    r"|(?:\b(?:expiring|expires)[ \t]+(?:on[ \t]+)?)"
    r"([A-Za-z0-9][A-Za-z0-9/\-]{0,20}(?:[ \t]+[A-Za-z0-9/\-]{1,20}){0,4})",
    re.IGNORECASE,
)
_TABLE_SEPARATOR_CELL = re.compile(r":?-{3,}:?")
_WATCH_BULLET = re.compile(r"^(?:•|\*(?!\*)|-(?!\s*-)|\d+[.)])\s*(.*)$")
_STRATEGY = re.compile(
    r"(?:strategy\s*[:=\-]\s*)([^\n]{3,80})"
    r"|((?:bull|bear)\s+(?:call|put)\s+spread)"
    r"|(cash[-\s]?secured\s+puts?)"
    r"|(credit\s+spreads?)"
    r"|(iron\s+condors?)"
    r"|(long\s+(?:call|put)s?)"
    r"|(LEAPS?)",
    re.IGNORECASE,
)

def _normalize_whitespace(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _is_plausible_ticker(token: str) -> bool:
    ticker = (token or "").strip().upper()
    if not ticker or len(ticker) > 5:
        return False
    if not ticker.isalpha():
        return False
    if ticker in _TICKER_BLOCKLIST:
        return False
    return True


def detect_parse_mode(raw_report_text: Optional[str]) -> str:
    """Classify email body as editorial, raw-scan fallback, or unrecognized."""
    text = _normalize_whitespace(str(raw_report_text or "")).strip()
    if not text:
        return PARSE_MODE_UNRECOGNIZED

    raw_hits = sum(1 for pattern in _RAW_SCAN_MARKERS if pattern.search(text))
    editorial_hits = sum(1 for pattern in _EDITORIAL_MARKERS if pattern.search(text))

    has_final_rankings = bool(_RAW_SCAN_MARKERS[0].search(text))
    has_candidates = bool(_RAW_SCAN_MARKERS[1].search(text) or _RAW_SCAN_MARKERS[2].search(text))
    has_macro = bool(_EDITORIAL_MARKERS[0].search(text))
    has_top = bool(_EDITORIAL_MARKERS[2].search(text))
    has_secondary = bool(_EDITORIAL_MARKERS[3].search(text))
    has_watch = bool(_EDITORIAL_MARKERS[4].search(text))

    # Raw scan dumps include FINAL RANKINGS + candidate sections and lack MACRO BRIEF.
    if has_final_rankings and has_candidates and not has_macro:
        return PARSE_MODE_RAW_SCAN_FALLBACK
    if raw_hits >= 2 and editorial_hits <= 1 and not has_macro:
        return PARSE_MODE_RAW_SCAN_FALLBACK

    if has_macro or (has_top and (has_secondary or has_watch)):
        return PARSE_MODE_EDITORIAL
    if has_top or has_secondary or has_watch:
        return PARSE_MODE_EDITORIAL
    if editorial_hits >= 2:
        return PARSE_MODE_EDITORIAL

    return PARSE_MODE_UNRECOGNIZED


def _strip_heading_decoration(line: str) -> str:
    """Remove heading markers, bold markers, numbering, and symbol prefixes."""
    text = line.strip()
    while text:
        for pattern in _HEADING_DECORATIONS:
            match = pattern.match(text)
            if match and match.end() > 0:
                text = text[match.end() :].strip()
                break
        else:
            break
    return text.strip()


def _remainder_starts_with_ticker(remainder: str) -> bool:
    """True when the heading tail opens with a ticker, not a later word."""
    line = remainder.strip()
    match = re.match(r"^(?:\*\*)?([A-Za-z]{1,5})(?:\*\*)?(?=\b|[\s(\-—:])", line)
    if not match:
        return False
    return _is_plausible_ticker(match.group(1).upper())


def _remainder_is_narrative(remainder: str) -> bool:
    """Reject a long clause that names the section without being a heading."""
    text = remainder.strip()
    if not text or _remainder_starts_with_ticker(text):
        return False
    return len(text.split()) > 8


def _match_section_header(line: str) -> Optional[tuple[str, str]]:
    stripped = _strip_heading_decoration(line)
    match = _SECTION_KEYWORD.match(stripped)
    if not match:
        return None
    keyword = re.sub(r"\s+", " ", match.group(1)).upper()
    classification = _SECTION_CLASS.get(keyword)
    if classification is None:
        return None
    remainder = (match.group(2) or "").strip().strip("*").strip()
    if _remainder_is_narrative(remainder):
        return None
    return classification, remainder


def _is_other_section_header(line: str) -> bool:
    if _match_section_header(line):
        return False
    return bool(_OTHER_SECTION_KEYWORD.match(_strip_heading_decoration(line)))


def _split_editorial_sections(text: str) -> list[tuple[str, str, str]]:
    """Return (classification, header_remainder, body) for pick sections only."""
    lines = _normalize_whitespace(text).split("\n")
    sections: list[tuple[str, str, list[str]]] = []
    current: Optional[tuple[str, str, list[str]]] = None

    for line in lines:
        header = _match_section_header(line)
        if header:
            if current is not None:
                sections.append(current)
            classification, remainder = header
            current = (classification, remainder, [])
            continue
        if current is not None and _is_other_section_header(line):
            sections.append(current)
            current = None
            continue
        if current is not None:
            current[2].append(line)

    if current is not None:
        sections.append(current)

    return [
        (classification, remainder, "\n".join(body_lines).strip())
        for classification, remainder, body_lines in sections
    ]


def _normalize_direction_token(token: str) -> Optional[str]:
    value = (token or "").strip().lower()
    if value in {"call", "calls"}:
        return "CALL"
    if value in {"put", "puts"}:
        return "PUT"
    if value in {"bullish", "bearish"}:
        return value
    return None


def _extract_direction(text: str) -> Optional[str]:
    paren = _DIRECTION_PAREN.search(text)
    if paren:
        return _normalize_direction_token(paren.group(1))
    labeled = _DIRECTION_LABELED.search(text)
    if labeled:
        return _normalize_direction_token(labeled.group(1))
    near = _DIRECTION_NEAR_TICKER.search(text)
    if near:
        return _normalize_direction_token(near.group(2))
    # Avoid treating "cash-secured put" / "put spread" as direction.
    bull_bear = _DIRECTION_BULL_BEAR.search(text)
    if bull_bear:
        return _normalize_direction_token(bull_bear.group(1))
    return None


def _extract_strike(text: str) -> Optional[float]:
    match = _STRIKE.search(text)
    if not match:
        return None
    raw = match.group(1) or match.group(2)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _looks_like_expiration_value(value: str) -> bool:
    text = value.strip()
    if not text or len(text) < 2:
        return False
    lowered = text.lower()
    banned = {
        "listed",
        "listed here",
        "here",
        "missing",
        "none",
        "n/a",
        "na",
        "unknown",
    }
    if lowered in banned:
        return False
    if re.search(r"\b(week|weeks|month|months|day|days|leap|leaps)\b", lowered):
        return True
    if re.search(r"\b(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\b", lowered):
        return True
    if re.search(r"\d{4}", text) or re.search(r"\d{1,2}/\d{1,2}", text):
        return True
    if re.fullmatch(r"[A-Za-z0-9/\- ]{2,40}", text) and any(ch.isdigit() for ch in text):
        return True
    return False


def _extract_expiration(text: str) -> Optional[str]:
    match = _EXPIRATION.search(text)
    if not match:
        return None
    raw = (match.group(1) or match.group(2) or "")
    # A value never crosses into the next line or a following label.
    raw = raw.splitlines()[0]
    raw = re.split(r"[ \t]+-[ \t]+(?=[A-Za-z])", raw, maxsplit=1)[0]
    raw = re.sub(r"\s+", " ", raw).strip(" .;,:")
    if not _looks_like_expiration_value(raw):
        return None
    if len(raw) > 48:
        raw = raw[:48].rstrip()
    return raw or None


def _is_markdown_separator_row(line: str) -> bool:
    text = line.strip()
    if not text.startswith("|"):
        return False
    cells = [cell.strip() for cell in text.strip("|").split("|")]
    return bool(cells) and all(_TABLE_SEPARATOR_CELL.fullmatch(cell) for cell in cells)


def _markdown_first_cell(line: str) -> Optional[str]:
    text = line.strip()
    if not text.startswith("|"):
        return None
    cells = [cell.strip() for cell in text.strip("|").split("|")]
    if not cells:
        return None
    return cells[0]


def _ticker_from_structural_label(text: str) -> Optional[str]:
    """Accept a ticker only when the line opens as a label, not as prose.

    A label is a bold symbol, a bare uppercase symbol followed by a delimiter,
    or a single-cell symbol. A title-case company name, a sentence that happens
    to begin with a short word, and a statistic such as ``FCF +920%`` are not
    labels. The symbol is not checked against a market-data listing.
    """
    raw = (text or "").strip()
    if not raw or raw[0].isdigit():
        return None
    match = re.match(r"^(?:\*\*\s*)?([A-Z]{1,5})\b(.*)$", raw)
    if not match or not _is_plausible_ticker(match.group(1)):
        return None
    ticker = match.group(1)
    rest = match.group(2)
    if raw.startswith("**"):
        if re.match(r"^(?:\s*\*\*|\s*[—\-:|(]|\s+\$)", rest):
            return ticker
        return None
    if rest == "" or re.match(r"^\s*(?:[—:|(]|$)", rest):
        return ticker
    # "SYM - note" is a label. "SYM -2.4%" is a statistic.
    if re.match(r"^\s+-\s+\D", rest):
        return ticker
    return None


def _watch_bullet_payload(line: str) -> Optional[str]:
    """Return the payload of one bullet. A bold label is not a bullet."""
    text = line.strip()
    if not text or text.startswith("**"):
        return None
    match = _WATCH_BULLET.match(text)
    if not match:
        return None
    payload = match.group(1).strip()
    return payload or None


def _watch_line_ticker(line: str, following_line: str) -> Optional[str]:
    """Ticker for one watch line, or None when the line is not an entry."""
    stripped = line.strip()
    if not stripped or _is_markdown_separator_row(stripped):
        return None
    if stripped.startswith("|"):
        if _is_markdown_separator_row(following_line):
            return None
        return _ticker_from_structural_label(_markdown_first_cell(stripped) or "")
    payload = _watch_bullet_payload(stripped)
    if payload is not None:
        return _ticker_from_structural_label(payload)
    return _ticker_from_structural_label(stripped)


def _normalize_strategy_text(value: str) -> Optional[str]:
    """Remove markdown emphasis wrapped around an otherwise unchanged phrase."""
    text = str(value).replace("**", "")
    text = text.strip().strip("*_`").strip()
    text = re.sub(r"\s+", " ", text).strip(" .;,:")
    if not text:
        return None
    return text[:120]


def _extract_strategy(text: str) -> Optional[str]:
    match = _STRATEGY.search(text)
    if not match:
        return None
    for group in match.groups():
        if group and str(group).strip():
            return _normalize_strategy_text(str(group))
    return None


def _first_ticker_candidate(text: str) -> Optional[str]:
    if not text:
        return None
    bold = _BOLD_TICKER.search(text)
    if bold and _is_plausible_ticker(bold.group(1)):
        return bold.group(1).upper()
    parens = _TICKER_IN_PARENS.search(text)
    if parens and _is_plausible_ticker(parens.group(1)):
        return parens.group(1).upper()
    # Prefer ticker at the start of the line/snippet.
    leading = re.match(
        r"^\s*(?:\*\*)?([A-Z]{1,5})(?:\*\*)?(?:\s*[\(:\-—,]|\s+)",
        text.strip(),
    )
    if leading and _is_plausible_ticker(leading.group(1)):
        return leading.group(1).upper()
    for match in _TICKER_TOKEN.finditer(text.upper()):
        token = match.group(1)
        if _is_plausible_ticker(token):
            return token
    return None


def _confidence_for_pick(
    *,
    classification: str,
    ticker: str,
    header_remainder: str,
    body: str,
    direction: Optional[str],
) -> float:
    score = 0.55
    combined_head = f"{header_remainder}\n{body}".strip()
    if ticker and ticker in (header_remainder.upper() if header_remainder else ""):
        score += 0.25
    elif ticker and combined_head.upper().startswith(ticker):
        score += 0.2
    elif ticker and re.search(rf"\b{re.escape(ticker)}\b", combined_head.upper()):
        score += 0.12
    if direction:
        score += 0.08
    if classification in {EMAIL_CLASSIFICATION_TOP, EMAIL_CLASSIFICATION_SECONDARY}:
        score += 0.05
    return round(min(score, 0.99), 2)


def _build_pick(
    *,
    classification: str,
    ticker: str,
    source_section: str,
    source_excerpt: str,
    direction: Optional[str],
    strike: Optional[float],
    expiration: Optional[str],
    strategy_text: Optional[str],
    parser_confidence: float,
    parse_mode: str,
) -> dict[str, Any]:
    return {
        "ticker": ticker,
        "email_classification": classification,
        "direction": direction,
        "strike": strike,
        "expiration": expiration,
        "strategy_text": strategy_text,
        "source_section": source_section,
        "parser_confidence": parser_confidence,
        "parse_mode": parse_mode,
        "parser_version": PARSER_VERSION,
        "source_excerpt": source_excerpt[:500],
    }


def _parse_featured_section(
    classification: str,
    header_remainder: str,
    body: str,
    *,
    parse_mode: str,
) -> list[dict[str, Any]]:
    """Parse TOP or SECONDARY section into at most one pick."""
    search_blob = "\n".join(part for part in (header_remainder, body) if part).strip()
    if not search_blob:
        return []

    # Prefer ticker from the header remainder / first non-empty body lines.
    head_lines = [header_remainder] if header_remainder else []
    body_lines = [line.strip() for line in body.split("\n") if line.strip()][:4]
    head_lines.extend(body_lines)
    ticker = None
    for line in head_lines:
        ticker = _first_ticker_candidate(line)
        if ticker:
            break
    if not ticker:
        ticker = _first_ticker_candidate(search_blob)
    if not ticker:
        return []

    direction = _extract_direction(search_blob)
    strike = _extract_strike(search_blob)
    expiration = _extract_expiration(search_blob)
    strategy_text = _extract_strategy(search_blob)
    excerpt_parts = [p for p in (header_remainder, body) if p]
    excerpt = "\n".join(excerpt_parts).strip()
    if len(excerpt) > 500:
        excerpt = excerpt[:500].rstrip() + "…"
    confidence = _confidence_for_pick(
        classification=classification,
        ticker=ticker,
        header_remainder=header_remainder,
        body=body,
        direction=direction,
    )
    # Require a reasonably clear ticker signal for featured picks.
    if confidence < 0.62:
        return []
    return [
        _build_pick(
            classification=classification,
            ticker=ticker,
            source_section=classification,
            source_excerpt=excerpt,
            direction=direction,
            strike=strike,
            expiration=expiration,
            strategy_text=strategy_text,
            parser_confidence=confidence,
            parse_mode=parse_mode,
        )
    ]


def _parse_watch_list_entries(
    header_remainder: str,
    body: str,
    *,
    parse_mode: str,
) -> list[dict[str, Any]]:
    """Watch entries come from labels, bullets, and table data rows.

    Table headers, separator rows, company-name sentences, and continuation
    prose are not entries, even when they contain a short uppercase word.
    """
    entries: list[dict[str, Any]] = []
    seen: set[str] = set()
    lines: list[str] = []
    if header_remainder.strip():
        lines.append(header_remainder)
    lines.extend(body.split("\n"))

    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        following = ""
        for later in lines[index + 1 :]:
            if later.strip():
                following = later.strip()
                break
        ticker = _watch_line_ticker(stripped, following)
        if not ticker or ticker in seen:
            continue
        seen.add(ticker)
        direction = _extract_direction(stripped)
        strike = _extract_strike(stripped)
        expiration = _extract_expiration(stripped)
        strategy_text = _extract_strategy(stripped)
        confidence = 0.72 if direction else 0.66
        compact = stripped.upper().replace(" ", "")
        if stripped.upper().startswith(ticker) or f"**{ticker}**" in compact:
            confidence += 0.1
        confidence = round(min(confidence, 0.95), 2)
        entries.append(
            _build_pick(
                classification=EMAIL_CLASSIFICATION_WATCH,
                ticker=ticker,
                source_section="watch",
                source_excerpt=stripped[:500],
                direction=direction,
                strike=strike,
                expiration=expiration,
                strategy_text=strategy_text,
                parser_confidence=confidence,
                parse_mode=parse_mode,
            )
        )
        if len(entries) >= 5:
            break
    return entries


def parse_scout_email_picks(raw_report_text: Optional[str]) -> dict[str, Any]:
    """Parse emailed picks from Scout v6 raw_report_text.

    Returns:
      {
        "parse_mode": "editorial" | "raw_scan_fallback" | "unrecognized",
        "picks": [ ... ],
      }

    Prefer fewer high-confidence picks over false positives. Does not invent
    missing fields. Raw-scan fallback never fabricates TOP/SECONDARY/WATCH from
    FINAL RANKINGS unless those section headers are explicitly present.
    """
    text = _normalize_whitespace(str(raw_report_text or ""))
    parse_mode = detect_parse_mode(text)

    if parse_mode == PARSE_MODE_RAW_SCAN_FALLBACK:
        # Preserve the report elsewhere; do not treat ranking dumps as editorial picks.
        # Only accept explicit Claude-style section headers if somehow present.
        sections = _split_editorial_sections(text)
        editorial_like = [
            section
            for section in sections
            if section[0]
            in {
                EMAIL_CLASSIFICATION_TOP,
                EMAIL_CLASSIFICATION_SECONDARY,
                EMAIL_CLASSIFICATION_WATCH,
            }
            and (section[1] or section[2])
            and not re.search(r"FINAL\s+RANKINGS", section[2], re.IGNORECASE)
        ]
        # In practice raw scans use "TOP PICK: TICKER (DIR)" as a one-liner under
        # FINAL RANKINGS, not a Claude section. Reject those one-liners.
        picks: list[dict[str, Any]] = []
        for classification, remainder, body in editorial_like:
            combined = f"{remainder}\n{body}".strip()
            # One-line ranking residue: "AAPL (CALL) Scout Score 87/100"
            if re.search(r"Scout\s+Score\s+\d+", combined, re.IGNORECASE):
                continue
            if classification == EMAIL_CLASSIFICATION_WATCH:
                picks.extend(
                    _parse_watch_list_entries(remainder, body, parse_mode=parse_mode)
                )
            else:
                picks.extend(
                    _parse_featured_section(
                        classification, remainder, body, parse_mode=parse_mode
                    )
                )
        return {"parse_mode": parse_mode, "picks": picks}

    if parse_mode == PARSE_MODE_UNRECOGNIZED:
        return {"parse_mode": parse_mode, "picks": []}

    sections = _split_editorial_sections(text)
    picks = []
    seen_keys: set[tuple[str, str]] = set()
    for classification, remainder, body in sections:
        if classification == EMAIL_CLASSIFICATION_WATCH:
            parsed = _parse_watch_list_entries(remainder, body, parse_mode=parse_mode)
        else:
            parsed = _parse_featured_section(
                classification, remainder, body, parse_mode=parse_mode
            )
        for pick in parsed:
            key = (pick["email_classification"], pick["ticker"])
            if key in seen_keys:
                continue
            seen_keys.add(key)
            picks.append(pick)

    return {"parse_mode": parse_mode, "picks": picks}


def extract_scan_candidates(structured_report: Any) -> list[dict[str, Any]]:
    """Return deterministic scan candidates from structured_report_json.picks.

    These are NOT emailed classifications. They are passed[:6] gate context.
    """
    if not isinstance(structured_report, dict):
        return []
    picks = structured_report.get("picks")
    if not isinstance(picks, list):
        return []
    candidates: list[dict[str, Any]] = []
    for pick in picks:
        if not isinstance(pick, dict):
            continue
        ticker = str(pick.get("ticker") or pick.get("symbol") or "").strip().upper()
        direction = str(pick.get("direction") or pick.get("side") or "").strip().upper()
        if not ticker:
            continue
        candidates.append(
            {
                "ticker": ticker,
                "direction": direction or None,
                "scout_score": pick.get("scout_score", pick.get("scoutScore")),
                "total_score": pick.get("total_score", pick.get("totalScore")),
                "strategy": pick.get("strategy"),
                "catalyst_level": pick.get("catalyst_level", pick.get("catalystLevel")),
                "expiration_rec": pick.get("expiration_rec", pick.get("expirationRec")),
                "strikes": pick.get("strikes") if isinstance(pick.get("strikes"), dict) else {},
                "source": "structured_report_json.picks",
                "role": "scan_candidate",
                "raw": pick,
            }
        )
    return candidates
