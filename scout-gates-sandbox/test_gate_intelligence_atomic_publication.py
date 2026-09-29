#!/usr/bin/env python3
"""Atomic gate-intelligence publication. Temporary databases only."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_store as ms
import performance_tracker
from migrate_derived_builds import CREATE_INDEX_SQL, CREATE_TABLE_SQL
from migrate_gate_intelligence_build_infrastructure import (
    BACKUP_TABLE,
    MigrationAbort,
    apply_schema,
    schema_status,
)
from observation_evidence import ResearchPopulationError


def legacy_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE gate_intelligence_metrics (
            gate_key TEXT PRIMARY KEY,
            gate_name TEXT NOT NULL,
            total_occurrences INTEGER NOT NULL,
            total_passes INTEGER NOT NULL,
            total_failures INTEGER NOT NULL,
            win_count INTEGER NOT NULL,
            loss_count INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_1d_return REAL,
            avg_3d_return REAL,
            avg_5d_return REAL,
            avg_10d_return REAL,
            avg_20d_return REAL,
            bullish_win_rate REAL NOT NULL,
            bearish_win_rate REAL NOT NULL,
            predictive_score REAL NOT NULL,
            confidence TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def insert_legacy(conn: sqlite3.Connection, gate_key: str) -> None:
    conn.execute(
        """
        INSERT INTO gate_intelligence_metrics (
            gate_key, gate_name, total_occurrences, total_passes, total_failures,
            win_count, loss_count, win_rate, bullish_win_rate, bearish_win_rate,
            predictive_score, confidence, updated_at
        ) VALUES (?, ?, 4, 3, 1, 2, 1, 66.67, 100, 0, 40, 'Medium', '2026-01-01T00:00:00+00:00')
        """,
        (gate_key, gate_key),
    )


class GateIntelligenceMigrationTests(unittest.TestCase):
    def test_fourteen_legacy_rows_stay_unstamped(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "intel.db", isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                legacy_table(conn)
                for index in range(14):
                    insert_legacy(conn, f"GATE-{index:02d}")
                self.assertEqual(apply_schema(conn), "applied")
                rows = conn.execute(
                    "SELECT gate_key, total_occurrences, build_id FROM gate_intelligence_metrics ORDER BY gate_key"
                ).fetchall()
                self.assertEqual(len(rows), 14)
                self.assertTrue(all(row["build_id"] is None for row in rows))
                self.assertEqual(rows[0]["total_occurrences"], 4)
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0], 0)
                version = conn.execute("PRAGMA data_version").fetchone()[0]
                self.assertEqual(apply_schema(conn), "already_applied")
                self.assertEqual(schema_status(conn), "already_applied")
                self.assertEqual(conn.execute("PRAGMA data_version").fetchone()[0], version)
            finally:
                conn.close()

    def test_conflicting_backup_fails_closed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "conflict.db", isolation_level=None)
            try:
                legacy_table(conn)
                insert_legacy(conn, "LEGACY")
                conn.execute(f"CREATE TABLE {BACKUP_TABLE} (gate_key TEXT)")
                self.assertEqual(schema_status(conn), "conflict")
                with self.assertRaises(MigrationAbort):
                    apply_schema(conn)
                self.assertNotIn(
                    "build_id",
                    [row[1] for row in conn.execute("PRAGMA table_info(gate_intelligence_metrics)")],
                )
            finally:
                conn.close()

    def test_failed_backup_creation_rolls_back_the_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "rollback.db", isolation_level=None)
            try:
                legacy_table(conn)
                conn.execute(f"CREATE VIEW {BACKUP_TABLE} AS SELECT 'x' AS gate_key")
                with self.assertRaises(sqlite3.OperationalError):
                    apply_schema(conn)
                self.assertNotIn(
                    "build_id",
                    [row[1] for row in conn.execute("PRAGMA table_info(gate_intelligence_metrics)")],
                )
            finally:
                conn.close()


class GateIntelligencePublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(
            """
            CREATE TABLE scan_results (
                id INTEGER PRIMARY KEY,
                run_id INTEGER,
                ticker TEXT,
                final_direction TEXT,
                stock_outcome_label TEXT,
                return_1d REAL,
                return_3d REAL,
                return_5d REAL,
                return_10d REAL,
                return_20d REAL,
                gate_snapshot_json TEXT,
                gates_json TEXT,
                is_test_record INTEGER
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
        legacy_table(self.conn)
        self.conn.execute("ALTER TABLE gate_intelligence_metrics ADD COLUMN build_id TEXT")
        self.conn.execute(
            """
            CREATE TABLE gate_intelligence_metrics_backup (
                gate_key TEXT PRIMARY KEY,
                gate_name TEXT NOT NULL,
                total_occurrences INTEGER NOT NULL,
                total_passes INTEGER NOT NULL,
                total_failures INTEGER NOT NULL,
                win_count INTEGER NOT NULL,
                loss_count INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                avg_1d_return REAL,
                avg_3d_return REAL,
                avg_5d_return REAL,
                avg_10d_return REAL,
                avg_20d_return REAL,
                bullish_win_rate REAL NOT NULL,
                bearish_win_rate REAL NOT NULL,
                predictive_score REAL NOT NULL,
                confidence TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                build_id TEXT
            )
            """
        )
        insert_legacy(self.conn, "LEGACY")
        self.conn.execute(
            """
            INSERT INTO gate_intelligence_metrics_backup (
                gate_key, gate_name, total_occurrences, total_passes, total_failures,
                win_count, loss_count, win_rate, bullish_win_rate, bearish_win_rate,
                predictive_score, confidence, updated_at
            ) VALUES ('PREVIOUS', 'PREVIOUS', 1, 0, 1, 0, 0, 0, 0, 0, 1, 'Low', '2026-01-01T00:00:00+00:00')
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _add(
        self,
        scan_id: int,
        gate_key: str,
        *,
        origin: str = "production",
        eligible: int = 1,
        parent: str | None = None,
        outcome: str = "WIN",
        direction: str = "Bullish",
        classifier_version: str = "b0-2026-09-28",
    ) -> None:
        snapshot = json.dumps({"gates": [{"key": gate_key, "name": gate_key, "passed": True}]})
        self.conn.execute(
            """
            INSERT INTO scan_results (
                id, run_id, ticker, final_direction, stock_outcome_label, return_20d,
                gate_snapshot_json, is_test_record
            ) VALUES (?, 1, ?, ?, ?, 5.0, ?, 0)
            """,
            (scan_id, gate_key, direction, outcome, snapshot),
        )
        self.conn.execute(
            """
            INSERT INTO observation_provenance (
                observation_uid, scan_result_id, origin, record_class,
                parent_observation_uid, research_eligible, classification_reason,
                classified_at, classifier_version
            ) VALUES (?, ?, ?, 'raw', ?, ?, 'test', '2026-09-28T00:00:00Z', ?)
            """,
            (f"sr:{scan_id}", scan_id, origin, parent, eligible, classifier_version),
        )

    def test_candidates_exclude_synthetic_and_keep_the_formula(self) -> None:
        self._add(1, "SENTINEL")
        self._add(10, "SYNTHETIC", origin="synthetic", eligible=0, parent="sr:1", outcome="LOSS", direction="Bearish")
        self.conn.commit()
        calculated = ms.calculate_gate_intelligence_candidates(self.conn)
        keys = {row["gate_key"] for row in calculated["candidates"]}
        self.assertEqual(calculated["source_rows"], 1)
        self.assertIn("SENTINEL", keys)
        self.assertNotIn("SYNTHETIC", keys)
        sentinel = next(row for row in calculated["candidates"] if row["gate_key"] == "SENTINEL")
        self.assertEqual(sentinel["total_occurrences"], 1)
        self.assertEqual(sentinel["total_passes"], 1)
        self.assertEqual(sentinel["win_count"], 1)
        self.assertEqual(sentinel["win_rate"], 100.0)
        self.assertEqual(sentinel["bullish_win_rate"], 100.0)
        self.assertEqual(
            sentinel["predictive_score"],
            ms.predictive_score_for_gate(sentinel["_scoring_item"]),
        )
        self.assertEqual(
            self.conn.execute("SELECT gate_key FROM gate_intelligence_metrics").fetchone()[0],
            "LEGACY",
        )

    def test_successful_publication_backs_up_legacy_rows(self) -> None:
        self._add(1, "SENTINEL")
        result = ms.publish_gate_intelligence_metrics(self.conn)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["source_rows"], 1)
        live = self.conn.execute("SELECT gate_key, build_id FROM gate_intelligence_metrics").fetchall()
        self.assertEqual([row["gate_key"] for row in live], ["SENTINEL"])
        self.assertEqual(live[0]["build_id"], result["build_id"])
        backup = self.conn.execute(f"SELECT gate_key, build_id FROM {BACKUP_TABLE}").fetchone()
        self.assertEqual(backup["gate_key"], "LEGACY")
        self.assertIsNone(backup["build_id"])
        manifest = self.conn.execute(
            "SELECT * FROM derived_builds WHERE build_id = ?",
            (result["build_id"],),
        ).fetchone()
        self.assertEqual(manifest["status"], "COMPLETED")
        self.assertEqual(manifest["artifact_type"], "gate_intelligence_metrics")
        self.assertEqual(manifest["builder_version"], "gate-intelligence-1")
        self.assertEqual(manifest["artifact_row_count"], 1)
        self.assertEqual(manifest["classifier_version"], "b0-2026-09-28")
        self.assertEqual(manifest["eligible_population_count"], 1)
        self.assertEqual(manifest["eligible_population_hash"], result["eligible_population_hash"])

    def _expect_failed(self, probe, error_type: type[BaseException]) -> None:
        self._add(1, "SENTINEL")
        with self.assertRaises(error_type):
            ms.publish_gate_intelligence_metrics(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT gate_key FROM gate_intelligence_metrics").fetchone()[0],
            "LEGACY",
        )
        self.assertEqual(
            self.conn.execute(f"SELECT gate_key FROM {BACKUP_TABLE}").fetchone()[0],
            "PREVIOUS",
        )
        self.assertEqual(self.conn.execute("SELECT status FROM derived_builds").fetchone()[0], "FAILED")

    def test_calculation_failure_leaves_metrics_untouched(self) -> None:
        self._expect_failed(
            lambda stage: (_ for _ in ()).throw(RuntimeError("calc failed")) if stage == "before_calculate" else None,
            RuntimeError,
        )

    def test_population_change_before_lock_does_not_publish(self) -> None:
        def probe(stage: str) -> None:
            if stage == "before_lock":
                self.conn.execute(
                    "UPDATE observation_provenance SET research_eligible = 0 WHERE scan_result_id = 1"
                )

        self._expect_failed(probe, ms.GateIntelligencePublishError)

    def test_population_change_inside_transaction_does_not_publish(self) -> None:
        def probe(stage: str) -> None:
            if stage == "inside_transaction":
                self.conn.execute(
                    "UPDATE observation_provenance SET research_eligible = 0 WHERE scan_result_id = 1"
                )

        self._expect_failed(probe, ms.GateIntelligencePublishError)

    def test_failure_after_delete_restores_live_and_previous_backup(self) -> None:
        def probe(stage: str) -> None:
            if stage == "after_delete":
                raise RuntimeError("insert failed")

        self._expect_failed(probe, RuntimeError)

    def test_manifest_completion_failure_rolls_back_publication(self) -> None:
        def probe(stage: str) -> None:
            if stage == "after_complete":
                raise RuntimeError("manifest completion failed")

        self._expect_failed(probe, RuntimeError)

    def test_invalid_provenance_fails_before_publication(self) -> None:
        self._add(228, "FIXTURE", origin="fixture", eligible=1)
        with self.assertRaises(ResearchPopulationError):
            ms.publish_gate_intelligence_metrics(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT gate_key FROM gate_intelligence_metrics").fetchone()[0],
            "LEGACY",
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM derived_builds").fetchone()[0], 0)

    def test_capture_during_calculation_aborts_then_composite_build_succeeds(self) -> None:
        self._add(1, "SENTINEL")

        def probe(stage: str) -> None:
            if stage == "before_lock":
                self._add(2, "CAPTURE", classifier_version="capture-v1")

        with self.assertRaises(ms.GateIntelligencePublishError):
            ms.publish_gate_intelligence_metrics(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT gate_key FROM gate_intelligence_metrics").fetchone()[0],
            "LEGACY",
        )
        self.assertEqual(self.conn.execute("SELECT status FROM derived_builds").fetchone()[0], "FAILED")
        result = ms.publish_gate_intelligence_metrics(self.conn)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["classifier_version"], "b0-2026-09-28+capture-v1")
        manifest = self.conn.execute(
            "SELECT classifier_version FROM derived_builds WHERE build_id = ?",
            (result["build_id"],),
        ).fetchone()
        self.assertEqual(manifest["classifier_version"], "b0-2026-09-28+capture-v1")


class GateIntelligenceCallerTests(unittest.TestCase):
    def test_outcome_refresh_and_scan_save_do_not_publish(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "maintain.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                with ms.connect() as conn:
                    conn.executescript(
                        """
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
                    conn.execute(
                        """
                        INSERT INTO scan_results (
                            id, run_id, timestamp, ticker, final_direction, stock_outcome_label,
                            return_1d, return_3d, return_5d, return_10d, return_20d
                        ) VALUES (1, 1, '2026-06-01T00:00:00Z', 'AAA', 'Bullish', 'WIN', 1, 1, 1, 1, 1)
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO observation_provenance (
                            observation_uid, scan_result_id, origin, record_class,
                            research_eligible, classification_reason, classified_at, classifier_version
                        ) VALUES ('sr:1', 1, 'production', 'raw', 1, 'test', '2026-09-28T00:00:00Z', 'b0-2026-09-28')
                        """
                    )
                    insert_legacy(conn, "LEGACY")
                    conn.commit()
                performance_tracker.update_outcomes()
                ms.save_scan_result(
                    {
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
                )
                called = []
                with patch.object(ms, "refresh_gate_intelligence_metrics", side_effect=lambda *args, **kwargs: called.append(1)):
                    created = ms.create_gate_alpha_test_record()
                self.assertEqual(called, [])
                self.assertTrue(created["ok"])
                with ms.connect() as conn:
                    self.assertEqual(
                        conn.execute("SELECT gate_key, build_id FROM gate_intelligence_metrics").fetchone()["gate_key"],
                        "LEGACY",
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
                    conn.execute(
                        """
                        INSERT INTO scan_results (
                            id, run_id, timestamp, ticker, final_direction, stock_outcome_label,
                            return_20d, gate_snapshot_json
                        ) VALUES (
                            1, 1, '2026-06-01T00:00:00Z', 'SENTINEL', 'Bullish', 'WIN', 5,
                            '{"gates":[{"key":"SENTINEL","name":"SENTINEL","passed":true}]}'
                        )
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO observation_provenance (
                            observation_uid, scan_result_id, origin, record_class,
                            research_eligible, classification_reason, classified_at, classifier_version
                        ) VALUES ('sr:1', 1, 'production', 'raw', 1, 'test', '2026-09-28T00:00:00Z', 'b0-2026-09-28')
                        """
                    )
                    conn.commit()
                result = ms.rebuild_gate_intelligence()
        self.assertTrue(result["ok"])
        self.assertEqual(result["rebuild"]["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
