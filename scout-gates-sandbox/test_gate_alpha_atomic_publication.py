#!/usr/bin/env python3
"""Atomic gate-alpha publication. Temporary databases only."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_store as ms
import performance_tracker
from migrate_derived_builds import CREATE_INDEX_SQL, CREATE_TABLE_SQL
from migrate_gate_alpha_build_infrastructure import (
    BACKUP_TABLE,
    MigrationAbort,
    apply_schema,
    schema_status,
)
from observation_evidence import ResearchPopulationError


def legacy_alpha_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE gate_alpha_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gate_name TEXT NOT NULL,
            sector TEXT NOT NULL,
            market_regime TEXT NOT NULL,
            volatility_regime TEXT NOT NULL,
            sample_count INTEGER NOT NULL,
            wins INTEGER NOT NULL,
            losses INTEGER NOT NULL,
            win_rate REAL NOT NULL,
            avg_return REAL NOT NULL,
            expectancy REAL NOT NULL,
            confidence_score REAL NOT NULL,
            last_updated_utc TEXT NOT NULL
        )
        """
    )


def insert_legacy_metric(conn: sqlite3.Connection, gate_name: str = "LEGACY") -> None:
    conn.execute(
        """
        INSERT INTO gate_alpha_metrics (
            gate_name, sector, market_regime, volatility_regime,
            sample_count, wins, losses, win_rate, avg_return,
            expectancy, confidence_score, last_updated_utc
        ) VALUES (?, 'GLOBAL', 'GLOBAL', 'GLOBAL', 4, 2, 2, 50, 1, 0.5, 10, '2026-01-01T00:00:00+00:00')
        """,
        (gate_name,),
    )


