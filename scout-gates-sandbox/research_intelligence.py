#!/usr/bin/env python3
"""Read-only synthesis layer over Scout research intelligence stores."""

from __future__ import annotations

import sqlite3
from collections import Counter
from typing import Any, Optional

from backtest_engine import BacktestFilters, compute_gate_contribution_audit, fetch_backtest_signals
from memory_store import connect, init_db
from research_findings_engine import init_research_findings_store, list_research_findings
from research_job_runner import init_research_job_store
from rule_candidates_engine import init_rule_candidates_store, list_rule_candidates
from rule_validation_engine import init_rule_validations_store, list_rule_validations


SEVERITY_RANK = {"critical": 0, "warning": 1, "watch": 2, "info": 3}
CONFIDENCE_RANK = {"high": 0, "medium": 1, "low": 2}
CANDIDATE_STATUS_RANK = {"testing": 0, "validated": 1, "proposed": 2, "rejected": 3, "archived": 4}


def _init_stores(conn: sqlite3.Connection) -> None:
    init_research_job_store(conn)
    init_research_findings_store(conn)
    init_rule_candidates_store(conn)
    init_rule_validations_store(conn)


def _numeric(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _finding_sort_key(finding: dict[str, Any]) -> tuple[Any, ...]:
    return (
        SEVERITY_RANK.get(str(finding.get("severity") or "info"), 99),
        CONFIDENCE_RANK.get(str(finding.get("confidence") or "low"), 99),
        finding.get("createdAt") or "",
    )


def _validation_sort_key(validation: dict[str, Any]) -> tuple[Any, ...]:
    expectancy_delta = _numeric(validation.get("expectancyDelta"))
    confidence = _numeric(validation.get("confidenceScore"))
    return (
        -(expectancy_delta if expectancy_delta is not None else -9999.0),
        -(confidence if confidence is not None else -1.0),
        validation.get("completedAt") or validation.get("createdAt") or "",
    )


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[Any, ...]:
    return (
        CANDIDATE_STATUS_RANK.get(str(candidate.get("status") or "proposed"), 99),
        candidate.get("updatedAt") or candidate.get("createdAt") or "",
    )


def _historical_signals(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return fetch_backtest_signals(conn, BacktestFilters(require_completed_outcomes=True))


def _findings_for_gate(findings: list[dict[str, Any]], gate_code: str) -> list[dict[str, Any]]:
    normalized = str(gate_code or "").strip().upper()
    linked: list[dict[str, Any]] = []
    for finding in findings:
        gates = [str(item).upper() for item in (finding.get("relatedGates") or [])]
        metrics_gate = str((finding.get("supportingMetrics") or {}).get("gateCode") or "").upper()
        if normalized in gates or (metrics_gate and metrics_gate == normalized):
            linked.append(
                {
                    "id": finding.get("id"),
                    "title": finding.get("title"),
                    "findingType": finding.get("findingType"),
                    "severity": finding.get("severity"),
                }
            )
    return linked


def get_research_intelligence_summary() -> dict[str, Any]:
    init_db()
    with connect() as conn:
        _init_stores(conn)
        from ingest_scout_reports import init_research_daily_reports_store

        init_research_daily_reports_store(conn)
        total_findings = conn.execute("SELECT COUNT(*) FROM research_findings").fetchone()[0]
        open_findings = conn.execute(
            "SELECT COUNT(*) FROM research_findings WHERE status = 'open'"
        ).fetchone()[0]
        rule_candidates = conn.execute("SELECT COUNT(*) FROM rule_candidates").fetchone()[0]
        completed_validations = conn.execute(
            "SELECT COUNT(*) FROM rule_validations WHERE status = 'completed'"
        ).fetchone()[0]
        highest = conn.execute(
            """
            SELECT rv.*, rc.title AS candidate_title
            FROM rule_validations rv
            JOIN rule_candidates rc ON rc.id = rv.candidate_id
            WHERE rv.status = 'completed'
            ORDER BY rv.confidence_score DESC, rv.expectancy_delta DESC, rv.id DESC
            LIMIT 1
            """
        ).fetchone()
        ingested_daily_reports = conn.execute(
            "SELECT COUNT(*) FROM research_daily_reports"
        ).fetchone()[0]
        latest_daily_scan = conn.execute(
            """
            SELECT market_date FROM research_daily_reports
            WHERE report_type = 'daily_scan'
            ORDER BY COALESCE(market_date, generated_at, ingested_at) DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        latest_monday_coffee = conn.execute(
            """
            SELECT market_date FROM research_daily_reports
            WHERE report_type = 'monday_coffee'
            ORDER BY COALESCE(market_date, generated_at, ingested_at) DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
        daily_report_findings = conn.execute(
            """
            SELECT COUNT(*) FROM research_findings
            WHERE finding_type = 'daily_report_observation'
               OR json_extract(supporting_metrics_json, '$.source') = 'daily_report'
            """
        ).fetchone()[0]

    from ingest_scout_reports import get_sent_picks_summary

    sent_picks_summary = get_sent_picks_summary()

    highest_validation = None
    if highest is not None:
        highest_validation = {
            "id": highest["id"],
            "candidateId": highest["candidate_id"],
            "candidateTitle": highest["candidate_title"],
            "confidenceScore": highest["confidence_score"],
            "expectancyDelta": highest["expectancy_delta"],
            "validationSummary": highest["validation_summary"],
        }

    return {
        "totalFindings": int(total_findings or 0),
        "openFindings": int(open_findings or 0),
        "ruleCandidates": int(rule_candidates or 0),
        "completedValidations": int(completed_validations or 0),
        "highestConfidenceValidation": highest_validation,
        "ingestedDailyReports": int(ingested_daily_reports or 0),
        "latestScoutV6ReportDate": latest_daily_scan["market_date"] if latest_daily_scan else None,
        "latestMondayCoffeeDate": (
            latest_monday_coffee["market_date"] if latest_monday_coffee else None
        ),
        "dailyReportFindingsCount": int(daily_report_findings or 0),
        "totalSentPicks": sent_picks_summary["totalSentPicks"],
        "sentReportsRepresented": sent_picks_summary["sentReportsRepresented"],
        "latestSentPickDate": sent_picks_summary["latestSentPickDate"],
        "sentPicksByClassification": sent_picks_summary.get("byClassification") or {},
    }


def get_daily_report_findings(limit: int = 10) -> list[dict[str, Any]]:
    findings = list_research_findings(finding_type="daily_report_observation", limit=200)
    # Also include any open findings tagged as daily_report source.
    extras = [
        finding
        for finding in list_research_findings(limit=200)
        if (finding.get("supportingMetrics") or {}).get("source") == "daily_report"
        and finding.get("findingType") != "daily_report_observation"
    ]
    combined = findings + extras
    ranked = sorted(combined, key=_finding_sort_key)
    bounded = min(max(int(limit), 1), 50)
    return [
        {
            "id": finding["id"],
            "title": finding["title"],
            "findingType": finding["findingType"],
            "severity": finding["severity"],
            "confidence": finding["confidence"],
            "reportType": (finding.get("supportingMetrics") or {}).get("reportType"),
            "marketDate": (finding.get("supportingMetrics") or {}).get("marketDate"),
            "reportId": (finding.get("supportingMetrics") or {}).get("reportId"),
            "recommendedNextTest": finding.get("recommendedNextTest"),
        }
        for finding in ranked[:bounded]
    ]


def get_recent_sent_picks(limit: int = 20) -> list[dict[str, Any]]:
    from ingest_scout_reports import list_sent_picks

    picks = list_sent_picks(limit=min(max(int(limit), 1), 100))
    return [
        {
            "sentPickId": pick.get("sentPickId"),
            "marketDate": pick.get("marketDate"),
            "ticker": pick.get("ticker"),
            "emailClassification": pick.get("emailClassification"),
            "direction": pick.get("direction"),
            "reportId": pick.get("reportId"),
            "parserConfidence": pick.get("parserConfidence"),
            "parseMode": pick.get("parseMode"),
            "role": "emailed_pick",
        }
        for pick in picks
    ]


def get_sent_pick_findings(limit: int = 10) -> list[dict[str, Any]]:
    findings = list_research_findings(finding_type="sent_pick_observation", limit=200)
    ranked = sorted(findings, key=_finding_sort_key)
    bounded = min(max(int(limit), 1), 50)
    return [
        {
            "id": finding["id"],
            "title": finding["title"],
            "findingType": finding["findingType"],
            "severity": finding["severity"],
            "confidence": finding["confidence"],
            "reportType": (finding.get("supportingMetrics") or {}).get("reportType"),
            "marketDate": (finding.get("supportingMetrics") or {}).get("marketDate"),
            "reportId": (finding.get("supportingMetrics") or {}).get("reportId"),
            "ticker": (finding.get("supportingMetrics") or {}).get("ticker"),
            "emailClassification": (finding.get("supportingMetrics") or {}).get(
                "emailClassification"
            ),
            "direction": (finding.get("supportingMetrics") or {}).get("direction"),
            "parseMode": (finding.get("supportingMetrics") or {}).get("parseMode"),
            "recommendedNextTest": finding.get("recommendedNextTest"),
        }
        for finding in ranked[:bounded]
    ]


def get_top_findings(limit: int = 10) -> list[dict[str, Any]]:
    findings = list_research_findings(limit=min(max(int(limit), 1), 100))
    ranked = sorted(findings, key=_finding_sort_key)
    return [
        {
            "id": finding["id"],
            "title": finding["title"],
            "findingType": finding["findingType"],
            "severity": finding["severity"],
            "confidence": finding["confidence"],
            "status": finding["status"],
            "recommendedNextTest": finding.get("recommendedNextTest"),
            "relatedGates": finding.get("relatedGates") or [],
            "relatedSectors": finding.get("relatedSectors") or [],
            "supportingMetrics": finding.get("supportingMetrics") or {},
        }
        for finding in ranked[:limit]
    ]


def get_top_rule_candidates(limit: int = 10) -> list[dict[str, Any]]:
    candidates = list_rule_candidates(limit=min(max(int(limit), 1), 100))
    ranked = sorted(candidates, key=_candidate_sort_key)
    bounded = min(max(int(limit), 1), 100)
    return [
        {
            "id": candidate["id"],
            "title": candidate["title"],
            "candidateType": candidate["candidateType"],
            "status": candidate["status"],
            "hypothesis": candidate.get("hypothesis"),
            "validationPlan": candidate.get("validationPlan"),
            "affectedScope": candidate.get("affectedScope") or {},
            "supportingMetrics": candidate.get("supportingMetrics") or {},
        }
        for candidate in ranked[:bounded]
    ]


def get_top_validations(limit: int = 10) -> list[dict[str, Any]]:
    validations = list_rule_validations(status="completed", limit=min(max(int(limit), 1), 100))
    ranked = sorted(validations, key=_validation_sort_key)
    bounded = min(max(int(limit), 1), 100)
    return [
        {
            "id": validation["id"],
            "candidateId": validation["candidateId"],
            "candidateTitle": validation.get("candidateTitle"),
            "candidateType": validation.get("candidateType"),
            "confidenceScore": validation.get("confidenceScore"),
            "expectancyDelta": validation.get("expectancyDelta"),
            "winRateDelta": validation.get("winRateDelta"),
            "candidateSignalCount": validation.get("candidateSignalCount"),
            "baselineSignalCount": validation.get("baselineSignalCount"),
            "validationSummary": validation.get("validationSummary"),
            "completedAt": validation.get("completedAt"),
        }
        for validation in ranked[:bounded]
    ]


def get_gate_strength_rankings(*, limit: int = 5) -> list[dict[str, Any]]:
    init_db()
    findings = list_research_findings(finding_type="gate_strength", limit=200)
    with connect() as conn:
        _init_stores(conn)
        audit = compute_gate_contribution_audit(_historical_signals(conn))
    best_gates = (audit.get("best_gates") or [])[: max(int(limit), 1)]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gate in best_gates:
        gate_code = str(gate.get("gate_code") or gate.get("gateCode") or "").upper()
        if not gate_code or gate_code in seen:
            continue
        seen.add(gate_code)
        rows.append(
            {
                "gateCode": gate_code,
                "gateName": gate.get("gate_name") or gate.get("gateName") or gate_code,
                "signalCount": gate.get("signal_count") or gate.get("signalCount") or 0,
                "expectancy": gate.get("expectancy"),
                "winRate": gate.get("win_rate") or gate.get("winRate"),
                "source": "historical_gate_audit",
                "linkedFindings": _findings_for_gate(findings, gate_code),
            }
        )

    for finding in findings:
        gate_code = str((finding.get("supportingMetrics") or {}).get("gateCode") or "").upper()
        if not gate_code:
            related = finding.get("relatedGates") or []
            gate_code = str(related[0]).upper() if related else ""
        if not gate_code or gate_code in seen:
            continue
        seen.add(gate_code)
        metrics = finding.get("supportingMetrics") or {}
        rows.append(
            {
                "gateCode": gate_code,
                "gateName": gate_code,
                "signalCount": metrics.get("signalCount") or 0,
                "expectancy": metrics.get("expectancy"),
                "winRate": metrics.get("winRate"),
                "source": "research_finding",
                "linkedFindings": _findings_for_gate(findings, gate_code),
            }
        )

    rows.sort(
        key=lambda row: (
            row["expectancy"] is None,
            -(row["expectancy"] if row["expectancy"] is not None else 0.0),
        )
    )
    return rows[: max(int(limit), 1)]


def get_gate_weakness_rankings(*, limit: int = 5) -> list[dict[str, Any]]:
    init_db()
    weakness_findings = list_research_findings(finding_type="gate_weakness", limit=200)
    direction_findings = list_research_findings(finding_type="direction_failure", limit=200)
    findings = weakness_findings + direction_findings

    with connect() as conn:
        audit = compute_gate_contribution_audit(_historical_signals(conn))
    worst_gates = (audit.get("worst_gates") or [])[: max(int(limit), 1)]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for gate in worst_gates:
        gate_code = str(gate.get("gate_code") or gate.get("gateCode") or "").upper()
        if not gate_code or gate_code in seen:
            continue
        seen.add(gate_code)
        rows.append(
            {
                "gateCode": gate_code,
                "gateName": gate.get("gate_name") or gate.get("gateName") or gate_code,
                "signalCount": gate.get("signal_count") or gate.get("signalCount") or 0,
                "expectancy": gate.get("expectancy"),
                "winRate": gate.get("win_rate") or gate.get("winRate"),
                "source": "historical_gate_audit",
                "linkedFindings": _findings_for_gate(findings, gate_code),
            }
        )

    for finding in weakness_findings:
        related = finding.get("relatedGates") or []
        gate_code = str(related[0]).upper() if related else ""
        metrics = finding.get("supportingMetrics") or {}
        gate_code = gate_code or str(metrics.get("gateCode") or "").upper()
        if not gate_code or gate_code in seen:
            continue
        seen.add(gate_code)
        rows.append(
            {
                "gateCode": gate_code,
                "gateName": gate_code,
                "signalCount": metrics.get("signalCount") or 0,
                "expectancy": metrics.get("expectancy"),
                "winRate": metrics.get("winRate"),
                "source": "research_finding",
                "linkedFindings": _findings_for_gate(findings, gate_code),
            }
        )

    rows.sort(
        key=lambda row: (
            row["expectancy"] is None,
            row["expectancy"] if row["expectancy"] is not None else 0.0,
        )
    )
    return rows[: max(int(limit), 1)]


def get_recurring_success_patterns(*, limit: int = 10) -> dict[str, Any]:
    findings = list_research_findings(limit=500)
    winners: Counter[str] = Counter()
    leadership_groups: Counter[str] = Counter()
    sectors: Counter[str] = Counter()

    for finding in findings:
        if finding.get("findingType") not in {"gate_strength", "leadership_trend"}:
            continue
        title = str(finding.get("title") or "").strip()
        if title:
            winners[title] += 1
        metrics = finding.get("supportingMetrics") or {}
        group_label = str(metrics.get("groupLabel") or metrics.get("groupId") or "").strip()
        if group_label:
            leadership_groups[group_label] += 1
        for sector in finding.get("relatedSectors") or []:
            sector_name = str(sector).strip()
            if sector_name:
                sectors[sector_name] += 1

    bounded = min(max(int(limit), 1), 50)
    return {
        "repeatedWinners": [
            {"label": label, "count": count}
            for label, count in winners.most_common(bounded)
        ],
        "repeatedLeadershipGroups": [
            {"label": label, "count": count}
            for label, count in leadership_groups.most_common(bounded)
        ],
        "repeatedSectors": [
            {"label": label, "count": count}
            for label, count in sectors.most_common(bounded)
        ],
    }


def get_recurring_failure_patterns(*, limit: int = 10) -> dict[str, Any]:
    findings = list_research_findings(limit=500)
    losers: Counter[str] = Counter()
    direction_failures: Counter[str] = Counter()
    gate_weaknesses: Counter[str] = Counter()

    for finding in findings:
        title = str(finding.get("title") or "").strip()
        finding_type = str(finding.get("findingType") or "")
        if finding_type in {"direction_failure", "sector_failure", "gate_weakness"} and title:
            losers[title] += 1
        if finding_type == "direction_failure" and title:
            direction_failures[title] += 1
        if finding_type == "gate_weakness":
            for gate in finding.get("relatedGates") or []:
                gate_name = str(gate).strip().upper()
                if gate_name:
                    gate_weaknesses[gate_name] += 1
            metrics = finding.get("supportingMetrics") or {}
            gate_code = str(metrics.get("gateCode") or "").strip().upper()
            if gate_code:
                gate_weaknesses[gate_code] += 1

    bounded = min(max(int(limit), 1), 50)
    return {
        "repeatedLosers": [
            {"label": label, "count": count}
            for label, count in losers.most_common(bounded)
        ],
        "repeatedDirectionFailures": [
            {"label": label, "count": count}
            for label, count in direction_failures.most_common(bounded)
        ],
        "repeatedGateWeaknesses": [
            {"label": label, "count": count}
            for label, count in gate_weaknesses.most_common(bounded)
        ],
    }


def get_recommended_next_experiments(limit: int = 10) -> list[dict[str, Any]]:
    findings = list_research_findings(status="open", limit=200)
    candidates = list_rule_candidates(status="testing", limit=100)
    validations = list_rule_validations(status="completed", limit=100)

    experiments: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add_experiment(
        *,
        title: str,
        rationale: str,
        source: str,
        priority: int = 50,
        metadata: Optional[dict[str, Any]] = None,
    ) -> None:
        normalized = title.strip().lower()
        if not normalized or normalized in seen:
            return
        seen.add(normalized)
        experiments.append(
            {
                "title": title.strip(),
                "rationale": rationale.strip(),
                "source": source,
                "priority": priority,
                "metadata": metadata or {},
            }
        )

    for finding in sorted(findings, key=_finding_sort_key):
        recommended = str(finding.get("recommendedNextTest") or "").strip()
        if recommended:
            add_experiment(
                title=recommended,
                rationale=finding.get("title") or "Open research finding",
                source="finding",
                priority=10 + SEVERITY_RANK.get(str(finding.get("severity") or "info"), 99),
                metadata={"findingId": finding.get("id"), "findingType": finding.get("findingType")},
            )

        finding_type = str(finding.get("findingType") or "")
        metrics = finding.get("supportingMetrics") or {}
        if finding_type == "direction_failure":
            add_experiment(
                title="Test bearish trend override on leadership cohorts",
                rationale=finding.get("description") or finding.get("title") or "",
                source="direction_failure_pattern",
                priority=20,
            )
        if finding_type == "gate_strength":
            gate_code = str(metrics.get("gateCode") or "SPECTER").upper()
            job_name = str(metrics.get("jobName") or "historical signals")
            add_experiment(
                title=f"Test {gate_code} emphasis on {job_name.lower()}",
                rationale=finding.get("description") or finding.get("title") or "",
                source="gate_strength_pattern",
                priority=25,
                metadata={"gateCode": gate_code},
            )
        if finding_type == "sector_failure":
            sectors = finding.get("relatedSectors") or []
            sector_label = str(sectors[0]) if sectors else "target sectors"
            add_experiment(
                title=f"Investigate recurring {sector_label} failures",
                rationale=finding.get("description") or finding.get("title") or "",
                source="sector_failure_pattern",
                priority=30,
            )

    for candidate in candidates:
        plan = str(candidate.get("validationPlan") or "").strip()
        scope = candidate.get("affectedScope") or {}
        preset_hint = scope.get("groupLabel") or scope.get("groupId") or scope.get("scopeType")
        if plan:
            title = plan
            if preset_hint and str(preset_hint).lower() not in plan.lower():
                title = f"{plan} ({preset_hint})"
            add_experiment(
                title=title,
                rationale=candidate.get("hypothesis") or candidate.get("title") or "",
                source="rule_candidate",
                priority=35,
                metadata={"candidateId": candidate.get("id")},
            )

    for validation in sorted(validations, key=_validation_sort_key):
        summary = str(validation.get("validationSummary") or "").strip()
        if summary:
            add_experiment(
                title=f"Follow up: {validation.get('candidateTitle') or 'validated candidate'}",
                rationale=summary,
                source="validation",
                priority=40,
                metadata={"validationId": validation.get("id")},
            )

    experiments.sort(key=lambda item: (item["priority"], item["title"]))
    bounded = min(max(int(limit), 1), 50)
    return experiments[:bounded]


def get_research_intelligence_dashboard() -> dict[str, Any]:
    return {
        "ok": True,
        "summary": get_research_intelligence_summary(),
        "strongestValidatedIdeas": get_top_validations(limit=10),
        "strongestFindings": get_top_findings(limit=10),
        "topRuleCandidates": get_top_rule_candidates(limit=10),
        "gateStrengthRankings": get_gate_strength_rankings(limit=5),
        "gateWeaknessRankings": get_gate_weakness_rankings(limit=5),
        "recurringSuccessPatterns": get_recurring_success_patterns(limit=10),
        "recurringFailurePatterns": get_recurring_failure_patterns(limit=10),
        "recommendedNextExperiments": get_recommended_next_experiments(limit=10),
        "dailyScoutIntelligence": get_daily_report_findings(limit=10),
        "recentSentPicks": get_recent_sent_picks(limit=20),
        "sentPickObservations": get_sent_pick_findings(limit=10),
    }
