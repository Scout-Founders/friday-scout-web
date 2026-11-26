#!/usr/bin/env python3
"""Explicit publication of scoring decompositions.

Scan capture and outcome refresh do not call this module. A generation is
published only by ``publish_scoring_decompositions``.

Failure policy: every canonical eligible observation is calculated. A
returned decomposition, including incomplete, conflict, and formula-mismatch
results, is stored. An exception from the decomposer aborts the build before
the live table is replaced, and the error is written on the manifest row.
Ambiguous totals stay null. No candidate is stored as the definitive score.

Versions stored on each row are independent:

- methodology_id: inspected scoring formula
- decomposer_version: this parser
- schema_version: this table contract
- deployment_verification: not the formula
- sandbox_engine_version: copied from scan_results.engine_version

Fingerprints, separate from the shared population stamp:

- scoring_input_fingerprint hashes the observation uid, scan id, and the
  exact raw_result_json text the decomposer parsed. Null text is a different
  token from an empty string. Outcome columns are not inputs and are not
  hashed. scan_results.scout_score is not read, not copied, and not hashed.
  The captured score stored here comes from the payload. A disagreement
  between that column and the payload is not detected and does not change
  the captured score.
- source_provenance_fingerprint hashes the same identity plus
  engine_version, because that value is copied onto the row and is not a
  scoring input. An engine_version edit therefore changes this fingerprint
  without changing the scoring-input fingerprint.

The shared derived_builds schema has no metadata column for these hashes.
They are stored on every scoring row and returned by
current_scoring_generation. Pattern, Gate Alpha, and Gate Intelligence
publication are unchanged.

This publisher replaces only the baseline methodology
scout-single-v1+243ce011a4e8ad9f. If either scoring table contains another
methodology, publication aborts before deleting any row.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from derived_build_manifest import (
    complete_build,
    fail_build,
    get_build,
    latest_completed_build,
    start_build,
)
from migrate_derived_builds import CANONICAL_DB
from migrate_scoring_decompositions import (
    ARTIFACT_TYPE,
    BACKUP_TABLE,
    COLUMNS,
    LIVE_TABLE,
    schema_status,
)
from observation_evidence import (
    assert_research_population,
    population_stamp,
    research_eligible_predicate,
)
from scoring_provenance import (
    DECOMPOSER_VERSION,
    DEPLOYMENT_VERIFICATION_STATUS,
    INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
    INSPECTED_SOURCE_SHA256,
    SCORING_DECOMPOSITION_SCHEMA_VERSION,
    ReconstructionClass,
    SingleScanDecomposition,
    decompose_single_scan,
)


BUILDER_VERSION = DECOMPOSER_VERSION
SCORING_INPUT_FINGERPRINT_SPEC = "scoring-payload-v1"
SOURCE_PROVENANCE_FINGERPRINT_SPEC = "source-provenance-v1"

CONTRIBUTION_FIELDS = (
    "base_score",
    "atlas_raw",
    "atlas_contribution",
    "oracle_raw",
    "oracle_contribution",
    "phantom_raw",
    "phantom_contribution",
    "catalyst_raw",
    "catalyst_contribution",
    "specter_clean_bonus",
    "specter_penalty",
    "wind_contribution",
    "alignment_bonus",
    "volatility_bonus",
    "earnings_penalty",
)


class ScoringPublicationError(Exception):
    """A scoring generation was not published."""


def scoring_input_fingerprint(
    rows: list[tuple[str, int, Optional[str]]],
) -> str:
    """Hash the payloads actually consumed, ordered by scan id then uid.

    Each row is ``(observation_uid, scan_result_id, raw_result_json)``.
    SQLite TEXT is hashed as UTF-8. A null payload writes the token
    ``null``. An empty string writes ``text:0:``. Those two inputs do not
    collide. The scan_results.scout_score column is intentionally absent.
    """
    return _fingerprint(SCORING_INPUT_FINGERPRINT_SPEC, rows)


def source_provenance_fingerprint(
    rows: list[tuple[str, int, Optional[str]]],
) -> str:
    """Hash copied source metadata that the decomposer does not read.

    Each row is ``(observation_uid, scan_result_id, engine_version)``.
    Null and empty engine versions use the same null-versus-text rule as
    the scoring-input fingerprint.
    """
    return _fingerprint(SOURCE_PROVENANCE_FINGERPRINT_SPEC, rows)


def publish_scoring_decompositions(
    conn: sqlite3.Connection,
    *,
    _probe: Optional[Callable[[str], None]] = None,
) -> dict[str, Any]:
    """Publish one scoring generation for the inspected single-scan formula."""
    if schema_status(conn) != "already_applied":
        raise ScoringPublicationError(
            "scoring decomposition schema is not applied; refusing to publish"
        )
    _end_read_transaction(conn)
    conn.execute("PRAGMA foreign_keys = ON")
    assert_research_population(conn)
    opening = population_stamp(conn)
    _end_read_transaction(conn)
    started = start_build(
        conn,
        artifact_type=ARTIFACT_TYPE,
        builder_version=BUILDER_VERSION,
        stamp=opening,
    )
    _end_read_transaction(conn)
    build_id = started["build_id"]
    try:
        if _probe is not None:
            _probe("before_calculate")
        prepared = _calculate(conn, opening.eligible_population_hash)
        _validate_prepared(prepared["rows"], opening.eligible_population_count)
        _end_read_transaction(conn)
        if _probe is not None:
            _probe("before_lock")
        _end_read_transaction(conn)
    except Exception as exc:
        _fail_started(conn, build_id, exc)
        raise

    created_at = datetime.now(timezone.utc).isoformat()
    try:
        conn.execute("BEGIN IMMEDIATE")
        if _probe is not None:
            _probe("inside_transaction")
        final = population_stamp(conn)
        if _identity(final) != _identity(opening):
            raise ScoringPublicationError("population stamp changed before publication")
        current = get_build(conn, build_id)
        if current is None or current["status"] != "STARTED":
            raise ScoringPublicationError("scoring build is no longer STARTED")
        if (
            current["classifier_version"],
            current["eligible_population_count"],
            current["eligible_population_hash"],
        ) != _identity(opening):
            raise ScoringPublicationError("manifest stamp does not match the opening population")
        _recheck_fingerprints(conn, prepared)
        _validate_identity(conn, prepared["rows"])
        _reject_other_methodologies(conn)
        _replace_live(conn, prepared["rows"], build_id)
        if _probe is not None:
            _probe("after_delete")
        _validate_live(conn, prepared["rows"], build_id, opening.eligible_population_hash)
        complete_build(
            conn,
            build_id,
            built_at=created_at,
            artifact_row_count=len(prepared["rows"]),
        )
        if _probe is not None:
            _probe("after_complete")
        conn.execute("COMMIT")
    except Exception as exc:
        _rollback(conn)
        _fail_started(conn, build_id, exc)
        raise
    return {
        "ok": True,
        "build_id": build_id,
        "status": "COMPLETED",
        "row_count": len(prepared["rows"]),
        "eligible_population_hash": opening.eligible_population_hash,
        "scoring_input_fingerprint": prepared["scoring_input_fingerprint"],
        "source_provenance_fingerprint": prepared["source_provenance_fingerprint"],
        "methodology_id": INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
        "decomposer_version": DECOMPOSER_VERSION,
    }


def current_scoring_generation(conn: sqlite3.Connection) -> Optional[dict[str, Any]]:
    """Return the latest completed scoring build and its live row count."""
    _require_schema(conn)
    build = latest_completed_build(conn, ARTIFACT_TYPE)
    if build is None:
        return None
    count = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {LIVE_TABLE} WHERE build_id = ?",
            (build["build_id"],),
        ).fetchone()[0]
    )
    fingerprints = [
        tuple(row)
        for row in conn.execute(
            f"""
            SELECT DISTINCT scoring_input_fingerprint, source_provenance_fingerprint
            FROM {LIVE_TABLE}
            WHERE build_id = ?
            """,
            (build["build_id"],),
        )
    ]
    if count and len(fingerprints) != 1:
        raise ScoringPublicationError("scoring generation has more than one fingerprint")
    scoring_fingerprint = fingerprints[0][0] if fingerprints else None
    provenance_fingerprint = fingerprints[0][1] if fingerprints else None
    return {
        "build": build,
        "live_row_count": count,
        "scoring_input_fingerprint": scoring_fingerprint,
        "source_provenance_fingerprint": provenance_fingerprint,
    }


def get_scoring_decomposition(
    conn: sqlite3.Connection,
    observation_uid: str,
) -> Optional[dict[str, Any]]:
    _require_schema(conn)
    row = conn.execute(
        f"SELECT {', '.join(COLUMNS)} FROM {LIVE_TABLE} WHERE observation_uid = ?",
        (observation_uid,),
    ).fetchone()
    if row is None:
        return None
    return _public_row(row)


def reconstruction_class_distribution(conn: sqlite3.Connection) -> dict[str, int]:
    _require_schema(conn)
    return {
        row["reconstruction_class"]: int(row["n"])
        for row in conn.execute(
            f"""
            SELECT reconstruction_class, COUNT(*) AS n
            FROM {LIVE_TABLE}
            GROUP BY reconstruction_class
            ORDER BY reconstruction_class
            """
        )
    }


def evidence_origin_distribution(conn: sqlite3.Connection) -> dict[str, int]:
    """Count evidence origins across the stored contribution vector."""
    _require_schema(conn)
    counts: dict[str, int] = {}
    for (payload,) in conn.execute(f"SELECT contributions_json FROM {LIVE_TABLE}"):
        contributions = json.loads(payload)
        for field, evidenced in contributions.items():
            key = f"{field}:{evidenced['origin']}"
            counts[key] = counts.get(key, 0) + 1
    return dict(sorted(counts.items()))


def ambiguous_observations(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    _require_schema(conn)
    rows = []
    for row in conn.execute(
        f"""
        SELECT observation_uid, scan_result_id, penalty_candidates_json,
               unclamped_score, clamped_score, reconstruction_error
        FROM {LIVE_TABLE}
        WHERE reconstruction_class = ?
        ORDER BY scan_result_id
        """,
        (ReconstructionClass.AMBIGUOUS.value,),
    ):
        rows.append(
            {
                "observation_uid": row["observation_uid"],
                "scan_result_id": row["scan_result_id"],
                "unclamped_score": row["unclamped_score"],
                "clamped_score": row["clamped_score"],
                "reconstruction_error": row["reconstruction_error"],
                "penalty_candidates": json.loads(row["penalty_candidates_json"]),
            }
        )
    return rows


def missing_or_conflicting_inputs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    _require_schema(conn)
    rows = []
    for row in conn.execute(
        f"""
        SELECT observation_uid, scan_result_id, reconstruction_class,
               input_completeness_json, disagreements_json, decomposition_error
        FROM {LIVE_TABLE}
        WHERE reconstruction_class IN (?, ?)
           OR decomposition_error IS NOT NULL
        ORDER BY scan_result_id
        """,
        (
            ReconstructionClass.INCOMPLETE.value,
            ReconstructionClass.EVIDENCE_CONFLICT.value,
        ),
    ):
        rows.append(
            {
                "observation_uid": row["observation_uid"],
                "scan_result_id": row["scan_result_id"],
                "reconstruction_class": row["reconstruction_class"],
                "input_completeness": json.loads(row["input_completeness_json"]),
                "disagreements": json.loads(row["disagreements_json"]),
                "decomposition_error": row["decomposition_error"],
            }
        )
    return rows


def residual_distribution(conn: sqlite3.Connection) -> dict[str, dict[str, int]]:
    """Residuals for independent and mathematically compatible rows.

    Ambiguous rows are omitted because they have no single residual.
    """
    _require_schema(conn)
    grouped: dict[str, dict[str, int]] = {}
    for row in conn.execute(
        f"""
        SELECT formula_confirmation, reconstruction_error, COUNT(*) AS n
        FROM {LIVE_TABLE}
        WHERE reconstruction_error IS NOT NULL
        GROUP BY formula_confirmation, reconstruction_error
        ORDER BY formula_confirmation, reconstruction_error
        """
    ):
        bucket = grouped.setdefault(row["formula_confirmation"], {})
        bucket[str(row["reconstruction_error"])] = int(row["n"])
    return grouped


def _eligible_source_rows(
    conn: sqlite3.Connection,
) -> list[tuple[str, int, Optional[str], Optional[str]]]:
    """Read each eligible payload once, in scan-id order.

    The returned text is the value passed to the decomposer and to the
    fingerprint. A later consistency check reads the table again inside
    the publication lock and compares fingerprints.
    """
    predicate = research_eligible_predicate("sr")
    rows = []
    for row in conn.execute(
        f"""
        SELECT sr.id AS scan_result_id, sr.raw_result_json, sr.engine_version,
               op.observation_uid
        FROM scan_results AS sr
        JOIN observation_provenance AS op ON op.scan_result_id = sr.id
        WHERE {predicate}
        ORDER BY sr.id, op.observation_uid
        """
    ):
        rows.append(
            (
                row["observation_uid"],
                int(row["scan_result_id"]),
                row["raw_result_json"],
                row["engine_version"],
            )
        )
    return rows


def _recheck_fingerprints(conn: sqlite3.Connection, prepared: dict[str, Any]) -> None:
    current = _eligible_source_rows(conn)
    scoring = scoring_input_fingerprint(
        [(uid, scan_id, raw) for uid, scan_id, raw, _engine in current]
    )
    provenance = source_provenance_fingerprint(
        [(uid, scan_id, engine) for uid, scan_id, _raw, engine in current]
    )
    if scoring != prepared["scoring_input_fingerprint"]:
        raise ScoringPublicationError("scoring-input fingerprint changed before publication")
    if provenance != prepared["source_provenance_fingerprint"]:
        raise ScoringPublicationError(
            "source provenance fingerprint changed before publication"
        )


def _validate_identity(conn: sqlite3.Connection, prepared: list[dict[str, Any]]) -> None:
    found = {
        row["observation_uid"]: int(row["scan_result_id"])
        for row in conn.execute(
            "SELECT observation_uid, scan_result_id FROM observation_provenance"
        )
    }
    for row in prepared:
        uid = row["observation_uid"]
        scan_id = int(row["scan_result_id"])
        if found.get(uid) != scan_id:
            raise ScoringPublicationError(
                f"observation {uid} does not belong to scan_result_id {scan_id}"
            )


def _reject_other_methodologies(conn: sqlite3.Connection) -> None:
    unexpected = [
        row["methodology_id"]
        for row in conn.execute(
            f"""
            SELECT methodology_id FROM {LIVE_TABLE}
            WHERE methodology_id != ?
            UNION
            SELECT methodology_id FROM {BACKUP_TABLE}
            WHERE methodology_id != ?
            ORDER BY methodology_id
            """,
            (
                INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
                INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
            ),
        )
    ]
    if unexpected:
        raise ScoringPublicationError(
            "unexpected scoring methodology: " + ", ".join(unexpected)
        )


def _calculate(conn: sqlite3.Connection, population_hash: str) -> dict[str, Any]:
    consumed = _eligible_source_rows(conn)
    scoring_fingerprint = scoring_input_fingerprint(
        [(uid, scan_id, raw) for uid, scan_id, raw, _engine in consumed]
    )
    provenance_fingerprint = source_provenance_fingerprint(
        [(uid, scan_id, engine) for uid, scan_id, _raw, engine in consumed]
    )
    prepared = []
    for observation_uid, scan_result_id, raw, engine_version in consumed:
        if raw is None:
            raise ScoringPublicationError(
                f"{observation_uid} has a null scoring payload"
            )
        decomposition = decompose_single_scan(json.loads(raw))
        prepared.append(
            _storage_row(
                decomposition,
                observation_uid=observation_uid,
                scan_result_id=scan_result_id,
                engine_version=engine_version,
                population_hash=population_hash,
                scoring_input_fingerprint=scoring_fingerprint,
                source_provenance_fingerprint=provenance_fingerprint,
            )
        )
    return {
        "rows": prepared,
        "consumed": consumed,
        "scoring_input_fingerprint": scoring_fingerprint,
        "source_provenance_fingerprint": provenance_fingerprint,
    }


def _storage_row(
    decomposition: SingleScanDecomposition,
    *,
    observation_uid: str,
    scan_result_id: int,
    engine_version: Optional[str],
    population_hash: str,
    scoring_input_fingerprint: str,
    source_provenance_fingerprint: str,
) -> dict[str, Any]:
    contributions = {
        field: getattr(decomposition, field).as_dict() for field in CONTRIBUTION_FIELDS
    }
    ambiguous = decomposition.classification == ReconstructionClass.AMBIGUOUS
    candidates = None
    if ambiguous:
        candidates = [item.as_dict() for item in decomposition.penalty_candidates]
    return {
        "observation_uid": observation_uid,
        "scan_result_id": scan_result_id,
        "methodology_id": decomposition.methodology.inspected_methodology_id,
        "historical_methodology_id": decomposition.methodology.historical_methodology_id,
        "source_snapshot_sha256": decomposition.methodology.inspected_source_sha256,
        "deployment_verification": decomposition.methodology.deployment_verification,
        "verified_deployment_id": decomposition.methodology.verified_deployment_id,
        "historical_compatibility": decomposition.methodology.historical_compatibility,
        "decomposer_version": DECOMPOSER_VERSION,
        "schema_version": SCORING_DECOMPOSITION_SCHEMA_VERSION,
        "sandbox_engine_version": engine_version,
        "reconstruction_class": decomposition.classification.value,
        "formula_confirmation": decomposition.formula_confirmation.value,
        "captured_scout_score": decomposition.captured_scout_score.value,
        "unclamped_score": None if ambiguous else decomposition.unclamped_score.value,
        "clamped_score": None if ambiguous else decomposition.clamped_score.value,
        "reconstruction_error": None if ambiguous else decomposition.reconstruction_error.value,
        "direction": decomposition.direction.value,
        "direction_status": decomposition.direction_status.value,
        "direction_argument": decomposition.direction_argument.value,
        "direction_argument_origin": decomposition.direction_argument.origin.value,
        "contributions_json": _dump(contributions),
        "penalty_candidates_json": None if candidates is None else _dump(candidates),
        "direction_candidates_json": _dump(list(decomposition.direction_candidates)),
        "input_completeness_json": _dump(decomposition.input_completeness.as_dict()),
        "warnings_json": _dump(list(decomposition.warnings)),
        "disagreements_json": _dump([item.as_dict() for item in decomposition.disagreements]),
        "decomposition_error": None,
        "eligible_population_hash": population_hash,
        "scoring_input_fingerprint": scoring_input_fingerprint,
        "source_provenance_fingerprint": source_provenance_fingerprint,
    }


def _validate_prepared(prepared: list[dict[str, Any]], eligible_count: int) -> None:
    if len(prepared) != eligible_count:
        raise ScoringPublicationError(
            f"prepared {len(prepared)} scoring rows for {eligible_count} eligible observations"
        )
    seen = set()
    for row in prepared:
        if row["observation_uid"] in seen:
            raise ScoringPublicationError(f"duplicate observation {row['observation_uid']}")
        seen.add(row["observation_uid"])
        if row["methodology_id"] != INSPECTED_SINGLE_SCAN_METHODOLOGY_ID:
            raise ScoringPublicationError("methodology identifier drifted")
        if row["source_snapshot_sha256"] != INSPECTED_SOURCE_SHA256:
            raise ScoringPublicationError("source snapshot hash drifted")
        if row["deployment_verification"] != DEPLOYMENT_VERIFICATION_STATUS:
            raise ScoringPublicationError("deployment status was marked verified")
        if row["verified_deployment_id"] is not None:
            raise ScoringPublicationError("verified deployment id must stay null")
        if row["decomposer_version"] != DECOMPOSER_VERSION:
            raise ScoringPublicationError("decomposer version drifted")
        _validate_ambiguity(row)


def _validate_ambiguity(row: dict[str, Any]) -> None:
    if row["reconstruction_class"] != ReconstructionClass.AMBIGUOUS.value:
        if row["reconstruction_class"] in {
            ReconstructionClass.EVIDENCE_DETERMINED.value,
            ReconstructionClass.ALGEBRAICALLY_INFERRED.value,
        } and row["unclamped_score"] is None:
            raise ScoringPublicationError(
                f"{row['observation_uid']} lost a uniquely determined raw score"
            )
        return
    if row["unclamped_score"] is not None or row["clamped_score"] is not None:
        raise ScoringPublicationError(
            f"{row['observation_uid']} stored a definitive score for an ambiguous vector"
        )
    if row["reconstruction_error"] is not None:
        raise ScoringPublicationError(
            f"{row['observation_uid']} stored a residual for an ambiguous vector"
        )
    candidates = json.loads(row["penalty_candidates_json"] or "null")
    if not isinstance(candidates, list) or len(candidates) < 2:
        raise ScoringPublicationError(
            f"{row['observation_uid']} ambiguous row does not keep multiple candidates"
        )


def _replace_live(
    conn: sqlite3.Connection,
    prepared: list[dict[str, Any]],
    build_id: str,
) -> None:
    listed = ", ".join(COLUMNS)
    conn.execute(f"DELETE FROM {BACKUP_TABLE}")
    conn.execute(
        f"INSERT INTO {BACKUP_TABLE} ({listed}) SELECT {listed} FROM {LIVE_TABLE}"
    )
    conn.execute(f"DELETE FROM {LIVE_TABLE}")
    placeholders = ", ".join("?" for _ in COLUMNS)
    for row in prepared:
        stored = dict(row)
        stored["build_id"] = build_id
        conn.execute(
            f"INSERT INTO {LIVE_TABLE} ({listed}) VALUES ({placeholders})",
            tuple(stored[column] for column in COLUMNS),
        )


def _validate_live(
    conn: sqlite3.Connection,
    prepared: list[dict[str, Any]],
    build_id: str,
    population_hash: str,
) -> None:
    live = int(conn.execute(f"SELECT COUNT(*) FROM {LIVE_TABLE}").fetchone()[0])
    if live != len(prepared):
        raise ScoringPublicationError("live scoring row count does not match the calculation")
    stamped = int(
        conn.execute(
            f"SELECT COUNT(*) FROM {LIVE_TABLE} WHERE build_id = ? AND eligible_population_hash = ?",
            (build_id, population_hash),
        ).fetchone()[0]
    )
    if stamped != len(prepared):
        raise ScoringPublicationError("live scoring rows are not stamped with this build")
    ineligible = int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {LIVE_TABLE} AS scoring
            JOIN observation_provenance AS op
              ON op.observation_uid = scoring.observation_uid
            WHERE op.research_eligible != 1
            """
        ).fetchone()[0]
    )
    if ineligible:
        raise ScoringPublicationError("an ineligible observation entered the scoring generation")


