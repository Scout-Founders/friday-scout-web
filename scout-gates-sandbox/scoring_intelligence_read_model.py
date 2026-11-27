#!/usr/bin/env python3
"""Read-only view of the published scoring decomposition generation.

This module does not create tables, publish a generation, refresh outcomes,
or write a database. A missing or stale generation is reported and left as it is.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from memory_store import get_db_path
from migrate_scoring_decompositions import schema_status
from observation_evidence import ResearchPopulationError, population_stamp
from scoring_decomposition_store import (
    ScoringPublicationError,
    ambiguous_observations,
    current_scoring_generation,
    evidence_origin_distribution,
    get_scoring_decomposition,
    missing_or_conflicting_inputs,
    reconstruction_class_distribution,
    residual_distribution,
    scoring_input_fingerprint,
    source_provenance_fingerprint,
)
from scoring_provenance import INSPECTED_SINGLE_SCAN_METHODOLOGY_ID


DEFAULT_PAGE_SIZE = 25
MAX_PAGE_SIZE = 50
RECONSTRUCTION_CLASSES = (
    "A_evidence_determined",
    "B_algebraically_inferred",
    "C_ambiguous",
    "D_incomplete",
    "E_formula_mismatch",
    "E_evidence_conflict",
)


class ScoringReadError(Exception):
    """A scoring read request was not valid."""


def build_scoring_intelligence_summary(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Describe the published generation and whether it still matches the evidence."""
    path = _database_path(db_path)
    if not path.exists():
        return _unavailable("database is not available")
    try:
        conn = _connect_readonly(path)
    except sqlite3.Error:
        return _unavailable("database is not available")
    try:
        return _summary(conn)
    finally:
        conn.close()


def list_scoring_observations(
    db_path: Optional[Path] = None,
    *,
    ticker: str = "",
    direction: str = "",
    reconstruction_class: str = "",
    score_min: Optional[int] = None,
    score_max: Optional[int] = None,
    limit: int = DEFAULT_PAGE_SIZE,
    offset: int = 0,
) -> dict[str, Any]:
    """Return one bounded page of published scoring observations."""
    filters = _filters(
        ticker=ticker,
        direction=direction,
        reconstruction_class=reconstruction_class,
        score_min=score_min,
        score_max=score_max,
        limit=limit,
        offset=offset,
    )
    with _reading(db_path) as conn:
        if schema_status(conn) != "already_applied":
            return {
                "ok": True,
                "generation_status": _schema_state(conn),
                "total": 0,
                "observations": [],
                **filters,
            }
        where, params = _where(filters)
        total = int(
            conn.execute(
                f"""
                SELECT COUNT(*)
                FROM scoring_decompositions AS scoring
                JOIN scan_results AS sr ON sr.id = scoring.scan_result_id
                JOIN observation_provenance AS op
                  ON op.observation_uid = scoring.observation_uid
                 AND op.scan_result_id = scoring.scan_result_id
                WHERE {where}
                """,
                params,
            ).fetchone()[0]
        )
        rows = conn.execute(
            f"""
            SELECT scoring.observation_uid, scoring.scan_result_id, sr.ticker,
                   sr.timestamp AS scanned_at, scoring.direction,
                   scoring.captured_scout_score, scoring.unclamped_score,
                   scoring.clamped_score, scoring.reconstruction_class,
                   scoring.formula_confirmation, scoring.warnings_json
            FROM scoring_decompositions AS scoring
            JOIN scan_results AS sr ON sr.id = scoring.scan_result_id
            JOIN observation_provenance AS op
              ON op.observation_uid = scoring.observation_uid
             AND op.scan_result_id = scoring.scan_result_id
            WHERE {where}
            ORDER BY scoring.scan_result_id
            LIMIT ? OFFSET ?
            """,
            (*params, filters["limit"], filters["offset"]),
        ).fetchall()
        return {
            "ok": True,
            "total": total,
            "limit": filters["limit"],
            "offset": filters["offset"],
            "observations": [_list_row(row) for row in rows],
        }


def get_scoring_observation(
    observation_uid: str,
    db_path: Optional[Path] = None,
) -> Optional[dict[str, Any]]:
    """Return one stored decomposition, or None when that uid is not published."""
    uid = _observation_uid(observation_uid)
    with _reading(db_path) as conn:
        if schema_status(conn) != "already_applied":
            return None
        stored = get_scoring_decomposition(conn, uid)
        if stored is None:
            return None
        meta = conn.execute(
            """
            SELECT sr.ticker, sr.timestamp AS scanned_at, sr.final_direction
            FROM scan_results AS sr
            JOIN observation_provenance AS op
              ON op.scan_result_id = sr.id
             AND op.observation_uid = ?
            WHERE sr.id = ?
            """,
            (uid, stored["scan_result_id"]),
        ).fetchone()
        stored["ticker"] = meta["ticker"] if meta is not None else None
        stored["scanned_at"] = meta["scanned_at"] if meta is not None else None
        stored["stored_final_direction"] = meta["final_direction"] if meta is not None else None
        return stored


