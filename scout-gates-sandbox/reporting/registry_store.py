#!/usr/bin/env python3
"""SQLite-backed scalable registry for PDF reports and export jobs."""

from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional
from uuid import uuid4

from reporting.config import (
    JOB_RETENTION_DAYS,
    MAX_REGISTRY_ENTRIES,
    ReportConfig,
    default_exports_dir,
    manifest_path,
    registry_db_path,
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ReportRegistryStore:
    """Thread-safe SQLite registry with indexed queries and job tracking."""

    def __init__(self, exports_dir: Path) -> None:
        self.exports_dir = exports_dir
        self.db_path = registry_db_path(exports_dir)
        self._lock = threading.RLock()
        self.exports_dir.mkdir(parents=True, exist_ok=True)
        self._init_db()
        self.migrate_manifest_if_needed()

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.row_factory = sqlite3.Row
            try:
                yield conn
                conn.commit()
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()

    def _init_db(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                PRAGMA journal_mode = WAL;

                CREATE TABLE IF NOT EXISTS report_jobs (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL,
                    report_type TEXT NOT NULL,
                    status TEXT NOT NULL,
                    ticker TEXT,
                    scan_session_id TEXT,
                    run_timestamp TEXT,
                    priority INTEGER NOT NULL DEFAULT 100,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    max_attempts INTEGER NOT NULL DEFAULT 3,
                    error_message TEXT,
                    pipeline_stage TEXT,
                    pipeline_stages_json TEXT,
                    scan_payload_json TEXT NOT NULL,
                    result_json TEXT,
                    batch_id TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT
                );

                CREATE UNIQUE INDEX IF NOT EXISTS idx_report_jobs_idempotency
                    ON report_jobs(idempotency_key)
                    WHERE status IN ('queued', 'running', 'completed');

                CREATE INDEX IF NOT EXISTS idx_report_jobs_status_created
                    ON report_jobs(status, created_at DESC);

                CREATE INDEX IF NOT EXISTS idx_report_jobs_batch
                    ON report_jobs(batch_id);

                CREATE TABLE IF NOT EXISTS report_registry (
                    id TEXT PRIMARY KEY,
                    job_id TEXT,
                    report_type TEXT NOT NULL,
                    filename TEXT NOT NULL UNIQUE,
                    ticker TEXT NOT NULL,
                    scan_session_id TEXT NOT NULL,
                    run_timestamp TEXT,
                    generated_at TEXT NOT NULL,
                    file_size_bytes INTEGER NOT NULL DEFAULT 0,
                    relative_path TEXT NOT NULL,
                    download_url TEXT NOT NULL,
                    pipeline_stages_json TEXT,
                    idempotency_key TEXT NOT NULL,
                    registered_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_report_registry_ticker_registered
                    ON report_registry(ticker, registered_at DESC);

                CREATE INDEX IF NOT EXISTS idx_report_registry_session
                    ON report_registry(scan_session_id, registered_at DESC);

                CREATE INDEX IF NOT EXISTS idx_report_registry_type_registered
                    ON report_registry(report_type, registered_at DESC);

                CREATE UNIQUE INDEX IF NOT EXISTS idx_report_registry_idempotency
                    ON report_registry(idempotency_key);
                """
            )

    def migrate_manifest_if_needed(self) -> int:
        legacy = manifest_path(self.exports_dir)
        if not legacy.is_file():
            return 0
        migrated = 0
        try:
            payload = json.loads(legacy.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            legacy.rename(legacy.with_suffix(".json.bak"))
            return 0
        rows = payload.get("reports", []) if isinstance(payload, dict) else []
        for row in rows:
            if not isinstance(row, dict) or not row.get("filename"):
                continue
            idempotency_key = self.build_idempotency_key(
                str(row.get("reportType") or "scoring_breakdown"),
                str(row.get("scanSessionId") or ""),
                str(row.get("ticker") or ""),
            )
            if self.get_report_by_idempotency(idempotency_key):
                continue
            self.insert_report(
                report_id=str(row.get("id") or uuid4().hex),
                job_id=None,
                report_type=str(row.get("reportType") or "scoring_breakdown"),
                filename=str(row["filename"]),
                ticker=str(row.get("ticker") or ""),
                scan_session_id=str(row.get("scanSessionId") or ""),
                run_timestamp=str(row.get("runTimestamp") or ""),
                generated_at=str(row.get("generatedAt") or _utc_now()),
                file_size_bytes=int(row.get("fileSizeBytes") or 0),
                relative_path=str(row.get("relativePath") or ""),
                download_url=str(row.get("downloadUrl") or f"/api/reports/download/{row['filename']}"),
                pipeline_stages=row.get("pipelineStages") if isinstance(row.get("pipelineStages"), list) else [],
                idempotency_key=idempotency_key,
            )
            migrated += 1
        legacy.rename(legacy.with_suffix(".json.bak"))
        return migrated

    @staticmethod
    def build_idempotency_key(report_type: str, scan_session_id: str, ticker: str) -> str:
        return f"{report_type}:{scan_session_id}:{ticker.upper()}"

    def create_job(
        self,
        *,
        report_type: str,
        scan_payload: dict[str, Any],
        ticker: Optional[str],
        scan_session_id: str,
        run_timestamp: str,
        idempotency_key: str,
        priority: int = 100,
        batch_id: Optional[str] = None,
    ) -> dict[str, Any]:
        job_id = uuid4().hex
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT INTO report_jobs (
                    id, idempotency_key, report_type, status, ticker, scan_session_id,
                    run_timestamp, priority, attempts, max_attempts, pipeline_stage,
                    scan_payload_json, batch_id, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, 0, 3, 'queued', ?, ?, ?, ?)
                """,
                (
                    job_id,
                    idempotency_key,
                    report_type,
                    ticker,
                    scan_session_id,
                    run_timestamp,
                    priority,
                    json.dumps(scan_payload),
                    batch_id,
                    now,
                    now,
                ),
            )
        return self.get_job(job_id) or {"id": job_id, "status": "queued"}

    def get_job(self, job_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM report_jobs WHERE id = ?", (job_id,)).fetchone()
        return self._job_row_to_dict(row) if row else None

    def get_job_by_idempotency(self, idempotency_key: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM report_jobs
                WHERE idempotency_key = ?
                ORDER BY CASE status
                    WHEN 'completed' THEN 0
                    WHEN 'running' THEN 1
                    WHEN 'queued' THEN 2
                    ELSE 3
                END,
                created_at DESC
                LIMIT 1
                """,
                (idempotency_key,),
            ).fetchone()
        return self._job_row_to_dict(row) if row else None

    def claim_next_job(self) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM report_jobs
                WHERE status = 'queued' AND attempts < max_attempts
                ORDER BY priority ASC, created_at ASC
                LIMIT 1
                """
            ).fetchone()
            if not row:
                return None
            now = _utc_now()
            conn.execute(
                """
                UPDATE report_jobs
                SET status = 'running',
                    attempts = attempts + 1,
                    pipeline_stage = 'running',
                    started_at = COALESCE(started_at, ?),
                    updated_at = ?
                WHERE id = ? AND status = 'queued'
                """,
                (now, now, row["id"]),
            )
            updated = conn.execute("SELECT * FROM report_jobs WHERE id = ?", (row["id"],)).fetchone()
        return self._job_row_to_dict(updated) if updated else None

    def update_job_progress(self, job_id: str, stage: str, stages: list[str]) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE report_jobs
                SET pipeline_stage = ?,
                    pipeline_stages_json = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (stage, json.dumps(stages), _utc_now(), job_id),
            )

    def complete_job(self, job_id: str, result: dict[str, Any]) -> None:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                UPDATE report_jobs
                SET status = 'completed',
                    pipeline_stage = 'completed',
                    result_json = ?,
                    error_message = NULL,
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (json.dumps(result), now, now, job_id),
            )

    def fail_job(self, job_id: str, error_message: str, *, retry: bool = False) -> None:
        now = _utc_now()
        with self.connect() as conn:
            row = conn.execute("SELECT attempts, max_attempts FROM report_jobs WHERE id = ?", (job_id,)).fetchone()
            if not row:
                return
            if retry and int(row["attempts"]) < int(row["max_attempts"]):
                conn.execute(
                    """
                    UPDATE report_jobs
                    SET status = 'queued',
                        pipeline_stage = 'queued',
                        error_message = ?,
                        updated_at = ?
                    WHERE id = ?
                    """,
                    (error_message, now, job_id),
                )
                return
            conn.execute(
                """
                UPDATE report_jobs
                SET status = 'failed',
                    pipeline_stage = 'failed',
                    error_message = ?,
                    completed_at = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (error_message, now, now, job_id),
            )

    def insert_report(
        self,
        *,
        report_id: str,
        job_id: Optional[str],
        report_type: str,
        filename: str,
        ticker: str,
        scan_session_id: str,
        run_timestamp: str,
        generated_at: str,
        file_size_bytes: int,
        relative_path: str,
        download_url: str,
        pipeline_stages: list[str],
        idempotency_key: str,
    ) -> dict[str, Any]:
        now = _utc_now()
        with self.connect() as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO report_registry (
                    id, job_id, report_type, filename, ticker, scan_session_id,
                    run_timestamp, generated_at, file_size_bytes, relative_path,
                    download_url, pipeline_stages_json, idempotency_key, registered_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    report_id,
                    job_id,
                    report_type,
                    filename,
                    ticker,
                    scan_session_id,
                    run_timestamp,
                    generated_at,
                    file_size_bytes,
                    relative_path,
                    download_url,
                    json.dumps(pipeline_stages),
                    idempotency_key,
                    now,
                ),
            )
            self._prune_registry(conn)
        return self.get_report(report_id) or {}

    def get_report(self, report_id: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM report_registry WHERE id = ?", (report_id,)).fetchone()
        return self._report_row_to_dict(row) if row else None

    def get_report_by_idempotency(self, idempotency_key: str) -> Optional[dict[str, Any]]:
        with self.connect() as conn:
            row = conn.execute(
                "SELECT * FROM report_registry WHERE idempotency_key = ?",
                (idempotency_key,),
            ).fetchone()
        return self._report_row_to_dict(row) if row else None

    def list_reports(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        ticker: Optional[str] = None,
        report_type: Optional[str] = None,
        scan_session_id: Optional[str] = None,
    ) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        if ticker:
            clauses.append("ticker = ?")
            params.append(ticker.upper())
        if report_type:
            clauses.append("report_type = ?")
            params.append(report_type)
        if scan_session_id:
            clauses.append("scan_session_id = ?")
            params.append(scan_session_id)
        where_sql = " AND ".join(clauses)
        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM report_registry WHERE {where_sql}",
                params,
            ).fetchone()["c"]
            rows = conn.execute(
                f"""
                SELECT * FROM report_registry
                WHERE {where_sql}
                ORDER BY registered_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "reports": [self._report_row_to_dict(row) for row in rows],
        }

    def list_jobs(
        self,
        *,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        batch_id: Optional[str] = None,
    ) -> dict[str, Any]:
        clauses = ["1=1"]
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if batch_id:
            clauses.append("batch_id = ?")
            params.append(batch_id)
        where_sql = " AND ".join(clauses)
        with self.connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) AS c FROM report_jobs WHERE {where_sql}",
                params,
            ).fetchone()["c"]
            rows = conn.execute(
                f"""
                SELECT * FROM report_jobs
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ? OFFSET ?
                """,
                [*params, limit, offset],
            ).fetchall()
        return {
            "total": int(total),
            "limit": limit,
            "offset": offset,
            "jobs": [self._job_row_to_dict(row) for row in rows],
        }

    def queue_stats(self) -> dict[str, int]:
        with self.connect() as conn:
            rows = conn.execute(
                """
                SELECT status, COUNT(*) AS c
                FROM report_jobs
                GROUP BY status
                """
            ).fetchall()
        stats = {str(row["status"]): int(row["c"]) for row in rows}
        for key in ("queued", "running", "completed", "failed"):
            stats.setdefault(key, 0)
        return stats

    def prune_old_jobs(self, retention_days: int = JOB_RETENTION_DAYS) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat()
        with self.connect() as conn:
            cursor = conn.execute(
                """
                DELETE FROM report_jobs
                WHERE status IN ('completed', 'failed')
                  AND completed_at IS NOT NULL
                  AND completed_at < ?
                """,
                (cutoff,),
            )
            return int(cursor.rowcount)

    def _prune_registry(self, conn: sqlite3.Connection) -> None:
        count = conn.execute("SELECT COUNT(*) AS c FROM report_registry").fetchone()["c"]
        overflow = int(count) - MAX_REGISTRY_ENTRIES
        if overflow <= 0:
            return
        conn.execute(
            """
            DELETE FROM report_registry
            WHERE id IN (
                SELECT id FROM report_registry
                ORDER BY registered_at ASC
                LIMIT ?
            )
            """,
            (overflow,),
        )

    def _job_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "id": row["id"],
            "idempotencyKey": row["idempotency_key"],
            "reportType": row["report_type"],
            "status": row["status"],
            "ticker": row["ticker"],
            "scanSessionId": row["scan_session_id"],
            "runTimestamp": row["run_timestamp"],
            "priority": row["priority"],
            "attempts": row["attempts"],
            "maxAttempts": row["max_attempts"],
            "errorMessage": row["error_message"],
            "pipelineStage": row["pipeline_stage"],
            "batchId": row["batch_id"],
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
            "startedAt": row["started_at"],
            "completedAt": row["completed_at"],
        }
        if row["pipeline_stages_json"]:
            try:
                payload["pipelineStages"] = json.loads(row["pipeline_stages_json"])
            except json.JSONDecodeError:
                payload["pipelineStages"] = []
        if row["result_json"]:
            try:
                payload["result"] = json.loads(row["result_json"])
            except json.JSONDecodeError:
                payload["result"] = None
        if row["scan_payload_json"]:
            try:
                payload["scanPayload"] = json.loads(row["scan_payload_json"])
            except json.JSONDecodeError:
                payload["scanPayload"] = {}
        return payload

    def _report_row_to_dict(self, row: sqlite3.Row) -> dict[str, Any]:
        stages: list[str] = []
        if row["pipeline_stages_json"]:
            try:
                stages = json.loads(row["pipeline_stages_json"])
            except json.JSONDecodeError:
                stages = []
        return {
            "id": row["id"],
            "jobId": row["job_id"],
            "reportType": row["report_type"],
            "filename": row["filename"],
            "ticker": row["ticker"],
            "scanSessionId": row["scan_session_id"],
            "runTimestamp": row["run_timestamp"],
            "generatedAt": row["generated_at"],
            "fileSizeBytes": row["file_size_bytes"],
            "relativePath": row["relative_path"],
            "downloadUrl": row["download_url"],
            "pipelineStages": stages,
            "idempotencyKey": row["idempotency_key"],
            "registeredAt": row["registered_at"],
        }


_STORES: dict[str, ReportRegistryStore] = {}
_STORE_LOCK = threading.Lock()


def get_registry_store(exports_dir: Optional[Path] = None) -> ReportRegistryStore:
    target = (exports_dir or default_exports_dir()).resolve()
    key = str(target)
    with _STORE_LOCK:
        store = _STORES.get(key)
        if store is None:
            store = ReportRegistryStore(target)
            _STORES[key] = store
        return store


def clear_registry_store_cache() -> None:
    with _STORE_LOCK:
        _STORES.clear()
