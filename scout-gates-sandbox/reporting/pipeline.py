#!/usr/bin/env python3
"""PDF export pipeline orchestration."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional
from uuid import uuid4

from reporting.config import REPORT_TYPE_SCORING_BREAKDOWN, ReportConfig, default_exports_dir
from reporting.pdf_renderer import chrome_available, write_html_and_render_pdf
from reporting.registry_store import get_registry_store
from reporting.scoring_breakdown import (
    build_pdf_filename,
    build_report_context,
    render_report_html,
)

ProgressCallback = Callable[[str, list[str]], None]


class ReportPipeline:
    """Runs validate → context → HTML → PDF → registry stages."""

    STAGES = ("validate", "build_context", "render_html", "render_pdf", "register")

    def __init__(self, config: Optional[ReportConfig] = None) -> None:
        self.config = config or ReportConfig(exports_dir=default_exports_dir())
        self.store = get_registry_store(self.config.exports_dir)

    def run(
        self,
        scan_payload: dict[str, Any],
        *,
        report_type: str = REPORT_TYPE_SCORING_BREAKDOWN,
        ticker: Optional[str] = None,
        job_id: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
    ) -> dict[str, Any]:
        completed: list[str] = []

        def progress(stage: str) -> None:
            completed.append(stage)
            if on_progress:
                on_progress(stage, list(completed))

        if report_type != REPORT_TYPE_SCORING_BREAKDOWN:
            raise ValueError(f"Unsupported report type: {report_type}")
        if not scan_payload.get("ok"):
            raise ValueError(scan_payload.get("message") or "Scan payload is not exportable.")
        progress("validate")

        context = build_report_context(scan_payload, ticker=ticker)
        progress("build_context")

        html_content = render_report_html(context)
        progress("render_html")

        filename = build_pdf_filename(context)
        pdf_path = self.config.exports_dir / filename
        html_path = self.config.exports_dir / f"{pdf_path.stem}.html"
        write_html_and_render_pdf(html_content, html_path, pdf_path)
        if not self.config.keep_html_artifacts and html_path.is_file():
            html_path.unlink()
        progress("render_pdf")

        report_id = uuid4().hex
        idempotency_key = self.store.build_idempotency_key(
            report_type,
            str(context["scan_session_id"]),
            str(context["ticker"]),
        )
        entry = self.store.insert_report(
            report_id=report_id,
            job_id=job_id,
            report_type=report_type,
            filename=filename,
            ticker=str(context["ticker"]),
            scan_session_id=str(context["scan_session_id"]),
            run_timestamp=str(context["run_timestamp"]),
            generated_at=str(context["generated_at"]),
            file_size_bytes=pdf_path.stat().st_size,
            relative_path=f"{self.config.relative_exports_path}/{filename}",
            download_url=f"/api/reports/download/{filename}",
            pipeline_stages=completed + ["register"],
            idempotency_key=idempotency_key,
        )
        progress("register")

        return {
            "ok": True,
            "reportId": report_id,
            "jobId": job_id,
            "reportType": report_type,
            "filename": filename,
            "path": str(pdf_path.resolve()),
            "relativePath": entry["relativePath"],
            "downloadUrl": entry["downloadUrl"],
            "scanSessionId": context["scan_session_id"],
            "ticker": context["ticker"],
            "generatedAt": context["generated_at"],
            "pipelineStages": completed,
            "registryEntry": entry,
            "reportContext": {
                "finalScore": context["final_score"],
                "peer": context["peer"],
                "percentiles": context["percentiles"],
            },
        }


def generate_scoring_report_pdf(
    scan_payload: dict[str, Any],
    exports_dir: Optional[Path] = None,
    ticker: Optional[str] = None,
) -> dict[str, Any]:
    from reporting.service import get_report_service

    return get_report_service(exports_dir).export(
        scan_payload,
        ticker=ticker,
        async_mode=False,
    )


def get_reporting_status(exports_dir: Optional[Path] = None) -> dict[str, Any]:
    from reporting.jobs import get_report_worker

    target = exports_dir or default_exports_dir()
    store = get_registry_store(target)
    config = ReportConfig(exports_dir=target)
    worker = get_report_worker(config)
    queue = store.queue_stats()
    listing = store.list_reports(limit=1, offset=0)
    return {
        "ok": True,
        "exportsDir": str(target.resolve()),
        "relativeExportsPath": config.relative_exports_path,
        "registryDb": str(store.db_path.resolve()),
        "chromeAvailable": chrome_available(),
        "workerRunning": worker.running,
        "supportedReportTypes": [REPORT_TYPE_SCORING_BREAKDOWN],
        "reportCount": int(listing["total"]),
        "queue": queue,
        "queuedJobs": queue.get("queued", 0),
        "runningJobs": queue.get("running", 0),
    }