def list_ambiguous_scoring_observations(db_path: Optional[Path] = None) -> dict[str, Any]:
    """Return every ambiguous published row without choosing a candidate."""
    with _reading(db_path) as conn:
        if schema_status(conn) != "already_applied":
            return {"ok": True, "generation_status": _schema_state(conn), "observations": []}
        rows = []
        for item in ambiguous_observations(conn):
            meta = conn.execute(
                """
                SELECT sr.ticker, sr.timestamp AS scanned_at, scoring.captured_scout_score,
                       scoring.direction, scoring.warnings_json
                FROM scoring_decompositions AS scoring
                JOIN scan_results AS sr ON sr.id = scoring.scan_result_id
                JOIN observation_provenance AS op
                  ON op.observation_uid = scoring.observation_uid
                 AND op.scan_result_id = scoring.scan_result_id
                WHERE scoring.observation_uid = ?
                """,
                (item["observation_uid"],),
            ).fetchone()
            rows.append(
                {
                    "observation_uid": item["observation_uid"],
                    "scan_result_id": item["scan_result_id"],
                    "ticker": meta["ticker"] if meta is not None else None,
                    "scanned_at": meta["scanned_at"] if meta is not None else None,
                    "direction": meta["direction"] if meta is not None else None,
                    "captured_scout_score": meta["captured_scout_score"] if meta is not None else None,
                    "unclamped_score": item["unclamped_score"],
                    "clamped_score": item["clamped_score"],
                    "reconstruction_error": item["reconstruction_error"],
                    "penalty_candidates": item["penalty_candidates"],
                }
            )
        return {"ok": True, "observations": rows}


def _summary(conn: sqlite3.Connection) -> dict[str, Any]:
    if schema_status(conn) != "already_applied":
        return _schema_absent(conn)
    try:
        generation = current_scoring_generation(conn)
    except ScoringPublicationError as exc:
        return {
            "ok": True,
            "generation_status": "inconsistent",
            "generation_message": f"{exc} The stored generation was left unchanged.",
            "published": None,
            "current_population": _current_population(conn),
            "distributions": _empty_distributions(),
            "counts": {"published_rows": 0, "ambiguous": 0, "missing_or_conflicting": 0},
        }
    if generation is None:
        return {
            "ok": True,
            "generation_status": "missing_generation",
            "generation_message": "No completed scoring generation is published. Nothing was rebuilt.",
            "published": None,
            "current_population": _current_population(conn),
            "distributions": _empty_distributions(),
            "counts": {"published_rows": 0, "ambiguous": 0, "missing_or_conflicting": 0},
        }
    identity = _published_identity(conn, generation["build"]["build_id"])
    current = _current_population(conn)
    status, message = _freshness(generation, identity, current)
    classes = reconstruction_class_distribution(conn)
    residuals = residual_distribution(conn)
    return {
        "ok": True,
        "generation_status": status,
        "generation_message": message,
        "published": {
            "build_id": generation["build"]["build_id"],
            "built_at": generation["build"]["built_at"],
            "status": generation["build"]["status"],
            "methodology_id": identity["methodology_id"],
            "source_snapshot_sha256": identity["source_snapshot_sha256"],
            "deployment_verification": identity["deployment_verification"],
            "verified_deployment_id": identity["verified_deployment_id"],
            "decomposer_version": identity["decomposer_version"],
            "schema_version": identity["schema_version"],
            "eligible_population_hash": generation["build"]["eligible_population_hash"],
            "scoring_input_fingerprint": generation["scoring_input_fingerprint"],
            "source_provenance_fingerprint": generation["source_provenance_fingerprint"],
            "authorized_methodology_id": INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
        },
        "current_population": current,
        "distributions": {
            "reconstruction_class": classes,
            "evidence_origin": evidence_origin_distribution(conn),
            "independent_residual": residuals.get("independent", {}),
            "mathematical_compatibility_residual": residuals.get("mathematical_compatibility", {}),
        },
        "counts": {
            "published_rows": generation["live_row_count"],
            "ambiguous": classes.get("C_ambiguous", 0),
            "missing_or_conflicting": len(missing_or_conflicting_inputs(conn)),
        },
    }


