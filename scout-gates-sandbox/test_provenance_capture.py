#!/usr/bin/env python3
"""Provenance written at observation capture. Temporary databases only."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_store as ms
from observation_evidence import ResearchPopulationError


def payload(timestamp: str = "2026-06-02T12:00:00+00:00") -> dict:
    return {
        "runTimestamp": timestamp,
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


class ProvenanceCaptureTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.tmp.name) / "capture.db"
        self.patches = [
            patch.object(ms, "DB_PATH", self.db_path),
            patch.object(ms, "_DB_INITIALIZED", False),
        ]
        for item in self.patches:
            item.start()
        ms.init_db()

    def tearDown(self) -> None:
        for item in reversed(self.patches):
            item.stop()
        self.tmp.cleanup()

    def test_production_save_creates_capture_provenance(self) -> None:
        run_id = ms.save_scan_result(payload())
        self.assertGreater(run_id, 0)
        with ms.connect() as conn:
            scans = conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]
            row = conn.execute(
                """
                SELECT observation_uid, origin, research_eligible, parent_observation_uid,
                       classifier_version
                FROM observation_provenance
                """
            ).fetchone()
        self.assertEqual(scans, 1)
        self.assertEqual(row["observation_uid"], "sr:1")
        self.assertEqual(row["origin"], "production")
        self.assertEqual(row["research_eligible"], 1)
        self.assertIsNone(row["parent_observation_uid"])
        self.assertEqual(row["classifier_version"], "capture-v1")

    def test_provenance_insert_failure_rolls_back_the_scan_result(self) -> None:
        with patch.object(ms, "record_capture_provenance", side_effect=RuntimeError("provenance failed")):
            with self.assertRaises(RuntimeError):
                ms.save_scan_result(payload())
        with ms.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0], 0)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_runs").fetchone()[0], 0)
            if conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'observation_provenance'
                """
            ).fetchone() is not None:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM observation_provenance").fetchone()[0], 0)

    def test_scan_result_failure_creates_no_provenance(self) -> None:
        with ms.connect() as conn:
            conn.execute(
                """
                CREATE TRIGGER abort_scan_insert
                BEFORE INSERT ON scan_results
                BEGIN
                    SELECT RAISE(ABORT, 'scan insert failed');
                END
                """
            )
            conn.commit()
        with self.assertRaises(sqlite3.Error):
            ms.save_scan_result(payload())
        with ms.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0], 0)
            table = conn.execute(
                """
                SELECT 1 FROM sqlite_master
                WHERE type = 'table' AND name = 'observation_provenance'
                """
            ).fetchone()
            if table is not None:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM observation_provenance").fetchone()[0], 0)

    def test_repeated_save_once_does_not_duplicate(self) -> None:
        first = ms.save_scan_result_once(payload())
        second = ms.save_scan_result_once(payload())
        self.assertFalse(first["alreadySaved"])
        self.assertTrue(second["alreadySaved"])
        self.assertEqual(first["memoryRunId"], second["memoryRunId"])
        with ms.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM observation_provenance").fetchone()[0], 1)

    def test_save_once_missing_provenance_fails_closed(self) -> None:
        body = payload("2026-06-03T12:00:00+00:00")
        with ms.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO scan_runs (
                    timestamp, universe_mode, pick_mode, candidates_json, api_url
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    body["runTimestamp"],
                    body["universeMode"],
                    body["pickMode"],
                    ms.json_dump(body["candidates"]),
                    body["apiUrl"],
                ),
            )
            conn.execute(
                """
                INSERT INTO scan_results (run_id, timestamp, ticker)
                VALUES (?, ?, 'AAPL')
                """,
                (int(cursor.lastrowid), body["runTimestamp"]),
            )
            conn.commit()
        with self.assertRaises(ResearchPopulationError) as caught:
            ms.save_scan_result_once(body)
        self.assertIn("lacks provenance", str(caught.exception))
        self.assertIn("manufacture", str(caught.exception))
        with ms.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0], 1)
            self.assertIsNone(
                conn.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'observation_provenance'
                    """
                ).fetchone()
            )

    def test_outcome_copy_records_synthetic_provenance(self) -> None:
        ms.save_scan_result(payload())
        created = ms.create_outcome_test_record(ticker="AAPL")
        with ms.connect() as conn:
            row = conn.execute(
                """
                SELECT origin, research_eligible, parent_observation_uid, classifier_version
                FROM observation_provenance
                WHERE scan_result_id = ?
                """,
                (created["test_result_id"],),
            ).fetchone()
        self.assertEqual(row["origin"], "synthetic")
        self.assertEqual(row["research_eligible"], 0)
        self.assertEqual(row["parent_observation_uid"], f"sr:{created['source_result_id']}")
        self.assertEqual(row["classifier_version"], "capture-v1")

    def test_gate_alpha_test_record_records_test_provenance(self) -> None:
        ms.save_scan_result(payload())
        created = ms.create_gate_alpha_test_record()
        self.assertTrue(created["created"])
        with ms.connect() as conn:
            row = conn.execute(
                """
                SELECT origin, research_eligible, parent_observation_uid, classifier_version
                FROM observation_provenance
                WHERE scan_result_id = ?
                """,
                (created["test_result_id"],),
            ).fetchone()
        self.assertEqual(row["origin"], "test")
        self.assertEqual(row["research_eligible"], 0)
        self.assertEqual(row["parent_observation_uid"], f"sr:{created['source_result_id']}")
        self.assertEqual(row["classifier_version"], "capture-v1")

    def test_production_save_does_not_require_derived_stores(self) -> None:
        with ms.connect() as conn:
            for table in (
                "pattern_intelligence",
                "pattern_intelligence_backup",
                "gate_alpha_metrics",
                "gate_alpha_metrics_backup",
                "gate_intelligence_metrics",
                "gate_intelligence_metrics_backup",
                "derived_builds",
            ):
                conn.execute(f"DROP TABLE IF EXISTS {table}")
            conn.commit()
        with patch.object(ms, "rebuild_patterns", side_effect=AssertionError("pattern")), \
             patch.object(ms, "rebuild_gate_alpha", side_effect=AssertionError("alpha")), \
             patch.object(ms, "rebuild_gate_intelligence", side_effect=AssertionError("intelligence")):
            ms.save_scan_result(payload())
        with ms.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM observation_provenance").fetchone()[0], 1)
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'derived_builds'"
                ).fetchone()
            )
            self.assertIsNone(
                conn.execute(
                    "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pattern_intelligence'"
                ).fetchone()
            )


if __name__ == "__main__":
    unittest.main()
