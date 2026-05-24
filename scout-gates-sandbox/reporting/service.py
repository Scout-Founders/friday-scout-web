#!/usr/bin/env python3
"""High-level reporting service for sync/async/batch PDF exports."""

from __future__ import annotations

from typing import Any, Optional
from uuid import uuid4

from reporting.config import REPORT_TYPE_SCORING_BREAKDOWN, ReportConfig, default_exports_dir
from reporting.jobs import ensure_report_worker, get_report_worker
from reporting.pipeline import ReportPipeline
from reporting.registry_store import ReportRegistryStore, get_registry_store
from reporting.scoring_breakdown import resolve_scan_session_id


class ReportService:
    def __init__(self, config: Optional[ReportConfig] = None) -> None:
        self.config = config or ReportConfig(exports_dir=default_exports_dir())
        self.store: ReportRegistryStore = get_registry_store(self.config.exports_dir)

    def _resolve_ticker(self, scan_payload: dict[str, Any], ticker: Optional[str]) -> Optional[str]:
        if ticker:
            return ticker.strip().upper()
        final_pick = scan_payload.get("finalPick")
        if isinstance(final_pick, dict) and final_pick.get("ticker"):
            return str(final_pick["ticker"]).upper()
        return None

    def _job_response(self, job: dict[str, Any], *, reused: bool = False) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "ok": True,
            "async": job.get("status") in ("queued", "running"),
            "reused": reused,
            "jobId": job.get("id"),
            "status": job.get("status"),
            "pipelineStage": job.get("pipelineStage"),
            "pipelineStages": job.get("pipelineStages") or [],
            "ticker": job.get("ticker"),
            "scanSessionId": job.get("scanSessionId"),
            "pollUrl": f"/api/reports/jobs/{job.get('id')}",
        }
        if job.get("status") == "completed" and isinstance(job.get("result"), dict):
            payload.update(job["result"])
        if job.get("errorMessage"):
            payload["errorMessage"] = job["errorMessage"]
        return payload

    def export(
        self,
        scan_payload: dict[str, Any],
        *,
        ticker: Optional[str] = None,
        report_type: str = REPORT_TYPE_SCORING_BREAKDOWN,
        async_mode: bool = False,
        priority: int = 100,
    ) -> dict[str, Any]:
        ticker_value = self._resolve_ticker(scan_payload, ticker)
        session_id = resolve_scan_session_id(scan_payload)
        idempotency_key = self.store.build_idempotency_key(
            report_type,
            session_id,
            ticker_value or "UNKNOWN",
        )

        existing_report = self.store.get_report_by_idempotency(idempotency_key)
        if existing_report:
            return {
                "ok": True,
                "reused": True,
                "async": False,
                "reportId": existing_report["id"],
                "filename": existing_report["filename"],
                "relativePath": existing_report["relativePath"],
                "downloadUrl": existing_report["downloadUrl"],
                "scanSessionId": existing_report["scanSessionId"],
                "ticker": existing_report["ticker"],
                "generatedAt": existing_report["generatedAt"],
                "registryEntry": existing_report,
            }

        existing_job = self.store.get_job_by_idempotency(idempotency_key)
        if existing_job and existing_job.get("status") in ("queued", "running", "completed"):
            if existing_job.get("status") == "completed" or not async_mode:
                return self._job_response(existing_job, reused=True)
            return self._job_response(existing_job, reused=True)

        job = self.store.create_job(
            report_type=report_type,
            scan_payload=scan_payload,
            ticker=ticker_value,
            scan_session_id=session_id,
            run_timestamp=str(scan_payload.get("runTimestamp") or ""),
            idempotency_key=idempotency_key,
            priority=priority,
        )

        if async_mode:
            worker = ensure_report_worker(self.config)
            worker.notify()
            return self._job_response(job)

        result = ReportPipeline(self.config).run(
            scan_payload,
            report_type=report_type,
            ticker=ticker_value,
            job_id=str(job["id"]),
        )
        self.store.complete_job(str(job["id"]), result)
        completed = self.store.get_job(str(job["id"])) or job
        return self._job_response(completed)

    def export_batch(
        self,
        scan_payload: dict[str, Any],
        *,
        tickers: list[str],
        report_type: str = REPORT_TYPE_SCORING_BREAKDOWN,
        async_mode: bool = True,
    ) -> dict[str, Any]:
        if not tickers:
            raise ValueError("tickers must be a non-empty list")
        batch_id = uuid4().hex
        session_id = resolve_scan_session_id(scan_payload)
        jobs: list[dict[str, Any]] = []
        for index, raw_ticker in enumerate(tickers):
            ticker_value = str(raw_ticker).strip().upper()
            if not ticker_value:
                continue
            idempotency_key = self.store.build_idempotency_key(
                report_type,
                session_id,
                ticker_value,
            )
            existing = self.store.get_job_by_idempotency(idempotency_key)
            if existing and existing.get("status") in ("queued", "running", "completed"):
                jobs.append(existing)
                continue
            job = self.store.create_job(
                report_type=report_type,
                scan_payload=scan_payload,
                ticker=ticker_value,
                scan_session_id=session_id,
                run_timestamp=str(scan_payload.get("runTimestamp") or ""),
                idempotency_key=idempotency_key,
                priority=100 + index,
                batch_id=batch_id,
            )
            jobs.append(job)

        if async_mode:
            ensure_report_worker(self.config).notify()

        if not async_mode:
            results = []
            for job in jobs:
                if job.get("status") == "completed" and job.get("result"):
                    results.append(job["result"])
                    continue
                result = ReportPipeline(self.config).run(
                    scan_payload,
                    report_type=report_type,
                    ticker=job.get("ticker"),
                    job_id=str(job["id"]),
                )
                self.store.complete_job(str(job["id"]), result)
                results.append(result)
            return {"ok": True, "batchId": batch_id, "async": False, "results": results}

        return {
            "ok": True,
            "async": True,
            "batchId": batch_id,
            "jobCount": len(jobs),
            "jobs": [
                {
                    "jobId": job.get("id"),
                    "ticker": job.get("ticker"),
                    "status": job.get("status"),
                    "pollUrl": f"/api/reports/jobs/{job.get('id')}",
                }
                for job in jobs
            ],
            "pollUrl": f"/api/reports/jobs?batchId={batch_id}",
        }

    def get_job(self, job_id: str) -> dict[str, Any]:
        job = self.store.get_job(job_id)
        if not job:
            return {"ok": False, "message": "Report job not found."}
        response = self._job_response(job)
        response["scanPayload"] = None
        return response


_SERVICES: dict[str, ReportService] = {}


def get_report_service(exports_dir: Optional[Any] = None) -> ReportService:
    from pathlib import Path

    cfg = ReportConfig(exports_dir=Path(exports_dir) if exports_dir else default_exports_dir())
    key = str(cfg.exports_dir.resolve())
    service = _SERVICES.get(key)
    if service is None:
        service = ReportService(cfg)
        _SERVICES[key] = service
    return service
