#!/usr/bin/env python3
"""Visual dashboard for the Scout gate sandbox runner (local and hosted modes)."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from directionality import build_directional_breakdown
from earnings_intelligence import attach_adjusted_scout_score, build_earnings_intelligence_for_result
from explainability import build_explanation
from memory_store import (
    MAX_HISTORY_PAGE_SIZE,
    build_memory_history_payload,
    build_memory_summary_payload,
    create_outcome_test_record,
    create_gate_alpha_test_record,
    export_csv,
    parse_history_filters,
    get_control_summary,
    get_gate_attribution_summary,
    get_gate_alpha_summary,
    get_horizon_self_audit,
    get_outcome_audit_log,
    get_recommendation_explanation,
    get_top_gate_failures,
    rebuild_regime_intelligence,
    run_horizon_backfill,
    rebuild_gate_alpha,
    rebuild_patterns,
    save_scan_result_once,
)
from option_picker import choose_option_contract, fmp_api_key
from peer_risk_adjusted_edge import (
    attach_peer_scoring,
    build_peer_bundle_for_run,
    build_scoring_breakdown,
)
from stable_signal_explainability import (
    apply_explainability_to_run_payload,
    build_scan_explain_context,
)
from stable_signal_layers import build_and_attach_stable_signal
from performance_tracker import update_outcomes
from reporting import (
    DEFAULT_ASYNC_EXPORT,
    ReportConfig,
    default_exports_dir,
    ensure_report_worker,
    get_report_service,
    get_reporting_status,
)
from run_gates import (
    DEFAULT_CANDIDATES,
    GATES,
    CandidateResult,
    choose_final_pick,
    fetch_gate_result,
    gate_api_url,
    load_env,
    parse_ticker_list,
)
from universe_presets import list_preset_catalog, resolve_universe_from_request
from backtest_engine import (
    get_backtest_run,
    list_backtest_runs,
    parse_backtest_filters,
    preview_backtest,
    run_backtest,
)
from research_job_runner import (
    create_default_research_jobs,
    list_research_job_runs,
    list_research_jobs,
    run_enabled_research_jobs,
    run_research_job,
)
from research_findings_engine import (
    generate_findings_from_recent_runs,
    list_research_findings,
    update_research_finding_status,
)
from rule_candidates_engine import (
    generate_rule_candidates_from_open_findings,
    list_rule_candidates,
    update_rule_candidate_status,
)
from rule_validation_engine import (
    create_rule_validation,
    generate_and_run_pending_validations,
    list_rule_validations,
    run_pending_validations,
    run_rule_validation,
)
from research_intelligence import get_research_intelligence_dashboard
from ingest_scout_reports import (
    generate_findings_from_daily_reports,
    ingest_scout_reports,
    list_ingested_daily_reports,
)
from hosted_config import (
    HOSTED_MODE_ENV,
    build_health_status,
    hosted_maintenance_blocked,
    hosted_maintenance_response,
    log_startup_config,
)
from scan_job_runner import (
    create_scan_job,
    ensure_scan_worker,
    get_scan_job,
    list_scan_jobs,
    recover_interrupted_scan_jobs,
)


SANDBOX_DIR = Path(__file__).resolve().parent
REPO_ROOT = SANDBOX_DIR.parent
DASHBOARD_HTML = SANDBOX_DIR / "dashboard.html"
RESEARCH_HTML = SANDBOX_DIR / "research.html"
CONTROL_HTML = SANDBOX_DIR / "control.html"
BACKTEST_HTML = SANDBOX_DIR / "backtest.html"
RESEARCH_QUEUE_HTML = SANDBOX_DIR / "research_queue.html"
RESEARCH_FINDINGS_HTML = SANDBOX_DIR / "research_findings.html"
RULE_CANDIDATES_HTML = SANDBOX_DIR / "rule_candidates.html"
RULE_VALIDATIONS_HTML = SANDBOX_DIR / "rule_validations.html"
RESEARCH_INTELLIGENCE_HTML = SANDBOX_DIR / "research_intelligence.html"
SAFE_REPORT_NAME = re.compile(r"^[A-Za-z0-9._-]+\.pdf$")


def get_reports_dir() -> Path:
    """Resolve PDF export directory (honors SCOUT_REPORTS_DIR)."""
    return default_exports_dir()


def first_failed_gate_payload(result: CandidateResult) -> Optional[dict[str, Any]]:
    failed = result.first_failed_gate
    if not failed:
        return None
    index, code, name = failed
    return {"index": index, "code": code, "name": name}


def serialize_result(
    result: CandidateResult,
    option_pick: Optional[dict[str, Any]] = None,
    explanation: Optional[dict[str, Any]] = None,
    direction_breakdown: Optional[dict[str, Any]] = None,
    peer_bundle: Optional[dict[str, dict[str, Any]]] = None,
) -> dict[str, Any]:
    earnings_intelligence = build_earnings_intelligence_for_result(result.data)
    payload = {
        "ticker": result.data.get("ticker", result.ticker),
        "score": result.score,
        "price": result.data.get("price"),
        "direction": result.data.get("direction"),
        "trend": result.data.get("trend"),
        "sector": result.data.get("sector"),
        "passedAllGates": result.passed_all_gates,
        "firstFailedGate": first_failed_gate_payload(result),
        "gates": [
            {
                "index": index,
                "key": key,
                "code": code,
                "name": name,
                "passed": result.gates.get(key) is True,
            }
            for index, (key, code, name) in enumerate(GATES, start=1)
        ],
        "optionPick": option_pick,
        "explanation": explanation,
        "directionBreakdown": direction_breakdown,
        "earningsIntelligence": earnings_intelligence,
        "raw": result.data,
    }
    payload = attach_adjusted_scout_score(payload, earnings_intelligence)
    if peer_bundle is not None:
        breakdown = build_scoring_breakdown(
            str(result.ticker),
            result.data,
            peer_bundle,
            earnings_intelligence=earnings_intelligence,
        )
        payload = attach_peer_scoring(payload, breakdown)
    return build_and_attach_stable_signal(result, payload)


def pick_winner(results: list[CandidateResult], pick_mode: str) -> CandidateResult:
    if pick_mode == "score_only":
        return max(results, key=lambda result: result.score)
    return choose_final_pick(results)


def attach_cohort_metadata(payload: dict[str, Any], request_payload: dict[str, Any]) -> None:
    """Attach universe cohort fields for memory persistence and telemetry."""
    _, cohort = resolve_universe_from_request(request_payload)
    payload["universePresetId"] = cohort.get("universePresetId")
    payload["universePresetLabel"] = cohort.get("universePresetLabel")
    payload["universePresetVersion"] = cohort.get("universePresetVersion")
    payload["scanPurpose"] = cohort.get("scanPurpose")
    payload["cohortClass"] = cohort.get("cohortClass")
    telemetry = payload.get("scanTelemetry")
    if not isinstance(telemetry, dict):
        telemetry = {}
    telemetry.update(
        {
            "universePresetId": cohort.get("universePresetId"),
            "universePresetLabel": cohort.get("universePresetLabel"),
            "scanPurpose": cohort.get("scanPurpose"),
            "cohortClass": cohort.get("cohortClass"),
            "universePresetVersion": cohort.get("universePresetVersion"),
        }
    )
    payload["scanTelemetry"] = telemetry


def execute_scan_run(
    request_payload: dict[str, Any],
    *,
    progress_callback: Optional[Callable[[int, int, Optional[str]], None]] = None,
) -> dict[str, Any]:
    """Run the gate scan engine (shared by /api/run and async scan jobs)."""
    universe_mode = str(request_payload.get("universeMode") or "custom")
    pick_mode = str(request_payload.get("pickMode") or "gate_runner")
    timeout = float(request_payload.get("timeout") or 25)
    run_timestamp = datetime.now(timezone.utc).isoformat()
    preset_id = str(request_payload.get("universePresetId") or "custom").strip() or "custom"
    if preset_id != "custom" and universe_mode != "fallback":
        universe_mode = "preset"

    candidates, _cohort = resolve_universe_from_request(request_payload)
    if not candidates:
        raise ValueError("Enter at least one ticker or choose the fallback universe.")

    api_url = gate_api_url()
    results: list[CandidateResult] = []
    errors: list[str] = []
    total_tickers = len(candidates)

    for index, ticker in enumerate(candidates):
        if progress_callback is not None:
            progress_callback(index, total_tickers, ticker)
        try:
            results.append(fetch_gate_result(api_url, ticker, timeout))
        except RuntimeError as exc:
            errors.append(str(exc))
        if progress_callback is not None:
            progress_callback(index + 1, total_tickers, ticker)

    if not results:
        failure_payload = {
            "ok": False,
            "apiUrl": api_url,
            "candidates": candidates,
            "universeMode": universe_mode,
            "pickMode": pick_mode,
            "timeout": timeout,
            "runTimestamp": run_timestamp,
            "errors": errors,
            "message": "No ticker scans completed successfully.",
        }
        attach_cohort_metadata(failure_payload, request_payload)
        return failure_payload

    winner = pick_winner(results, pick_mode)
    peer_bundle = build_peer_bundle_for_run(results, run_timestamp=run_timestamp)
    explanations = {
        result.ticker: build_explanation(
            result.data,
            GATES,
            winner.ticker,
            pick_mode,
        )
        for result in results
    }
    direction_breakdowns = {
        result.ticker: build_directional_breakdown(result.data) for result in results
    }
    option_picks: dict[str, dict[str, Any]] = {}
    for result in results:
        if result.passed_all_gates:
            option_picks[result.ticker] = choose_option_contract(
                result.ticker,
                str(result.data.get("direction") or ""),
                timeout=timeout,
            )

    rejected = [
        serialize_result(
            result,
            option_picks.get(result.ticker),
            explanations.get(result.ticker),
            direction_breakdowns.get(result.ticker),
            peer_bundle=peer_bundle,
        )
        for result in sorted(results, key=lambda item: item.score, reverse=True)
        if result.ticker != winner.ticker
    ]

    payload = {
        "ok": True,
        "apiUrl": api_url,
        "candidates": candidates,
        "universeMode": universe_mode,
        "pickMode": pick_mode,
        "timeout": timeout,
        "runTimestamp": run_timestamp,
        "finalPick": serialize_result(
            winner,
            option_picks.get(winner.ticker),
            explanations.get(winner.ticker),
            direction_breakdowns.get(winner.ticker),
            peer_bundle=peer_bundle,
        ),
        "rejected": rejected,
        "results": [
            serialize_result(
                result,
                option_picks.get(result.ticker),
                explanations.get(result.ticker),
                direction_breakdowns.get(result.ticker),
                peer_bundle=peer_bundle,
            )
            for result in results
        ],
        "optionPicks": option_picks,
        "explanations": explanations,
        "directionBreakdowns": direction_breakdowns,
        "errors": errors,
    }
    payload["memoryRunId"] = None
    payload["savedToMemory"] = False

    explain_context = build_scan_explain_context(
        results,
        pick_mode=pick_mode,
        final_pick_ticker=winner.ticker,
        run_timestamp=run_timestamp,
    )
    payload = apply_explainability_to_run_payload(payload, explain_context)
    attach_cohort_metadata(payload, request_payload)
    return payload


def build_run_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    return execute_scan_run(request_payload)


def build_memory_summary() -> dict[str, Any]:
    return build_memory_summary_payload()


def build_control_summary() -> dict[str, Any]:
    return get_control_summary(fmp_key_present=bool(fmp_api_key()))


def build_horizon_self_audit() -> dict[str, Any]:
    return get_horizon_self_audit(
        fmp_key_present=bool(fmp_api_key()),
        control_route_available=CONTROL_HTML.exists(),
    )


def execute_horizon_backfill() -> dict[str, Any]:
    return run_horizon_backfill()


def execute_pattern_rebuild() -> dict[str, Any]:
    return rebuild_patterns()


def execute_gate_alpha_rebuild() -> dict[str, Any]:
    return rebuild_gate_alpha()


def execute_gate_alpha_test_bridge() -> dict[str, Any]:
    return create_gate_alpha_test_record()


def execute_regime_intelligence_rebuild() -> dict[str, Any]:
    return rebuild_regime_intelligence()


class DashboardHandler(BaseHTTPRequestHandler):
    server_version = "ScoutGateDashboard/1.0"

    def reject_hosted_maintenance(self, path: str, method: str) -> bool:
        if hosted_maintenance_blocked(path, method):
            self.send_json(
                hosted_maintenance_response(),
                status=HTTPStatus.FORBIDDEN,
            )
            return True
        return False

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if self.reject_hosted_maintenance(parsed.path, "GET"):
            return
        if parsed.path == "/api/health":
            self.send_json(build_health_status())
            return
        if parsed.path == "/api/scan-jobs":
            params = urllib.parse.parse_qs(parsed.query)
            try:
                limit = min(max(int((params.get("limit") or ["20"])[0]), 1), 100)
            except ValueError:
                limit = 20
            self.send_json(list_scan_jobs(limit=limit))
            return
        if parsed.path.startswith("/api/scan-jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1]
            job = get_scan_job(job_id)
            if job is None:
                self.send_json(
                    {"ok": False, "message": "Scan job was not found."},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            self.send_json(job)
            return
        if parsed.path in ("/", "/dashboard.html"):
            self.send_file(DASHBOARD_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/research", "/research.html"):
            self.send_file(RESEARCH_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/control", "/control.html"):
            self.send_file(CONTROL_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/backtest", "/backtest.html"):
            self.send_file(BACKTEST_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/research-queue", "/research-queue.html"):
            self.send_file(RESEARCH_QUEUE_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/research-findings", "/research-findings.html"):
            self.send_file(RESEARCH_FINDINGS_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/rule-candidates", "/rule-candidates.html"):
            self.send_file(RULE_CANDIDATES_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/rule-validations", "/rule-validations.html"):
            self.send_file(RULE_VALIDATIONS_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/research-intelligence", "/research-intelligence.html"):
            self.send_file(RESEARCH_INTELLIGENCE_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/default-candidates":
            self.send_json({"candidates": DEFAULT_CANDIDATES})
            return
        if parsed.path == "/api/universe-presets":
            self.send_json(list_preset_catalog())
            return
        if parsed.path == "/api/control/summary":
            self.send_json(build_control_summary())
            return
        if parsed.path == "/api/control/self-audit":
            self.send_json(build_horizon_self_audit())
            return
        if parsed.path == "/api/control/patterns":
            self.send_json(execute_pattern_rebuild())
            return
        if parsed.path == "/api/control/attribution":
            self.send_json(get_gate_attribution_summary())
            return
        if parsed.path == "/api/control/gate-alpha":
            self.send_json(get_gate_alpha_summary())
            return
        if parsed.path.startswith("/api/explanation/") or parsed.path.startswith("/api/horizon-trace/"):
            scan_id_text = parsed.path.rsplit("/", 1)[-1]
            try:
                scan_id = int(scan_id_text)
            except ValueError:
                self.send_json(
                    {"ok": False, "message": "Horizon Trace scan_id must be numeric."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            response = get_recommendation_explanation(scan_id)
            if response is None:
                self.send_json(
                    {"ok": False, "message": "Horizon Trace was not found."},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            self.send_json(response)
            return
        if parsed.path == "/api/backtest/runs":
            params = urllib.parse.parse_qs(parsed.query)
            try:
                limit = min(max(int((params.get("limit") or ["20"])[0]), 1), 100)
            except ValueError:
                limit = 20
            self.send_json({"ok": True, "runs": list_backtest_runs(limit=limit)})
            return
        if parsed.path.startswith("/api/backtest/runs/"):
            run_id_text = parsed.path.rsplit("/", 1)[-1]
            try:
                run_id = int(run_id_text)
            except ValueError:
                self.send_json(
                    {"ok": False, "message": "Backtest run id must be numeric."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            result = get_backtest_run(run_id)
            if result is None:
                self.send_json(
                    {"ok": False, "message": "Backtest run was not found."},
                    status=HTTPStatus.NOT_FOUND,
                )
                return
            self.send_json(result)
            return
        if parsed.path == "/api/research-jobs":
            self.send_json({"ok": True, "jobs": list_research_jobs()})
            return
        if parsed.path == "/api/research-jobs/runs":
            params = urllib.parse.parse_qs(parsed.query)
            try:
                limit = min(max(int((params.get("limit") or ["50"])[0]), 1), 200)
            except ValueError:
                limit = 50
            self.send_json({"ok": True, "runs": list_research_job_runs(limit=limit)})
            return
        if parsed.path == "/api/research-findings":
            params = urllib.parse.parse_qs(parsed.query)
            status = (params.get("status") or [None])[0]
            severity = (params.get("severity") or [None])[0]
            finding_type = (params.get("findingType") or params.get("finding_type") or [None])[0]
            try:
                limit = min(max(int((params.get("limit") or ["100"])[0]), 1), 500)
            except ValueError:
                limit = 100
            self.send_json(
                {
                    "ok": True,
                    "findings": list_research_findings(
                        status=status,
                        severity=severity,
                        finding_type=finding_type,
                        limit=limit,
                    ),
                }
            )
            return
        if parsed.path == "/api/rule-candidates":
            params = urllib.parse.parse_qs(parsed.query)
            status = (params.get("status") or [None])[0]
            candidate_type = (params.get("candidateType") or params.get("candidate_type") or [None])[0]
            try:
                limit = min(max(int((params.get("limit") or ["100"])[0]), 1), 500)
            except ValueError:
                limit = 100
            self.send_json(
                {
                    "ok": True,
                    "candidates": list_rule_candidates(
                        status=status,
                        candidate_type=candidate_type,
                        limit=limit,
                    ),
                }
            )
            return
        if parsed.path == "/api/rule-validations":
            params = urllib.parse.parse_qs(parsed.query)
            status = (params.get("status") or [None])[0]
            candidate_id_raw = (params.get("candidateId") or params.get("candidate_id") or [None])[0]
            candidate_id = None
            if candidate_id_raw not in (None, ""):
                try:
                    candidate_id = int(candidate_id_raw)
                except ValueError:
                    self.send_json(
                        {"ok": False, "message": "candidateId must be numeric."},
                        status=HTTPStatus.BAD_REQUEST,
                    )
                    return
            try:
                limit = min(max(int((params.get("limit") or ["100"])[0]), 1), 500)
            except ValueError:
                limit = 100
            self.send_json(
                {
                    "ok": True,
                    "validations": list_rule_validations(
                        status=status,
                        candidate_id=candidate_id,
                        limit=limit,
                    ),
                }
            )
            return
        if parsed.path == "/api/research-intelligence":
            self.send_json(get_research_intelligence_dashboard())
            return
        if parsed.path == "/api/research-daily-reports":
            params = urllib.parse.parse_qs(parsed.query)
            report_type = (params.get("reportType") or params.get("report_type") or [None])[0]
            try:
                limit = min(max(int((params.get("limit") or ["50"])[0]), 1), 500)
            except ValueError:
                limit = 50
            self.send_json(
                {
                    "ok": True,
                    "reports": list_ingested_daily_reports(
                        report_type=report_type,
                        limit=limit,
                    ),
                }
            )
            return
        if parsed.path == "/api/memory/summary":
            self.send_json(build_memory_summary())
            return
        if parsed.path == "/api/memory/history":
            from memory_store import DB_PATH

            params = urllib.parse.parse_qs(parsed.query)
            filters = parse_history_filters(params)
            try:
                limit = min(max(int((params.get("limit") or ["100"])[0]), 1), MAX_HISTORY_PAGE_SIZE)
            except ValueError:
                limit = 100
            try:
                offset = max(int((params.get("offset") or ["0"])[0]), 0)
            except ValueError:
                offset = 0
            payload = build_memory_history_payload(limit=limit, offset=offset, filters=filters)
            print(
                "[dashboard] /api/memory/history "
                f"db={DB_PATH} total={payload.get('total')} "
                f"filtered={payload.get('filteredTotal')} returned={len(payload.get('history') or [])} "
                f"limit={limit} offset={offset} filters={payload.get('debug', {}).get('filters')}",
                flush=True,
            )
            self.send_json(payload)
            return
        if parsed.path == "/api/memory/audit":
            params = urllib.parse.parse_qs(parsed.query)
            try:
                limit = min(max(int((params.get("limit") or ["50"])[0]), 1), 200)
            except ValueError:
                limit = 50
            self.send_json({"ok": True, "outcomeAuditLog": get_outcome_audit_log(limit=limit)})
            return
        if parsed.path == "/api/memory/ticker":
            params = urllib.parse.parse_qs(parsed.query)
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            filters = parse_history_filters({"ticker": [ticker]} if ticker else {})
            payload = build_memory_history_payload(
                limit=MAX_HISTORY_PAGE_SIZE,
                offset=0,
                filters=filters,
            )
            self.send_json(
                {
                    "ok": True,
                    "history": payload["history"],
                    "total": payload.get("total"),
                    "filteredTotal": payload.get("filteredTotal"),
                    "timings": payload.get("timings"),
                }
            )
            return
        if parsed.path == "/api/memory/export.csv":
            params = urllib.parse.parse_qs(parsed.query)
            filters = parse_history_filters(params)
            self.send_text(
                export_csv(filters=filters),
                "text/csv; charset=utf-8",
                extra_headers={
                    "Content-Disposition": 'attachment; filename="scout-memory-export.csv"'
                },
            )
            return
        if parsed.path == "/api/reports/status":
            self.send_json(get_reporting_status(get_reports_dir()))
            return
        if parsed.path == "/api/reports/list":
            params = urllib.parse.parse_qs(parsed.query)
            try:
                limit = min(max(int((params.get("limit") or ["20"])[0]), 1), 200)
            except ValueError:
                limit = 20
            try:
                offset = max(int((params.get("offset") or ["0"])[0]), 0)
            except ValueError:
                offset = 0
            ticker = (params.get("ticker") or [""])[0].strip().upper() or None
            report_type = (params.get("reportType") or [""])[0].strip() or None
            session_id = (params.get("scanSessionId") or [""])[0].strip() or None
            store = get_report_service(get_reports_dir()).store
            payload = store.list_reports(
                limit=limit,
                offset=offset,
                ticker=ticker,
                report_type=report_type,
                scan_session_id=session_id,
            )
            self.send_json({"ok": True, **payload})
            return
        if parsed.path == "/api/reports/jobs":
            params = urllib.parse.parse_qs(parsed.query)
            try:
                limit = min(max(int((params.get("limit") or ["20"])[0]), 1), 200)
            except ValueError:
                limit = 20
            try:
                offset = max(int((params.get("offset") or ["0"])[0]), 0)
            except ValueError:
                offset = 0
            status = (params.get("status") or [""])[0].strip() or None
            batch_id = (params.get("batchId") or [""])[0].strip() or None
            store = get_report_service(get_reports_dir()).store
            self.send_json(
                {
                    "ok": True,
                    **store.list_jobs(
                        limit=limit,
                        offset=offset,
                        status=status,
                        batch_id=batch_id,
                    ),
                }
            )
            return
        if parsed.path.startswith("/api/reports/jobs/"):
            job_id = parsed.path.rsplit("/", 1)[-1].strip()
            if not job_id or not re.fullmatch(r"[a-f0-9]{32}", job_id):
                self.send_json(
                    {"ok": False, "message": "Invalid job id."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            self.send_json(get_report_service(get_reports_dir()).get_job(job_id))
            return
        if parsed.path.startswith("/api/reports/download/"):
            filename = parsed.path.rsplit("/", 1)[-1]
            if not SAFE_REPORT_NAME.match(filename):
                self.send_error(HTTPStatus.BAD_REQUEST, "Invalid report filename")
                return
            reports_dir = get_reports_dir()
            report_path = (reports_dir / filename).resolve()
            if report_path.parent != reports_dir.resolve() or not report_path.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Report not found")
                return
            self.send_file(report_path, "application/pdf")
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if self.reject_hosted_maintenance(parsed.path, "POST"):
            return
        if parsed.path == "/api/memory/update-outcomes":
            try:
                payload = self.read_json()
                response = update_outcomes(
                    limit=int(payload.get("limit") or 250),
                    timeout=float(payload.get("timeout") or 25),
                )
                self.send_json(response)
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Outcome update error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/memory/create-outcome-test-record":
            try:
                payload = self.read_json()
                response = create_outcome_test_record(
                    ticker=str(payload.get("ticker") or "").strip().upper() or None,
                    days_old=int(payload.get("daysOld") or 30),
                )
                self.send_json(response)
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Outcome test record error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path == "/api/research-findings/generate-recent":
            try:
                payload = self.read_json()
                limit = int(payload.get("limit") or 20)
                self.send_json(generate_findings_from_recent_runs(limit=limit))
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Research findings generation error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path.startswith("/api/research-findings/") and parsed.path.endswith("/status"):
            finding_id_text = parsed.path.split("/")[-2]
            try:
                finding_id = int(finding_id_text)
            except ValueError:
                self.send_json(
                    {"ok": False, "message": "Research finding id must be numeric."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                payload = self.read_json()
                status = str(payload.get("status") or "").strip()
                self.send_json(update_research_finding_status(finding_id, status))
            except ValueError as exc:
                self.send_json(
                    {"ok": False, "message": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Research finding status error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/rule-candidates/generate-open-findings":
            try:
                payload = self.read_json()
                limit = int(payload.get("limit") or 20)
                self.send_json(generate_rule_candidates_from_open_findings(limit=limit))
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Rule candidate generation error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path.startswith("/api/rule-candidates/") and parsed.path.endswith("/status"):
            candidate_id_text = parsed.path.split("/")[-2]
            try:
                candidate_id = int(candidate_id_text)
            except ValueError:
                self.send_json(
                    {"ok": False, "message": "Rule candidate id must be numeric."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                payload = self.read_json()
                status = str(payload.get("status") or "").strip()
                result = update_rule_candidate_status(candidate_id, status)
                if result.get("ok") and status == "testing":
                    validation_result = create_rule_validation(candidate_id)
                    result["validationCreated"] = bool(validation_result.get("created"))
                    if validation_result.get("validation"):
                        result["validation"] = validation_result["validation"]
                self.send_json(result)
            except ValueError as exc:
                self.send_json(
                    {"ok": False, "message": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Rule candidate status error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/research-daily-reports/ingest":
            try:
                payload = self.read_json()
                limit = int(payload.get("limit") or 50)
                since = payload.get("since")
                self.send_json(
                    ingest_scout_reports(
                        limit=limit,
                        since=str(since).strip() if since else None,
                    )
                )
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Daily report ingest error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path == "/api/research-daily-reports/generate-findings":
            try:
                payload = self.read_json()
                limit = int(payload.get("limit") or 20)
                self.send_json(generate_findings_from_daily_reports(limit=limit))
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Daily report findings error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path == "/api/rule-validations/run-pending":
            try:
                payload = self.read_json()
                generate_limit = int(payload.get("generateLimit") or payload.get("limit") or 50)
                run_limit = int(payload.get("runLimit") or payload.get("limit") or 20)
                self.send_json(
                    generate_and_run_pending_validations(
                        generate_limit=generate_limit,
                        run_limit=run_limit,
                    )
                )
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Rule validation run error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path.startswith("/api/rule-validations/") and parsed.path.endswith("/run"):
            candidate_id_text = parsed.path.split("/")[-2]
            try:
                candidate_id = int(candidate_id_text)
            except ValueError:
                self.send_json(
                    {"ok": False, "message": "Rule candidate id must be numeric."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                self.send_json(run_rule_validation(candidate_id))
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Rule validation error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/research-jobs/defaults":
            try:
                self.send_json(create_default_research_jobs())
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Research job defaults error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/research-jobs/run-enabled":
            try:
                self.send_json(run_enabled_research_jobs())
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Enabled research job run error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path.startswith("/api/research-jobs/") and parsed.path.endswith("/run"):
            job_id_text = parsed.path.split("/")[-2]
            try:
                job_id = int(job_id_text)
            except ValueError:
                self.send_json(
                    {"ok": False, "message": "Research job id must be numeric."},
                    status=HTTPStatus.BAD_REQUEST,
                )
                return
            try:
                self.send_json(run_research_job(job_id))
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Research job run error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/backtest/preview":
            try:
                payload = self.read_json()
                filters = parse_backtest_filters(payload.get("filters") if isinstance(payload.get("filters"), dict) else payload)
                self.send_json(preview_backtest(filters))
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Backtest preview error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path == "/api/backtest/run":
            try:
                payload = self.read_json()
                filters_payload = payload.get("filters") if isinstance(payload.get("filters"), dict) else payload
                response = run_backtest(
                    name=str(payload.get("name") or "Research Backtest"),
                    description=str(payload.get("description") or "").strip() or None,
                    filters=parse_backtest_filters(filters_payload),
                )
                self.send_json(response)
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Backtest run error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path == "/api/control/backfill":
            try:
                self.send_json(execute_horizon_backfill())
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Horizon backfill error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/control/gate-alpha":
            try:
                self.send_json(execute_gate_alpha_rebuild())
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Gate Alpha rebuild error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/control/gate-alpha/test-record":
            try:
                self.send_json(execute_gate_alpha_test_bridge())
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Gate Alpha test record error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path == "/api/control/regime-intelligence":
            try:
                self.send_json(execute_regime_intelligence_rebuild())
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Regime Intelligence error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/run/save":
            try:
                payload = self.read_json()
                scan_payload = payload.get("scan") if isinstance(payload.get("scan"), dict) else payload
                self.send_json(save_scan_result_once(scan_payload))
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Save Results error: {exc}"},
                    status=HTTPStatus.BAD_REQUEST,
                )
            return

        if parsed.path == "/api/reports/export-pdf":
            try:
                payload = self.read_json()
                scan_payload = payload.get("scan") if isinstance(payload.get("scan"), dict) else payload
                ticker = str(payload.get("ticker") or "").strip().upper() or None
                async_mode = payload.get("async")
                if async_mode is None:
                    async_mode = DEFAULT_ASYNC_EXPORT
                else:
                    async_mode = async_mode in (True, "true", "1", 1)
                service = get_report_service(get_reports_dir())
                batch_tickers = payload.get("tickers")
                if isinstance(batch_tickers, list) and batch_tickers:
                    response = service.export_batch(
                        scan_payload,
                        tickers=[str(item) for item in batch_tickers],
                        async_mode=async_mode,
                    )
                else:
                    response = service.export(
                        scan_payload,
                        ticker=ticker,
                        async_mode=async_mode,
                    )
                self.send_json(response)
            except ValueError as exc:
                self.send_json(
                    {"ok": False, "message": str(exc)},
                    status=HTTPStatus.BAD_REQUEST,
                )
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"PDF export error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path == "/api/scan-jobs":
            try:
                payload = self.read_json()
                self.send_json(create_scan_job(payload), status=HTTPStatus.ACCEPTED)
            except ValueError as exc:
                self.send_json({"ok": False, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
            except Exception as exc:
                self.send_json(
                    {"ok": False, "message": f"Scan job error: {exc}"},
                    status=HTTPStatus.INTERNAL_SERVER_ERROR,
                )
            return

        if parsed.path != "/api/run":
            self.send_error(HTTPStatus.NOT_FOUND, "Not found")
            return

        try:
            payload = self.read_json()
            response = build_run_payload(payload)
            status = HTTPStatus.OK if response.get("ok") else HTTPStatus.BAD_GATEWAY
            self.send_json(response, status=status)
        except ValueError as exc:
            self.send_json({"ok": False, "message": str(exc)}, status=HTTPStatus.BAD_REQUEST)
        except Exception as exc:  # Keep local beta dashboard from crashing the server.
            self.send_json(
                {"ok": False, "message": f"Dashboard error: {exc}"},
                status=HTTPStatus.INTERNAL_SERVER_ERROR,
            )

    def read_json(self) -> dict[str, Any]:
        content_length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(content_length).decode("utf-8")
        if not body:
            return {}
        payload = json.loads(body)
        if not isinstance(payload, dict):
            raise ValueError("Request body must be a JSON object.")
        return payload

    def send_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_json(self, payload: dict[str, Any], status: HTTPStatus = HTTPStatus.OK) -> None:
        data = json.dumps(payload, indent=2).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def send_text(
        self,
        text: str,
        content_type: str,
        status: HTTPStatus = HTTPStatus.OK,
        extra_headers: Optional[dict[str, str]] = None,
    ) -> None:
        data = text.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        for key, value in (extra_headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[dashboard] {self.address_string()} - {format % args}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run Scout's gate sandbox dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Bind host address.")
    parser.add_argument("--port", type=int, default=8765, help="Dashboard port.")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the dashboard in the default browser.",
    )
    parser.add_argument(
        "--hosted",
        action="store_true",
        help="Hosted production mode (sets SCOUT_HOSTED_MODE, implies --no-open).",
    )
    return parser


def apply_cli_hosted_mode(args: argparse.Namespace) -> None:
    if args.hosted:
        os.environ[HOSTED_MODE_ENV] = "1"
        args.no_open = True


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    apply_cli_hosted_mode(args)
    reports_dir = get_reports_dir()
    ensure_report_worker(ReportConfig(exports_dir=reports_dir))
    recover_interrupted_scan_jobs()
    ensure_scan_worker()
    address = (args.host, args.port)
    server = ThreadingHTTPServer(address, DashboardHandler)

    log_startup_config(args.host, args.port)

    if not args.no_open:
        url = f"http://{args.host}:{args.port}"
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping dashboard.")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
