#!/usr/bin/env python3
"""Research Master — advisory gate 15 over Scout-Deploy picks.

Advisory mode only: never changes ranking, gate pass/fail, or pick selection.
Uses Research Memory to explain how to handle a pick that cleared Scout-Deploy.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from memory_store import connect, count_scan_results, get_db_path, init_db
from research_findings_engine import list_research_findings


GATE_KEY = "research_master"
GATE_CODE = "RESEARCH"
GATE_NAME = "Research Master"
GATE_INDEX = 15

VERDICT_APPROVE = "APPROVE"
VERDICT_CAUTION = "CAUTION"
VERDICT_INSUFFICIENT_DATA = "INSUFFICIENT_DATA"

MIN_MEMORY_ROWS = 3
MIN_COMPARABLE_OUTCOMES = 5
NEGATIVE_EXPECTANCY_THRESHOLD = -1.5
POSITIVE_EXPECTANCY_THRESHOLD = 1.5
WEAK_WIN_RATE_THRESHOLD = 45.0
STRONG_WIN_RATE_THRESHOLD = 55.0

QUARANTINED_COHORTS = frozenset({"failure_learning", "preview", "regime_probe"})

FUNDAMENTALS: tuple[dict[str, str], ...] = (
    {
        "id": "gate_integrity",
        "title": "Gate Integrity",
        "description": "Scout-Deploy gates remain authoritative; Research Master advises, never overrides.",
    },
    {
        "id": "sample_size",
        "title": "Sample Size Discipline",
        "description": "Historical fit requires enough completed outcomes before high conviction.",
    },
    {
        "id": "expectancy_over_win_rate",
        "title": "Expectancy Over Win Rate",
        "description": "Average forward return and expectancy matter more than streaky win rates.",
    },
    {
        "id": "cohort_quarantine",
        "title": "Cohort Quarantine",
        "description": "Preview and failure-learning scans are not actionable training denominators.",
    },
    {
        "id": "regime_awareness",
        "title": "Regime Awareness",
        "description": "Sector and direction history must match the current pick profile.",
    },
    {
        "id": "promotion_bar",
        "title": "Promotion Bar",
        "description": "Only validated research rules may change Scout-Deploy; advisory stays read-only.",
    },
)


def research_master_enabled() -> bool:
    return os.environ.get("SCOUT_RESEARCH_MASTER", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def _normalize_direction(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "Unknown"
    lowered = text.lower()
    if "bull" in lowered:
        return "Bullish"
    if "bear" in lowered:
        return "Bearish"
    if "neutral" in lowered:
        return "Neutral"
    return text.title()


def _gate_state_for_verdict(verdict: str) -> str:
    if verdict == VERDICT_APPROVE:
        return "pass"
    if verdict == VERDICT_CAUTION:
        return "caution"
    return "info"


def _confidence_label(score: float) -> str:
    if score >= 70:
        return "high"
    if score >= 40:
        return "medium"
    return "low"


def _comparable_outcome_stats(
    conn: Any,
    *,
    sector: str,
    direction: str,
    exclude_ticker: Optional[str] = None,
) -> dict[str, Any]:
    params: list[Any] = [direction]
    ticker_clause = ""
    if exclude_ticker:
        ticker_clause = "AND sr.ticker != ?"
        params.append(exclude_ticker.upper())

    sector_clause = ""
    if sector and sector.lower() not in {"n/a", "unknown", ""}:
        sector_clause = """
            AND LOWER(COALESCE(json_extract(sr.raw_result_json, '$.sector'), '')) = LOWER(?)
        """
        params.insert(0, sector)

    row = conn.execute(
        f"""
        SELECT
            COUNT(*) AS sample_size,
            SUM(CASE WHEN sr.stock_outcome_label = 'WIN' THEN 1 ELSE 0 END) AS wins,
            AVG(sr.return_5d) AS avg_return_5d,
            AVG(sr.return_10d) AS avg_return_10d
        FROM scan_results sr
        WHERE sr.final_direction = ?
          AND sr.stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
          {sector_clause}
          {ticker_clause}
        """,
        params,
    ).fetchone()

    sample_size = int(row["sample_size"] or 0)
    wins = int(row["wins"] or 0)
    win_rate = round(wins / sample_size * 100, 2) if sample_size else None
    return {
        "sampleSize": sample_size,
        "wins": wins,
        "winRate": win_rate,
        "avgReturn5d": round(float(row["avg_return_5d"]), 4) if row["avg_return_5d"] is not None else None,
        "avgReturn10d": round(float(row["avg_return_10d"]), 4) if row["avg_return_10d"] is not None else None,
        "sector": sector or None,
        "direction": direction,
    }


def _gate_alpha_flags(conn: Any, *, sector: str, gate_codes: list[str]) -> list[dict[str, Any]]:
    flags: list[dict[str, Any]] = []
    normalized_sector = (sector or "GLOBAL").strip() or "GLOBAL"
    for code in gate_codes:
        row = conn.execute(
            """
            SELECT gate_name, sector, sample_count, win_rate, expectancy, confidence_score
            FROM gate_alpha_metrics
            WHERE gate_name = ?
              AND sector IN (?, 'GLOBAL')
              AND sample_count >= ?
            ORDER BY CASE WHEN sector = ? THEN 0 ELSE 1 END, sample_count DESC
            LIMIT 1
            """,
            (code.upper(), normalized_sector, MIN_COMPARABLE_OUTCOMES, normalized_sector),
        ).fetchone()
        if row is None:
            continue
        expectancy = float(row["expectancy"] or 0.0)
        if expectancy <= NEGATIVE_EXPECTANCY_THRESHOLD:
            flags.append(
                {
                    "gate": row["gate_name"],
                    "sector": row["sector"],
                    "sampleSize": int(row["sample_count"] or 0),
                    "expectancy": expectancy,
                    "winRate": float(row["win_rate"] or 0.0),
                    "message": (
                        f"{row['gate_name']} shows negative historical expectancy "
                        f"({expectancy:.2f}) in {row['sector']} cohorts."
                    ),
                }
            )
    return flags


def _matching_open_findings(
    *,
    sector: str,
    direction: str,
    gate_codes: list[str],
) -> list[dict[str, Any]]:
    findings = list_research_findings(status="open", limit=100)
    matches: list[dict[str, Any]] = []
    sector_lower = (sector or "").lower()
    direction_lower = direction.lower()
    gate_set = {code.upper() for code in gate_codes}

    for finding in findings:
        related_sectors = [
            str(item).lower()
            for item in (finding.get("relatedSectors") or [])
        ]
        related_gates = {
            str(item).upper()
            for item in (finding.get("relatedGates") or [])
        }
        sector_match = not sector_lower or any(
            sector_lower in related or related in sector_lower for related in related_sectors
        )
        gate_match = not related_gates or bool(related_gates & gate_set)
        severity = str(finding.get("severity") or "info")
        if severity not in {"warning", "critical"}:
            continue
        if sector_match or gate_match:
            matches.append(
                {
                    "id": finding.get("id"),
                    "title": finding.get("title"),
                    "severity": severity,
                    "findingType": finding.get("findingType"),
                    "description": finding.get("description"),
                }
            )
    if direction_lower == "bearish":
        for finding in findings:
            if finding.get("findingType") == "direction_failure" and finding.get("severity") in {
                "warning",
                "critical",
            }:
                if finding not in matches:
                    matches.append(
                        {
                            "id": finding.get("id"),
                            "title": finding.get("title"),
                            "severity": finding.get("severity"),
                            "findingType": finding.get("findingType"),
                            "description": finding.get("description"),
                        }
                    )
    return matches[:5]


def _build_handling_advice(
    *,
    verdict: str,
    confidence: str,
    reasons: list[str],
    pick: dict[str, Any],
) -> str:
    ticker = str(pick.get("ticker") or "This pick")
    if verdict == VERDICT_INSUFFICIENT_DATA:
        return (
            f"{ticker} cleared Scout-Deploy, but Research Memory is still thin. "
            "Treat as a live hypothesis: smaller size, save the scan, and let outcomes build."
        )
    if verdict == VERDICT_APPROVE:
        if confidence == "high":
            return (
                f"{ticker} aligns with learned history. Proceed with normal risk controls; "
                "save to memory and track the 5d/10d outcome."
            )
        return (
            f"{ticker} looks reasonable, but confidence is moderate. "
            "Proceed with standard size and confirm the thesis against the gate card."
        )
    if not pick.get("passedAllGates"):
        return (
            f"{ticker} is the top score but did not pass every Scout-Deploy gate. "
            "Use watchlist sizing or wait for a cleaner gate profile before committing capital."
        )
    if reasons:
        return (
            f"{ticker} passed Scout-Deploy, but memory flags caution: {reasons[0]} "
            "Reduce size, tighten risk, or wait for confirmation."
        )
    return (
        f"{ticker} passed Scout-Deploy with mixed historical support. "
        "Handle as a caution pick until memory strengthens."
    )


def evaluate_pick_advisory(
    pick: dict[str, Any],
    *,
    cohort_class: Optional[str] = None,
    scan_purpose: Optional[str] = None,
) -> dict[str, Any]:
    """Return advisory Research Master payload for one serialized pick."""
    init_db()
    sector = str(pick.get("sector") or "unknown")
    direction = _normalize_direction(pick.get("direction"))
    gate_codes = [
        str(gate.get("code") or "")
        for gate in (pick.get("gates") or [])
        if isinstance(gate, dict) and gate.get("passed")
    ]

    reasons: list[str] = []
    fundamentals_applied = [item["id"] for item in FUNDAMENTALS]
    confidence_score = 50.0

    with connect() as conn:
        memory_rows = count_scan_results(conn=conn)
        comparable = _comparable_outcome_stats(
            conn,
            sector=sector,
            direction=direction,
            exclude_ticker=str(pick.get("ticker") or ""),
        )
        gate_alpha_flags = _gate_alpha_flags(conn, sector=sector, gate_codes=gate_codes)

    open_findings = _matching_open_findings(
        sector=sector,
        direction=direction,
        gate_codes=gate_codes,
    )

    if memory_rows < MIN_MEMORY_ROWS:
        verdict = VERDICT_INSUFFICIENT_DATA
        confidence_score = 20.0
        reasons.append(
            f"Research Memory has only {memory_rows} saved scans; more history is needed."
        )
    else:
        verdict = VERDICT_APPROVE

    if cohort_class in QUARANTINED_COHORTS or scan_purpose in {"preview", "research"}:
        verdict = VERDICT_CAUTION
        confidence_score -= 15.0
        reasons.append(
            "Scan cohort is quarantined for learning/research; do not treat as fully actionable."
        )

    if not pick.get("passedAllGates"):
        verdict = VERDICT_CAUTION
        confidence_score -= 25.0
        reasons.append("Pick did not pass all 14 Scout-Deploy gates.")

    comparable_size = int(comparable.get("sampleSize") or 0)
    if comparable_size >= MIN_COMPARABLE_OUTCOMES:
        avg_return_5d = comparable.get("avgReturn5d")
        win_rate = comparable.get("winRate")
        if avg_return_5d is not None and avg_return_5d <= NEGATIVE_EXPECTANCY_THRESHOLD:
            verdict = VERDICT_CAUTION
            confidence_score -= 20.0
            reasons.append(
                f"Similar {direction} picks in {sector} averaged {avg_return_5d:.2f}% at 5d "
                f"across {comparable_size} outcomes."
            )
        elif (
            avg_return_5d is not None
            and avg_return_5d >= POSITIVE_EXPECTANCY_THRESHOLD
            and (win_rate or 0) >= STRONG_WIN_RATE_THRESHOLD
        ):
            confidence_score += 20.0
            reasons.append(
                f"Similar {direction} picks in {sector} show positive 5d history "
                f"({avg_return_5d:.2f}% avg, {win_rate:.1f}% win rate)."
            )
        elif win_rate is not None and win_rate <= WEAK_WIN_RATE_THRESHOLD:
            verdict = VERDICT_CAUTION
            confidence_score -= 10.0
            reasons.append(
                f"Similar {direction} picks in {sector} win only {win_rate:.1f}% of the time."
            )
    elif memory_rows >= MIN_MEMORY_ROWS and verdict != VERDICT_INSUFFICIENT_DATA:
        confidence_score -= 10.0
        reasons.append(
            f"Only {comparable_size} comparable completed outcomes for {direction}/{sector}; "
            "historical fit is still forming."
        )

    for flag in gate_alpha_flags:
        verdict = VERDICT_CAUTION
        confidence_score -= 8.0
        reasons.append(flag["message"])

    for finding in open_findings:
        verdict = VERDICT_CAUTION
        confidence_score -= 12.0 if finding.get("severity") == "critical" else 6.0
        reasons.append(f"Open research finding: {finding.get('title')}")

    confidence_score = max(0.0, min(100.0, confidence_score))
    confidence = _confidence_label(confidence_score)

    if verdict == VERDICT_INSUFFICIENT_DATA:
        pass
    elif verdict == VERDICT_APPROVE and confidence_score < 45:
        verdict = VERDICT_CAUTION

    deduped_reasons: list[str] = []
    seen: set[str] = set()
    for reason in reasons:
        if reason not in seen:
            seen.add(reason)
            deduped_reasons.append(reason)

    handling_advice = _build_handling_advice(
        verdict=verdict,
        confidence=confidence,
        reasons=deduped_reasons,
        pick=pick,
    )

    return {
        "enabled": True,
        "mode": "advisory",
        "gateIndex": GATE_INDEX,
        "gateKey": GATE_KEY,
        "gateCode": GATE_CODE,
        "gateName": GATE_NAME,
        "verdict": verdict,
        "confidence": confidence,
        "confidenceScore": round(confidence_score, 1),
        "handlingAdvice": handling_advice,
        "reasons": deduped_reasons,
        "fundamentals": list(FUNDAMENTALS),
        "fundamentalsApplied": fundamentals_applied,
        "memory": {
            "dbPath": str(get_db_path()),
            "savedScans": memory_rows,
            "comparableOutcomes": comparable,
            "gateAlphaFlags": gate_alpha_flags,
            "openFindings": open_findings,
        },
    }


def build_research_master_gate(advisory: dict[str, Any]) -> dict[str, Any]:
    verdict = str(advisory.get("verdict") or VERDICT_INSUFFICIENT_DATA)
    state = _gate_state_for_verdict(verdict)
    return {
        "index": GATE_INDEX,
        "key": GATE_KEY,
        "code": GATE_CODE,
        "name": GATE_NAME,
        "passed": True,
        "advisory": True,
        "advisoryMode": "advisory",
        "verdict": verdict,
        "state": state,
        "confidence": advisory.get("confidence"),
        "summary": advisory.get("handlingAdvice"),
    }


def attach_research_master_advisory(
    pick: dict[str, Any],
    *,
    cohort_class: Optional[str] = None,
    scan_purpose: Optional[str] = None,
) -> dict[str, Any]:
    if not research_master_enabled():
        return pick
    advisory = evaluate_pick_advisory(
        pick,
        cohort_class=cohort_class,
        scan_purpose=scan_purpose,
    )
    updated = dict(pick)
    updated["researchMaster"] = advisory
    gates = list(updated.get("gates") or [])
    gates = [gate for gate in gates if gate.get("key") != GATE_KEY]
    gates.append(build_research_master_gate(advisory))
    updated["gates"] = gates
    return updated


def apply_research_master_to_run_payload(payload: dict[str, Any]) -> dict[str, Any]:
    if not payload.get("ok") or not isinstance(payload.get("finalPick"), dict):
        return payload
    if not research_master_enabled():
        payload["researchMaster"] = {"enabled": False, "mode": "advisory"}
        return payload

    cohort_class = payload.get("cohortClass")
    scan_purpose = payload.get("scanPurpose")
    final_pick = attach_research_master_advisory(
        payload["finalPick"],
        cohort_class=str(cohort_class) if cohort_class else None,
        scan_purpose=str(scan_purpose) if scan_purpose else None,
    )
    payload["finalPick"] = final_pick
    payload["researchMaster"] = {
        "enabled": True,
        "mode": "advisory",
        "finalPickTicker": final_pick.get("ticker"),
        **(final_pick.get("researchMaster") or {}),
    }
    return payload
