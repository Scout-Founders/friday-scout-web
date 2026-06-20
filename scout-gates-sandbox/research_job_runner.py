#!/usr/bin/env python3
"""Read-only research job orchestration over Scout Horizon memory and backtests."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional

from backtest_engine import (
    BacktestFilters,
    parse_backtest_filters,
    preview_backtest,
)
from memory_store import connect, init_db, json_dump, json_load


DEFAULT_RESEARCH_JOB_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "Mega Cap Tech Scan",
        "job_type": "cohort_scan",
        "preset": "mega_cap_tech",
        "cohort": "actionable",
        "filters_json": {},
        "schedule_label": "weekly",
        "enabled": True,
    },
    {
        "name": "Semiconductors Scan",
        "job_type": "cohort_scan",
        "preset": "semiconductors",
        "cohort": "actionable",
        "filters_json": {},
        "schedule_label": "weekly",
        "enabled": True,
    },
    {
        "name": "ETFs Scan",
        "job_type": "cohort_scan",
        "preset": "etfs",
        "cohort": "research",
        "filters_json": {},
        "schedule_label": "weekly",
        "enabled": True,
    },
    {
        "name": "Failure Learning Scan",
        "job_type": "cohort_scan",
        "preset": "failure_learning",
        "cohort": "failure_learning",
        "filters_json": {},
        "schedule_label": "weekly",
        "enabled": True,
    },
    {
        "name": "Bearish Failure Audit",
        "job_type": "audit",
        "preset": None,
        "cohort": "actionable",
        "filters_json": {"auditKind": "bearish_failure"},
        "schedule_label": "daily",
        "enabled": True,
    },
    {
        "name": "SPECTER Positive Audit",
        "job_type": "audit",
        "preset": None,
        "cohort": "actionable",
        "filters_json": {"auditKind": "specter_positive", "gateCode": "SPECTER"},
        "schedule_label": "daily",
        "enabled": True,
    },
    {
        "name": "AI Leadership Audit",
        "job_type": "audit",
        "preset": None,
        "cohort": "actionable",
        "filters_json": {"auditKind": "trend_leadership"},
        "schedule_label": "weekly",
        "enabled": True,
    },
)


def init_research_job_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS research_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            job_type TEXT NOT NULL,
            preset TEXT,
            cohort TEXT,
            filters_json TEXT NOT NULL DEFAULT '{}',
            schedule_label TEXT,
            enabled INTEGER NOT NULL DEFAULT 1,
            last_run_at TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE INDEX IF NOT EXISTS idx_research_jobs_enabled
            ON research_jobs(enabled, name);

        CREATE TABLE IF NOT EXISTS research_job_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL REFERENCES research_jobs(id) ON DELETE CASCADE,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            status TEXT NOT NULL,
            signals_count INTEGER NOT NULL DEFAULT 0,
            summary_json TEXT,
            error_message TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_research_job_runs_job
            ON research_job_runs(job_id, started_at DESC);
        """
    )


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def research_job_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "jobType": row["job_type"],
        "preset": row["preset"],
        "cohort": row["cohort"],
        "filters": json_load(row["filters_json"]) or {},
        "scheduleLabel": row["schedule_label"],
        "enabled": bool(row["enabled"]),
        "lastRunAt": row["last_run_at"],
        "createdAt": row["created_at"],
    }


def research_job_run_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": row["id"],
        "jobId": row["job_id"],
        "startedAt": row["started_at"],
        "completedAt": row["completed_at"],
        "status": row["status"],
        "signalsCount": int(row["signals_count"] or 0),
        "summary": json_load(row["summary_json"]) or {},
        "errorMessage": row["error_message"],
    }


def backtest_filters_for_job(job: sqlite3.Row) -> BacktestFilters:
    filters_payload = dict(json_load(job["filters_json"]) or {})
    preset_id = job["preset"] or filters_payload.get("preset")
    job_type = str(job["job_type"] or "")

    if job_type == "cohort_scan" and preset_id:
        from universe_presets import resolve_preset

        preset = resolve_preset(str(preset_id))
        filters_payload["tickers"] = list(preset.get("tickers") or [])
        if "scanPurpose" not in filters_payload:
            filters_payload.pop("scanPurpose", None)
        if "cohortClass" not in filters_payload:
            filters_payload.pop("cohortClass", None)
    elif job_type == "audit":
        if "cohortClass" not in filters_payload:
            filters_payload.pop("cohortClass", None)
        if "scanPurpose" not in filters_payload:
            filters_payload.pop("scanPurpose", None)
    elif job["cohort"]:
        filters_payload.setdefault("cohortClass", job["cohort"])
    return parse_backtest_filters(filters_payload)


