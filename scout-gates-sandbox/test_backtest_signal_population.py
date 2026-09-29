#!/usr/bin/env python3
"""Live backtest population tests. These never open scout_memory.db."""

from __future__ import annotations

import sqlite3
import unittest

from backtest_engine import (
    BacktestFilters,
    fetch_backtest_signals,
    fetch_signals_for_backtest_run,
)
from observation_evidence import ResearchPopulationError


SCHEMA = """
CREATE TABLE scan_runs (
    id INTEGER PRIMARY KEY,
    universe_preset_id TEXT,
    cohort_class TEXT,
    scan_purpose TEXT
);

CREATE TABLE scan_results (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    timestamp TEXT,
    ticker TEXT,
    scout_score REAL,
    final_direction TEXT,
    stock_outcome_label TEXT,
    return_1d REAL,
    return_3d REAL,
    return_5d REAL,
    return_10d REAL,
    return_20d REAL,
    gate_snapshot_json TEXT,
    gates_json TEXT,
    feature_vector_json TEXT,
    universe_preset_id TEXT,
    cohort_class TEXT,
    scan_purpose TEXT,
    is_test_record INTEGER
);

CREATE TABLE feature_vectors (
    recommendation_id INTEGER,
    sector_name TEXT
);

CREATE TABLE observation_provenance (
    observation_uid TEXT PRIMARY KEY,
    scan_result_id INTEGER NOT NULL,
    origin TEXT NOT NULL,
    record_class TEXT NOT NULL DEFAULT 'raw',
    parent_observation_uid TEXT,
    research_eligible INTEGER,
    classification_reason TEXT NOT NULL DEFAULT 'test',
    classified_at TEXT NOT NULL DEFAULT '2026-09-28T00:00:00Z',
    classifier_version TEXT
);

CREATE TABLE backtest_signals (
    id INTEGER PRIMARY KEY,
    run_id INTEGER NOT NULL,
    recommendation_id INTEGER NOT NULL,
    sector TEXT
);
"""


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        """
        INSERT INTO scan_runs (id, universe_preset_id, cohort_class, scan_purpose)
        VALUES (1, 'core', 'actionable', 'research')
        """
    )
    return conn


def add_observation(
    conn: sqlite3.Connection,
    scan_id: int,
    *,
    origin: str = "production",
    research_eligible: int = 1,
    parent: str | None = None,
    direction: str = "Bullish",
    outcome: str = "WIN",
    score: float = 80.0,
    ticker: str = "AAPL",
    is_test_record: int = 0,
    classifier_version: str = "b0-2026-09-28",
    timestamp: str = "2026-06-01T00:00:00Z",
) -> None:
    conn.execute(
        """
        INSERT INTO scan_results (
            id, run_id, timestamp, ticker, scout_score, final_direction,
            stock_outcome_label, return_20d, is_test_record
        ) VALUES (?, 1, ?, ?, ?, ?, ?, 5.0, ?)
        """,
        (scan_id, timestamp, ticker, score, direction, outcome, is_test_record),
    )
    conn.execute(
        """
        INSERT INTO observation_provenance (
            observation_uid, scan_result_id, origin, parent_observation_uid,
            research_eligible, classifier_version
        ) VALUES (?, ?, ?, ?, ?, ?)
        """,
        (f"sr:{scan_id}", scan_id, origin, parent, research_eligible, classifier_version),
    )


class LiveBacktestPopulationTests(unittest.TestCase):
    def test_live_selector_returns_only_eligible_signals(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="production", research_eligible=1, direction="Bullish", outcome="WIN")
        add_observation(
            conn,
            10,
            origin="synthetic",
            research_eligible=0,
            parent="sr:1",
            direction="Bearish",
            outcome="LOSS",
            ticker="AMD",
            is_test_record=0,
        )
        add_observation(
            conn,
            31,
            origin="test",
            research_eligible=0,
            parent="sr:1",
            direction="Bearish",
            outcome="WIN",
            is_test_record=1,
        )
        add_observation(conn, 228, origin="fixture", research_eligible=0, direction="Bullish", outcome="PENDING")

        signals = fetch_backtest_signals(conn, BacktestFilters())
        self.assertEqual([signal["recommendation_id"] for signal in signals], [1])

    def test_existing_filters_still_apply_on_top_of_eligibility(self) -> None:
        conn = connect()
        add_observation(conn, 1, direction="Bullish", outcome="WIN", score=90, ticker="NVDA")
        add_observation(conn, 2, direction="Bearish", outcome="LOSS", score=40, ticker="AMD")
        add_observation(
            conn,
            10,
            origin="synthetic",
            research_eligible=0,
            parent="sr:1",
            direction="Bullish",
            outcome="WIN",
            score=95,
            ticker="NVDA",
            is_test_record=0,
        )

        signals = fetch_backtest_signals(
            conn,
            BacktestFilters(directions=("Bullish",), min_score=80, tickers=("NVDA",)),
        )
        self.assertEqual([signal["recommendation_id"] for signal in signals], [1])

    def test_historical_replay_keeps_ineligible_saved_membership(self) -> None:
        conn = connect()
        add_observation(conn, 1, direction="Bullish", outcome="WIN")
        add_observation(
            conn,
            10,
            origin="synthetic",
            research_eligible=0,
            parent="sr:1",
            direction="Bearish",
            outcome="LOSS",
            is_test_record=0,
        )
        conn.execute(
            "INSERT INTO backtest_signals (run_id, recommendation_id, sector) VALUES (5, 1, 'Tech'), (5, 10, 'Tech')"
        )

        replay = fetch_signals_for_backtest_run(conn, 5)
        self.assertEqual([signal["recommendation_id"] for signal in replay], [1, 10])

        conn.execute("DROP TABLE observation_provenance")
        replay_without_provenance = fetch_signals_for_backtest_run(conn, 5)
        self.assertEqual(
            [signal["recommendation_id"] for signal in replay_without_provenance],
            [1, 10],
        )

    def test_missing_provenance_table_fails_closed(self) -> None:
        conn = connect()
        conn.execute("DROP TABLE observation_provenance")
        conn.execute(
            """
            INSERT INTO scan_results (
                id, run_id, timestamp, ticker, scout_score, final_direction,
                stock_outcome_label, return_20d, is_test_record
            ) VALUES (1, 1, '2026-06-01T00:00:00Z', 'AAPL', 80, 'Bullish', 'WIN', 5, 0)
            """
        )
        with self.assertRaises(ResearchPopulationError) as caught:
            fetch_backtest_signals(conn, BacktestFilters())
        self.assertIn("observation_provenance table is missing", str(caught.exception))
        self.assertIn("Refusing to fall back", str(caught.exception))

    def test_incomplete_provenance_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1)
        conn.execute(
            """
            INSERT INTO scan_results (
                id, run_id, timestamp, ticker, scout_score, final_direction,
                stock_outcome_label, return_20d, is_test_record
            ) VALUES (2, 1, '2026-06-02T00:00:00Z', 'MSFT', 70, 'Bearish', 'LOSS', -3, 0)
            """
        )
        with self.assertRaises(ResearchPopulationError) as caught:
            fetch_backtest_signals(conn, BacktestFilters())
        self.assertIn("scan result lacks provenance", str(caught.exception))

    def test_invalid_provenance_population_fails_closed(self) -> None:
        conn = connect()
        add_observation(conn, 1, origin="fixture", research_eligible=1)
        with self.assertRaises(ResearchPopulationError) as caught:
            fetch_backtest_signals(conn, BacktestFilters())
        self.assertIn("eligible non-production", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
