#!/usr/bin/env python3
"""Read-only Scout Overview snapshot.

Opens SQLite with mode=ro. Does not call init_db(), create tables, or
publish derived intelligence. Missing tables stay missing and become null.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from memory_store import (
    ACTIONABLE_DIRECTION_SQL,
    COMPLETED_OUTCOME_LABELS_SQL,
    PRIMARY_RETURN_SQL,
    freshness_status,
    get_anomaly_monitor,
    get_db_path,
    readiness_status,
)
from observation_evidence import population_problems, research_eligible_predicate
from schema_registry import EXPECTED_SCHEMAS, normalize_type, table_columns, table_exists


ATTENTION_CODES = frozenset(
    {
        "derived_build_status",
        "schema_drift",
        "schema_warning",
        "research_population",
        "observation_freshness",
        "outcome_freshness",
        "stale_pending_outcomes",
        "missing_feature_vectors",
        "anomaly_monitor",
        "research_finding",
        "missing_fmp_key",
    }
)

# Same grouping query already used by get_regime_intelligence_summary.
_REGIME_GROUP_SQL = """
    SELECT rs.market_trend, rs.volatility_regime, rs.liquidity_regime, rs.macro_bias,
           COUNT(*) AS sample_size,
           SUM(CASE WHEN COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) > 0 THEN 1 ELSE 0 END) AS wins,
           SUM(CASE WHEN COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) <= 0 THEN 1 ELSE 0 END) AS losses,
           ROUND(
               SUM(CASE WHEN COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) > 0 THEN 1 ELSE 0 END)
               * 100.0 / COUNT(*),
               2
           ) AS win_rate,
           ROUND(AVG(COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d)), 4) AS avg_return,
           ROUND(
               AVG(COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d))
               * (
                   SUM(CASE WHEN COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) > 0 THEN 1 ELSE 0 END)
                   * 1.0 / COUNT(*)
               ),
               4
           ) AS expectancy
    FROM regime_snapshots rs
    JOIN scan_results sr
      ON sr.run_id = rs.scan_id AND sr.ticker = rs.ticker
    WHERE sr.stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
      AND COALESCE(sr.return_20d, sr.return_10d, sr.return_5d, sr.return_3d, sr.return_1d) IS NOT NULL
      AND {eligible}
    GROUP BY rs.market_trend, rs.volatility_regime, rs.liquidity_regime, rs.macro_bias
