#!/usr/bin/env python3
"""CLI for Scout sandbox PDF report generation."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from reporting.config import ReportConfig, default_exports_dir
from reporting.jobs import ensure_report_worker
from reporting.pipeline import get_reporting_status
from reporting.service import get_report_service


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scout sandbox PDF reporting")
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Generate a scoring breakdown PDF from scan JSON")
    export.add_argument("scan_json", type=Path, help="Path to scan payload JSON file")
    export.add_argument("--ticker", default=None, help="Ticker to export (default: final pick)")
    export.add_argument("--output-dir", default=None, help="Override exports directory")
    export.add_argument("--async", action="store_true", help="Queue job for background worker")

    sub.add_parser("status", help="Show reporting infrastructure status")
    list_cmd = sub.add_parser("list", help="List registered reports")
    list_cmd.add_argument("--limit", type=int, default=20)
    list_cmd.add_argument("--offset", type=int, default=0)
    list_cmd.add_argument("--ticker", default=None)

    jobs_cmd = sub.add_parser("jobs", help="List export jobs")
    jobs_cmd.add_argument("--limit", type=int, default=20)
    jobs_cmd.add_argument("--offset", type=int, default=0)
    jobs_cmd.add_argument("--status", default=None)

    return parser.parse_args()


def main() -> int:
    args = parse_args()
    exports_dir = default_exports_dir()
    if getattr(args, "output_dir", None):
        exports_dir = Path(args.output_dir).expanduser()

    config = ReportConfig(exports_dir=exports_dir)
    service = get_report_service(exports_dir)

    if args.command == "status":
        ensure_report_worker(config)
        print(json.dumps(get_reporting_status(exports_dir), indent=2))
        return 0

    if args.command == "list":
        payload = service.store.list_reports(
            limit=args.limit,
            offset=args.offset,
            ticker=args.ticker,
        )
        print(json.dumps({"ok": True, **payload}, indent=2))
        return 0

    if args.command == "jobs":
        payload = service.store.list_jobs(
            limit=args.limit,
            offset=args.offset,
            status=args.status,
        )
        print(json.dumps({"ok": True, **payload}, indent=2))
        return 0

    if args.command == "export":
        scan_payload = json.loads(args.scan_json.read_text(encoding="utf-8"))
        if args.async:
            ensure_report_worker(config).notify()
        result = service.export(
            scan_payload,
            ticker=args.ticker,
            async_mode=bool(args.async),
        )
        print(json.dumps(result, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
