#!/usr/bin/env python3
"""Atomic pattern publication. Temporary databases only."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_store as ms
from migrate_derived_builds import CREATE_INDEX_SQL, CREATE_TABLE_SQL
from migrate_pattern_build_infrastructure import (
    BACKUP_TABLE,
    MigrationAbort,
    apply_schema,
    schema_status,
)
from observation_evidence import ResearchPopulationError
from pattern_engine import (
    PATTERN_BUILDER_VERSION,
    PatternPublishError,
    calculate_pattern_candidates,
    completed_rows,
    rebuild_pattern_intelligence,
    signatures_for_row,
    summarize_pattern,
)


def legacy_pattern_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE pattern_intelligence (
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
            created_at_utc TEXT NOT NULL
        )
        """
    )


def insert_legacy_pattern(conn: sqlite3.Connection, pattern_id: str = "legacy") -> None:
    conn.execute(
        """
        INSERT INTO pattern_intelligence (
            pattern_id, pattern_signature, sample_size, win_rate, loss_rate,
            expectancy_score, confidence_score, created_at_utc
        ) VALUES (?, '{}', 9, 50, 50, 0, 10, '2026-01-01T00:00:00+00:00')
        """,
        (pattern_id,),
    )


class PatternMigrationTests(unittest.TestCase):
    def test_legacy_rows_gain_nullable_build_id_and_empty_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "patterns.db", isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                legacy_pattern_table(conn)
                insert_legacy_pattern(conn, "legacy-a")
                insert_legacy_pattern(conn, "legacy-b")
                self.assertEqual(apply_schema(conn), "applied")
                rows = conn.execute(
                    "SELECT pattern_id, sample_size, build_id FROM pattern_intelligence ORDER BY pattern_id"
                ).fetchall()
                self.assertEqual([row["pattern_id"] for row in rows], ["legacy-a", "legacy-b"])
                self.assertEqual([row["sample_size"] for row in rows], [9, 9])
                self.assertEqual([row["build_id"] for row in rows], [None, None])
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0], 0)
                self.assertFalse(
                    conn.execute(
                        "SELECT 1 FROM sqlite_master WHERE name = 'derived_builds'"
                    ).fetchone()
                )
                version = conn.execute("PRAGMA data_version").fetchone()[0]
                self.assertEqual(schema_status(conn), "already_applied")
                self.assertEqual(apply_schema(conn), "already_applied")
                self.assertEqual(conn.execute("PRAGMA data_version").fetchone()[0], version)
            finally:
                conn.close()

    def test_conflicting_backup_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "conflict.db", isolation_level=None)
            try:
                legacy_pattern_table(conn)
                insert_legacy_pattern(conn)
                conn.execute(f"CREATE TABLE {BACKUP_TABLE} (pattern_id TEXT)")
                self.assertEqual(schema_status(conn), "conflict")
                with self.assertRaises(MigrationAbort):
                    apply_schema(conn)
                columns = [row[1] for row in conn.execute("PRAGMA table_info(pattern_intelligence)")]
                self.assertNotIn("build_id", columns)
                self.assertEqual(
                    conn.execute("SELECT sample_size FROM pattern_intelligence").fetchone()[0],
                    9,
                )
            finally:
                conn.close()

    def test_failed_backup_creation_rolls_back_the_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "rollback.db", isolation_level=None)
            try:
                legacy_pattern_table(conn)
                conn.execute(f"CREATE VIEW {BACKUP_TABLE} AS SELECT 1 AS pattern_id")
                with self.assertRaises(sqlite3.OperationalError):
                    apply_schema(conn)
                columns = [row[1] for row in conn.execute("PRAGMA table_info(pattern_intelligence)")]
                self.assertNotIn("build_id", columns)
            finally:
                conn.close()


class PatternPublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE scan_results (
                id INTEGER PRIMARY KEY,
                run_id INTEGER,
                final_direction TEXT,
                stock_outcome_label TEXT,
                return_1d REAL,
                return_3d REAL,
                return_5d REAL,
                return_10d REAL,
                return_20d REAL
            );
            CREATE TABLE feature_vectors (
                recommendation_id INTEGER,
                sector_name TEXT,
                iv_percentile REAL,
                atr REAL,
                relative_volume REAL,
                options_volume_score REAL,
                gate_scores_json TEXT,
                gate_states_json TEXT,
                return_1d REAL,
                return_3d REAL,
                return_5d REAL,
                return_10d REAL,
                return_20d REAL
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
            + CREATE_TABLE_SQL
            + ";\n"
            + CREATE_INDEX_SQL
            + ";"
        )
        legacy_pattern_table(self.conn)
        self.conn.execute("ALTER TABLE pattern_intelligence ADD COLUMN build_id TEXT")
        self.conn.execute(
            """
            CREATE TABLE pattern_intelligence_backup (
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
            )
            """
        )
        insert_legacy_pattern(self.conn)
        self.conn.execute(
            f"""
            INSERT INTO {BACKUP_TABLE} (
                pattern_id, pattern_signature, sample_size, win_rate, loss_rate,
                expectancy_score, confidence_score, created_at_utc
            ) VALUES ('previous-backup', '{{}}', 1, 0, 0, 0, 0, '2026-01-01T00:00:00+00:00')
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _add(
        self,
        scan_id: int,
        *,
        origin: str = "production",
        eligible: int = 1,
        classifier_version: str = "b0-2026-09-28",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO scan_results (
                id, run_id, final_direction, stock_outcome_label, return_20d
            ) VALUES (?, 1, 'Bullish', 'WIN', 4.0)
            """,
            (scan_id,),
        )
        self.conn.execute(
            "INSERT INTO feature_vectors (recommendation_id, sector_name) VALUES (?, 'Technology')",
            (scan_id,),
        )
        self.conn.execute(
            """
            INSERT INTO observation_provenance (
                observation_uid, scan_result_id, origin, record_class,
                parent_observation_uid, research_eligible, classification_reason,
                classified_at, classifier_version
            ) VALUES (?, ?, ?, 'raw', NULL, ?, 'test', '2026-09-28T00:00:00Z', ?)
            """,
            (f"sr:{scan_id}", scan_id, origin, eligible, classifier_version),
        )

    def test_successful_build_preserves_legacy_rows_in_backup(self) -> None:
        for scan_id in (1, 2, 3):
            self._add(scan_id)
        result = rebuild_pattern_intelligence(self.conn)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["completed_outcomes"], 3)
        live = self.conn.execute(
            "SELECT pattern_id, build_id FROM pattern_intelligence"
        ).fetchall()
        self.assertGreater(len(live), 0)
        self.assertTrue(all(row["build_id"] == result["build_id"] for row in live))
        backup = self.conn.execute(
            f"SELECT pattern_id, build_id FROM {BACKUP_TABLE}"
        ).fetchall()
        self.assertEqual([row["pattern_id"] for row in backup], ["legacy"])
        self.assertIsNone(backup[0]["build_id"])
        manifest = self.conn.execute(
            "SELECT * FROM derived_builds WHERE build_id = ?",
            (result["build_id"],),
        ).fetchone()
        self.assertEqual(manifest["status"], "COMPLETED")
        self.assertEqual(manifest["artifact_row_count"], len(live))
        self.assertEqual(manifest["builder_version"], PATTERN_BUILDER_VERSION)
        self.assertEqual(manifest["classifier_version"], "b0-2026-09-28")
        self.assertEqual(manifest["eligible_population_count"], 3)
        self.assertEqual(len(manifest["eligible_population_hash"]), 64)

    def test_calculation_failure_leaves_live_and_backup_untouched(self) -> None:
        self._add(1)
        with self.assertRaises(RuntimeError):
            rebuild_pattern_intelligence(
                self.conn,
                _probe=lambda stage: (_ for _ in ()).throw(RuntimeError("calc failed"))
                if stage == "before_calculate"
                else None,
            )
        self.assertEqual(
            self.conn.execute("SELECT pattern_id FROM pattern_intelligence").fetchone()[0],
            "legacy",
        )
        self.assertEqual(
            self.conn.execute(f"SELECT pattern_id FROM {BACKUP_TABLE}").fetchone()[0],
            "previous-backup",
        )
        manifest = self.conn.execute("SELECT status, error_text FROM derived_builds").fetchone()
        self.assertEqual(manifest["status"], "FAILED")
        self.assertIn("calc failed", manifest["error_text"])

    def test_population_change_before_lock_does_not_publish(self) -> None:
        for scan_id in (1, 2, 3):
            self._add(scan_id)

        def probe(stage: str) -> None:
            if stage == "before_lock":
                self.conn.execute(
                    "UPDATE observation_provenance SET research_eligible = 0 WHERE scan_result_id = 1"
                )

        with self.assertRaises(PatternPublishError):
            rebuild_pattern_intelligence(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT pattern_id FROM pattern_intelligence").fetchone()[0],
            "legacy",
        )
        self.assertEqual(
            self.conn.execute(f"SELECT pattern_id FROM {BACKUP_TABLE}").fetchone()[0],
            "previous-backup",
        )
        manifest = self.conn.execute("SELECT status FROM derived_builds").fetchone()
        self.assertEqual(manifest["status"], "FAILED")

    def test_population_change_inside_transaction_does_not_publish(self) -> None:
        for scan_id in (1, 2, 3):
            self._add(scan_id)

        def probe(stage: str) -> None:
            if stage == "inside_transaction":
                self.conn.execute(
                    "UPDATE observation_provenance SET research_eligible = 0 WHERE scan_result_id = 1"
                )

        with self.assertRaises(PatternPublishError):
            rebuild_pattern_intelligence(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT pattern_id, build_id FROM pattern_intelligence").fetchone()["pattern_id"],
            "legacy",
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM derived_builds").fetchone()[0],
            "FAILED",
        )

    def test_failure_after_delete_restores_live_and_previous_backup(self) -> None:
        for scan_id in (1, 2, 3):
            self._add(scan_id)

        def probe(stage: str) -> None:
            if stage == "after_delete":
                raise RuntimeError("insert failed")

        with self.assertRaises(RuntimeError):
            rebuild_pattern_intelligence(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT pattern_id FROM pattern_intelligence").fetchone()[0],
            "legacy",
        )
        self.assertEqual(
            self.conn.execute(f"SELECT pattern_id FROM {BACKUP_TABLE}").fetchone()[0],
            "previous-backup",
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM derived_builds").fetchone()[0],
            "FAILED",
        )

    def test_manifest_completion_failure_rolls_back_publication(self) -> None:
        for scan_id in (1, 2, 3):
            self._add(scan_id)

        def probe(stage: str) -> None:
            if stage == "after_complete":
                raise RuntimeError("manifest completion failed")

        with self.assertRaises(RuntimeError):
            rebuild_pattern_intelligence(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT pattern_id FROM pattern_intelligence").fetchone()[0],
            "legacy",
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM derived_builds").fetchone()[0],
            "FAILED",
        )
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM derived_builds WHERE status = 'COMPLETED'"
            ).fetchone()[0],
            0,
        )

    def test_invalid_provenance_fails_before_publication(self) -> None:
        self._add(1, origin="fixture", eligible=1)
        with self.assertRaises(ResearchPopulationError):
            rebuild_pattern_intelligence(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT pattern_id FROM pattern_intelligence").fetchone()[0],
            "legacy",
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM derived_builds").fetchone()[0], 0)
        self.assertEqual(
            self.conn.execute(f"SELECT pattern_id FROM {BACKUP_TABLE}").fetchone()[0],
            "previous-backup",
        )

    def test_candidate_metrics_match_the_existing_summary_formula(self) -> None:
        for scan_id in (1, 2, 3):
            self._add(scan_id)
        self.conn.commit()
        rows = completed_rows(self.conn)
        created_at = "2026-09-28T21:00:00+00:00"
        candidates = calculate_pattern_candidates(rows, created_at)
        expected = []
        grouped: dict[str, list[sqlite3.Row]] = {}
        for row in rows:
            for signature in signatures_for_row(row):
                grouped.setdefault(str(signature), []).append((signature, row))
        for items in grouped.values():
            signature = items[0][0]
            signature_rows = [item[1] for item in items]
            if len(signature_rows) >= 3:
                expected.append(summarize_pattern(signature, signature_rows, created_at))
        got = {(item["pattern_id"], item["sample_size"], item["win_rate"], item["expectancy_score"]) for item in candidates}
        want = {(item["pattern_id"], item["sample_size"], item["win_rate"], item["expectancy_score"]) for item in expected}
        self.assertEqual(got, want)
        sector = next(item for item in candidates if item["pattern_signature"]["kind"] == "direction_sector")
        self.assertEqual(sector["sample_size"], 3)
        self.assertEqual(sector["win_rate"], 100.0)
        self.assertEqual(sector["expectancy_score"], 104.0)
        self.assertEqual(sector["confidence_score"], 47.2)

    def test_capture_during_calculation_aborts_then_composite_build_succeeds(self) -> None:
        self._add(1)

        def probe(stage: str) -> None:
            if stage == "before_lock":
                self._add(2, classifier_version="capture-v1")

        with self.assertRaises(PatternPublishError):
            rebuild_pattern_intelligence(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT pattern_id FROM pattern_intelligence").fetchone()[0],
            "legacy",
        )
        self.assertEqual(self.conn.execute("SELECT status FROM derived_builds").fetchone()[0], "FAILED")
        result = rebuild_pattern_intelligence(self.conn)
        manifest = self.conn.execute(
            "SELECT status, classifier_version FROM derived_builds WHERE build_id = ?",
            (result["build_id"],),
        ).fetchone()
        self.assertEqual(manifest["status"], "COMPLETED")
        self.assertEqual(manifest["classifier_version"], "b0-2026-09-28+capture-v1")


class ExplicitRebuildTests(unittest.TestCase):
    def test_production_save_does_not_rebuild_patterns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "save.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                with ms.connect() as conn:
                    insert_legacy_pattern(conn)
                    conn.commit()
                payload = {
                    "runTimestamp": "2026-06-02T12:00:00+00:00",
                    "universeMode": "preset",
                    "pickMode": "score_only",
                    "timeout": 25,
                    "apiUrl": "https://example.test/gates",
                    "candidates": ["AAPL"],
                    "results": [
                        {
                            "ticker": "AAPL",
                            "score": 70,
                            "direction": "Bullish",
                            "passedAllGates": True,
                            "gates": [{"key": "sentinel", "name": "Market Filter", "passed": True}],
                            "directionBreakdown": {"direction": "Bullish"},
                            "raw": {"ticker": "AAPL", "direction": "Bullish"},
                        }
                    ],
                }
                run_id = ms.save_scan_result(payload)
                self.assertGreater(run_id, 0)
                with ms.connect() as conn:
                    self.assertEqual(
                        conn.execute("SELECT pattern_id, build_id FROM pattern_intelligence").fetchone()["pattern_id"],
                        "legacy",
                    )
                    self.assertIsNone(
                        conn.execute(
                            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'derived_builds'"
                        ).fetchone()
                    )

    def test_explicit_rebuild_entry_point_remains_callable(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "rebuild.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                with ms.connect() as conn:
                    conn.executescript(
                        CREATE_TABLE_SQL
                        + ";\n"
                        + CREATE_INDEX_SQL
                        + """;
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
                    )
                    conn.execute(
                        """
                        INSERT INTO scan_runs (timestamp, universe_mode, pick_mode)
                        VALUES ('2026-06-01T00:00:00Z', 'custom', 'score_only')
                        """
                    )
                    for scan_id in (1, 2, 3):
                        conn.execute(
                            """
                            INSERT INTO scan_results (
                                id, run_id, timestamp, ticker, final_direction, stock_outcome_label, return_20d
                            ) VALUES (?, 1, '2026-06-01T00:00:00Z', 'AAA', 'Bullish', 'WIN', 4)
                            """,
                            (scan_id,),
                        )
                        conn.execute(
                            """
                            INSERT INTO observation_provenance (
                                observation_uid, scan_result_id, origin, record_class,
                                research_eligible, classification_reason, classified_at, classifier_version
                            ) VALUES (?, ?, 'production', 'raw', 1, 'test', '2026-09-28T00:00:00Z', 'b0-2026-09-28')
                            """,
                            (f"sr:{scan_id}", scan_id),
                        )
                    conn.commit()
                result = ms.rebuild_patterns()
        self.assertTrue(result["ok"])
        self.assertEqual(result["rebuild"]["status"], "COMPLETED")
        self.assertIs(ms.rebuild_patterns.__name__, "rebuild_patterns")


if __name__ == "__main__":
    unittest.main()