def summarize_specter_positive_audit(analytics: dict[str, Any], gate_code: str = "SPECTER") -> dict[str, Any]:
    gates = (analytics.get("gate_contribution_audit") or {}).get("gates") or []
    gate_row = next((row for row in gates if row.get("gate_code") == gate_code), None)
    if gate_row is None:
        return {
            "gateCode": gate_code,
            "signalCount": 0,
            "winRate": None,
            "avgSignalReturn": None,
            "avgStockReturn": None,
            "expectancy": None,
        }
    return {
        "gateCode": gate_code,
        "signalCount": gate_row.get("signal_count", 0),
        "winRate": gate_row.get("win_rate"),
        "avgSignalReturn": gate_row.get("avg_signal_return"),
        "avgStockReturn": gate_row.get("avg_stock_return"),
        "expectancy": gate_row.get("expectancy"),
    }


def summarize_trend_leadership_audit(analytics: dict[str, Any]) -> list[dict[str, Any]]:
    groups = (analytics.get("trend_leadership_audit") or {}).get("groups") or []
    return [
        {
            "id": group.get("id"),
            "label": group.get("label"),
            "signalCount": group.get("signal_count", 0),
            "bullishExpectancy": (group.get("bullish") or {}).get("expectancy"),
            "bearishExpectancy": (group.get("bearish") or {}).get("expectancy"),
        }
        for group in groups
    ]


def build_job_summary(job: sqlite3.Row, preview_result: dict[str, Any]) -> dict[str, Any]:
    metrics = preview_result.get("metrics") or {}
    analytics = preview_result.get("analytics") or {}
    filters = json_load(job["filters_json"]) or {}
    signals_count = int(metrics.get("sample_size") or preview_result.get("signalsMatched") or 0)

    summary: dict[str, Any] = {
        "jobType": job["job_type"],
        "jobName": job["name"],
        "preset": job["preset"],
        "cohort": job["cohort"],
        "signalsMatched": signals_count,
        "winRate": metrics.get("win_rate"),
        "avgSignalReturn": metrics.get("avg_return"),
        "avgStockReturn": metrics.get("avg_stock_return"),
        "expectancy": metrics.get("expectancy"),
    }

    audit_kind = filters.get("auditKind")
    if audit_kind == "bearish_failure":
        audit = analytics.get("bearish_failure_audit") or {}
        summary["audit"] = {
            "kind": audit_kind,
            "summary": audit.get("summary") or {},
            "topLosingTrades": len(audit.get("top_losing_trades") or []),
            "commonLossSectors": len(audit.get("common_loss_sectors") or []),
        }
    elif audit_kind == "trend_leadership":
        summary["audit"] = {
            "kind": audit_kind,
            "groups": summarize_trend_leadership_audit(analytics),
        }
    elif audit_kind == "specter_positive":
        gate_code = str(filters.get("gateCode") or "SPECTER").upper()
        summary["audit"] = {
            "kind": audit_kind,
            **summarize_specter_positive_audit(analytics, gate_code=gate_code),
        }

    return summary


