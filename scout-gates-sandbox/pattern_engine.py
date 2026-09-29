#!/usr/bin/env python3
"""Pattern Intelligence engine for Scout Horizon-1 sandbox memory."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from derived_build_manifest import complete_build, fail_build, get_build, start_build
from observation_evidence import assert_research_population, population_stamp, research_eligible_predicate


MIN_PATTERN_SAMPLE_SIZE = 3
PATTERN_BUILDER_VERSION = "pattern-1"
PATTERN_BACKUP_TABLE = "pattern_intelligence_backup"
PATTERN_COLUMNS = (
    "pattern_id",
    "pattern_signature",
    "sample_size",
    "win_rate",
    "loss_rate",
    "avg_1d",
    "avg_3d",
    "avg_5d",
    "avg_10d",
    "avg_20d",
    "expectancy_score",
    "confidence_score",
    "created_at_utc",
    "build_id",
)


class PatternPublishError(Exception):
    """The pattern publication stopped before replacing the live rows."""


def json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def json_load(value: Any) -> Any:
    if value in (None, ""):
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return value


def init_pattern_store(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS pattern_intelligence (
            pattern_id TEXT PRIMARY KEY,
            pattern_signature TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            loss_rate REAL NOT NULL,
            avg_1d REAL,
            avg_3d REAL,
            avg_5d REAL,
            avg_10d REAL,
            avg_20d REAL,
            expectancy_score REAL NOT NULL,
            confidence_score REAL NOT NULL,
            created_at_utc TEXT NOT NULL,
            build_id TEXT
        );

        CREATE TABLE IF NOT EXISTS pattern_intelligence_backup (
            pattern_id TEXT PRIMARY KEY,
            pattern_signature TEXT NOT NULL,
            sample_size INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            loss_rate REAL NOT NULL,
            avg_1d REAL,
            avg_3d REAL,
            avg_5d REAL,
            avg_10d REAL,
            avg_20d REAL,
            expectancy_score REAL NOT NULL,
            confidence_score REAL NOT NULL,
            created_at_utc TEXT NOT NULL,
            build_id TEXT
        );

        CREATE INDEX IF NOT EXISTS idx_pattern_expectancy
            ON pattern_intelligence(expectancy_score DESC);
        CREATE INDEX IF NOT EXISTS idx_pattern_confidence
            ON pattern_intelligence(confidence_score DESC);
        """
    )


