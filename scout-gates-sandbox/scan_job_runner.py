#!/usr/bin/env python3
"""Background async scan jobs for the Scout gate sandbox dashboard.

v1 execution model:
- One scan job runs at a time; additional jobs remain queued.
- Jobs persist in SQLite (scan_jobs table) for hosted reliability.
- Uses the same execute_scan_run() engine as synchronous POST /api/run.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from memory_store import connect, init_db, json_dump, json_load
from universe_presets import resolve_universe_from_request

SCAN_JOB_POLL_INTERVAL_SEC = 0.5
SCAN_JOB_STATUSES = frozenset({"queued", "running", "completed", "failed"})
INTERRUPTED_ERROR = "interrupted by server restart"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_scan_job_store(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS scan_jobs (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            request_json TEXT NOT NULL,
            result_json TEXT,
            error TEXT,
            total_tickers INTEGER NOT NULL DEFAULT 0,
            completed_tickers INTEGER NOT NULL DEFAULT 0,
            current_ticker TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_scan_jobs_created
            ON scan_jobs(created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_scan_jobs_status
            ON scan_jobs(status, created_at);
        """
    )


def _validate_scan_request(request_payload: dict[str, Any]) -> list[str]:
    if not isinstance(request_payload, dict):
        raise ValueError("Request body must be a JSON object.")
    candidates, _cohort = resolve_universe_from_request(request_payload)
    if not candidates:
        raise ValueError("Enter at least one ticker or choose the fallback universe.")
    return candidates


def _row_to_job(row) -> dict[str, Any]:
    payload = {
        "ok": True,
        "jobId": row["id"],
        "status": row["status"],
        "createdAt": row["created_at"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "totalTickers": int(row["total_tickers"] or 0),
        "completedTickers": int(row["completed_tickers"] or 0),
        "currentTicker": row["current_ticker"],
        "error": row["error"],
    }
    if row["status"] == "completed" and row["result_json"]:
        result = json_load(row["result_json"])
        if isinstance(result, dict):
            payload["result"] = result
    return payload


def recover_interrupted_scan_jobs() -> int:
    """Mark queued/running jobs from a prior process as failed on startup."""
    init_db()
    now = utc_now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE scan_jobs
            SET status = 'failed',
                error = ?,
                completed_at = ?,
                current_ticker = NULL
            WHERE status IN ('queued', 'running')
            """,
            (INTERRUPTED_ERROR, now),
        )
        conn.commit()
        return int(cursor.rowcount)


def create_scan_job(request_payload: dict[str, Any]) -> dict[str, Any]:
    init_db()
    candidates = _validate_scan_request(request_payload)
    job_id = uuid.uuid4().hex
    now = utc_now_iso()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO scan_jobs (
                id, status, request_json, total_tickers, completed_tickers,
                current_ticker, created_at
            ) VALUES (?, 'queued', ?, ?, 0, NULL, ?)
            """,
            (job_id, json_dump(request_payload), len(candidates), now),
        )
        conn.commit()
    get_scan_worker().notify()
    return {"ok": True, "jobId": job_id, "status": "queued"}


def get_scan_job(job_id: str) -> Optional[dict[str, Any]]:
    init_db()
    normalized = str(job_id or "").strip()
    if not normalized:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM scan_jobs WHERE id = ?",
            (normalized,),
        ).fetchone()
    if row is None:
        return None
    return _row_to_job(row)