def create_default_research_jobs() -> dict[str, Any]:
    init_db()
    created = 0
    with connect() as conn:
        init_research_job_store(conn)
        for spec in DEFAULT_RESEARCH_JOB_SPECS:
            existing = conn.execute(
                "SELECT id FROM research_jobs WHERE name = ?",
                (spec["name"],),
            ).fetchone()
            if existing is not None:
                continue
            conn.execute(
                """
                INSERT INTO research_jobs (
                    name, job_type, preset, cohort, filters_json,
                    schedule_label, enabled, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    spec["name"],
                    spec["job_type"],
                    spec.get("preset"),
                    spec.get("cohort"),
                    json_dump(spec.get("filters_json") or {}),
                    spec.get("schedule_label"),
                    1 if spec.get("enabled", True) else 0,
                    utc_now_iso(),
                ),
            )
            created += 1
    return {
        "ok": True,
        "created": created,
        "totalDefaults": len(DEFAULT_RESEARCH_JOB_SPECS),
    }


def list_research_jobs(*, include_disabled: bool = True) -> list[dict[str, Any]]:
    init_db()
    with connect() as conn:
        init_research_job_store(conn)
        if include_disabled:
            rows = conn.execute(
                "SELECT * FROM research_jobs ORDER BY name ASC"
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM research_jobs WHERE enabled = 1 ORDER BY name ASC"
            ).fetchall()
        jobs: list[dict[str, Any]] = []
        for row in rows:
            job = research_job_row(row)
            last_run_row = conn.execute(
                """
                SELECT * FROM research_job_runs
                WHERE job_id = ?
                ORDER BY started_at DESC, id DESC
                LIMIT 1
                """,
                (row["id"],),
            ).fetchone()
            if last_run_row is not None:
                last_run = research_job_run_row(last_run_row)
                job["lastRun"] = {
                    "status": last_run["status"],
                    "signalsCount": last_run["signalsCount"],
                    "expectancy": last_run["summary"].get("expectancy"),
                    "startedAt": last_run["startedAt"],
                    "completedAt": last_run["completedAt"],
                }
            else:
                job["lastRun"] = None
            jobs.append(job)
        return jobs


def list_research_job_runs(limit: int = 50) -> list[dict[str, Any]]:
    init_db()
    bounded_limit = min(max(int(limit), 1), 200)
    with connect() as conn:
        init_research_job_store(conn)
        rows = conn.execute(
            """
            SELECT r.*, j.name AS job_name
            FROM research_job_runs r
            JOIN research_jobs j ON j.id = r.job_id
            ORDER BY r.started_at DESC, r.id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        return [
            {
                **research_job_run_row(row),
                "jobName": row["job_name"],
            }
            for row in rows
        ]


def get_research_job(job_id: int) -> Optional[dict[str, Any]]:
    init_db()
    with connect() as conn:
        init_research_job_store(conn)
        row = conn.execute("SELECT * FROM research_jobs WHERE id = ?", (job_id,)).fetchone()
        return research_job_row(row) if row is not None else None


def _fetch_job_row(conn: sqlite3.Connection, job_id: int) -> Optional[sqlite3.Row]:
    return conn.execute("SELECT * FROM research_jobs WHERE id = ?", (job_id,)).fetchone()


def run_research_job(job_id: int) -> dict[str, Any]:
    init_db()
    started_at = utc_now_iso()
    with connect() as conn:
        init_research_job_store(conn)
        job = _fetch_job_row(conn, job_id)
        if job is None:
            return {"ok": False, "message": f"Research job {job_id} was not found."}

        cursor = conn.execute(
            """
            INSERT INTO research_job_runs (
                job_id, started_at, status, signals_count
            ) VALUES (?, ?, 'running', 0)
            """,
            (job_id, started_at),
        )
        run_id = int(cursor.lastrowid)

        try:
            preview = preview_backtest(backtest_filters_for_job(job))
            if not preview.get("ok"):
                raise RuntimeError(str(preview.get("message") or "Research preview failed."))
            summary = build_job_summary(job, preview)
            signals_count = int(summary.get("signalsMatched") or 0)
            completed_at = utc_now_iso()
            conn.execute(
                """
                UPDATE research_job_runs
                SET completed_at = ?, status = 'completed', signals_count = ?, summary_json = ?
                WHERE id = ?
                """,
                (completed_at, signals_count, json_dump(summary), run_id),
            )
            conn.execute(
                "UPDATE research_jobs SET last_run_at = ? WHERE id = ?",
                (completed_at, job_id),
            )
            run_row = conn.execute(
                "SELECT * FROM research_job_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            return {
                "ok": True,
                "job": research_job_row(job),
                "run": research_job_run_row(run_row),
            }
        except Exception as exc:
            completed_at = utc_now_iso()
            conn.execute(
                """
                UPDATE research_job_runs
                SET completed_at = ?, status = 'failed', error_message = ?
                WHERE id = ?
                """,
                (completed_at, str(exc), run_id),
            )
            run_row = conn.execute(
                "SELECT * FROM research_job_runs WHERE id = ?",
                (run_id,),
            ).fetchone()
            return {
                "ok": False,
                "job": research_job_row(job),
                "run": research_job_run_row(run_row),
                "message": str(exc),
            }


def run_enabled_research_jobs() -> dict[str, Any]:
    init_db()
    results: list[dict[str, Any]] = []
    with connect() as conn:
        init_research_job_store(conn)
        rows = conn.execute(
            "SELECT id FROM research_jobs WHERE enabled = 1 ORDER BY name ASC"
        ).fetchall()
        job_ids = [int(row["id"]) for row in rows]

    for job_id in job_ids:
        results.append(run_research_job(job_id))

    completed = sum(1 for result in results if result.get("ok"))
    failed = len(results) - completed
    return {
        "ok": failed == 0,
        "ran": len(results),
        "completed": completed,
        "failed": failed,
        "results": results,
    }