def to_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def average(values: list[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def bucket(value: Any, low: float, high: float, labels: tuple[str, str, str]) -> str:
    numeric = to_float(value)
    if numeric is None:
        return "unknown"
    if numeric < low:
        return labels[0]
    if numeric > high:
        return labels[2]
    return labels[1]


def volatility_regime(row: sqlite3.Row) -> str:
    iv = row["iv_percentile"]
    if iv is not None:
        return bucket(iv, 35, 65, ("low_iv", "mid_iv", "high_iv"))
    atr = row["atr"]
    return bucket(atr, 2, 6, ("low_atr", "mid_atr", "high_atr"))


def liquidity_regime(row: sqlite3.Row) -> str:
    rel_volume = row["relative_volume"]
    if rel_volume is not None:
        return bucket(rel_volume, 0.8, 1.5, ("thin_volume", "normal_volume", "high_volume"))
    option_score = row["options_volume_score"]
    return bucket(option_score, 35, 70, ("thin_options", "normal_options", "high_options"))


def gate_profile(row: sqlite3.Row) -> str:
    scores = json_load(row["gate_scores_json"]) or {}
    if not isinstance(scores, dict):
        return "gate_unknown"
    numeric_scores = [to_float(value) for value in scores.values()]
    numeric_scores = [value for value in numeric_scores if value is not None]
    if not numeric_scores:
        states = json_load(row["gate_states_json"]) or {}
        if isinstance(states, dict):
            pass_rate = sum(1 for value in states.values() if value is True) / max(len(states), 1)
            return bucket(pass_rate, 0.5, 0.8, ("weak_gate_pass", "mixed_gate_pass", "strong_gate_pass"))
        return "gate_unknown"
    return bucket(sum(numeric_scores) / len(numeric_scores), 40, 70, ("weak_gate_score", "mixed_gate_score", "strong_gate_score"))


def pattern_id(signature: dict[str, Any]) -> str:
    return hashlib.sha1(json_dump(signature).encode("utf-8")).hexdigest()[:16]


def completed_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    assert_research_population(conn)
    eligible = research_eligible_predicate("sr")
    return conn.execute(
        f"""
        SELECT
            sr.id AS recommendation_id,
            sr.run_id AS scan_id,
            sr.final_direction,
            sr.stock_outcome_label,
            sr.return_1d AS sr_return_1d,
            sr.return_3d AS sr_return_3d,
            sr.return_5d AS sr_return_5d,
            sr.return_10d AS sr_return_10d,
            sr.return_20d AS sr_return_20d,
            fv.*
        FROM scan_results sr
        LEFT JOIN feature_vectors fv ON fv.recommendation_id = sr.id
        WHERE sr.stock_outcome_label IN ('WIN', 'LOSS', 'FLAT')
          AND {eligible}
        """
    ).fetchall()


def signatures_for_row(row: sqlite3.Row) -> list[dict[str, Any]]:
    direction = row["final_direction"] or "Unknown"
    sector = row["sector_name"] or "Unknown"
    vol = volatility_regime(row)
    liquidity = liquidity_regime(row)
    gate = gate_profile(row)
    return [
        {"kind": "direction_sector", "direction": direction, "sector": sector},
        {"kind": "direction_vol_liquidity", "direction": direction, "volatility_regime": vol, "liquidity_regime": liquidity},
        {"kind": "direction_gate_profile", "direction": direction, "gate_score_range": gate},
        {"kind": "feature_similarity", "direction": direction, "sector": sector, "volatility_regime": vol, "liquidity_regime": liquidity, "gate_score_range": gate},
    ]


def row_return(row: sqlite3.Row, horizon: str) -> Optional[float]:
    value = row[f"sr_return_{horizon}"]
    if value is None and horizon != "10d":
        value = row[f"return_{horizon}"]
    return to_float(value)


def summarize_pattern(signature: dict[str, Any], rows: list[sqlite3.Row], created_at: str) -> dict[str, Any]:
    sample_size = len(rows)
    wins = sum(1 for row in rows if row["stock_outcome_label"] == "WIN")
    losses = sum(1 for row in rows if row["stock_outcome_label"] == "LOSS")
    win_rate = round(wins / sample_size * 100, 2) if sample_size else 0.0
    loss_rate = round(losses / sample_size * 100, 2) if sample_size else 0.0
    avg_1d = average([value for row in rows if (value := row_return(row, "1d")) is not None])
    avg_3d = average([value for row in rows if (value := row_return(row, "3d")) is not None])
    avg_5d = average([value for row in rows if (value := row_return(row, "5d")) is not None])
    avg_10d = average([value for row in rows if (value := row_return(row, "10d")) is not None])
    avg_20d = average([value for row in rows if (value := row_return(row, "20d")) is not None])
    return_values = [value for value in (avg_1d, avg_3d, avg_5d, avg_10d, avg_20d) if value is not None]
    avg_return_component = sum(return_values) / len(return_values) if return_values else 0.0
    expectancy_score = round((win_rate - loss_rate) + avg_return_component, 2)
    confidence_score = round(min(sample_size / 25, 1.0) * 60 + abs(win_rate - loss_rate) / 100 * 40, 2)
    return {
        "pattern_id": pattern_id(signature),
        "pattern_signature": signature,
        "sample_size": sample_size,
        "win_rate": win_rate,
        "loss_rate": loss_rate,
        "avg_1d": avg_1d,
        "avg_3d": avg_3d,
        "avg_5d": avg_5d,
        "avg_10d": avg_10d,
        "avg_20d": avg_20d,
        "expectancy_score": expectancy_score,
        "confidence_score": confidence_score,
        "created_at_utc": created_at,
    }


def _stamp_identity(stamp: Any) -> tuple[str, int, str]:
    return (
        stamp.classifier_version,
        stamp.eligible_population_count,
        stamp.eligible_population_hash,
    )


def _end_read_transaction(conn: sqlite3.Connection) -> None:
    try:
        conn.commit()
    except sqlite3.OperationalError:
        pass


def _rollback(conn: sqlite3.Connection) -> None:
    try:
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError:
        pass


def _fail_started_build(conn: sqlite3.Connection, build_id: str, exc: BaseException) -> None:
    _rollback(conn)
    _end_read_transaction(conn)
    current = get_build(conn, build_id)
    if current is None or current["status"] != "STARTED":
        return
    message = str(exc).strip() or exc.__class__.__name__
    fail_build(conn, build_id, error_text=message[:500])
    _end_read_transaction(conn)


def _replace_live_patterns(
    conn: sqlite3.Connection,
    candidates: list[dict[str, Any]],
    build_id: str,
    probe: Optional[Callable[[str], None]],
) -> None:
    listed = ", ".join(PATTERN_COLUMNS)
    placeholders = ", ".join("?" for _ in PATTERN_COLUMNS)
    conn.execute(f"DELETE FROM {PATTERN_BACKUP_TABLE}")
    conn.execute(
        f"""
        INSERT INTO {PATTERN_BACKUP_TABLE} ({listed})
        SELECT {listed} FROM pattern_intelligence
        """
    )
    conn.execute("DELETE FROM pattern_intelligence")
    if probe is not None:
        probe("after_delete")
    for pattern in candidates:
        conn.execute(
            f"""
            INSERT INTO pattern_intelligence ({listed})
            VALUES ({placeholders})
            """,
            tuple(_pattern_storage_value(pattern, column, build_id) for column in PATTERN_COLUMNS),
        )


def _pattern_storage_value(pattern: dict[str, Any], column: str, build_id: str) -> Any:
    if column == "build_id":
        return build_id
    if column == "pattern_signature":
        return json_dump(pattern["pattern_signature"])
    return pattern[column]


def calculate_pattern_candidates(rows: list[sqlite3.Row], created_at: str) -> list[dict[str, Any]]:
    """Group eligible completed rows. This does not write pattern_intelligence."""
    grouped: dict[str, dict[str, Any]] = {}
    for row in rows:
        for signature in signatures_for_row(row):
            key = json_dump(signature)
            item = grouped.setdefault(key, {"signature": signature, "rows": []})
            item["rows"].append(row)
    return [
        summarize_pattern(item["signature"], item["rows"], created_at)
        for item in grouped.values()
        if len(item["rows"]) >= MIN_PATTERN_SAMPLE_SIZE
    ]


def validate_pattern_candidates(candidates: list[dict[str, Any]]) -> None:
    seen: set[str] = set()
    for pattern in candidates:
        signature = pattern["pattern_signature"]
        if not isinstance(signature, dict):
            raise PatternPublishError("pattern signature must be an object")
        if int(pattern["sample_size"]) < MIN_PATTERN_SAMPLE_SIZE:
            raise PatternPublishError("candidate is below the minimum sample size")
        expected_id = pattern_id(signature)
        if pattern["pattern_id"] != expected_id:
            raise PatternPublishError("pattern_id does not match its signature")
        if pattern["pattern_id"] in seen:
            raise PatternPublishError(f"duplicate pattern_id {pattern['pattern_id']}")
        seen.add(pattern["pattern_id"])


def publish_pattern_intelligence(
    conn: sqlite3.Connection,
    *,
    _probe: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Publish one manifest-backed pattern generation.

    Calculation runs before the write lock. The live table changes only inside
    a short transaction that rechecks the population stamp after BEGIN IMMEDIATE.
    """
    init_pattern_store(conn)
    _end_read_transaction(conn)
    assert_research_population(conn)
    opening = population_stamp(conn)
    _end_read_transaction(conn)
    started = start_build(
        conn,
        artifact_type="pattern_intelligence",
        builder_version=PATTERN_BUILDER_VERSION,
        stamp=opening,
    )
    _end_read_transaction(conn)
    build_id = started["build_id"]
    try:
        if _probe is not None:
            _probe("before_calculate")
        rows = completed_rows(conn)
        created_at = datetime.now(timezone.utc).isoformat()
        candidates = calculate_pattern_candidates(rows, created_at)
        validate_pattern_candidates(candidates)
        _end_read_transaction(conn)
        if _probe is not None:
            _probe("before_lock")
        _end_read_transaction(conn)
    except Exception as exc:
        _fail_started_build(conn, build_id, exc)
        raise

    try:
        conn.execute("BEGIN IMMEDIATE")
        if _probe is not None:
            _probe("inside_transaction")
        final = population_stamp(conn)
        if _stamp_identity(final) != _stamp_identity(opening):
            raise PatternPublishError("population stamp changed before publication")
        current = get_build(conn, build_id)
        if current is None or current["status"] != "STARTED":
            raise PatternPublishError("pattern build is no longer STARTED")
        if (
            current["classifier_version"],
            current["eligible_population_count"],
            current["eligible_population_hash"],
        ) != _stamp_identity(opening):
            raise PatternPublishError("manifest stamp does not match the opening population")
        _replace_live_patterns(conn, candidates, build_id, _probe)
        complete_build(
            conn,
            build_id,
            built_at=created_at,
            artifact_row_count=len(candidates),
        )
        if _probe is not None:
            _probe("after_complete")
        conn.execute("COMMIT")
    except Exception as exc:
        _rollback(conn)
        _fail_started_build(conn, build_id, exc)
        raise

    return {
        "ok": True,
        "completed_outcomes": len(rows),
        "patterns_rebuilt": len(candidates),
        "build_id": build_id,
        "status": "COMPLETED",
    }


def rebuild_pattern_intelligence(
    conn: sqlite3.Connection,
    *,
    _probe: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Explicit Horizon pattern rebuild. Scan save does not call this."""
    return publish_pattern_intelligence(conn, _probe=_probe)


def pattern_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "pattern_id": row["pattern_id"],
        "pattern_signature": json_load(row["pattern_signature"]) or {},
        "sample_size": row["sample_size"],
        "win_rate": row["win_rate"],
        "loss_rate": row["loss_rate"],
        "avg_1d": row["avg_1d"],
        "avg_3d": row["avg_3d"],
        "avg_5d": row["avg_5d"],
        "avg_10d": row["avg_10d"],
        "avg_20d": row["avg_20d"],
        "expectancy_score": row["expectancy_score"],
        "confidence_score": row["confidence_score"],
        "created_at_utc": row["created_at_utc"],
    }


def get_pattern_intelligence(conn: sqlite3.Connection) -> dict[str, Any]:
    init_pattern_store(conn)
    rows = [pattern_row(row) for row in conn.execute("SELECT * FROM pattern_intelligence").fetchall()]
    bullish = [row for row in rows if row["pattern_signature"].get("direction") == "Bullish"]
    bearish = [row for row in rows if row["pattern_signature"].get("direction") == "Bearish"]
    strongest_bullish = max(bullish, key=lambda row: row["expectancy_score"], default=None)
    strongest_bearish = max(bearish, key=lambda row: row["expectancy_score"], default=None)
    highest_confidence = max(rows, key=lambda row: row["confidence_score"], default=None)
    weakest = min(rows, key=lambda row: row["expectancy_score"], default=None)
    avg_sample = round(sum(row["sample_size"] for row in rows) / len(rows), 2) if rows else 0.0
    return {
        "total_patterns_discovered": len(rows),
        "strongest_bullish_pattern": strongest_bullish,
        "strongest_bearish_pattern": strongest_bearish,
        "highest_confidence_pattern": highest_confidence,
        "weakest_pattern": weakest,
        "average_sample_size": avg_sample,
        "patterns": sorted(rows, key=lambda row: row["expectancy_score"], reverse=True),
    }