"""


def build_overview_snapshot(
    db_path: Optional[Path] = None,
    *,
    fmp_key_present: bool = False,
) -> dict[str, Any]:
    """Return the Overview contract from one read-only connection."""
    path = Path(db_path) if db_path is not None else get_db_path()
    generated_at = datetime.now(timezone.utc).isoformat()
    empty = _empty_snapshot(generated_at)
    if not path.exists():
        empty["ok"] = False
        empty["message"] = "database is not available"
        return empty

    try:
        conn = _connect_readonly(path)
    except sqlite3.Error:
        empty["ok"] = False
        empty["message"] = "database is not available"
        return empty

    try:
        return _snapshot(conn, generated_at, fmp_key_present=fmp_key_present)
    finally:
        conn.close()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    quoted = urllib.parse.quote(str(path), safe="/:")
    conn = sqlite3.connect(f"file:{quoted}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _empty_snapshot(generated_at: str) -> dict[str, Any]:
    return {
        "ok": True,
        "generatedAt": generated_at,
        "system": {
            "observationTimestamp": None,
            "observationFreshness": "NOT READY",
            "outcomeUpdatedAt": None,
            "outcomeFreshness": "NOT READY",
            "evidenceBankRows": None,
            "readiness": [],
            "schemaStatus": None,
            "anomalyCount": None,
            "derivedBuilds": None,
        },
        "live": {
            "scanRunId": None,
            "observationTimestamp": None,
            "tickerCount": None,
            "directionCounts": {"Bullish": 0, "Bearish": 0, "Neutral": 0},
            "href": "/scanner",
        },
        "horizon": {
            "patternCount": None,
            "gateAlphaSegmentCount": None,
            "gateIntelligenceGateCount": None,
            "regimeSnapshotCount": None,
            "existingLeaders": {
                "strongestBullishPattern": None,
                "strongestBearishPattern": None,
                "topGlobalGateAlpha": None,
                "mostPredictiveGate": None,
                "highestExpectancyRegime": None,
            },
            "href": "/control",
        },
        "memory": {
            "eligibleObservations": None,
            "eligibleCompleted": None,
            "eligiblePending": None,
            "actionableCompleted": None,
            "href": "/research",
        },
        "backtests": {"savedRunCount": None, "latest": None, "href": "/backtest"},
        "experiments": {
            "enabledJobs": None,
            "runStatusCounts": None,
            "openFindings": None,
            "openFindingsBySeverity": None,
            "candidatesByStatus": None,
            "validationsByStatus": None,
            "href": "/research-intelligence",
        },
        "attention": [],
    }


def _snapshot(conn: sqlite3.Connection, generated_at: str, *, fmp_key_present: bool) -> dict[str, Any]:
    payload = _empty_snapshot(generated_at)
    attention: list[dict[str, str]] = []

    observation_timestamp = _max_value(conn, "scan_runs", "timestamp")
    outcome_updated_at = _max_value(conn, "scan_results", "outcome_last_updated_at")
    evidence_rows = _count(conn, "scan_results")
    schema = _schema_status(conn)
    anomaly = _anomaly_state(conn)
    builds = _derived_builds(conn)
    bank = _bank_counts(conn)
    live = _live_scan(conn, observation_timestamp)
    horizon = _horizon(conn)
    memory = _memory(conn)
    backtests = _backtests(conn)
    experiments = _experiments(conn)

    payload["system"].update(
        {
            "observationTimestamp": observation_timestamp,
            "observationFreshness": freshness_status(observation_timestamp),
            "outcomeUpdatedAt": outcome_updated_at,
            "outcomeFreshness": freshness_status(outcome_updated_at),
            "evidenceBankRows": evidence_rows,
            "readiness": _readiness(bank, horizon),
            "schemaStatus": schema["status"],
            "anomalyCount": None if anomaly is None else anomaly["anomaly_count"],
            "derivedBuilds": builds,
        }
    )
    payload["live"].update(live)
    payload["horizon"].update(horizon)
    payload["memory"].update(memory)
    payload["backtests"].update(backtests)
    payload["experiments"].update(experiments)

    _append_build_attention(attention, builds)
    _append_schema_attention(attention, schema)
    _append_population_attention(attention, conn)
    _append_freshness_attention(attention, observation_timestamp, outcome_updated_at)
    _append_bank_attention(attention, bank, anomaly)
    _append_finding_attention(attention, conn)
    if not fmp_key_present:
        attention.append(
            _item(
                "action",
                "missing_fmp_key",
                "Missing FMP API key. Outcome refreshes will remain limited.",
                "/control",
            )
        )
    payload["attention"] = attention
    return payload


def _item(severity: str, code: str, message: str, href: str) -> dict[str, str]:
    if code not in ATTENTION_CODES:
        raise ValueError(f"unsupported attention code: {code}")
    return {"severity": severity, "code": code, "message": message, "href": href}


def _count(conn: sqlite3.Connection, table: str) -> Optional[int]:
    if not table_exists(conn, table):
        return None
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0] or 0)


def _max_value(conn: sqlite3.Connection, table: str, column: str) -> Optional[str]:
    if not table_exists(conn, table) or column not in table_columns(conn, table):
        return None
    value = conn.execute(f'SELECT MAX("{column}") FROM "{table}"').fetchone()[0]
    return str(value) if value else None


def _schema_status(conn: sqlite3.Connection) -> dict[str, Any]:
    checks = []
    warnings = []
    drift = []
    for name, expected in EXPECTED_SCHEMAS.items():
        table = expected["table"]
        expected_columns = expected["columns"]
        if not table_exists(conn, table):
            message = f"{name}: table {table} is missing."
            warnings.append(message)
            checks.append({"name": name, "status": "WARNING", "message": message})
            continue
        live_columns = table_columns(conn, table)
        missing = [column for column in expected_columns if column not in live_columns]
        mismatched = [
            {
                "column": column,
                "expected": normalize_type(expected_type),
                "actual": live_columns.get(column),
            }
            for column, expected_type in expected_columns.items()
            if column in live_columns
            and normalize_type(expected_type)
            and live_columns.get(column) != normalize_type(expected_type)
        ]
        if mismatched:
            message = f"{name}: schema drift detected in {len(mismatched)} columns."
            drift.append({"schema": name, "columns": mismatched})
            checks.append({"name": name, "status": "DRIFT DETECTED", "message": message})
        elif missing:
            message = f"{name}: missing columns: {', '.join(missing)}."
            warnings.append(message)
            checks.append({"name": name, "status": "WARNING", "message": message})
        else:
            checks.append({"name": name, "status": "PASS", "message": f"{name}: schema matches registry."})
    status = "PASS"
    if warnings:
        status = "WARNING"
    if drift:
        status = "DRIFT DETECTED"
    return {"status": status, "checks": checks, "warnings": warnings, "drift": drift}


def _anomaly_state(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    if not table_exists(conn, "scan_results") or not table_exists(conn, "feature_vectors"):
        return None
    try:
        return get_anomaly_monitor(conn)
    except sqlite3.Error:
        return None


def _derived_builds(conn: sqlite3.Connection) -> Optional[list[dict[str, Any]]]:
    if not table_exists(conn, "derived_builds"):
        return None
    rows = conn.execute(
        """
        SELECT artifact_type, status, built_at, artifact_row_count, build_id
        FROM derived_builds
        ORDER BY started_at, build_id
        """
    ).fetchall()
    return [
        {
            "artifact": row["artifact_type"],
            "status": row["status"],
            "builtAt": row["built_at"],
            "rowCount": row["artifact_row_count"],
            "buildId": row["build_id"],
        }
        for row in rows
    ]


def _bank_counts(conn: sqlite3.Connection) -> dict[str, Optional[int]]:
    recommendations = _count(conn, "scan_results")
    feature_vectors = _count(conn, "feature_vectors")
    scans = _count(conn, "scan_runs")
    result_columns = table_columns(conn, "scan_results") if table_exists(conn, "scan_results") else {}
    completed = (
        _labeled_count(conn, COMPLETED_OUTCOME_LABELS_SQL)
        if "stock_outcome_label" in result_columns
        else None
    )
    gate_snapshots = (
        _labeled_count(conn, "gate_snapshot_json IS NOT NULL AND gate_snapshot_json != ''")
        if "gate_snapshot_json" in result_columns
        else None
    )
    universe_snapshots = None
    if table_exists(conn, "scan_runs") and "universe_snapshot_json" in table_columns(conn, "scan_runs"):
        universe_snapshots = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scan_runs
                WHERE universe_snapshot_json IS NOT NULL AND universe_snapshot_json != ''
                """
            ).fetchone()[0]
            or 0
        )
    audit = None
    outcome_audit = _count(conn, "outcome_update_audit")
    institutional_audit = _count(conn, "institutional_audit_log")
    if outcome_audit is not None or institutional_audit is not None:
        audit = (outcome_audit or 0) + (institutional_audit or 0)
    stale_pending = None
    if "timestamp" in result_columns and "stock_outcome_label" in result_columns:
        stale_pending = int(
            conn.execute(
                """
                SELECT COUNT(*) FROM scan_results
                WHERE (stock_outcome_label IS NULL OR stock_outcome_label IN ('PENDING', 'Pending'))
                  AND datetime(timestamp) < datetime('now', '-7 days')
                """
            ).fetchone()[0]
            or 0
        )
    missing_vectors = None
    if recommendations is not None and feature_vectors is not None:
        missing_vectors = recommendations - feature_vectors
    missing_gate_snapshots = None
    if recommendations is not None and gate_snapshots is not None:
        missing_gate_snapshots = recommendations - gate_snapshots
    missing_universe = None
    if scans is not None and universe_snapshots is not None:
        missing_universe = scans - universe_snapshots
    return {
        "recommendations": recommendations,
        "completed": completed,
        "feature_vectors": feature_vectors,
        "scans": scans,
        "universe_snapshots": universe_snapshots,
        "audit": audit,
        "attributions": _count(conn, "gate_attributions"),
        "gate_alpha": _count(conn, "gate_alpha_metrics"),
        "regime_snapshots": _count(conn, "regime_snapshots"),
        "missing_vectors": missing_vectors,
        "missing_gate_snapshots": missing_gate_snapshots,
        "missing_universe": missing_universe,
        "stale_pending": stale_pending,
    }


