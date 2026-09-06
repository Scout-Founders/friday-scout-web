#!/usr/bin/env python3
"""Scheduled research runner for Scout Horizon.

Read-only orchestration only. This script initializes Scout memory, ensures default
research jobs exist, optionally ingests Firestore daily reports, runs enabled research
jobs, and generates research findings. It never places trades, changes scoring,
gate weights, Stable Signal behavior, or recommendation logic.

Schedule this script from the command line without opening the dashboard UI.

macOS launchd example (run daily at 6:00 AM local time):

    # ~/Library/LaunchAgents/com.scout.scheduled-research.plist
    # ProgramArguments: /usr/bin/python3
    #                   /path/to/scout-gates-sandbox/scheduled_research_runner.py
    # WorkingDirectory: /path/to/scout-gates-sandbox
    # StandardOutPath / StandardErrorPath: logs/scheduled-research.log
    # StartCalendarInterval: Hour=6 Minute=0

cron example (run weekly on Monday at 6:00 AM):

    0 6 * * 1 cd /path/to/scout-gates-sandbox && python3 scheduled_research_runner.py >> logs/scheduled-research.log 2>&1
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from cloud_research_worker import (
    configure_cloud_research_database,
    format_cloud_worker_validation_errors,
    validate_cloud_worker_environment,
)
from memory_store import init_db
from research_job_runner import create_default_research_jobs, run_enabled_research_jobs


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_findings_generator() -> Optional[Callable[..., dict[str, Any]]]:
    try:
        from research_findings_engine import generate_findings_from_recent_runs

        return generate_findings_from_recent_runs
    except ImportError:
        return None


def generate_findings_if_available(*, limit: int = 20) -> dict[str, Any]:
    generator = _load_findings_generator()
    if generator is None:
        return {
            "ok": True,
            "available": False,
            "generated": 0,
            "skippedDuplicates": 0,
            "message": "Research Findings Engine not available.",
        }

    try:
        result = generator(limit=limit)
        result["available"] = True
        return result
    except Exception as exc:
        return {
            "ok": False,
            "available": True,
            "generated": 0,
            "skippedDuplicates": 0,
            "message": str(exc),
        }


def ingest_daily_reports_if_available(*, limit: int = 50) -> dict[str, Any]:
    try:
        from ingest_scout_reports import ingest_scout_reports
    except ImportError:
        return {
            "ok": True,
            "available": False,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "message": "daily report ingest unavailable",
        }

    try:
        return ingest_scout_reports(limit=limit)
    except Exception as exc:
        return {
            "ok": True,
            "available": False,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "message": f"daily report ingest unavailable: {exc}",
            "errors": [str(exc)],
        }


def ingest_daily_reports_from_bundle_if_available(
    bundle_dir: Optional[str | Path] = None,
) -> dict[str, Any]:
    try:
        from ingest_scout_reports import ingest_scout_reports_from_bundle_dir
    except ImportError:
        return {
            "ok": True,
            "available": False,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "message": "daily report artifact ingest unavailable",
        }

    resolved_dir = str(bundle_dir or os.environ.get("SCOUT_DAILY_REPORTS_BUNDLE_DIR", "")).strip()
    if not resolved_dir:
        return {
            "ok": True,
            "available": False,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "message": "daily report artifact bundle not configured",
        }

    bundle_path = Path(resolved_dir).expanduser()
    if not bundle_path.exists():
        return {
            "ok": True,
            "available": False,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "message": f"daily report artifact bundle not found: {bundle_path}",
        }

    try:
        result = ingest_scout_reports_from_bundle_dir(bundle_path)
        result["available"] = True
        return result
    except Exception as exc:
        return {
            "ok": False,
            "available": True,
            "imported": 0,
            "updated": 0,
            "skipped": 0,
            "message": f"daily report artifact ingest failed: {exc}",
            "errors": [str(exc)],
        }


def generate_daily_report_findings_if_available(*, limit: int = 20) -> dict[str, Any]:
    try:
        from ingest_scout_reports import generate_findings_from_daily_reports
    except ImportError:
        return {
            "ok": True,
            "available": False,
            "generated": 0,
            "skippedDuplicates": 0,
            "message": "daily report findings adapter unavailable",
        }

    try:
        result = generate_findings_from_daily_reports(limit=limit)
        result["available"] = True
        return result
    except Exception as exc:
        return {
            "ok": False,
            "available": True,
            "generated": 0,
            "skippedDuplicates": 0,
            "message": str(exc),
        }


def run_scheduled_research(
    *,
    findings_limit: int = 20,
    generate_findings: bool = True,
    ingest_reports: bool = True,
    report_ingest_limit: int = 50,
    report_bundle_dir: Optional[str | Path] = None,
    skip_firestore_ingest: bool = False,
) -> dict[str, Any]:
    """Run the scheduled research workflow and return a structured summary."""
    summary: dict[str, Any] = {
        "ok": True,
        "timestamp": utc_now_iso(),
        "defaultsCreated": 0,
        "jobsRun": 0,
        "completed": 0,
        "failed": 0,
        "findingsGenerated": 0,
        "findingsSkipped": 0,
        "findingsAvailable": False,
        "dailyReportsImported": 0,
        "dailyReportsUpdated": 0,
        "dailyReportFindingsGenerated": 0,
        "dailyReportIngestAvailable": False,
        "dailyReportIngestMode": None,
        "errors": [],
    }

    try:
        init_db()
    except Exception as exc:
        summary["ok"] = False
        summary["errors"].append(f"Database initialization failed: {exc}")
        return summary

    if ingest_reports:
        bundle_result = ingest_daily_reports_from_bundle_if_available(report_bundle_dir)
        if bundle_result.get("available"):
            ingest_result = bundle_result
        elif skip_firestore_ingest:
            ingest_result = {
                "ok": True,
                "available": False,
                "imported": 0,
                "updated": 0,
                "skipped": 0,
                "message": "daily report artifact ingest unavailable; Firestore ingest skipped",
            }
        else:
            ingest_result = ingest_daily_reports_if_available(limit=report_ingest_limit)

        summary["dailyReportIngestAvailable"] = bool(ingest_result.get("available"))
        summary["dailyReportIngestMode"] = ingest_result.get("ingestMode")
        summary["dailyReportsImported"] = int(ingest_result.get("imported") or 0)
        summary["dailyReportsUpdated"] = int(ingest_result.get("updated") or 0)
        if ingest_result.get("message") and not ingest_result.get("available"):
            print(
                f"[scheduled-research] {ingest_result.get('message')}",
                file=sys.stderr,
            )
        if ingest_result.get("available") and not ingest_result.get("ok"):
            summary["ok"] = False
            summary["errors"].append(
                f"Daily report ingest failed: {ingest_result.get('message') or 'unknown error'}"
            )

    try:
        defaults = create_default_research_jobs()
        summary["defaultsCreated"] = int(defaults.get("created") or 0)
    except Exception as exc:
        summary["ok"] = False
        summary["errors"].append(f"Default research jobs failed: {exc}")
        return summary

    try:
        batch = run_enabled_research_jobs()
        summary["jobsRun"] = int(batch.get("ran") or 0)
        summary["completed"] = int(batch.get("completed") or 0)
        summary["failed"] = int(batch.get("failed") or 0)
        if summary["failed"] > 0:
            summary["ok"] = False
            for result in batch.get("results") or []:
                if result.get("ok"):
                    continue
                job_name = (result.get("job") or {}).get("name") or "unknown job"
                message = result.get("message") or (result.get("run") or {}).get("errorMessage")
                summary["errors"].append(f"Job failed ({job_name}): {message or 'unknown error'}")
    except Exception as exc:
        summary["ok"] = False
        summary["errors"].append(f"Enabled research jobs failed: {exc}")

    if generate_findings:
        findings = generate_findings_if_available(limit=findings_limit)
        summary["findingsAvailable"] = bool(findings.get("available"))
        summary["findingsGenerated"] = int(findings.get("generated") or 0)
        summary["findingsSkipped"] = int(findings.get("skippedDuplicates") or 0)
        if findings.get("available") and not findings.get("ok"):
            summary["ok"] = False
            summary["errors"].append(
                f"Research findings generation failed: {findings.get('message') or 'unknown error'}"
            )

        report_findings = generate_daily_report_findings_if_available(limit=findings_limit)
        summary["dailyReportFindingsGenerated"] = int(report_findings.get("generated") or 0)
        summary["findingsGenerated"] += int(report_findings.get("generated") or 0)
        summary["findingsSkipped"] += int(report_findings.get("skippedDuplicates") or 0)
        if report_findings.get("available") and not report_findings.get("ok"):
            summary["ok"] = False
            summary["errors"].append(
                f"Daily report findings generation failed: {report_findings.get('message') or 'unknown error'}"
            )

    return summary


def format_scheduled_research_summary(summary: dict[str, Any]) -> str:
    lines = [
        "Scout scheduled research run",
        f"timestamp: {summary.get('timestamp')}",
        f"jobs run: {summary.get('jobsRun', 0)}",
        f"completed: {summary.get('completed', 0)}",
        f"failed: {summary.get('failed', 0)}",
        f"findings generated: {summary.get('findingsGenerated', 0)}",
    ]
    defaults_created = int(summary.get("defaultsCreated") or 0)
    if defaults_created:
        lines.append(f"default jobs created: {defaults_created}")
    database_path = summary.get("databasePath")
    if database_path:
        lines.append(f"database: {database_path}")
    lines.append(
        "daily reports imported: "
        f"{summary.get('dailyReportsImported', 0)} "
        f"(updated: {summary.get('dailyReportsUpdated', 0)})"
    )
    lines.append(
        f"daily report findings generated: {summary.get('dailyReportFindingsGenerated', 0)}"
    )
    if not summary.get("dailyReportIngestAvailable"):
        lines.append("daily report ingest: unavailable")
    elif summary.get("dailyReportIngestMode"):
        lines.append(f"daily report ingest mode: {summary.get('dailyReportIngestMode')}")
    findings_skipped = int(summary.get("findingsSkipped") or 0)
    if findings_skipped:
        lines.append(f"findings skipped (duplicates): {findings_skipped}")
    if not summary.get("findingsAvailable"):
        lines.append("findings engine: unavailable")
    for error in summary.get("errors") or []:
        lines.append(f"error: {error}")
    status = "ok" if summary.get("ok") else "failed"
    lines.append(f"status: {status}")
    return "\n".join(lines)


def log_errors(summary: dict[str, Any], *, stream: Any = None) -> None:
    target = stream or sys.stderr
    for error in summary.get("errors") or []:
        print(f"[scheduled-research] ERROR: {error}", file=target)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run enabled Scout research jobs and generate findings from recent runs. "
            "Read-only orchestration only."
        )
    )
    parser.add_argument(
        "--findings-limit",
        type=int,
        default=20,
        help="Number of recent completed runs to scan for findings (default: 20).",
    )
    parser.add_argument(
        "--skip-findings",
        action="store_true",
        help="Run research jobs only; skip findings generation.",
    )
    parser.add_argument(
        "--skip-report-ingest",
        action="store_true",
        help="Skip daily report ingest (Firestore and artifact bundle).",
    )
    parser.add_argument(
        "--skip-firestore-ingest",
        action="store_true",
        help=(
            "Skip direct Firestore ingest. Artifact bundle ingest still runs when "
            "--from-bundle-dir or SCOUT_DAILY_REPORTS_BUNDLE_DIR is configured."
        ),
    )
    parser.add_argument(
        "--from-bundle-dir",
        dest="from_bundle_dir",
        default=None,
        help="Import scout-daily-reports artifact bundle before running research jobs.",
    )
    parser.add_argument(
        "--report-ingest-limit",
        type=int,
        default=50,
        help="Max Firestore scout_reports documents to ingest when Firestore fallback is used.",
    )
    parser.add_argument(
        "--cloud-worker",
        action="store_true",
        help=(
            "Validate GitHub Actions cloud worker secrets before running. "
            "Use from the Scout Cloud Research Worker workflow."
        ),
    )
    return parser


def ensure_cloud_worker_ready() -> dict[str, Any]:
    result = validate_cloud_worker_environment()
    if not result.get("ok"):
        return {
            "ok": False,
            "timestamp": utc_now_iso(),
            "jobsRun": 0,
            "completed": 0,
            "failed": 0,
            "findingsGenerated": 0,
            "errors": list(result.get("errors") or []),
        }
    db_path = configure_cloud_research_database()
    return {"ok": True, "databasePath": str(db_path)}


def main(argv: Optional[list[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.cloud_worker:
        cloud_result = ensure_cloud_worker_ready()
        if not cloud_result.get("ok"):
            print(format_cloud_worker_validation_errors(cloud_result), file=sys.stderr)
            for error in cloud_result.get("errors") or []:
                print(f"[scheduled-research] ERROR: {error}", file=sys.stderr)
            return 2
        cloud_db_path = cloud_result.get("databasePath")
    else:
        cloud_db_path = None
    summary = run_scheduled_research(
        findings_limit=max(int(args.findings_limit), 1),
        generate_findings=not args.skip_findings,
        ingest_reports=not args.skip_report_ingest,
        report_ingest_limit=max(int(args.report_ingest_limit), 1),
        report_bundle_dir=args.from_bundle_dir,
        skip_firestore_ingest=bool(args.skip_firestore_ingest or args.cloud_worker),
    )
    if cloud_db_path:
        summary["databasePath"] = cloud_db_path
    if summary.get("errors"):
        log_errors(summary)
    print(format_scheduled_research_summary(summary))
    return 0 if summary.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
