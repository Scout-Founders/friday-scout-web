#!/usr/bin/env python3
"""Add observation_provenance to scout_memory.db.

Dry-run is the default. Nothing is written unless --apply is passed.
The script classifies scan_results from stored evidence, then refuses to
continue unless that classification matches the approved Phase B0 baseline.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


SANDBOX_DIR = Path(__file__).resolve().parent
CANONICAL_DB = SANDBOX_DIR / "scout_memory.db"
ARCHIVE_DB = SANDBOX_DIR / "data-archives" / "scout_memory_pre_horizon_migration_2026-09-28.db"
ARCHIVE_SHA256 = "dbfb8fb4394e4af7419ac8544d004d431c5d1cb526ef3a7b5d18a806cd8de436"

FRIDAY_SCOUT_URL = "https://us-central1-scout-493918.cloudfunctions.net/friday-scout"
FIXTURE_URL = "https://example.test/gates"
SYNTHETIC_URL = "local-test-copy"
SYNTHETIC_PICK_MODE = "outcome_test_copy"

CLASSIFIER_VERSION = "b0-2026-09-28"
RECORD_CLASS = "raw"

EXPECTED_COUNTS = {
    "production": 227,
    "test": 1,
    "synthetic": 9,
    "fixture": 37,
}
EXPECTED_TOTAL = 274
EXPECTED_ELIGIBLE = 227
EXPECTED_EXCLUDED = 47

EXPECTED_TEST_IDS = frozenset({31})
EXPECTED_SYNTHETIC_IDS = frozenset({10, 11, 12, 13, 14, 15, 16, 17, 18})
EXPECTED_FIXTURE_IDS = frozenset(
    {
        228,
        239,
        240,
        241,
        242,
        243,
        244,
        245,
        246,
        247,
        248,
        249,
        250,
        251,
        252,
        253,
        254,
        255,
        256,
        257,
        258,
        259,
        260,
        261,
        262,
        263,
        264,
        265,
        266,
        267,
        268,
        269,
        270,
        271,
        272,
        273,
        274,
    }
)
EXPECTED_SYNTHETIC_PARENT = "sr:9"
EXPECTED_TEST_PARENT = "sr:25"

CREATE_TABLE_SQL = """
CREATE TABLE observation_provenance (
    observation_uid TEXT PRIMARY KEY,
    scan_result_id INTEGER NOT NULL UNIQUE,
    origin TEXT NOT NULL,
    record_class TEXT NOT NULL,
    parent_observation_uid TEXT,
    research_eligible INTEGER NOT NULL,
    classification_reason TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    classifier_version TEXT NOT NULL,
    FOREIGN KEY (scan_result_id)
        REFERENCES scan_results(id)
        ON DELETE RESTRICT,
    FOREIGN KEY (parent_observation_uid)
        REFERENCES observation_provenance(observation_uid)
        ON DELETE RESTRICT,
    CHECK (origin IN (
        'production', 'test', 'synthetic', 'fixture',
        'backtest', 'imported', 'reconstructed'
    )),
    CHECK (record_class IN (
        'raw', 'normalized', 'derived',
        'research_result', 'validated_result'
    )),
    CHECK (research_eligible IN (0, 1)),
    CHECK (length(classification_reason) > 0),
    CHECK (length(classifier_version) > 0),
    CHECK (length(classified_at) > 0),
    CHECK (
        research_eligible = CASE
            WHEN origin = 'production'
                 AND parent_observation_uid IS NULL
            THEN 1
            ELSE 0
        END
    )
)
"""

CREATE_INDEX_SQL = (
    """
    CREATE INDEX idx_observation_provenance_origin
        ON observation_provenance(origin)
    """,
    """
    CREATE INDEX idx_observation_provenance_eligible
        ON observation_provenance(research_eligible)
    """,
    """
    CREATE INDEX idx_observation_provenance_parent
        ON observation_provenance(parent_observation_uid)
    """,
)

COMPARE_FIELDS = (
    "observation_uid",
    "scan_result_id",
    "origin",
    "record_class",
    "parent_observation_uid",
    "research_eligible",
    "classification_reason",
    "classifier_version",
)


class MigrationAbort(Exception):
    """Stop the migration without writing."""


def sha256_file(path: Path) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def digest_raw_result_json(conn: sqlite3.Connection) -> str:
    """Deterministic digest of scan_results.raw_result_json ordered by id."""
    hasher = hashlib.sha256()
    for scan_id, raw in conn.execute(
        "SELECT id, raw_result_json FROM scan_results ORDER BY id ASC"
    ):
        hasher.update(b"id:")
        hasher.update(str(scan_id).encode("ascii"))
        hasher.update(b"\0")
        if raw is None:
            hasher.update(b"null")
        else:
            data = raw.encode("utf-8")
            hasher.update(b"text:")
            hasher.update(str(len(data)).encode("ascii"))
            hasher.update(b":")
            hasher.update(data)
        hasher.update(b"\n")
    return hasher.hexdigest()


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    names = [
        row[0]
        for row in conn.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table' AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        )
    ]
    return {
        name: int(conn.execute(f'SELECT COUNT(*) FROM "{name}"').fetchone()[0])
        for name in names
    }


def load_json_object(raw: Optional[str], scan_id: int, field: str) -> dict[str, Any]:
    if raw is None or raw == "":
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise MigrationAbort(
            f"scan_results.id={scan_id} {field} is not valid JSON: {exc}"
        ) from exc
    if not isinstance(parsed, dict):
        raise MigrationAbort(
            f"scan_results.id={scan_id} {field} is {type(parsed).__name__}, expected object"
        )
    return parsed


def provenance_fields(payload: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "source_result_id",
        "source_scan_id",
        "sandbox_test_copy",
        "sandbox_test_data",
        "gate_alpha_test_bridge",
    )
    return {key: payload[key] for key in keys if key in payload}


def require_agree(scan_id: int, left: dict[str, Any], right: dict[str, Any]) -> dict[str, Any]:
    left_fields = provenance_fields(left)
    right_fields = provenance_fields(right)
    if left_fields != right_fields:
        raise MigrationAbort(
            "parent disagreement for scan_results.id="
            f"{scan_id}: raw_fmp_inputs_json {left_fields} "
            f"!= raw_result_json {right_fields}"
        )
    return left_fields


def observation_uid(scan_id: int) -> str:
    return f"sr:{scan_id}"


def classify_origin(row: sqlite3.Row) -> str:
    api_url = row["api_url"] or ""
    pick_mode = row["pick_mode"] or ""
    if api_url == FIXTURE_URL:
        return "fixture"
    if api_url == SYNTHETIC_URL or pick_mode == SYNTHETIC_PICK_MODE:
        if api_url != SYNTHETIC_URL or pick_mode != SYNTHETIC_PICK_MODE:
            raise MigrationAbort(
                f"scan_results.id={row['id']} has a partial synthetic marker: "
                f"api_url={api_url!r} pick_mode={pick_mode!r}"
            )
        return "synthetic"
    if api_url == FRIDAY_SCOUT_URL:
        marked_test = int(row["is_test_record"] or 0) == 1 or row["outcome"] == "SANDBOX_GATE_ALPHA_TEST"
        if marked_test:
            return "test"
        return "production"
    raise MigrationAbort(
        f"unknown observation scan_results.id={row['id']} "
        f"api_url={api_url!r} pick_mode={pick_mode!r}"
    )


def build_plan(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
            r.id,
            r.run_id,
            r.ticker,
            r.is_test_record,
            r.outcome,
            r.result_notes,
            r.raw_fmp_inputs_json,
            r.raw_result_json,
            s.api_url,
            s.pick_mode
        FROM scan_results r
        JOIN scan_runs s ON s.id = r.run_id
        ORDER BY r.id ASC
        """
    ).fetchall()
    if len(rows) != EXPECTED_TOTAL:
        raise MigrationAbort(f"scan_results count is {len(rows)}, expected {EXPECTED_TOTAL}")

    by_id = {int(row["id"]): row for row in rows}
    planned: list[dict[str, Any]] = []
    for row in rows:
        scan_id = int(row["id"])
        origin = classify_origin(row)
        inputs = load_json_object(row["raw_fmp_inputs_json"], scan_id, "raw_fmp_inputs_json")
        result = load_json_object(row["raw_result_json"], scan_id, "raw_result_json")
        agreed = require_agree(scan_id, inputs, result)
        parent: Optional[str]
        reason: str

        if origin == "production":
            if agreed:
                raise MigrationAbort(
                    f"scan_results.id={scan_id} classified production but carries copy metadata {agreed}"
                )
            if int(row["is_test_record"] or 0) != 0 or row["outcome"] == "SANDBOX_GATE_ALPHA_TEST":
                raise MigrationAbort(f"scan_results.id={scan_id} production row is also marked test")
            parent = None
            reason = "api_url is friday-scout and not a marked test"
        elif origin == "fixture":
            if agreed:
                raise MigrationAbort(
                    f"scan_results.id={scan_id} classified fixture but carries source metadata {agreed}"
                )
            parent = None
            reason = "api_url example.test/gates; no source_result_id"
        elif origin == "synthetic":
            if agreed.get("sandbox_test_copy") is not True:
                raise MigrationAbort(
                    f"scan_results.id={scan_id} synthetic row is missing sandbox_test_copy"
                )
            if "source_result_id" not in agreed:
                raise MigrationAbort(
                    f"scan_results.id={scan_id} synthetic row is missing source_result_id"
                )
            source_id = int(agreed["source_result_id"])
            parent = observation_uid(source_id)
            reason = f"api_url local-test-copy; source_result_id {source_id}"
        elif origin == "test":
            if row["outcome"] != "SANDBOX_GATE_ALPHA_TEST" or int(row["is_test_record"] or 0) != 1:
                raise MigrationAbort(
                    f"scan_results.id={scan_id} test classification is not a gate-alpha test record"
                )
            if agreed.get("sandbox_test_data") is not True or agreed.get("gate_alpha_test_bridge") is not True:
                raise MigrationAbort(
                    f"scan_results.id={scan_id} test row is missing gate-alpha metadata"
                )
            if "source_result_id" not in agreed or "source_scan_id" not in agreed:
                raise MigrationAbort(
                    f"scan_results.id={scan_id} test row is missing source ids"
                )
            source_id = int(agreed["source_result_id"])
            source_scan_id = int(agreed["source_scan_id"])
            note = row["result_notes"] or ""
            expected_note = f"Source scan_results.id={source_id} scan_id={source_scan_id}."
            if expected_note not in note:
                raise MigrationAbort(
                    f"scan_results.id={scan_id} test note does not confirm source {source_id}/{source_scan_id}"
                )
            parent = observation_uid(source_id)
            reason = f"outcome SANDBOX_GATE_ALPHA_TEST and source_result_id {source_id}"
            if source_scan_id != int(by_id[source_id]["run_id"]):
                raise MigrationAbort(
                    f"scan_results.id={scan_id} source_scan_id {source_scan_id} "
                    f"does not match source run_id {by_id[source_id]['run_id']}"
                )
        else:
            raise MigrationAbort(f"unhandled origin {origin} for scan_results.id={scan_id}")

        eligible = 1 if origin == "production" and parent is None else 0
        planned.append(
            {
                "observation_uid": observation_uid(scan_id),
                "scan_result_id": scan_id,
                "origin": origin,
                "record_class": RECORD_CLASS,
                "parent_observation_uid": parent,
                "research_eligible": eligible,
                "classification_reason": reason,
                "classifier_version": CLASSIFIER_VERSION,
            }
        )

    assert_baseline(planned, by_id)
    return planned