def _labeled_count(conn: sqlite3.Connection, predicate: str) -> Optional[int]:
    if not table_exists(conn, "scan_results"):
        return None
    return int(conn.execute(f"SELECT COUNT(*) FROM scan_results WHERE {predicate}").fetchone()[0] or 0)


def _readiness(bank: dict[str, Optional[int]], horizon: dict[str, Any]) -> list[dict[str, str]]:
    recommendations = bank["recommendations"] or 0
    completed = bank["completed"] or 0
    patterns = horizon["patternCount"] or 0
    return [
        {
            "name": "Gate Intelligence",
            "status": readiness_status((horizon["gateIntelligenceGateCount"] or 0) > 0, completed > 0),
        },
        {"name": "Outcome Tracking", "status": readiness_status(completed > 0, recommendations > 0)},
        {
            "name": "Feature Store",
            "status": readiness_status(
                recommendations > 0 and bank["missing_vectors"] == 0,
                (bank["feature_vectors"] or 0) > 0,
            ),
        },
        {
            "name": "Universe Snapshot",
            "status": readiness_status(
                (bank["scans"] or 0) > 0 and bank["missing_universe"] == 0,
                (bank["universe_snapshots"] or 0) > 0,
            ),
        },
        {"name": "Audit Trail", "status": readiness_status((bank["audit"] or 0) > 0, completed > 0)},
        {"name": "Pattern Learning", "status": readiness_status(patterns > 0, completed > 0)},
        {
            "name": "Gate Attribution",
            "status": readiness_status((bank["attributions"] or 0) > 0, recommendations > 0),
        },
        {"name": "Gate Alpha", "status": readiness_status((bank["gate_alpha"] or 0) > 0, completed > 0)},
        {
            "name": "Regime Intelligence",
            "status": readiness_status((bank["regime_snapshots"] or 0) > 0, completed > 0),
        },
    ]


