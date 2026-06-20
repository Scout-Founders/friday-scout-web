#!/usr/bin/env python3
"""Facade over the SQLite report registry."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

from reporting.config import ReportConfig, default_exports_dir
from reporting.registry_store import get_registry_store


def new_report_id() -> str:
    return uuid4().hex


def register_report(
    config: ReportConfig,
    *,
    report_id: str,
    report_type: str,
    filename: str,
    ticker: str,
    scan_session_id: str,
    run_timestamp: str,
    generated_at: str,
    file_size_bytes: int,
    pipeline_stages: list[str],
    job_id: Optional[str] = None,
) -> dict[str, Any]:
    store = get_registry_store(config.exports_dir)
    idempotency_key = store.build_idempotency_key(report_type, scan_session_id, ticker)
    return store.insert_report(
        report_id=report_id,
        job_id=job_id,
        report_type=report_type,
        filename=filename,
        ticker=ticker,
        scan_session_id=scan_session_id,
        run_timestamp=run_timestamp,
        generated_at=generated_at,
        file_size_bytes=file_size_bytes,
        relative_path=f"{config.relative_exports_path}/{filename}",
        download_url=f"/api/reports/download/{filename}",
        pipeline_stages=pipeline_stages,
        idempotency_key=idempotency_key,
    )


def list_reports(
    exports_dir: Path,
    *,
    limit: int = 50,
    offset: int = 0,
    ticker: Optional[str] = None,
    report_type: Optional[str] = None,
    scan_session_id: Optional[str] = None,
) -> list[dict[str, Any]]:
    payload = get_registry_store(exports_dir).list_reports(
        limit=limit,
        offset=offset,
        ticker=ticker,
        report_type=report_type,
        scan_session_id=scan_session_id,
    )
    return payload["reports"]