def _public_row(row: sqlite3.Row) -> dict[str, Any]:
    body = {column: row[column] for column in COLUMNS}
    for key in (
        "contributions_json",
        "penalty_candidates_json",
        "direction_candidates_json",
        "input_completeness_json",
        "warnings_json",
        "disagreements_json",
    ):
        body[key[:-5]] = json.loads(body.pop(key)) if body[key] is not None else None
    return body


def _require_schema(conn: sqlite3.Connection) -> None:
    if schema_status(conn) != "already_applied":
        raise ScoringPublicationError("scoring decomposition schema is not applied")


def _identity(stamp: Any) -> tuple[str, int, str]:
    return (
        stamp.classifier_version,
        stamp.eligible_population_count,
        stamp.eligible_population_hash,
    )


def _fingerprint(
    spec: str,
    rows: list[tuple[str, int, Optional[str]]],
) -> str:
    hasher = hashlib.sha256()
    hasher.update(spec.encode("ascii"))
    hasher.update(b"\n")
    ordered = sorted(rows, key=lambda item: (item[1], item[0]))
    for uid, scan_id, text in ordered:
        hasher.update(str(uid).encode("utf-8"))
        hasher.update(b"\0")
        hasher.update(str(int(scan_id)).encode("ascii"))
        hasher.update(b"\0")
        if text is None:
            hasher.update(b"null\n")
        else:
            data = text.encode("utf-8")
            hasher.update(b"text:")
            hasher.update(str(len(data)).encode("ascii"))
            hasher.update(b":")
            hasher.update(data)
            hasher.update(b"\n")
    return hasher.hexdigest()


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


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