def _live_scan(conn: sqlite3.Connection, observation_timestamp: Optional[str]) -> dict[str, Any]:
    live = {
        "scanRunId": None,
        "observationTimestamp": observation_timestamp,
        "tickerCount": None,
        "directionCounts": {"Bullish": 0, "Bearish": 0, "Neutral": 0},
        "href": "/scanner",
    }
    if observation_timestamp is None or not table_exists(conn, "scan_runs"):
        return live
    row = conn.execute(
        """
        SELECT id FROM scan_runs
        WHERE timestamp = ?
        ORDER BY id DESC
        LIMIT 1
        """,
        (observation_timestamp,),
    ).fetchone()
    if row is None or not table_exists(conn, "scan_results"):
        return live
    run_id = int(row["id"])
    live["scanRunId"] = run_id
    live["tickerCount"] = int(
        conn.execute("SELECT COUNT(*) FROM scan_results WHERE run_id = ?", (run_id,)).fetchone()[0] or 0
    )
    counts = {"Bullish": 0, "Bearish": 0, "Neutral": 0}
    for item in conn.execute(
        """
        SELECT final_direction, COUNT(*) AS total
        FROM scan_results
        WHERE run_id = ?
        GROUP BY final_direction
        """,
        (run_id,),
    ):
        direction = item["final_direction"]
        if direction in counts:
            counts[direction] = int(item["total"] or 0)
    live["directionCounts"] = counts
    return live


