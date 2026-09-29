#!/usr/bin/env python3
"""In-memory tests for the research population contract.

These tests never open scout_memory.db.
"""

from __future__ import annotations

import hashlib
import sqlite3
import subprocess
import sys
import unittest
from pathlib import Path

from observation_evidence import (
    CAPTURE_CLASSIFIER_VERSION,
    HISTORICAL_CLASSIFIER_VERSION,
    ResearchPopulationError,
    assert_research_population,
    classifier_identity,
    hash_eligible_observation_uids,
    population_stamp,
    research_eligible_predicate,
)


SCHEMA = """
CREATE TABLE scan_results (
    id INTEGER PRIMARY KEY
);

CREATE TABLE observation_provenance (
    observation_uid TEXT PRIMARY KEY,
    scan_result_id INTEGER NOT NULL,
    origin TEXT NOT NULL,
    record_class TEXT NOT NULL,
    parent_observation_uid TEXT,
    research_eligible INTEGER,
    classification_reason TEXT NOT NULL,
    classified_at TEXT NOT NULL,
    classifier_version TEXT
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.executescript(SCHEMA)
    return conn


def add_observation(
    conn: sqlite3.Connection,
    scan_id: int,
    *,
    origin: str = "production",
    research_eligible: int = 1,
    parent: str | None = None,
    classifier_version: str = "b0-2026-09-28",
    insert_scan_result: bool = True,
) -> None:
    if insert_scan_result:
        conn.execute("INSERT INTO scan_results (id) VALUES (?)", (scan_id,))
    conn.execute(
        """
        INSERT INTO observation_provenance (
            observation_uid, scan_result_id, origin, record_class,
            parent_observation_uid, research_eligible, classification_reason,
            classified_at, classifier_version
        ) VALUES (?, ?, ?, 'raw', ?, ?, 'test', '2026-09-28T00:00:00Z', ?)
        """,
        (
            f"sr:{scan_id}",
            scan_id,
            origin,
            parent,
            research_eligible,
            classifier_version,
        ),
    )


class ObservationEvidenceTests(unittest.TestCase):
    def test_complete_valid_population_passes(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="production", research_eligible=1, parent=None)
        add_observation(conn, 2, origin="fixture", research_eligible=0, parent=None)
        add_observation(conn, 3, origin="test", research_eligible=0, parent="sr:1")
        add_observation(conn, 4, origin="synthetic", research_eligible=0, parent="sr:1")
        assert_research_population(conn)
        stamp = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        self.assertEqual(stamp.classifier_version, "b0-2026-09-28")
        self.assertEqual(stamp.eligible_population_count, 1)
        self.assertEqual(stamp.eligible_population_hash, hash_eligible_observation_uids(["sr:1"]))

    def test_valid_eligible_production_observation(self) -> None:
        conn = connect()
        add_observation(conn, 9, origin="production", research_eligible=1, parent=None)
        assert_research_population(conn)
        stamp = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        self.assertEqual(stamp.eligible_population_count, 1)
        self.assertEqual(stamp.eligible_population_hash, hashlib.sha256(b"sr:9\n").hexdigest())

    def test_valid_excluded_fixture_test_and_synthetic(self) -> None:
        conn = connect()
        add_observation(conn, 25, origin="production", research_eligible=1)
        add_observation(conn, 228, origin="fixture", research_eligible=0, parent=None)
        add_observation(conn, 31, origin="test", research_eligible=0, parent="sr:25")
        add_observation(conn, 10, origin="synthetic", research_eligible=0, parent="sr:9")
        assert_research_population(conn)
        stamp = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        self.assertEqual(stamp.eligible_population_count, 1)
        self.assertEqual(stamp.classifier_version, "b0-2026-09-28")

    def test_provenance_table_missing_fails_closed(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.execute("CREATE TABLE scan_results (id INTEGER PRIMARY KEY)")
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("observation_provenance table is missing", str(caught.exception))
        self.assertIn("Refusing to fall back", str(caught.exception))

    def test_scan_result_missing_provenance_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1)
        conn.execute("INSERT INTO scan_results (id) VALUES (2)")
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        message = str(caught.exception)
        self.assertIn("scan result lacks provenance", message)
        self.assertIn("2", message)

    def test_orphan_provenance_row_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1)
        add_observation(conn, 99, insert_scan_result=False)
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("provenance row lacks a scan_result", str(caught.exception))
        self.assertIn("99", str(caught.exception))

    def test_unknown_origin_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="sandbox", research_eligible=0)
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("unknown origin", str(caught.exception))
        self.assertIn("sandbox", str(caught.exception))

    def test_invalid_research_eligible_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, research_eligible=2)
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("invalid research_eligible", str(caught.exception))

    def test_eligible_non_production_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="fixture", research_eligible=1, parent=None)
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("eligible non-production", str(caught.exception))

    def test_eligible_row_with_parent_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="production", research_eligible=1, parent=None)
        add_observation(conn, 2, origin="production", research_eligible=1, parent="sr:1")
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("production child is research eligible", str(caught.exception))

    def test_multiple_classifier_versions_fail_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, classifier_version="b0-2026-09-28")
        add_observation(conn, 2, origin="fixture", research_eligible=0, classifier_version="b1-later")
        with self.assertRaises(ResearchPopulationError) as caught:
            population_stamp(conn)
        self.assertIn("unapproved classifier version", str(caught.exception))
        self.assertIn("b1-later", str(caught.exception))

    def test_historical_b0_population_passes(self) -> None:
        conn = connect()
        add_observation(conn, 1, classifier_version=HISTORICAL_CLASSIFIER_VERSION)
        stamp = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        self.assertEqual(stamp.classifier_version, HISTORICAL_CLASSIFIER_VERSION)

    def test_capture_v1_population_passes(self) -> None:
        conn = connect()
        add_observation(conn, 1, classifier_version=CAPTURE_CLASSIFIER_VERSION)
        stamp = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        self.assertEqual(stamp.classifier_version, CAPTURE_CLASSIFIER_VERSION)
        self.assertEqual(stamp.eligible_population_count, 1)

    def test_historical_and_capture_versions_coexist(self) -> None:
        conn = connect()
        add_observation(conn, 1, classifier_version=HISTORICAL_CLASSIFIER_VERSION)
        add_observation(conn, 2, classifier_version=CAPTURE_CLASSIFIER_VERSION)
        assert_research_population(conn)
        stamp = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        self.assertEqual(
            stamp.classifier_version,
            classifier_identity([HISTORICAL_CLASSIFIER_VERSION, CAPTURE_CLASSIFIER_VERSION]),
        )

    def test_unknown_classifier_version_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, classifier_version="draft-unknown")
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("unapproved classifier version", str(caught.exception))
        self.assertIn("draft-unknown", str(caught.exception))

    def test_eligible_test_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="test", research_eligible=1, parent=None)
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("eligible non-production", str(caught.exception))

    def test_eligible_synthetic_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="synthetic", research_eligible=1, parent=None)
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("eligible non-production", str(caught.exception))

    def test_eligible_fixture_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="fixture", research_eligible=1, parent=None)
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("eligible non-production", str(caught.exception))

    def test_eligible_production_child_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1)
        add_observation(conn, 2, origin="production", research_eligible=1, parent="sr:1")
        with self.assertRaises(ResearchPopulationError) as caught:
            assert_research_population(conn)
        self.assertIn("production child is research eligible", str(caught.exception))

    def test_classifier_identity_is_deterministic(self) -> None:
        self.assertEqual(
            classifier_identity([CAPTURE_CLASSIFIER_VERSION, HISTORICAL_CLASSIFIER_VERSION]),
            "b0-2026-09-28+capture-v1",
        )
        self.assertEqual(
            classifier_identity([HISTORICAL_CLASSIFIER_VERSION, HISTORICAL_CLASSIFIER_VERSION]),
            HISTORICAL_CLASSIFIER_VERSION,
        )

    def test_excluded_capture_does_not_change_eligible_hash(self) -> None:
        conn = connect()
        add_observation(conn, 1, classifier_version=HISTORICAL_CLASSIFIER_VERSION)
        before = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        add_observation(
            conn,
            2,
            origin="test",
            research_eligible=0,
            parent="sr:1",
            classifier_version=CAPTURE_CLASSIFIER_VERSION,
        )
        add_observation(
            conn,
            3,
            origin="synthetic",
            research_eligible=0,
            parent="sr:1",
            classifier_version=CAPTURE_CLASSIFIER_VERSION,
        )
        add_observation(
            conn,
            4,
            origin="fixture",
            research_eligible=0,
            classifier_version=CAPTURE_CLASSIFIER_VERSION,
        )
        after = population_stamp(conn, generated_at="2026-09-28T18:05:00Z")
        self.assertEqual(after.eligible_population_hash, before.eligible_population_hash)
        self.assertEqual(after.eligible_population_count, before.eligible_population_count)
        self.assertEqual(after.classifier_version, HISTORICAL_CLASSIFIER_VERSION)
        self.assertEqual(after.eligible_population_hash, hash_eligible_observation_uids(["sr:1"]))

    def test_eligible_capture_changes_hash_and_composite_identity(self) -> None:
        conn = connect()
        add_observation(conn, 1, classifier_version=HISTORICAL_CLASSIFIER_VERSION)
        before = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        add_observation(conn, 2, classifier_version=CAPTURE_CLASSIFIER_VERSION)
        after = population_stamp(conn, generated_at="2026-09-28T18:05:00Z")
        self.assertEqual(after.eligible_population_count, 2)
        self.assertNotEqual(after.eligible_population_hash, before.eligible_population_hash)
        self.assertEqual(after.eligible_population_hash, hash_eligible_observation_uids(["sr:1", "sr:2"]))
        self.assertEqual(after.classifier_version, "b0-2026-09-28+capture-v1")

    def test_population_hash_is_deterministic_and_ordered_by_scan_result_id(self) -> None:
        conn = connect()
        add_observation(conn, 10)
        add_observation(conn, 2)
        first = population_stamp(conn, generated_at="2026-09-28T18:00:00Z")
        second = population_stamp(conn, generated_at="2026-09-28T18:05:00Z")
        expected = hashlib.sha256(b"sr:2\nsr:10\n").hexdigest()
        self.assertEqual(first.eligible_population_hash, expected)
        self.assertEqual(second.eligible_population_hash, expected)
        self.assertEqual(first.eligible_population_count, 2)
        self.assertNotEqual(
            first.eligible_population_hash,
            hashlib.sha256(b"sr:10\nsr:2\n").hexdigest(),
        )

    def test_predicate_uses_validated_alias(self) -> None:
        predicate = research_eligible_predicate("sr")
        self.assertIn("observation_provenance AS _observation_provenance", predicate)
        self.assertIn("_observation_provenance.scan_result_id = sr.id", predicate)
        self.assertIn("_observation_provenance.research_eligible = 1", predicate)
        with self.assertRaises(ValueError):
            research_eligible_predicate("sr; DROP TABLE scan_results")
        with self.assertRaises(ValueError):
            research_eligible_predicate("select")

    def test_import_does_not_open_a_database_or_memory_store(self) -> None:
        script = """
import re
import sqlite3
import sys
calls = []
real_connect = sqlite3.connect

def wrapped(*args, **kwargs):
    calls.append((args, kwargs))
    return real_connect(*args, **kwargs)

sqlite3.connect = wrapped
import observation_evidence
assert "memory_store" not in sys.modules, sorted(sys.modules)
assert calls == [], calls
source = open("observation_evidence.py", encoding="utf-8").read()
assert re.search(r"(?m)^\\s*(?:import|from)\\s+memory_store\\b", source) is None
assert "scout_memory" not in source
print("import_ok")
"""
        completed = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent,
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("import_ok", completed.stdout)


if __name__ == "__main__":
    unittest.main()
