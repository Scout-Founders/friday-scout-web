#!/usr/bin/env python3
"""Manifest store and derived_builds migration. Temporary databases only."""

from __future__ import annotations

import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from derived_build_manifest import (
    DerivedBuildError,
    complete_build,
    fail_build,
    generate_build_id,
    get_build,
    latest_completed_build,
    mark_rolled_back,
    start_build,
)
from migrate_derived_builds import (
    CREATE_INDEX_SQL,
    CREATE_TABLE_SQL,
    MigrationAbort,
    apply_schema,
    schema_status,
)
from observation_evidence import PopulationStamp


def memory_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(CREATE_TABLE_SQL + ";\n" + CREATE_INDEX_SQL + ";")
    return conn


def stamp(**overrides: object) -> PopulationStamp:
    values = {
        "classifier_version": "b0-2026-09-28",
        "eligible_population_count": 227,
        "eligible_population_hash": "eaa0e0ec2c10ff7bd5ac99fa8d9ed5cdaed763579e0a7aa4bb24e84292763fc8",
        "generated_at": "2026-09-28T21:00:00Z",
    }
    values.update(overrides)
    return PopulationStamp(**values)


class ManifestStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = memory_db()

    def tearDown(self) -> None:
        self.conn.close()

    def test_started_copies_population_stamp(self) -> None:
        population = stamp()
        row = start_build(
            self.conn,
            artifact_type="pattern_intelligence",
            builder_version="pattern-1",
            stamp=population,
        )
        self.assertEqual(row["status"], "STARTED")
        self.assertEqual(row["classifier_version"], population.classifier_version)
        self.assertEqual(row["eligible_population_count"], population.eligible_population_count)
        self.assertEqual(row["eligible_population_hash"], population.eligible_population_hash)
        self.assertEqual(row["started_at"], population.generated_at)
        self.assertIsNone(row["built_at"])
        self.assertIsNone(row["artifact_row_count"])

    def test_generated_build_ids_are_unique_text_keys(self) -> None:
        moment = datetime(2026, 9, 28, 21, 15, 0, tzinfo=timezone.utc)
        seen = {
            generate_build_id("gate_alpha_metrics", "gate-alpha-1", now=moment)
            for _ in range(40)
        }
        self.assertEqual(len(seen), 40)
        for build_id in seen:
            self.assertRegex(
                build_id,
                r"^gate-alpha-gate-alpha-1-20260928T211500Z-[0-9a-f]{8}$",
            )

    def test_duplicate_build_id_fails_closed(self) -> None:
        first = start_build(
            self.conn,
            artifact_type="pattern_intelligence",
            builder_version="pattern-1",
            stamp=stamp(),
        )
        with patch(
            "derived_build_manifest.generate_build_id",
            return_value=first["build_id"],
        ):
            with self.assertRaises(DerivedBuildError) as caught:
                start_build(
                    self.conn,
                    artifact_type="pattern_intelligence",
                    builder_version="pattern-1",
                    stamp=stamp(),
                )
        self.assertIn("refusing to overwrite", str(caught.exception))
        self.assertEqual(
            self.conn.execute("SELECT COUNT(*) FROM derived_builds").fetchone()[0],
            1,
        )

    def test_started_can_complete_fail_or_roll_back_only_on_legal_paths(self) -> None:
        completed = start_build(
            self.conn,
            artifact_type="pattern_intelligence",
            builder_version="pattern-1",
            stamp=stamp(),
        )
        done = complete_build(
            self.conn,
            completed["build_id"],
            built_at="2026-09-28T21:05:00Z",
            artifact_row_count=36,
        )
        self.assertEqual(done["status"], "COMPLETED")
        self.assertEqual(done["artifact_row_count"], 36)
        rolled = mark_rolled_back(self.conn, completed["build_id"])
        self.assertEqual(rolled["status"], "ROLLED_BACK")

        failed = start_build(
            self.conn,
            artifact_type="gate_alpha_metrics",
            builder_version="gate-alpha-1",
            stamp=stamp(),
        )
        self.assertEqual(
            fail_build(self.conn, failed["build_id"], error_text="stamp moved")["status"],
            "FAILED",
        )

    def test_illegal_transitions_are_rejected(self) -> None:
        started = start_build(
            self.conn,
            artifact_type="gate_intelligence_metrics",
            builder_version="gate-intelligence-1",
            stamp=stamp(),
        )
        with self.assertRaises(DerivedBuildError):
            mark_rolled_back(self.conn, started["build_id"])
        fail_build(self.conn, started["build_id"], error_text="stopped")
        with self.assertRaises(DerivedBuildError):
            complete_build(
                self.conn,
                started["build_id"],
                built_at="2026-09-28T21:06:00Z",
                artifact_row_count=14,
            )
        other = start_build(
            self.conn,
            artifact_type="pattern_intelligence",
            builder_version="pattern-1",
            stamp=stamp(),
        )
        complete_build(
            self.conn,
            other["build_id"],
            built_at="2026-09-28T21:07:00Z",
            artifact_row_count=0,
        )
        with self.assertRaises(DerivedBuildError):
            complete_build(
                self.conn,
                other["build_id"],
                built_at="2026-09-28T21:08:00Z",
                artifact_row_count=1,
            )

    def test_unknown_artifact_negative_count_and_empty_error_are_rejected(self) -> None:
        with self.assertRaises(DerivedBuildError):
            start_build(
                self.conn,
                artifact_type="regime_snapshots",
                builder_version="regime-1",
                stamp=stamp(),
            )
        row = start_build(
            self.conn,
            artifact_type="pattern_intelligence",
            builder_version="pattern-1",
            stamp=stamp(),
        )
        with self.assertRaises(DerivedBuildError):
            complete_build(
                self.conn,
                row["build_id"],
                built_at="2026-09-28T21:09:00Z",
                artifact_row_count=-1,
            )
        with self.assertRaises(DerivedBuildError):
            fail_build(self.conn, row["build_id"], error_text="   ")
        self.assertEqual(get_build(self.conn, row["build_id"])["status"], "STARTED")

    def test_latest_completed_build_returns_the_newest_completion(self) -> None:
        older = start_build(
            self.conn,
            artifact_type="pattern_intelligence",
            builder_version="pattern-1",
            stamp=stamp(generated_at="2026-09-28T21:00:00Z"),
        )
        complete_build(
            self.conn,
            older["build_id"],
            built_at="2026-09-28T21:10:00Z",
            artifact_row_count=36,
        )
        newer = start_build(
            self.conn,
            artifact_type="pattern_intelligence",
            builder_version="pattern-1",
            stamp=stamp(generated_at="2026-09-28T21:11:00Z"),
        )
        complete_build(
            self.conn,
            newer["build_id"],
            built_at="2026-09-28T21:12:00Z",
            artifact_row_count=36,
        )
        start_build(
            self.conn,
            artifact_type="gate_alpha_metrics",
            builder_version="gate-alpha-1",
            stamp=stamp(),
        )
        latest = latest_completed_build(self.conn, "pattern_intelligence")
        self.assertEqual(latest["build_id"], newer["build_id"])
        self.assertIsNone(latest_completed_build(self.conn, "gate_alpha_metrics"))

    def test_import_does_not_open_a_database(self) -> None:
        script = """
import sqlite3
calls = []
real_connect = sqlite3.connect
def wrapped(*args, **kwargs):
    calls.append(args)
    return real_connect(*args, **kwargs)
sqlite3.connect = wrapped
import derived_build_manifest
import migrate_derived_builds
assert calls == [], calls
assert "scout_memory" not in open("derived_build_manifest.py", encoding="utf-8").read()
"""
        result = subprocess.run(
            [sys.executable, "-c", script],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


class MigrationTests(unittest.TestCase):
    def test_apply_is_idempotent_and_writes_nothing_the_second_time(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "manifest.db"
            conn = sqlite3.connect(path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                self.assertEqual(schema_status(conn), "missing")
                self.assertEqual(apply_schema(conn), "applied")
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM derived_builds").fetchone()[0],
                    0,
                )
                version = conn.execute("PRAGMA data_version").fetchone()[0]
                self.assertEqual(apply_schema(conn), "already_applied")
                self.assertEqual(conn.execute("PRAGMA data_version").fetchone()[0], version)
            finally:
                conn.close()

    def test_conflicting_schema_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "conflict.db"
            conn = sqlite3.connect(path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute("CREATE TABLE derived_builds (build_id TEXT PRIMARY KEY)")
                self.assertEqual(schema_status(conn), "conflict")
                with self.assertRaises(MigrationAbort):
                    apply_schema(conn)
                columns = [row[1] for row in conn.execute("PRAGMA table_info(derived_builds)")]
                self.assertEqual(columns, ["build_id"])
            finally:
                conn.close()

    def test_failed_index_creation_rolls_back_the_table(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rollback.db"
            conn = sqlite3.connect(path, isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                conn.execute(
                    "CREATE TABLE idx_derived_builds_artifact_status_started (id INTEGER)"
                )
                with self.assertRaises(sqlite3.OperationalError):
                    apply_schema(conn)
                self.assertIsNone(
                    conn.execute(
                        """
                        SELECT 1 FROM sqlite_master
                        WHERE type = 'table' AND name = 'derived_builds'
                        """
                    ).fetchone()
                )
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