def list_scan_jobs(*, limit: int = 20) -> dict[str, Any]:
    init_db()
    bounded = min(max(int(limit), 1), 100)
    with connect() as conn:
        rows = conn.execute(
            """
            SELECT * FROM scan_jobs
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (bounded,),
        ).fetchall()
    return {
        "ok": True,
        "jobs": [_row_to_job(row) for row in rows],
    }


def _update_job_progress(
    job_id: str,
    *,
    completed_tickers: int,
    total_tickers: int,
    current_ticker: Optional[str],
) -> None:
    with connect() as conn:
        conn.execute(
            """
            UPDATE scan_jobs
            SET completed_tickers = ?,
                total_tickers = ?,
                current_ticker = ?
            WHERE id = ?
            """,
            (completed_tickers, total_tickers, current_ticker, job_id),
        )
        conn.commit()


def _mark_job_running(job_id: str) -> bool:
    now = utc_now_iso()
    with connect() as conn:
        cursor = conn.execute(
            """
            UPDATE scan_jobs
            SET status = 'running',
                started_at = ?,
                current_ticker = NULL,
                error = NULL
            WHERE id = ? AND status = 'queued'
            """,
            (now, job_id),
        )
        conn.commit()
        return int(cursor.rowcount) == 1


def _complete_job(job_id: str, result: dict[str, Any]) -> None:
    now = utc_now_iso()
    with connect() as conn:
        conn.execute(
            """
            UPDATE scan_jobs
            SET status = 'completed',
                result_json = ?,
                error = NULL,
                completed_at = ?,
                current_ticker = NULL,
                completed_tickers = total_tickers
            WHERE id = ?
            """,
            (json_dump(result), now, job_id),
        )
        conn.commit()


def _fail_job(job_id: str, error: str) -> None:
    now = utc_now_iso()
    with connect() as conn:
        conn.execute(
            """
            UPDATE scan_jobs
            SET status = 'failed',
                error = ?,
                completed_at = ?,
                current_ticker = NULL
            WHERE id = ?
            """,
            (error, now, job_id),
        )
        conn.commit()


def _claim_next_queued_job_id() -> Optional[str]:
    with connect() as conn:
        running = conn.execute(
            "SELECT id FROM scan_jobs WHERE status = 'running' LIMIT 1"
        ).fetchone()
        if running is not None:
            return None
        row = conn.execute(
            """
            SELECT id FROM scan_jobs
            WHERE status = 'queued'
            ORDER BY created_at ASC
            LIMIT 1
            """
        ).fetchone()
    if row is None:
        return None
    job_id = str(row["id"])
    if _mark_job_running(job_id):
        return job_id
    return None


def _load_job_request(job_id: str) -> Optional[dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            "SELECT request_json FROM scan_jobs WHERE id = ?",
            (job_id,),
        ).fetchone()
    if row is None:
        return None
    payload = json_load(row["request_json"])
    return payload if isinstance(payload, dict) else None


class ScanJobWorker:
    """Single-threaded worker — one scan executes at a time; others queue."""

    def __init__(self) -> None:
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run_loop,
            name="scout-scan-worker",
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
                print(f"[scan-worker] loop error: {exc}", flush=True)
            if processed:
                continue
            self._wake.wait(timeout=SCAN_JOB_POLL_INTERVAL_SEC)
            self._wake.clear()

    def _process_one(self) -> bool:
        job_id = _claim_next_queued_job_id()
        if not job_id:
            return False

        request_payload = _load_job_request(job_id)
        if request_payload is None:
            _fail_job(job_id, "Scan job request payload was missing or invalid.")
            return True

        def progress_callback(
            completed_tickers: int,
            total_tickers: int,
            current_ticker: Optional[str],
        ) -> None:
            _update_job_progress(
                job_id,
                completed_tickers=completed_tickers,
                total_tickers=total_tickers,
                current_ticker=current_ticker,
            )

        try:
            from dashboard import execute_scan_run

            result = execute_scan_run(
                request_payload,
                progress_callback=progress_callback,
            )
            _complete_job(job_id, result)
        except ValueError as exc:
            _fail_job(job_id, str(exc))
        except Exception as exc:
            _fail_job(job_id, f"Scan job error: {exc}")
        return True


_WORKER: Optional[ScanJobWorker] = None
_WORKER_LOCK = threading.Lock()


def get_scan_worker() -> ScanJobWorker:
    global _WORKER
    with _WORKER_LOCK:
        if _WORKER is None:
            _WORKER = ScanJobWorker()
        return _WORKER


def ensure_scan_worker() -> ScanJobWorker:
    worker = get_scan_worker()
    worker.start()
    return worker
