#!/usr/bin/env python3
"""Background worker for queued PDF export jobs."""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Optional

from reporting.config import JOB_POLL_INTERVAL_SEC, ReportConfig, default_exports_dir
from reporting.pipeline import ReportPipeline
from reporting.registry_store import ReportRegistryStore, get_registry_store


class ReportJobWorker:
    """Single-threaded worker to avoid concurrent Chrome PDF renders."""

    def __init__(self, config: ReportConfig) -> None:
        self.config = config
        self.store = get_registry_store(config.exports_dir)
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="scout-report-worker",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=5)

    def notify(self) -> None:
        self._wake.set()

    @property
    def running(self) -> bool:
        return bool(self._thread and self._thread.is_alive())

    def _run_loop(self) -> None:
        while not self._stop.is_set():
            processed = False
            try:
                processed = self._process_one()
            except Exception as exc:
                print(f"[report-worker] loop error: {exc}", flush=True)
            if processed:
                continue
            self._wake.wait(timeout=JOB_POLL_INTERVAL_SEC)
            self._wake.clear()
            try:
                self.store.prune_old_jobs()
            except Exception:
                pass

    def _process_one(self) -> bool:
        job = self.store.claim_next_job()
        if not job:
            return False
        job_id = str(job["id"])
        payload = job.get("scanPayload")
        if not isinstance(payload, dict):
            self.store.fail_job(job_id, "Invalid or missing scan payload on job", retry=False)
            return True

        pipeline = ReportPipeline(self.config)

        def on_progress(stage: str, stages: list[str]) -> None:
            self.store.update_job_progress(job_id, stage, stages)

        try:
            result = pipeline.run(
                payload,
                report_type=str(job.get("reportType") or "scoring_breakdown"),
                ticker=job.get("ticker"),
                job_id=job_id,
                on_progress=on_progress,
            )
            self.store.complete_job(job_id, result)
        except Exception as exc:
            retry = int(job.get("attempts") or 0) < int(job.get("maxAttempts") or 3)
            self.store.fail_job(job_id, str(exc), retry=retry)
        return True


_WORKERS: dict[str, ReportJobWorker] = {}
_WORKER_LOCK = threading.Lock()


def get_report_worker(config: Optional[ReportConfig] = None) -> ReportJobWorker:
    cfg = config or ReportConfig(exports_dir=default_exports_dir())
    key = str(cfg.exports_dir.resolve())
    with _WORKER_LOCK:
        worker = _WORKERS.get(key)
        if worker is None:
            worker = ReportJobWorker(cfg)
            _WORKERS[key] = worker
        return worker


def ensure_report_worker(config: Optional[ReportConfig] = None) -> ReportJobWorker:
    worker = get_report_worker(config)
    worker.start()
    return worker