def _horizon(conn: sqlite3.Connection) -> dict[str, Any]:
    leaders = {
        "strongestBullishPattern": _pattern_leader(conn, "Bullish"),
        "strongestBearishPattern": _pattern_leader(conn, "Bearish"),
        "topGlobalGateAlpha": _top_global_gate_alpha(conn),
        "mostPredictiveGate": _most_predictive_gate(conn),
        "highestExpectancyRegime": _highest_expectancy_regime(conn),
    }
    return {
        "patternCount": _count(conn, "pattern_intelligence"),
        "gateAlphaSegmentCount": _count(conn, "gate_alpha_metrics"),
        "gateIntelligenceGateCount": _count(conn, "gate_intelligence_metrics"),
        "regimeSnapshotCount": _count(conn, "regime_snapshots"),
        "existingLeaders": leaders,
        "href": "/control",
    }


def _json_object(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _pattern_leader(conn: sqlite3.Connection, direction: str) -> Optional[dict[str, Any]]:
    if not table_exists(conn, "pattern_intelligence"):
        return None
    chosen = None
    for row in conn.execute("SELECT * FROM pattern_intelligence").fetchall():
        signature = _json_object(row["pattern_signature"])
        if signature.get("direction") != direction:
            continue
        if chosen is None or (row["expectancy_score"] or 0) > (chosen["expectancy_score"] or 0):
            chosen = row
    if chosen is None:
        return None
    return {
        "pattern_id": chosen["pattern_id"],
        "signature": _json_object(chosen["pattern_signature"]),
        "sample_size": chosen["sample_size"],
        "win_rate": chosen["win_rate"],
        "expectancy_score": chosen["expectancy_score"],
    }


def _top_global_gate_alpha(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    if not table_exists(conn, "gate_alpha_metrics"):
        return None
    row = conn.execute(
        """
        SELECT gate_name, sector, market_regime, volatility_regime, sample_count,
               wins, losses, win_rate, avg_return, expectancy, confidence_score
        FROM gate_alpha_metrics
        WHERE sector = 'GLOBAL' AND market_regime = 'GLOBAL' AND volatility_regime = 'GLOBAL'
        ORDER BY expectancy DESC, sample_count DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def _most_predictive_gate(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    if not table_exists(conn, "gate_intelligence_metrics"):
        return None
    row = conn.execute(
        """
        SELECT gate_name, win_rate, predictive_score
        FROM gate_intelligence_metrics
        WHERE win_count + loss_count > 0
        ORDER BY predictive_score DESC, win_rate DESC, total_passes DESC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return None
    return {
        "gate": row["gate_name"],
        "win_rate": round(float(row["win_rate"] or 0), 1),
        "predictive_score": round(float(row["predictive_score"] or 0), 2),
    }


def _highest_expectancy_regime(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    if not table_exists(conn, "regime_snapshots") or not table_exists(conn, "scan_results"):
        return None
    if population_problems(conn):
        return None
    eligible = research_eligible_predicate("sr")
    try:
        rows = conn.execute(_REGIME_GROUP_SQL.format(eligible=eligible)).fetchall()
    except sqlite3.Error:
        return None
    if not rows:
        return None
    highest = sorted(rows, key=lambda row: (row["expectancy"] or 0, row["sample_size"]), reverse=True)[0]
    return {
        "regime": " | ".join(
            [
                f"trend:{highest['market_trend']}",
                f"vol:{highest['volatility_regime']}",
                f"liq:{highest['liquidity_regime']}",
                f"macro:{highest['macro_bias']}",
            ]
        ),
        "market_trend": highest["market_trend"],
        "volatility_regime": highest["volatility_regime"],
        "liquidity_regime": highest["liquidity_regime"],
        "macro_bias": highest["macro_bias"],
        "sample_size": highest["sample_size"],
        "wins": highest["wins"],
        "losses": highest["losses"],
        "win_rate": highest["win_rate"],
        "avg_return": highest["avg_return"],
        "expectancy": highest["expectancy"],
    }


def _memory(conn: sqlite3.Connection) -> dict[str, Any]:
    memory = {
        "eligibleObservations": None,
        "eligibleCompleted": None,
        "eligiblePending": None,
        "actionableCompleted": None,
        "href": "/research",
    }
    if not table_exists(conn, "scan_results") or not table_exists(conn, "observation_provenance"):
        return memory
    eligible = research_eligible_predicate("scan_results")
    try:
        memory["eligibleObservations"] = int(
            conn.execute(f"SELECT COUNT(*) FROM scan_results WHERE {eligible}").fetchone()[0] or 0
        )
        memory["eligibleCompleted"] = int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM scan_results
                WHERE {COMPLETED_OUTCOME_LABELS_SQL} AND {eligible}
                """
            ).fetchone()[0]
            or 0
        )
        memory["eligiblePending"] = int(
            conn.execute(
                f"""
                SELECT SUM(
                    CASE
                        WHEN (stock_outcome_label IS NULL OR stock_outcome_label = 'PENDING')
                         AND {PRIMARY_RETURN_SQL} IS NULL
                        THEN 1 ELSE 0
                    END
                )
                FROM scan_results
                WHERE {eligible}
                """
            ).fetchone()[0]
            or 0
        )
        memory["actionableCompleted"] = int(
            conn.execute(
                f"""
                SELECT COUNT(*) FROM scan_results
                WHERE {COMPLETED_OUTCOME_LABELS_SQL}
                  AND {ACTIONABLE_DIRECTION_SQL}
                  AND {eligible}
                """
            ).fetchone()[0]
            or 0
        )
    except sqlite3.Error:
        return {
            "eligibleObservations": None,
            "eligibleCompleted": None,
            "eligiblePending": None,
            "actionableCompleted": None,
            "href": "/research",
        }
    return memory


def _backtests(conn: sqlite3.Connection) -> dict[str, Any]:
    result = {"savedRunCount": None, "latest": None, "href": "/backtest"}
    if not table_exists(conn, "backtest_runs"):
        return result
    result["savedRunCount"] = _count(conn, "backtest_runs")
    if table_exists(conn, "backtest_metrics"):
        row = conn.execute(
            """
            SELECT br.id, br.name, br.status, br.created_at, bm.win_rate, bm.avg_return
            FROM backtest_runs br
            LEFT JOIN backtest_metrics bm ON bm.run_id = br.id
            ORDER BY br.created_at DESC, br.id DESC
            LIMIT 1
            """
        ).fetchone()
    else:
        row = conn.execute(
            """
            SELECT id, name, status, created_at, NULL AS win_rate, NULL AS avg_return
            FROM backtest_runs
            ORDER BY created_at DESC, id DESC
            LIMIT 1
            """
        ).fetchone()
    if row is not None:
        result["latest"] = {
            "id": row["id"],
            "name": row["name"],
            "status": row["status"],
            "createdAt": row["created_at"],
            "winRate": row["win_rate"],
            "avgReturn": row["avg_return"],
        }
    return result


def _experiments(conn: sqlite3.Connection) -> dict[str, Any]:
    result = {
        "enabledJobs": _enabled_jobs(conn),
        "runStatusCounts": _status_counts(conn, "research_job_runs"),
        "openFindings": None,
        "openFindingsBySeverity": None,
        "candidatesByStatus": _status_counts(conn, "rule_candidates"),
        "validationsByStatus": _status_counts(conn, "rule_validations"),
        "href": "/research-intelligence",
    }
    if table_exists(conn, "research_findings"):
        result["openFindings"] = int(
            conn.execute("SELECT COUNT(*) FROM research_findings WHERE status = 'open'").fetchone()[0] or 0
        )
        result["openFindingsBySeverity"] = {
            str(row["severity"]): int(row["total"])
            for row in conn.execute(
                """
                SELECT severity, COUNT(*) AS total
                FROM research_findings
                WHERE status = 'open'
                GROUP BY severity
                """
            )
        }
    return result


def _enabled_jobs(conn: sqlite3.Connection) -> Optional[int]:
    if not table_exists(conn, "research_jobs"):
        return None
    return int(conn.execute("SELECT COUNT(*) FROM research_jobs WHERE enabled = 1").fetchone()[0] or 0)


def _status_counts(conn: sqlite3.Connection, table: str) -> Optional[dict[str, int]]:
    if not table_exists(conn, table):
        return None
    return {
        str(row["status"]): int(row["total"])
        for row in conn.execute(f'SELECT status, COUNT(*) AS total FROM "{table}" GROUP BY status')
    }


def _append_build_attention(attention: list[dict[str, str]], builds: Optional[list[dict[str, Any]]]) -> None:
    for build in builds or []:
        if build["status"] in {"STARTED", "FAILED", "ROLLED_BACK"}:
            attention.append(
                _item(
                    "critical",
                    "derived_build_status",
                    f"{build['artifact']} build {build['buildId']} is {build['status']}.",
                    "/control",
                )
            )


def _append_schema_attention(attention: list[dict[str, str]], schema: dict[str, Any]) -> None:
    if schema["drift"]:
        attention.append(
            _item(
                "critical",
                "schema_drift",
                "SCHEMA DRIFT warning: live SQLite schema differs from registry.",
                "/control",
            )
        )
    for message in schema["warnings"]:
        attention.append(_item("action", "schema_warning", message, "/control"))


def _append_population_attention(attention: list[dict[str, str]], conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "scan_results") and not table_exists(conn, "observation_provenance"):
        return
    for problem in population_problems(conn):
        attention.append(_item("critical", "research_population", problem, "/research"))


def _append_freshness_attention(
    attention: list[dict[str, str]],
    observation_timestamp: Optional[str],
    outcome_updated_at: Optional[str],
) -> None:
    if observation_timestamp and freshness_status(observation_timestamp) != "READY":
        attention.append(
            _item(
                "action",
                "observation_freshness",
                "Latest scan is stale. Run a sandbox scan to refresh memory.",
                "/scanner",
            )
        )
    if outcome_updated_at and freshness_status(outcome_updated_at) != "READY":
        attention.append(
            _item(
                "action",
                "outcome_freshness",
                "Latest outcome update is stale. Run Update Outcomes when ready.",
                "/research",
            )
        )


def _append_bank_attention(
    attention: list[dict[str, str]],
    bank: dict[str, Optional[int]],
    anomaly: Optional[dict[str, Any]],
) -> None:
    stale_pending = bank["stale_pending"]
    if stale_pending:
        attention.append(
            _item(
                "action",
                "stale_pending_outcomes",
                f"Stale pending outcomes older than 7 days: {stale_pending}.",
                "/research",
            )
        )
    missing_vectors = bank["missing_vectors"]
    if missing_vectors:
        attention.append(
            _item(
                "action",
                "missing_feature_vectors",
                f"Missing feature vectors: {missing_vectors}.",
                "/research",
            )
        )
    if anomaly and anomaly["anomaly_count"]:
        attention.append(
            _item(
                "action",
                "anomaly_monitor",
                f"Anomaly Monitor warning: {anomaly['anomaly_count']} anomalies detected.",
                "/control",
            )
        )


def _append_finding_attention(attention: list[dict[str, str]], conn: sqlite3.Connection) -> None:
    if not table_exists(conn, "research_findings"):
        return
    rows = conn.execute(
        """
        SELECT id, title, severity
        FROM research_findings
        WHERE status = 'open' AND severity IN ('warning', 'critical')
        ORDER BY id
        """
    ).fetchall()
    for row in rows:
        attention.append(
            _item(
                "action",
                "research_finding",
                f"{row['severity']}: {row['title']}",
                "/research-findings",
            )
        )
