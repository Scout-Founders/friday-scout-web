#!/usr/bin/env python3
"""Future Horizon derived-builder eligibility. Temporary databases only."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import memory_store as ms
from observation_evidence import ResearchPopulationError
from migrate_derived_builds import CREATE_INDEX_SQL, CREATE_TABLE_SQL
from pattern_engine import completed_rows, rebuild_pattern_intelligence


PROVENANCE_SQL = """
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


def add_provenance(
    conn: sqlite3.Connection,
    scan_id: int,
    *,
    origin: str,
    research_eligible: int,
    parent: str | None = None,
) -> None:
    conn.execute(
        """
        INSERT INTO observation_provenance (
            observation_uid, scan_result_id, origin, record_class,
            parent_observation_uid, research_eligible, classification_reason,
            classified_at, classifier_version
        ) VALUES (?, ?, ?, 'raw', ?, ?, 'test', '2026-09-28T00:00:00Z', 'b0-2026-09-28')
        """,
        (f"sr:{scan_id}", scan_id, origin, parent, research_eligible),
    )


class PatternBuilderTests(unittest.TestCase):
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
            """
            + PROVENANCE_SQL
        )

    def tearDown(self) -> None:
        self.conn.close()

    def _add(self, scan_id: int, *, origin: str, eligible: int, outcome: str, parent: str | None = None) -> None:
        self.conn.execute(
            """
            INSERT INTO scan_results (
                id, run_id, final_direction, stock_outcome_label, return_20d
            ) VALUES (?, 1, 'Bullish', ?, 4.0)
            """,
            (scan_id, outcome),
        )
        self.conn.execute(
            """
            INSERT INTO feature_vectors (recommendation_id, sector_name)
            VALUES (?, 'Technology')
            """,
            (scan_id,),
        )
        add_provenance(self.conn, scan_id, origin=origin, research_eligible=eligible, parent=parent)

    def test_completed_rows_keep_eligible_completed_production_only(self) -> None:
        self._add(1, origin="production", eligible=1, outcome="WIN")
        self._add(2, origin="production", eligible=1, outcome="PENDING")
        self._add(10, origin="synthetic", eligible=0, outcome="LOSS", parent="sr:1")
        self._add(228, origin="fixture", eligible=0, outcome="WIN")
        rows = completed_rows(self.conn)
        self.assertEqual([row["recommendation_id"] for row in rows], [1])

    def test_min_sample_still_excludes_ineligible_companions(self) -> None:
        for scan_id in (1, 2, 3):
            self._add(scan_id, origin="production", eligible=1, outcome="WIN")
        self._add(10, origin="synthetic", eligible=0, outcome="LOSS", parent="sr:1")
        self._add(11, origin="synthetic", eligible=0, outcome="LOSS", parent="sr:1")
        self.conn.executescript(CREATE_TABLE_SQL + ";\n" + CREATE_INDEX_SQL + ";")
        result = rebuild_pattern_intelligence(self.conn)
        self.assertEqual(result["completed_outcomes"], 3)
        stored = self.conn.execute(
            """
            SELECT sample_size FROM pattern_intelligence
            WHERE pattern_signature LIKE '%direction_sector%'
            """
        ).fetchone()
        self.assertEqual(stored["sample_size"], 3)

    def test_missing_provenance_fails_closed(self) -> None:
        self.conn.execute("DROP TABLE observation_provenance")
        self.conn.execute(
            """
            INSERT INTO scan_results (id, run_id, final_direction, stock_outcome_label)
            VALUES (1, 1, 'Bullish', 'WIN')
            """
        )
        with self.assertRaises(ResearchPopulationError) as caught:
            completed_rows(self.conn)
        self.assertIn("observation_provenance table is missing", str(caught.exception))

    def test_invalid_eligible_fixture_fails_closed(self) -> None:
        self._add(228, origin="fixture", eligible=1, outcome="WIN")
        with self.assertRaises(ResearchPopulationError) as caught:
            completed_rows(self.conn)
        self.assertIn("eligible non-production", str(caught.exception))


class HorizonBuilderTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "horizon.db"
        self._patchers = [
            patch.object(ms, "DB_PATH", self._db_path),
            patch.object(ms, "_DB_INITIALIZED", False),
        ]
        for patcher in self._patchers:
            patcher.start()
        ms.init_db()
        with ms.connect() as conn:
            conn.executescript(PROVENANCE_SQL)
            conn.execute(
                """
                INSERT INTO scan_runs (timestamp, universe_mode, pick_mode)
                VALUES ('2026-06-01T00:00:00Z', 'custom', 'score_only')
                """
            )
            conn.commit()

    def tearDown(self) -> None:
        for patcher in self._patchers:
            patcher.stop()
        self._tmpdir.cleanup()

    def _result(self, scan_id: int, ticker: str, outcome: str, return_20d: float, *, run_id: int = 1) -> None:
        with ms.connect() as conn:
            conn.execute(
                """
                INSERT INTO scan_results (
                    id, run_id, timestamp, ticker, final_direction, stock_outcome_label,
                    return_20d, gate_snapshot_json, raw_result_json
                ) VALUES (?, ?, '2026-06-01T00:00:00Z', ?, 'Bullish', ?, ?, ?, '{}')
                """,
                (
                    scan_id,
                    run_id,
                    ticker,
                    outcome,
                    return_20d,
                    ms.json_dump({"gates": [{"key": ticker, "name": ticker, "passed": True}]}),
                ),
            )
            conn.commit()

    def test_gate_alpha_future_input_excludes_ineligible_rows(self) -> None:
        self._result(1, "AAA", "WIN", 5.0)
        self._result(10, "BBB", "LOSS", -4.0)
        with ms.connect() as conn:
            add_provenance(conn, 1, origin="production", research_eligible=1)
            add_provenance(conn, 10, origin="synthetic", research_eligible=0, parent="sr:1")
            for scan_id, ticker in ((1, "AAA"), (10, "BBB")):
                conn.execute(
                    """
                    INSERT INTO gate_attributions (
                        scan_id, ticker, gate_name, gate_weight, contribution_pct,
                        gate_rank, created_at_utc
                    ) VALUES (1, ?, ?, 1, 100, 1, '2026-06-01T00:00:00Z')
                    """,
                    (ticker, ticker),
                )
            conn.executescript(CREATE_TABLE_SQL + ";\n" + CREATE_INDEX_SQL + ";")
            conn.commit()
            summary = ms.refresh_gate_alpha_metrics(conn)
            names = {
                row["gate_name"]
                for row in conn.execute("SELECT gate_name FROM gate_alpha_metrics")
            }
        self.assertEqual(summary["completed_outcomes_checked"], 1)
        self.assertIn("AAA", names)
        self.assertNotIn("BBB", names)

    def test_gate_alpha_missing_provenance_fails_closed(self) -> None:
        self._result(1, "AAA", "WIN", 5.0)
        with ms.connect() as conn:
            conn.execute("DROP TABLE observation_provenance")
            with self.assertRaises(ResearchPopulationError):
                ms.refresh_gate_alpha_metrics(conn)

    def test_gate_intelligence_excludes_unflagged_synthetic(self) -> None:
        self._result(1, "SENTINEL", "WIN", 5.0)
        self._result(10, "SYNTHETIC_GATE", "LOSS", -4.0)
        with ms.connect() as conn:
            add_provenance(conn, 1, origin="production", research_eligible=1)
            add_provenance(conn, 10, origin="synthetic", research_eligible=0, parent="sr:1")
            conn.execute("UPDATE scan_results SET is_test_record = 0 WHERE id = 10")
            conn.executescript(CREATE_TABLE_SQL + ";\n" + CREATE_INDEX_SQL + ";")
            conn.commit()
            metrics = ms.refresh_gate_intelligence_metrics(conn)
        keys = {row["gate_key"] for row in metrics}
        self.assertIn("SENTINEL", keys)
        self.assertNotIn("SYNTHETIC_GATE", keys)
        sentinel = next(row for row in metrics if row["gate_key"] == "SENTINEL")
        self.assertEqual(sentinel["total_occurrences"], 1)
        self.assertEqual(sentinel["win_count"], 1)

    def test_regime_summary_uses_eligible_scan_results(self) -> None:
        self._result(1, "AAA", "WIN", 5.0)
        with ms.connect() as conn:
            conn.execute(
                """
                INSERT INTO scan_runs (timestamp, universe_mode, pick_mode)
                VALUES ('2026-06-02T00:00:00Z', 'custom', 'score_only')
                """
            )
            conn.execute(
                """
                INSERT INTO scan_results (
                    id, run_id, timestamp, ticker, final_direction, stock_outcome_label, return_20d
                ) VALUES (228, 2, '2026-06-02T00:00:00Z', 'BBB', 'Bullish', 'LOSS', -9)
                """
            )
            add_provenance(conn, 1, origin="production", research_eligible=1)
            add_provenance(conn, 228, origin="fixture", research_eligible=0)
            for scan_id, ticker, trend in ((1, "AAA", "bullish"), (2, "BBB", "bearish")):
                conn.execute(
                    """
                    INSERT INTO regime_snapshots (
                        scan_id, ticker, market_trend, volatility_regime, liquidity_regime,
                        earnings_proximity, macro_bias, timestamp
                    ) VALUES (?, ?, ?, 'normal', 'normal', 'none', 'neutral', '2026-06-01T00:00:00Z')
                    """,
                    (scan_id, ticker, trend),
                )
            conn.commit()
            summary = ms.get_regime_intelligence_summary(conn)
        self.assertEqual(summary["completed_regime_samples"], 1)
        self.assertEqual(summary["total_regime_snapshots"], 2)
        self.assertEqual(summary["strongest_bullish_regime"]["sample_size"], 1)
        self.assertIsNone(summary["strongest_bearish_regime"])

    def test_regime_missing_provenance_fails_closed(self) -> None:
        self._result(1, "AAA", "WIN", 5.0)
        with ms.connect() as conn:
            conn.execute("DROP TABLE observation_provenance")
            with self.assertRaises(ResearchPopulationError):
                ms.get_regime_intelligence_summary(conn)


if __name__ == "__main__":
    unittest.main()