class GateAlphaMigrationTests(unittest.TestCase):
    def test_legacy_rows_gain_nullable_build_id_and_empty_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "alpha.db", isolation_level=None)
            conn.row_factory = sqlite3.Row
            try:
                legacy_alpha_table(conn)
                insert_legacy_metric(conn, "LEGACY-A")
                insert_legacy_metric(conn, "LEGACY-B")
                self.assertEqual(apply_schema(conn), "applied")
                rows = conn.execute(
                    "SELECT gate_name, sample_count, build_id FROM gate_alpha_metrics ORDER BY id"
                ).fetchall()
                self.assertEqual([row["gate_name"] for row in rows], ["LEGACY-A", "LEGACY-B"])
                self.assertEqual([row["sample_count"] for row in rows], [4, 4])
                self.assertEqual([row["build_id"] for row in rows], [None, None])
                self.assertEqual(conn.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0], 0)
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
                legacy_alpha_table(conn)
                insert_legacy_metric(conn)
                conn.execute(f"CREATE TABLE {BACKUP_TABLE} (id INTEGER)")
                self.assertEqual(schema_status(conn), "conflict")
                with self.assertRaises(MigrationAbort):
                    apply_schema(conn)
                columns = [row[1] for row in conn.execute("PRAGMA table_info(gate_alpha_metrics)")]
                self.assertNotIn("build_id", columns)
            finally:
                conn.close()

    def test_failed_backup_creation_rolls_back_the_column(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            conn = sqlite3.connect(Path(tmp) / "rollback.db", isolation_level=None)
            try:
                legacy_alpha_table(conn)
                conn.execute(f"CREATE VIEW {BACKUP_TABLE} AS SELECT 1 AS id")
                with self.assertRaises(sqlite3.OperationalError):
                    apply_schema(conn)
                columns = [row[1] for row in conn.execute("PRAGMA table_info(gate_alpha_metrics)")]
                self.assertNotIn("build_id", columns)
            finally:
                conn.close()


class GateAlphaPublicationTests(unittest.TestCase):
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
                raw_result_json TEXT
            );
            CREATE TABLE feature_vectors (
                recommendation_id INTEGER,
                sector_name TEXT,
                risk_regime TEXT,
                iv_percentile REAL,
                vix_level REAL,
                raw_feature_json TEXT
            );
            CREATE TABLE gate_attributions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_id INTEGER,
                ticker TEXT,
                gate_name TEXT,
                gate_rank INTEGER
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
        legacy_alpha_table(self.conn)
        self.conn.execute("ALTER TABLE gate_alpha_metrics ADD COLUMN build_id TEXT")
        self.conn.execute(
            """
            CREATE TABLE gate_alpha_metrics_backup (
                id INTEGER PRIMARY KEY,
                gate_name TEXT NOT NULL,
                sector TEXT NOT NULL,
                market_regime TEXT NOT NULL,
                volatility_regime TEXT NOT NULL,
                sample_count INTEGER NOT NULL,
                wins INTEGER NOT NULL,
                losses INTEGER NOT NULL,
                win_rate REAL NOT NULL,
                avg_return REAL NOT NULL,
                expectancy REAL NOT NULL,
                confidence_score REAL NOT NULL,
                last_updated_utc TEXT NOT NULL,
                build_id TEXT
            )
            """
        )
        insert_legacy_metric(self.conn)
        self.conn.execute(
            """
            INSERT INTO gate_alpha_metrics_backup (
                gate_name, sector, market_regime, volatility_regime,
                sample_count, wins, losses, win_rate, avg_return,
                expectancy, confidence_score, last_updated_utc
            ) VALUES ('PREVIOUS', 'GLOBAL', 'GLOBAL', 'GLOBAL', 1, 0, 1, 0, -1, 0, 1, '2026-01-01T00:00:00+00:00')
            """
        )
        self.conn.commit()

    def tearDown(self) -> None:
        self.conn.close()

    def _add(
        self,
        scan_id: int,
        ticker: str,
        *,
        origin: str = "production",
        eligible: int = 1,
        parent: str | None = None,
        outcome: str = "WIN",
        return_20d: float = 5.0,
        classifier_version: str = "b0-2026-09-28",
    ) -> None:
        self.conn.execute(
            """
            INSERT INTO scan_results (
                id, run_id, ticker, final_direction, stock_outcome_label, return_20d, raw_result_json
            ) VALUES (?, 1, ?, 'Bullish', ?, ?, '{}')
            """,
            (scan_id, ticker, outcome, return_20d),
        )
        self.conn.execute(
            """
            INSERT INTO gate_attributions (scan_id, ticker, gate_name, gate_rank)
            VALUES (1, ?, ?, 1)
            """,
            (ticker, ticker),
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

    def test_candidates_ignore_excluded_observations_and_keep_formulas(self) -> None:
        self._add(1, "SENTINEL")
        self._add(10, "SYNTHETIC", origin="synthetic", eligible=0, parent="sr:1", outcome="LOSS", return_20d=-4)
        self.conn.commit()
        calculated = ms.calculate_gate_alpha_candidates(self.conn)
        names = {row["gate_name"] for row in calculated["candidates"]}
        self.assertEqual(calculated["completed_outcomes_checked"], 1)
        self.assertIn("SENTINEL", names)
        self.assertNotIn("SYNTHETIC", names)
        global_row = next(
            row for row in calculated["candidates"]
            if row["gate_name"] == "SENTINEL"
            and row["sector"] == "GLOBAL"
            and row["market_regime"] == "GLOBAL"
            and row["volatility_regime"] == "GLOBAL"
        )
        self.assertEqual(global_row["sample_count"], 1)
        self.assertEqual(global_row["wins"], 1)
        self.assertEqual(global_row["losses"], 0)
        self.assertEqual(global_row["win_rate"], 100.0)
        self.assertEqual(global_row["avg_return"], 5.0)
        self.assertEqual(global_row["expectancy"], 5.0)
        self.assertEqual(
            global_row["confidence_score"],
            ms.gate_alpha_confidence(1, 100.0, [5.0]),
        )
        self.assertEqual(
            self.conn.execute("SELECT gate_name FROM gate_alpha_metrics").fetchone()[0],
            "LEGACY",
        )

    def test_successful_publication_backs_up_legacy_rows(self) -> None:
        self._add(1, "SENTINEL")
        result = ms.refresh_gate_alpha_metrics(self.conn)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["completed_outcomes_checked"], 1)
        live = self.conn.execute(
            "SELECT build_id FROM gate_alpha_metrics"
        ).fetchall()
        self.assertEqual(len(live), result["metric_rows"])
        self.assertTrue(all(row["build_id"] == result["build_id"] for row in live))
        backup = self.conn.execute(
            f"SELECT gate_name, build_id FROM {BACKUP_TABLE}"
        ).fetchone()
        self.assertEqual(backup["gate_name"], "LEGACY")
        self.assertIsNone(backup["build_id"])
        manifest = self.conn.execute(
            "SELECT * FROM derived_builds WHERE build_id = ?",
            (result["build_id"],),
        ).fetchone()
        self.assertEqual(manifest["status"], "COMPLETED")
        self.assertEqual(manifest["artifact_type"], "gate_alpha_metrics")
        self.assertEqual(manifest["builder_version"], "gate-alpha-1")
        self.assertEqual(manifest["artifact_row_count"], len(live))
        self.assertEqual(manifest["classifier_version"], "b0-2026-09-28")
        self.assertEqual(manifest["eligible_population_count"], 1)

    def _expect_failed(self, probe, error_type: type[BaseException]) -> None:
        self._add(1, "SENTINEL")
        with self.assertRaises(error_type):
            ms.refresh_gate_alpha_metrics(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT gate_name FROM gate_alpha_metrics").fetchone()[0],
            "LEGACY",
        )
        self.assertEqual(
            self.conn.execute(f"SELECT gate_name FROM {BACKUP_TABLE}").fetchone()[0],
            "PREVIOUS",
        )
        self.assertEqual(
            self.conn.execute("SELECT status FROM derived_builds").fetchone()[0],
            "FAILED",
        )

    def test_calculation_failure_leaves_metrics_untouched(self) -> None:
        self._expect_failed(
            lambda stage: (_ for _ in ()).throw(RuntimeError("calc failed"))
            if stage == "before_calculate"
            else None,
            RuntimeError,
        )

    def test_population_change_before_lock_does_not_publish(self) -> None:
        def probe(stage: str) -> None:
            if stage == "before_lock":
                self.conn.execute(
                    "UPDATE observation_provenance SET research_eligible = 0 WHERE scan_result_id = 1"
                )

        self._expect_failed(probe, ms.GateAlphaPublishError)

    def test_population_change_inside_transaction_does_not_publish(self) -> None:
        def probe(stage: str) -> None:
            if stage == "inside_transaction":
                self.conn.execute(
                    "UPDATE observation_provenance SET research_eligible = 0 WHERE scan_result_id = 1"
                )

        self._expect_failed(probe, ms.GateAlphaPublishError)

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
        self.assertEqual(
            self.conn.execute(
                "SELECT COUNT(*) FROM derived_builds WHERE status = 'COMPLETED'"
            ).fetchone()[0],
            0,
        )

    def test_invalid_provenance_fails_before_publication(self) -> None:
        self._add(1, "FIXTURE", origin="fixture", eligible=1)
        with self.assertRaises(ResearchPopulationError):
            ms.refresh_gate_alpha_metrics(self.conn)
        self.assertEqual(
            self.conn.execute("SELECT gate_name FROM gate_alpha_metrics").fetchone()[0],
            "LEGACY",
        )
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM derived_builds").fetchone()[0], 0)

    def test_capture_during_calculation_aborts_then_composite_build_succeeds(self) -> None:
        self._add(1, "SENTINEL")

        def probe(stage: str) -> None:
            if stage == "before_lock":
                self._add(2, "CAPTURE", classifier_version="capture-v1")

        with self.assertRaises(ms.GateAlphaPublishError):
            ms.publish_gate_alpha_metrics(self.conn, _probe=probe)
        self.assertEqual(
            self.conn.execute("SELECT gate_name FROM gate_alpha_metrics").fetchone()[0],
            "LEGACY",
        )
        self.assertEqual(self.conn.execute("SELECT status FROM derived_builds").fetchone()[0], "FAILED")
        result = ms.publish_gate_alpha_metrics(self.conn)
        self.assertEqual(result["status"], "COMPLETED")
        manifest = self.conn.execute(
            "SELECT classifier_version FROM derived_builds WHERE build_id = ?",
            (result["build_id"],),
        ).fetchone()
        self.assertEqual(manifest["classifier_version"], "b0-2026-09-28+capture-v1")


class GateAlphaCallerTests(unittest.TestCase):
    def test_outcome_refresh_does_not_publish_gate_alpha(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "outcomes.db"
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
                    insert_legacy_metric(conn)
                    conn.commit()
                performance_tracker.update_outcomes()
                with ms.connect() as conn:
                    self.assertEqual(
                        conn.execute("SELECT gate_name, build_id FROM gate_alpha_metrics").fetchone()["gate_name"],
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
                            id, run_id, timestamp, ticker, final_direction, stock_outcome_label, return_20d
                        ) VALUES (1, 1, '2026-06-01T00:00:00Z', 'SENTINEL', 'Bullish', 'WIN', 5)
                        """
                    )
                    conn.execute(
                        """
                        INSERT INTO gate_attributions (
                            scan_id, ticker, gate_name, gate_weight, contribution_pct,
                            gate_rank, created_at_utc
                        ) VALUES (1, 'SENTINEL', 'SENTINEL', 1, 100, 1, '2026-06-01T00:00:00Z')
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
                result = ms.rebuild_gate_alpha()
        self.assertTrue(result["ok"])
        self.assertEqual(result["rebuild"]["status"], "COMPLETED")


if __name__ == "__main__":
    unittest.main()
