#!/usr/bin/env python3
"""Research Memory aggregate eligibility. Uses a temporary database, never scout_memory.db."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_store as ms
from observation_evidence import ResearchPopulationError


def add_observation(
    conn,
    scan_id: int,
    run_id: int,
    *,
    origin: str,
    research_eligible: int,
    parent: str | None = None,
    ticker: str = "AAA",
    direction: str = "Bullish",
    outcome: str = "WIN",
    score: float = 80.0,
    gates_json: str | None = None,
    failed_gates_json: str | None = None,
    is_test_record: int = 0,
    classifier_version: str = "b0-2026-09-28",
) -> None:
    conn.execute(
        """
        INSERT INTO scan_results (
            id, run_id, timestamp, ticker, scout_score, final_direction,
            stock_outcome_label, gates_json, failed_gates_json, is_test_record
        ) VALUES (?, ?, '2026-06-01T00:00:00Z', ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            scan_id,
            run_id,
            ticker,
            score,
            direction,
            outcome,
            gates_json,
            failed_gates_json,
            is_test_record,
        ),
    )
    conn.execute(
        """
        INSERT INTO observation_provenance (
            observation_uid, scan_result_id, origin, record_class,
            parent_observation_uid, research_eligible, classification_reason,
            classified_at, classifier_version
        ) VALUES (?, ?, ?, 'raw', ?, ?, 'test', '2026-09-28T00:00:00Z', ?)
        """,
        (f"sr:{scan_id}", scan_id, origin, parent, research_eligible, classifier_version),
    )


class ResearchMemoryAggregateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "memory.db"
        self._patchers = [
            patch.object(ms, "DB_PATH", self._db_path),
            patch.object(ms, "_DB_INITIALIZED", False),
        ]
        for patcher in self._patchers:
            patcher.start()
        ms.init_db()

    def tearDown(self) -> None:
        for patcher in self._patchers:
            patcher.stop()
        self._tmpdir.cleanup()

    def _seed_mixed_population(self) -> None:
        with ms.connect() as conn:
            conn.execute(
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
                )
                """
            )
            run_id = int(
                conn.execute(
                    """
                    INSERT INTO scan_runs (timestamp, universe_mode, pick_mode)
                    VALUES ('2026-06-01T00:00:00Z', 'custom', 'score_only')
                    """
                ).lastrowid
            )
            add_observation(
                conn,
                1,
                run_id,
                origin="production",
                research_eligible=1,
                ticker="NVDA",
                direction="Bullish",
                outcome="WIN",
                score=90,
                gates_json='{"SENTINEL": true, "PULSE": false}',
                failed_gates_json='["Threat Scan"]',
            )
            add_observation(
                conn,
                2,
                run_id,
                origin="production",
                research_eligible=1,
                ticker="MSFT",
                direction="Neutral",
                outcome="WIN",
                score=40,
            )
            add_observation(
                conn,
                3,
                run_id,
                origin="production",
                research_eligible=1,
                ticker="AAPL",
                direction="Bullish",
                outcome="PENDING",
                score=70,
            )
            add_observation(
                conn,
                10,
                run_id,
                origin="synthetic",
                research_eligible=0,
                parent="sr:1",
                ticker="AMD",
                direction="Bearish",
                outcome="LOSS",
                score=99,
                is_test_record=0,
                gates_json='{"SENTINEL": true}',
                failed_gates_json='["Volatility Read"]',
            )
            add_observation(
                conn,
                31,
                run_id,
                origin="test",
                research_eligible=0,
                parent="sr:1",
                ticker="AAPL",
                direction="Bearish",
                outcome="WIN",
                is_test_record=1,
                failed_gates_json='["Threat Scan"]',
            )
            add_observation(
                conn,
                228,
                run_id,
                origin="fixture",
                research_eligible=0,
                ticker="AAPL",
                direction="Bullish",
                outcome="PENDING",
                gates_json='{"SENTINEL": true}',
            )
            conn.commit()

    def test_research_aggregates_keep_eligible_production_only(self) -> None:
        self._seed_mixed_population()
        analytics = ms.get_outcome_analytics()
        self.assertEqual(analytics["total_completed"], 2)
        self.assertEqual(analytics["actionable_total"], 1)
        self.assertEqual(analytics["actionable_bullish_count"], 1)
        self.assertEqual(analytics["neutral_count"], 1)
        self.assertEqual(analytics["pending"], 1)
        self.assertEqual(analytics["bullish_win_rate"], 100.0)

        directions = {row["direction"]: row["total"] for row in ms.get_direction_accuracy()}
        self.assertEqual(directions["Bullish"], 2)
        self.assertEqual(directions["Neutral"], 1)
        self.assertNotIn("Bearish", directions)

        gate_rows = {row["gate"]: row for row in ms.get_gate_statistics()}
        self.assertEqual(gate_rows["SENTINEL"]["total"], 1)
        self.assertEqual(gate_rows["PULSE"]["fail"], 1)

        failures = {row["gate"]: row["count"] for row in ms.get_top_gate_failures()}
        self.assertEqual(failures, {"Threat Scan": 1})

    def test_audit_history_still_returns_excluded_observations(self) -> None:
        self._seed_mixed_population()
        history = ms.query_scan_results(limit=20)
        self.assertEqual(sorted(row["id"] for row in history), [1, 2, 3, 10, 31, 228])
        self.assertEqual(ms.count_scan_results(), 6)

    def test_missing_provenance_fails_closed_for_aggregates_only(self) -> None:
        with ms.connect() as conn:
            run_id = int(
                conn.execute(
                    """
                    INSERT INTO scan_runs (timestamp, universe_mode, pick_mode)
                    VALUES ('2026-06-01T00:00:00Z', 'custom', 'score_only')
                    """
                ).lastrowid
            )
            conn.execute(
                """
                INSERT INTO scan_results (
                    run_id, timestamp, ticker, scout_score, final_direction, stock_outcome_label
                ) VALUES (?, '2026-06-01T00:00:00Z', 'NVDA', 80, 'Bullish', 'WIN')
                """,
                (run_id,),
            )
            conn.commit()
        self.assertEqual(ms.count_scan_results(), 1)
        with self.assertRaises(ResearchPopulationError):
            ms.get_outcome_analytics()
        with self.assertRaises(ResearchPopulationError):
            ms.get_gate_statistics()

    def test_incomplete_provenance_fails_closed(self) -> None:
        self._seed_mixed_population()
        with ms.connect() as conn:
            conn.execute(
                """
                INSERT INTO scan_results (
                    id, run_id, timestamp, ticker, scout_score, final_direction, stock_outcome_label
                ) VALUES (99, 1, '2026-06-02T00:00:00Z', 'QQQ', 10, 'Neutral', 'FLAT')
                """
            )
            conn.commit()
        with self.assertRaises(ResearchPopulationError) as caught:
            ms.get_direction_accuracy()
        self.assertIn("scan result lacks provenance", str(caught.exception))

    def test_production_save_records_capture_provenance(self) -> None:
        payload = {
            "runTimestamp": "2026-06-02T12:00:00+00:00",
            "universeMode": "preset",
            "pickMode": "score_only",
            "timeout": 25,
            "apiUrl": "https://example.test/gates",
            "candidates": ["AAPL"],
            "universePresetId": "healthcare",
            "scanPurpose": "cohort_baseline",
            "cohortClass": "actionable",
            "results": [
                {
                    "ticker": "AAPL",
                    "score": 70,
                    "direction": "Bullish",
                    "passedAllGates": True,
                    "gates": [
                        {
                            "key": "sentinel",
                            "code": "SENTINEL",
                            "name": "Market Filter",
                            "passed": True,
                        }
                    ],
                    "directionBreakdown": {
                        "direction": "Bullish",
                        "bullConviction": 60,
                        "bearConviction": 40,
                        "netDirectionalEdge": 20,
                    },
                    "raw": {"ticker": "AAPL", "direction": "Bullish"},
                }
            ],
        }
        run_id = ms.save_scan_result(payload)
        self.assertGreater(run_id, 0)
        with ms.connect() as conn:
            saved = conn.execute("SELECT COUNT(*) FROM scan_results").fetchone()[0]
            provenance = conn.execute(
                """
                SELECT origin, research_eligible, parent_observation_uid, classifier_version
                FROM observation_provenance
                """
            ).fetchone()
        self.assertEqual(saved, 1)
        self.assertEqual(provenance["origin"], "production")
        self.assertEqual(provenance["research_eligible"], 1)
        self.assertIsNone(provenance["parent_observation_uid"])
        self.assertEqual(provenance["classifier_version"], "capture-v1")
        self.assertEqual(ms.query_scan_results(limit=5)[0]["ticker"], "AAPL")
        self.assertIsInstance(ms.get_outcome_analytics(), dict)


if __name__ == "__main__":
    unittest.main()