def _fail_started(conn: sqlite3.Connection, build_id: str, exc: BaseException) -> None:
    _rollback(conn)
    _end_read_transaction(conn)
    current = get_build(conn, build_id)
    if current is None or current["status"] != "STARTED":
        return
    message = str(exc).strip() or exc.__class__.__name__
    fail_build(conn, build_id, error_text=message[:500])
    _end_read_transaction(conn)


def main(argv: Optional[list[str]] = None) -> int:
    """Publish only when --publish is passed. Canonical writes also need --allow-canonical."""
    parser = argparse.ArgumentParser(
        description="Publish one scoring decomposition generation. Does not migrate."
    )
    parser.add_argument("--database", type=str, default=str(CANONICAL_DB))
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--allow-canonical", action="store_true")
    args = parser.parse_args(argv)
    target = str(Path(args.database).resolve())
    canonical = str(CANONICAL_DB.resolve())
    try:
        if not args.publish:
            print("result=not_published")
            print("write_skipped=true")
            return 0
        if target == canonical and not args.allow_canonical:
            raise ScoringPublicationError(
                "refusing to publish into the canonical research database without --allow-canonical"
            )
        conn = sqlite3.connect(target, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            result = publish_scoring_decompositions(conn)
        finally:
            conn.close()
    except (ScoringPublicationError, sqlite3.Error) as exc:
        print("result=aborted", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2
    print("result=published")
    print(f"build_id={result['build_id']}")
    print(f"row_count={result['row_count']}")
    print(f"eligible_population_hash={result['eligible_population_hash']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
