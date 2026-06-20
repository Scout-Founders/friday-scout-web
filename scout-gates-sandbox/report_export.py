#!/usr/bin/env python3
"""Backward-compatible entry point for PDF report export."""

from reporting import (
    ReportService,
    build_report_context,
    default_exports_dir,
    ensure_report_worker,
    export_report_json,
    generate_scoring_report_pdf,
    get_report_service,
    get_reporting_status,
    list_reports,
    resolve_scan_session_id,
)

__all__ = [
    "ReportService",
    "build_report_context",
    "default_exports_dir",
    "ensure_report_worker",
    "export_report_json",
    "generate_scoring_report_pdf",
    "get_report_service",
    "get_reporting_status",
    "list_reports",
    "resolve_scan_session_id",
]