def assert_baseline(planned: list[dict[str, Any]], by_id: dict[int, sqlite3.Row]) -> None:
    if len(planned) != EXPECTED_TOTAL:
        raise MigrationAbort(f"planned provenance rows {len(planned)}, expected {EXPECTED_TOTAL}")

    counts: dict[str, int] = {origin: 0 for origin in EXPECTED_COUNTS}
    ids_by_origin: dict[str, set[int]] = {origin: set() for origin in EXPECTED_COUNTS}
    eligible = 0
    for item in planned:
        origin = item["origin"]
        if origin not in counts:
            raise MigrationAbort(f"unexpected origin {origin}")
        counts[origin] += 1
        ids_by_origin[origin].add(item["scan_result_id"])
        eligible += int(item["research_eligible"])
        if item["record_class"] != RECORD_CLASS or item["classifier_version"] != CLASSIFIER_VERSION:
            raise MigrationAbort(f"unexpected class or version on {item['observation_uid']}")

    if counts != EXPECTED_COUNTS:
        raise MigrationAbort(f"origin counts {counts} != {EXPECTED_COUNTS}")
    if ids_by_origin["test"] != EXPECTED_TEST_IDS:
        raise MigrationAbort(f"test ids {sorted(ids_by_origin['test'])} != {sorted(EXPECTED_TEST_IDS)}")
    if ids_by_origin["synthetic"] != EXPECTED_SYNTHETIC_IDS:
        raise MigrationAbort(
            f"synthetic ids {sorted(ids_by_origin['synthetic'])} != {sorted(EXPECTED_SYNTHETIC_IDS)}"
        )
    if ids_by_origin["fixture"] != EXPECTED_FIXTURE_IDS:
        raise MigrationAbort(
            f"fixture ids {sorted(ids_by_origin['fixture'])} != {sorted(EXPECTED_FIXTURE_IDS)}"
        )
    if eligible != EXPECTED_ELIGIBLE or (EXPECTED_TOTAL - eligible) != EXPECTED_EXCLUDED:
        raise MigrationAbort(f"eligible={eligible}, expected {EXPECTED_ELIGIBLE}")

    planned_by_id = {item["scan_result_id"]: item for item in planned}
    for scan_id in sorted(EXPECTED_SYNTHETIC_IDS):
        parent = planned_by_id[scan_id]["parent_observation_uid"]
        if parent != EXPECTED_SYNTHETIC_PARENT:
            raise MigrationAbort(f"scan_results.id={scan_id} parent {parent} != {EXPECTED_SYNTHETIC_PARENT}")
    for scan_id in sorted(EXPECTED_TEST_IDS):
        parent = planned_by_id[scan_id]["parent_observation_uid"]
        if parent != EXPECTED_TEST_PARENT:
            raise MigrationAbort(f"scan_results.id={scan_id} parent {parent} != {EXPECTED_TEST_PARENT}")
    for scan_id in sorted(EXPECTED_FIXTURE_IDS):
        if planned_by_id[scan_id]["parent_observation_uid"] is not None:
            raise MigrationAbort(f"fixture scan_results.id={scan_id} has a parent")

    for item in planned:
        parent = item["parent_observation_uid"]
        if parent is None:
            continue
        parent_id = int(parent.removeprefix("sr:"))
        if parent_id not in planned_by_id:
            raise MigrationAbort(f"{item['observation_uid']} parent {parent} is not an observation")
        parent_item = planned_by_id[parent_id]
        if parent_item["origin"] != "production" or parent_item["parent_observation_uid"] is not None:
            raise MigrationAbort(f"{item['observation_uid']} parent {parent} is not a production observation")
        if int(by_id[parent_id]["id"]) != parent_id:
            raise MigrationAbort(f"parent id mismatch for {parent}")

    covered = set().union(*ids_by_origin.values())
    if covered != set(by_id):
        raise MigrationAbort("classified ids do not cover every scan_results row")