def _published_identity(conn: sqlite3.Connection, build_id: str) -> dict[str, Any]:
    rows = conn.execute(
        """
        SELECT DISTINCT methodology_id, source_snapshot_sha256, deployment_verification,
               verified_deployment_id, decomposer_version, schema_version
        FROM scoring_decompositions
        WHERE build_id = ?
        """,
        (build_id,),
    ).fetchall()
    if len(rows) != 1:
        return {
            "methodology_id": None,
            "source_snapshot_sha256": None,
            "deployment_verification": None,
            "verified_deployment_id": None,
            "decomposer_version": None,
            "schema_version": None,
            "consistent": False,
        }
    row = rows[0]
    return {
        "methodology_id": row["methodology_id"],
        "source_snapshot_sha256": row["source_snapshot_sha256"],
        "deployment_verification": row["deployment_verification"],
        "verified_deployment_id": row["verified_deployment_id"],
        "decomposer_version": row["decomposer_version"],
        "schema_version": row["schema_version"],
        "consistent": True,
    }


def _current_population(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    try:
        stamp = population_stamp(conn)
    except (ResearchPopulationError, sqlite3.Error):
        return None
    try:
        rows = [
            (
                row["observation_uid"],
                int(row["scan_result_id"]),
                row["raw_result_json"],
                row["engine_version"],
            )
            for row in conn.execute(
                """
                SELECT op.observation_uid, sr.id AS scan_result_id,
                       sr.raw_result_json, sr.engine_version
                FROM scan_results AS sr
                JOIN observation_provenance AS op ON op.scan_result_id = sr.id
                WHERE op.research_eligible = 1
                ORDER BY sr.id, op.observation_uid
                """
            )
        ]
    except sqlite3.Error:
        return None
    return {
        "classifier_version": stamp.classifier_version,
        "eligible_count": stamp.eligible_population_count,
        "eligible_population_hash": stamp.eligible_population_hash,
        "scoring_input_fingerprint": scoring_input_fingerprint(
            [(uid, scan_id, raw) for uid, scan_id, raw, _engine in rows]
        ),
        "source_provenance_fingerprint": source_provenance_fingerprint(
            [(uid, scan_id, engine) for uid, scan_id, _raw, engine in rows]
        ),
    }


def _freshness(
    generation: dict[str, Any],
    identity: dict[str, Any],
    current: Optional[dict[str, Any]],
) -> tuple[str, str]:
    if not identity["consistent"] or current is None:
        return (
            "inconsistent",
            "The published scoring rows do not share one methodology identity, or the current evidence population cannot be read. Nothing was rebuilt.",
        )
    published_count = generation["live_row_count"]
    reasons = []
    if generation["build"]["eligible_population_hash"] != current["eligible_population_hash"]:
        reasons.append("eligible population")
    if published_count != current["eligible_count"]:
        reasons.append("published row count")
    if generation["scoring_input_fingerprint"] != current["scoring_input_fingerprint"]:
        reasons.append("scoring inputs")
    if generation["source_provenance_fingerprint"] != current["source_provenance_fingerprint"]:
        reasons.append("source provenance")
    if identity["methodology_id"] != INSPECTED_SINGLE_SCAN_METHODOLOGY_ID:
        reasons.append("methodology")
    if not reasons:
        return (
            "current",
            "The published generation matches the current eligible population and the scoring inputs used to decompose it.",
        )
    if "scoring inputs" in reasons or "source provenance" in reasons:
        status = "stale_inputs"
    elif "eligible population" in reasons or "published row count" in reasons:
        status = "stale_population"
    else:
        status = "inconsistent"
    return (
        status,
        "The published generation does not match the current evidence: "
        + ", ".join(reasons)
        + ". The stored generation was left unchanged.",
    )


def _list_row(row: sqlite3.Row) -> dict[str, Any]:
    warnings = json.loads(row["warnings_json"] or "[]")
    return {
        "observation_uid": row["observation_uid"],
        "scan_result_id": row["scan_result_id"],
        "ticker": row["ticker"],
        "scanned_at": row["scanned_at"],
        "direction": row["direction"],
        "captured_scout_score": row["captured_scout_score"],
        "unclamped_score": row["unclamped_score"],
        "clamped_score": row["clamped_score"],
        "reconstruction_class": row["reconstruction_class"],
        "formula_confirmation": row["formula_confirmation"],
        "warning_count": len(warnings) if isinstance(warnings, list) else 0,
    }


def _filters(**values: Any) -> dict[str, Any]:
    ticker = str(values["ticker"] or "").strip().upper()
    if ticker and (len(ticker) > 16 or not all(character.isalnum() or character in ".-" for character in ticker)):
        raise ScoringReadError("ticker filter is not valid")
    direction = str(values["direction"] or "").strip().upper()
    if direction and (len(direction) > 32 or not direction.replace("_", "").isalpha()):
        raise ScoringReadError("direction filter is not valid")
    reconstruction_class = str(values["reconstruction_class"] or "").strip()
    if reconstruction_class and reconstruction_class not in RECONSTRUCTION_CLASSES:
        raise ScoringReadError("reconstruction class filter is not valid")
    limit = values["limit"]
    offset = values["offset"]
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= MAX_PAGE_SIZE:
        raise ScoringReadError("limit must be from 1 to 50")
    if isinstance(offset, bool) or not isinstance(offset, int) or offset < 0:
        raise ScoringReadError("offset cannot be negative")
    score_min = values["score_min"]
    score_max = values["score_max"]
    for label, score in (("score_min", score_min), ("score_max", score_max)):
        if score is not None and (isinstance(score, bool) or not isinstance(score, int)):
            raise ScoringReadError(f"{label} must be an integer")
    if score_min is not None and score_max is not None and score_min > score_max:
        raise ScoringReadError("score range is reversed")
    return {
        "ticker": ticker,
        "direction": direction,
        "reconstruction_class": reconstruction_class,
        "score_min": score_min,
        "score_max": score_max,
        "limit": limit,
        "offset": offset,
    }


def _where(filters: dict[str, Any]) -> tuple[str, list[Any]]:
    clauses = ["1 = 1"]
    params: list[Any] = []
    if filters["ticker"]:
        clauses.append("UPPER(sr.ticker) = ?")
        params.append(filters["ticker"])
    if filters["direction"]:
        clauses.append("UPPER(scoring.direction) = ?")
        params.append(filters["direction"])
    if filters["reconstruction_class"]:
        clauses.append("scoring.reconstruction_class = ?")
        params.append(filters["reconstruction_class"])
    if filters["score_min"] is not None:
        clauses.append("scoring.captured_scout_score >= ?")
        params.append(filters["score_min"])
    if filters["score_max"] is not None:
        clauses.append("scoring.captured_scout_score <= ?")
        params.append(filters["score_max"])
    return " AND ".join(clauses), params


def _observation_uid(value: str) -> str:
    uid = str(value or "").strip()
    if not uid or len(uid) > 200 or any(character in uid for character in "/\\\n\r\t"):
        raise ScoringReadError("observation uid is not valid")
    return uid


def _schema_absent(conn: sqlite3.Connection) -> dict[str, Any]:
    state = _schema_state(conn)
    if state == "missing_schema":
        message = "The scoring decomposition schema is not applied. Nothing was created."
    else:
        message = "The scoring schema is present but does not match the read contract. Nothing was changed."
    return {
        "ok": True,
        "generation_status": state,
        "generation_message": message,
        "published": None,
        "current_population": _current_population(conn),
        "distributions": _empty_distributions(),
        "counts": {"published_rows": 0, "ambiguous": 0, "missing_or_conflicting": 0},
    }


def _schema_state(conn: sqlite3.Connection) -> str:
    return "missing_schema" if schema_status(conn) == "missing" else "inconsistent"


def _empty_distributions() -> dict[str, Any]:
    return {
        "reconstruction_class": {},
        "evidence_origin": {},
        "independent_residual": {},
        "mathematical_compatibility_residual": {},
    }


def _unavailable(message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "generation_status": "unavailable",
        "generation_message": message,
        "published": None,
        "current_population": None,
        "distributions": _empty_distributions(),
        "counts": {"published_rows": 0, "ambiguous": 0, "missing_or_conflicting": 0},
    }


def _database_path(db_path: Optional[Path]) -> Path:
    return Path(db_path) if db_path is not None else get_db_path()


def _connect_readonly(path: Path) -> sqlite3.Connection:
    quoted = urllib.parse.quote(str(path), safe="/:")
    conn = sqlite3.connect(f"file:{quoted}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


class _reading:
    def __init__(self, db_path: Optional[Path]) -> None:
        self.path = _database_path(db_path)

    def __enter__(self) -> sqlite3.Connection:
        if not self.path.exists():
            raise ScoringReadError("database is not available")
        try:
            self.conn = _connect_readonly(self.path)
        except sqlite3.Error as exc:
            raise ScoringReadError("database is not available") from exc
        return self.conn

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.conn.close()
