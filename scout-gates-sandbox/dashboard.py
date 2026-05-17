#!/usr/bin/env python3
"""Local-only visual dashboard for the Scout gate sandbox runner."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.parse
import webbrowser
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from directionality import build_directional_breakdown
from explainability import build_explanation
from memory_store import (
    create_outcome_test_record,
    create_gate_alpha_test_record,
    export_csv,
    get_control_summary,
    get_direction_accuracy,
    get_gate_attribution_summary,
    get_gate_alpha_summary,
    get_gate_intelligence_metrics,
    get_gate_statistics,
    get_horizon_self_audit,
    get_option_pick_history,
    get_outcome_analytics,
    get_outcome_audit_log,
    get_recommendation_explanation,
    get_ticker_history,
    get_top_gate_failures,
    rebuild_regime_intelligence,
    run_horizon_backfill,
    rebuild_gate_alpha,
    rebuild_patterns,
    save_scan_result,
)
from option_picker import choose_option_contract, fmp_api_key
from performance_tracker import update_outcomes
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


SANDBOX_DIR = Path(__file__).resolve().parent
DASHBOARD_HTML = SANDBOX_DIR / "dashboard.html"
RESEARCH_HTML = SANDBOX_DIR / "research.html"
CONTROL_HTML = SANDBOX_DIR / "control.html"


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
) -> dict[str, Any]:
    return {
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
        "raw": result.data,
    }


def pick_winner(results: list[CandidateResult], pick_mode: str) -> CandidateResult:
    if pick_mode == "score_only":
        return max(results, key=lambda result: result.score)
    return choose_final_pick(results)


def build_run_payload(request_payload: dict[str, Any]) -> dict[str, Any]:
    universe_mode = str(request_payload.get("universeMode") or "custom")
    raw_tickers = str(request_payload.get("tickers") or "")
    pick_mode = str(request_payload.get("pickMode") or "gate_runner")
    timeout = float(request_payload.get("timeout") or 25)
    run_timestamp = datetime.now(timezone.utc).isoformat()

    candidates = (
        DEFAULT_CANDIDATES
        if universe_mode == "fallback"
        else parse_ticker_list(raw_tickers)
    )
    if not candidates:
        raise ValueError("Enter at least one ticker or choose the fallback universe.")

    api_url = gate_api_url()
    results: list[CandidateResult] = []
    errors: list[str] = []

    for ticker in candidates:
        try:
            results.append(fetch_gate_result(api_url, ticker, timeout))
        except RuntimeError as exc:
            errors.append(str(exc))

    if not results:
        return {
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

    winner = pick_winner(results, pick_mode)
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
        ),
        "rejected": rejected,
        "results": [
            serialize_result(
                result,
                option_picks.get(result.ticker),
                explanations.get(result.ticker),
                direction_breakdowns.get(result.ticker),
            )
            for result in results
        ],
        "optionPicks": option_picks,
        "explanations": explanations,
        "directionBreakdowns": direction_breakdowns,
        "errors": errors,
    }
    payload["memoryRunId"] = save_scan_result(payload)
    return payload


def build_memory_summary() -> dict[str, Any]:
    return {
        "ok": True,
        "recentScans": get_ticker_history(limit=100),
        "gateStatistics": get_gate_statistics(),
        "gateIntelligence": get_gate_intelligence_metrics(),
        "directionAccuracy": get_direction_accuracy(),
        "topGateFailures": get_top_gate_failures(),
        "optionPickHistory": get_option_pick_history(),
        "outcomeAnalytics": get_outcome_analytics(),
        "outcomeAuditLog": get_outcome_audit_log(),
    }


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

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path in ("/", "/dashboard.html"):
            self.send_file(DASHBOARD_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/research", "/research.html"):
            self.send_file(RESEARCH_HTML, "text/html; charset=utf-8")
            return
        if parsed.path in ("/control", "/control.html"):
            self.send_file(CONTROL_HTML, "text/html; charset=utf-8")
            return
        if parsed.path == "/api/default-candidates":
            self.send_json({"candidates": DEFAULT_CANDIDATES})
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
        if parsed.path == "/api/memory/summary":
            self.send_json(build_memory_summary())
            return
        if parsed.path == "/api/memory/ticker":
            params = urllib.parse.parse_qs(parsed.query)
            ticker = (params.get("ticker") or [""])[0].strip().upper()
            self.send_json({"ok": True, "history": get_ticker_history(ticker or None)})
            return
        if parsed.path == "/api/memory/export.csv":
            self.send_text(
                export_csv(),
                "text/csv; charset=utf-8",
                extra_headers={
                    "Content-Disposition": 'attachment; filename="scout-memory-export.csv"'
                },
            )
            return
        self.send_error(HTTPStatus.NOT_FOUND, "Not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
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
    parser = argparse.ArgumentParser(description="Run Scout's local beta gate dashboard.")
    parser.add_argument("--host", default="127.0.0.1", help="Local bind host.")
    parser.add_argument("--port", type=int, default=8765, help="Local dashboard port.")
    parser.add_argument(
        "--no-open",
        action="store_true",
        help="Do not open the dashboard in the default browser.",
    )
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    address = (args.host, args.port)
    server = ThreadingHTTPServer(address, DashboardHandler)
    url = f"http://{args.host}:{args.port}"

    print("Scout gate sandbox dashboard")
    print(f"Local URL: {url}")
    print("Press Ctrl+C to stop.")

    if not args.no_open:
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