def provenance_table_exists(conn: sqlite3.Connection) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE type = 'table' AND name = 'observation_provenance'
        """
    ).fetchone()
    return row is not None


def stored_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    columns = ", ".join(COMPARE_FIELDS)
    return [
        {field: row[field] for field in COMPARE_FIELDS}
        for row in conn.execute(
            f"SELECT {columns} FROM observation_provenance ORDER BY scan_result_id ASC"
        )
    ]


def logical_rows(planned: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [{field: item[field] for field in COMPARE_FIELDS} for item in planned]


def preflight_archive() -> None:
    if not ARCHIVE_DB.is_file():
        raise MigrationAbort(f"archive is missing: {ARCHIVE_DB.name}")
    actual = sha256_file(ARCHIVE_DB)
    if actual != ARCHIVE_SHA256:
        raise MigrationAbort(f"archive SHA-256 mismatch: {actual}")


def open_readonly(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def validate_stored(conn: sqlite3.Connection, before_digest: str, before_counts: dict[str, int]) -> dict[str, Any]:
    scan_results = int(conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0])
    provenance = int(conn.execute("SELECT COUNT(*) FROM observation_provenance").fetchone()[0])
    if scan_results != EXPECTED_TOTAL or provenance != EXPECTED_TOTAL:
        raise MigrationAbort(f"counts scan_results={scan_results} provenance={provenance}")

    origin_counts = {
        row["origin"]: int(row["n"])
        for row in conn.execute(
            "SELECT origin, COUNT(*) AS n FROM observation_provenance GROUP BY origin"
        )
    }
    if origin_counts != EXPECTED_COUNTS:
        raise MigrationAbort(f"stored origin counts {origin_counts}")

    eligible = int(
        conn.execute(
            "SELECT COALESCE(SUM(research_eligible), 0) FROM observation_provenance"
        ).fetchone()[0]
    )
    excluded = provenance - eligible
    if eligible != EXPECTED_ELIGIBLE or excluded != EXPECTED_EXCLUDED:
        raise MigrationAbort(f"eligible={eligible} excluded={excluded}")

    orphans = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM observation_provenance p
            LEFT JOIN scan_results r ON r.id = p.scan_result_id
            WHERE r.id IS NULL
            """
        ).fetchone()[0]
    )
    missing = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM scan_results r
            LEFT JOIN observation_provenance p ON p.scan_result_id = r.id
            WHERE p.scan_result_id IS NULL
            """
        ).fetchone()[0]
    )
    duplicate_groups = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM (
                SELECT scan_result_id FROM observation_provenance
                GROUP BY scan_result_id HAVING COUNT(*) <> 1
            )
            """
        ).fetchone()[0]
    )
    if orphans or missing or duplicate_groups:
        raise MigrationAbort(
            f"link check orphans={orphans} missing={missing} duplicate_groups={duplicate_groups}"
        )

    ineligible_breach = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM observation_provenance
            WHERE origin IN ('test', 'synthetic', 'fixture')
              AND research_eligible <> 0
            """
        ).fetchone()[0]
    )
    if ineligible_breach:
        raise MigrationAbort(f"{ineligible_breach} excluded-origin rows are research eligible")

    bad_parents = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM observation_provenance
            WHERE (
                scan_result_id BETWEEN 10 AND 18
                AND parent_observation_uid IS NOT 'sr:9'
            ) OR (
                scan_result_id = 31
                AND parent_observation_uid IS NOT 'sr:25'
            ) OR (
                origin = 'fixture'
                AND parent_observation_uid IS NOT NULL
            )
            """
        ).fetchone()[0]
    )
    if bad_parents:
        raise MigrationAbort(f"{bad_parents} parent links do not match the approved baseline")

    bad_class = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM observation_provenance
            WHERE record_class <> ? OR classifier_version <> ?
            """,
            (RECORD_CLASS, CLASSIFIER_VERSION),
        ).fetchone()[0]
    )
    if bad_class:
        raise MigrationAbort(f"{bad_class} rows have an unexpected class or classifier version")

    after_digest = digest_raw_result_json(conn)
    if after_digest != before_digest:
        raise MigrationAbort("raw_result_json digest changed")

    after_counts = table_counts(conn)
    for name, count in before_counts.items():
        if name == "observation_provenance":
            continue
        if after_counts.get(name) != count:
            raise MigrationAbort(
                f"table {name} row count changed from {count} to {after_counts.get(name)}"
            )

    integrity = [row[0] for row in conn.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise MigrationAbort(f"integrity_check {integrity}")

    return {
        "scan_results": scan_results,
        "provenance": provenance,
        "origin_counts": origin_counts,
        "eligible": eligible,
        "excluded": excluded,
        "raw_result_json_digest": after_digest,
        "integrity_check": integrity[0],
    }


def apply_plan(
    db_path: Path,
    planned: list[dict[str, Any]],
    before_digest: str,
    before_counts: dict[str, int],
) -> dict[str, Any]:
    conn = sqlite3.connect(db_path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    started = False
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        if digest_raw_result_json(conn) != before_digest:
            raise MigrationAbort("raw_result_json digest changed before apply")
        if int(conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]) != EXPECTED_TOTAL:
            raise MigrationAbort("scan_results count changed before apply")

        conn.execute("BEGIN IMMEDIATE")
        started = True
        if provenance_table_exists(conn):
            raise MigrationAbort("observation_provenance appeared inside the apply transaction")
        conn.execute(CREATE_TABLE_SQL)
        for statement in CREATE_INDEX_SQL:
            conn.execute(statement)

        classified_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        insert_sql = """
            INSERT INTO observation_provenance (
                observation_uid, scan_result_id, origin, record_class,
                parent_observation_uid, research_eligible, classification_reason,
                classified_at, classifier_version
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        ordered = sorted(
            planned,
            key=lambda item: (item["parent_observation_uid"] is not None, item["scan_result_id"]),
        )
        conn.executemany(
            insert_sql,
            [
                (
                    item["observation_uid"],
                    item["scan_result_id"],
                    item["origin"],
                    item["record_class"],
                    item["parent_observation_uid"],
                    item["research_eligible"],
                    item["classification_reason"],
                    classified_at,
                    item["classifier_version"],
                )
                for item in ordered
            ],
        )
        if stored_rows(conn) != logical_rows(planned):
            raise MigrationAbort("inserted provenance does not match the classified plan")
        summary = validate_stored(conn, before_digest, before_counts)
        conn.execute("COMMIT")
        started = False
        summary["classified_at"] = classified_at
        return summary
    except Exception:
        if started:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Create observation_provenance in scout_memory.db")
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Write the provenance table. Without this flag the script only reports.",
    )
    args = parser.parse_args(argv)

    try:
        preflight_archive()
        if not CANONICAL_DB.is_file():
            raise MigrationAbort("scout_memory.db is missing")

        read_conn = open_readonly(CANONICAL_DB)
        try:
            before_counts = table_counts(read_conn)
            before_digest = digest_raw_result_json(read_conn)
            planned = build_plan(read_conn)
            already = provenance_table_exists(read_conn)
            stored_match = already and stored_rows(read_conn) == logical_rows(planned)
            if already and not stored_match:
                raise MigrationAbort(
                    "observation_provenance already exists and does not match this classifier"
                )
            if already and stored_match:
                summary = validate_stored(read_conn, before_digest, before_counts)
        finally:
            read_conn.close()

        mode = "apply" if args.apply else "dry-run"
        print(f"mode={mode}")
        print(f"archive_sha256={ARCHIVE_SHA256}")
        print(f"raw_result_json_digest={before_digest}")
        print(f"planned_rows={len(planned)}")
        print(
            "origin_counts="
            + json.dumps({origin: sum(item["origin"] == origin for item in planned) for origin in EXPECTED_COUNTS})
        )
        print(f"eligible={sum(item['research_eligible'] for item in planned)}")
        print(f"excluded={len(planned) - sum(item['research_eligible'] for item in planned)}")
        print(f"already_applied={already and stored_match}")

        if already and stored_match:
            print("result=already_applied")
            print(f"integrity_check={summary['integrity_check']}")
            return 0

        if not args.apply:
            print("result=dry_run_passed")
            print("apply_not_executed=true")
            return 0

        summary = apply_plan(CANONICAL_DB, planned, before_digest, before_counts)
        print("result=applied")
        print(f"classified_at={summary['classified_at']}")
        print(f"provenance_rows={summary['provenance']}")
        print(f"integrity_check={summary['integrity_check']}")
        print(f"post_raw_result_json_digest={summary['raw_result_json_digest']}")
        return 0
    except MigrationAbort as exc:
        print(f"result=aborted", file=sys.stderr)
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
