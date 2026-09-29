#!/usr/bin/env python3
"""Overview read-model and route tests. Temporary databases only."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import unittest
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import ThreadingHTTPServer
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from dashboard import DashboardHandler
from memory_store import RESEARCH_DB_PATH_ENV
from overview_read_model import ATTENTION_CODES, build_overview_snapshot


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _tables(path: Path) -> set[str]:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
        }
    finally:
        conn.close()


def _recent_timestamp() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _provenance(conn: sqlite3.Connection, scan_id: int) -> None:
    conn.execute(
        """
        INSERT INTO observation_provenance (
            observation_uid, scan_result_id, origin, record_class,
            parent_observation_uid, research_eligible, classification_reason,
            classified_at, classifier_version
        ) VALUES (?, ?, 'production', 'raw', NULL, 1, 'test', '2026-09-28T00:00:00Z', 'b0-2026-09-28')
        """,
        (f"sr:{scan_id}", scan_id),
    )


def _count_fixture(path: Path) -> None:
    timestamp = _recent_timestamp()
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE scan_runs (id INTEGER PRIMARY KEY, timestamp TEXT);
        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY,
            run_id INTEGER,
            timestamp TEXT,
            ticker TEXT,
            final_direction TEXT,
            stock_outcome_label TEXT,
            return_1d REAL,
            return_3d REAL,
            return_5d REAL,
            return_10d REAL,
            return_20d REAL,
            outcome_last_updated_at TEXT
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
        CREATE TABLE pattern_intelligence (
            pattern_id TEXT,
            pattern_signature TEXT,
            sample_size INTEGER,
            win_rate REAL,
            expectancy_score REAL
        );
        CREATE TABLE gate_alpha_metrics (
            gate_name TEXT,
            sector TEXT,
            market_regime TEXT,
            volatility_regime TEXT,
            sample_count INTEGER,
            wins INTEGER,
            losses INTEGER,
            win_rate REAL,
            avg_return REAL,
            expectancy REAL,
            confidence_score REAL
        );
        CREATE TABLE gate_intelligence_metrics (
            gate_name TEXT,
            win_rate REAL,
            predictive_score REAL,
            win_count INTEGER,
            loss_count INTEGER,
            total_passes INTEGER
        );
        CREATE TABLE derived_builds (
            build_id TEXT,
            artifact_type TEXT,
            status TEXT,
            started_at TEXT,
            built_at TEXT,
            artifact_row_count INTEGER
        );
        CREATE TABLE backtest_runs (
            id INTEGER PRIMARY KEY,
            name TEXT,
            status TEXT,
            created_at TEXT
        );
        CREATE TABLE backtest_metrics (
            run_id INTEGER,
            win_rate REAL,
            avg_return REAL
        );
        CREATE TABLE research_jobs (id INTEGER PRIMARY KEY, enabled INTEGER);
        CREATE TABLE research_job_runs (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE research_findings (
            id INTEGER PRIMARY KEY,
            title TEXT,
            severity TEXT,
            status TEXT
        );
        CREATE TABLE rule_candidates (id INTEGER PRIMARY KEY, status TEXT);
        CREATE TABLE rule_validations (id INTEGER PRIMARY KEY, status TEXT);
        """
    )
    conn.execute("INSERT INTO scan_runs (id, timestamp) VALUES (7, ?)", (timestamp,))
    conn.execute(
        """
        INSERT INTO scan_results (
            id, run_id, timestamp, ticker, final_direction, stock_outcome_label,
            return_20d, outcome_last_updated_at
        ) VALUES (1, 7, ?, 'AAA', 'Bullish', 'WIN', 1.5, ?)
        """,
        (timestamp, timestamp),
    )
    conn.execute(
        """
        INSERT INTO scan_results (
            id, run_id, timestamp, ticker, final_direction, stock_outcome_label,
            outcome_last_updated_at
        ) VALUES (2, 7, ?, 'BBB', 'Neutral', 'PENDING', ?)
        """,
        (timestamp, timestamp),
    )
    _provenance(conn, 1)
    _provenance(conn, 2)
    conn.execute(
        """
        INSERT INTO pattern_intelligence (
            pattern_id, pattern_signature, sample_size, win_rate, expectancy_score
        ) VALUES
            ('bull-high', '{"direction":"Bullish","sector":"TECH"}', 10, 70, 2.0),
            ('bull-low', '{"direction":"Bullish","sector":"ENERGY"}', 4, 40, 0.2),
            ('bear', '{"direction":"Bearish","sector":"FIN"}', 6, 55, 0.4)
        """
    )
    conn.execute(
        """
        INSERT INTO gate_alpha_metrics (
            gate_name, sector, market_regime, volatility_regime, sample_count,
            wins, losses, win_rate, avg_return, expectancy, confidence_score
        ) VALUES ('CATALYST', 'GLOBAL', 'GLOBAL', 'GLOBAL', 5, 4, 1, 80, 1.2, 2.5, 60)
        """
    )
    conn.execute(
        """
        INSERT INTO gate_intelligence_metrics (
            gate_name, win_rate, predictive_score, win_count, loss_count, total_passes
        ) VALUES ('SPECTER', 75, 10.25, 3, 1, 4)
        """
    )
    conn.execute(
        """
        INSERT INTO derived_builds (
            build_id, artifact_type, status, started_at, built_at, artifact_row_count
        ) VALUES (
            'pattern-pattern-1-test', 'pattern_intelligence', 'COMPLETED',
            '2026-09-29T13:08:14Z', '2026-09-29T13:08:14Z', 3
        )
        """
    )
    conn.execute(
        """
        INSERT INTO backtest_runs (id, name, status, created_at)
        VALUES (4, 'Fixture', 'completed', '2026-09-26T00:00:00Z')
        """
    )
    conn.execute("INSERT INTO research_jobs (id, enabled) VALUES (1, 1)")
    conn.execute("INSERT INTO research_job_runs (id, status) VALUES (1, 'completed')")
    conn.execute(
        """
        INSERT INTO research_findings (id, title, severity, status) VALUES
            (1, 'Bearish signals underperforming', 'warning', 'open'),
            (2, 'Informational note', 'info', 'open')
        """
    )
    conn.execute("INSERT INTO rule_candidates (id, status) VALUES (1, 'testing')")
    conn.execute("INSERT INTO rule_validations (id, status) VALUES (1, 'completed')")
    conn.commit()
    conn.close()


class OverviewReadModelTests(unittest.TestCase):
    def test_contract_counts_and_unchanged_database(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "overview.db"
            _count_fixture(path)
            before = _hash(path)
            before_tables = _tables(path)
            with (
                patch("memory_store.init_db") as init_db,
                patch("memory_store.rebuild_patterns") as rebuild_patterns,
                patch("memory_store.rebuild_gate_alpha") as rebuild_gate_alpha,
                patch("memory_store.rebuild_gate_intelligence") as rebuild_gate_intelligence,
                patch("memory_store.run_horizon_backfill") as backfill,
            ):
                payload = build_overview_snapshot(path, fmp_key_present=True)
                init_db.assert_not_called()
                rebuild_patterns.assert_not_called()
                rebuild_gate_alpha.assert_not_called()
                rebuild_gate_intelligence.assert_not_called()
                backfill.assert_not_called()

            self.assertEqual(_hash(path), before)
            self.assertEqual(_tables(path), before_tables)
            self.assertTrue(payload["ok"])
            self.assertEqual(
                set(payload),
                {
                    "ok",
                    "generatedAt",
                    "system",
                    "live",
                    "horizon",
                    "memory",
                    "backtests",
                    "experiments",
                    "attention",
                },
            )
            encoded = json.dumps(payload)
            self.assertNotIn("strongestSignal", encoded)
            self.assertNotIn("strongest_signal", encoded)
            self.assertEqual(payload["live"]["scanRunId"], 7)
            self.assertEqual(payload["live"]["tickerCount"], 2)
            self.assertEqual(
                payload["live"]["directionCounts"],
                {"Bullish": 1, "Bearish": 0, "Neutral": 1},
            )
            self.assertEqual(payload["live"]["href"], "/scanner")
            self.assertEqual(payload["memory"]["eligibleObservations"], 2)
            self.assertEqual(payload["memory"]["eligibleCompleted"], 1)
            self.assertEqual(payload["memory"]["eligiblePending"], 1)
            self.assertEqual(payload["memory"]["actionableCompleted"], 1)
            self.assertEqual(payload["horizon"]["patternCount"], 3)
            self.assertEqual(payload["horizon"]["gateAlphaSegmentCount"], 1)
            self.assertEqual(payload["horizon"]["gateIntelligenceGateCount"], 1)
            leaders = payload["horizon"]["existingLeaders"]
            self.assertEqual(leaders["strongestBullishPattern"]["pattern_id"], "bull-high")
            self.assertEqual(leaders["strongestBearishPattern"]["pattern_id"], "bear")
            self.assertEqual(leaders["topGlobalGateAlpha"]["gate_name"], "CATALYST")
            self.assertEqual(leaders["mostPredictiveGate"]["gate"], "SPECTER")
            self.assertEqual(payload["backtests"]["savedRunCount"], 1)
            self.assertIsNone(payload["backtests"]["latest"]["winRate"])
            self.assertIsNone(payload["backtests"]["latest"]["avgReturn"])
            self.assertEqual(payload["system"]["derivedBuilds"][0]["buildId"], "pattern-pattern-1-test")
            self.assertEqual(payload["system"]["derivedBuilds"][0]["rowCount"], 3)
            self.assertEqual(payload["experiments"]["enabledJobs"], 1)
            self.assertEqual(payload["experiments"]["runStatusCounts"], {"completed": 1})
            self.assertEqual(payload["experiments"]["openFindings"], 2)
            self.assertEqual(payload["experiments"]["openFindingsBySeverity"]["warning"], 1)
            self.assertEqual(payload["experiments"]["candidatesByStatus"], {"testing": 1})
            self.assertEqual(payload["experiments"]["validationsByStatus"], {"completed": 1})
            codes = {item["code"] for item in payload["attention"]}
            self.assertTrue(codes <= ATTENTION_CODES)
            self.assertIn("research_finding", codes)
            self.assertNotIn("missing_fmp_key", codes)

    def test_attention_uses_explicit_states_only(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "attention.db"
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE scan_runs (id INTEGER PRIMARY KEY, timestamp TEXT);
                CREATE TABLE scan_results (
                    id INTEGER PRIMARY KEY,
                    run_id INTEGER,
                    ticker TEXT,
                    timestamp TEXT,
                    final_direction TEXT,
                    stock_outcome_label TEXT,
                    return_1d REAL,
                    return_3d REAL,
                    return_5d REAL,
                    return_10d REAL,
                    return_20d REAL,
                    entry_price REAL,
                    price_after_1d REAL,
                    price_after_3d REAL,
                    price_after_5d REAL,
                    price_after_10d REAL,
                    price_after_20d REAL,
                    outcome_last_updated_at TEXT
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
                CREATE TABLE feature_vectors (
                    recommendation_id INTEGER,
                    scan_id INTEGER,
                    ticker TEXT,
                    timestamp TEXT,
                    sector_name TEXT,
                    relative_volume REAL,
                    volume_spike_percent REAL,
                    options_volume_score REAL
                );
                CREATE TABLE derived_builds (
                    build_id TEXT,
                    artifact_type TEXT,
                    status TEXT,
                    started_at TEXT,
                    built_at TEXT,
                    artifact_row_count INTEGER,
                    error_text TEXT
                );
                CREATE TABLE research_findings (
                    id INTEGER PRIMARY KEY,
                    title TEXT,
                    severity TEXT,
                    status TEXT
                );
                """
            )
            conn.execute(
                "INSERT INTO scan_runs (id, timestamp) VALUES (1, '2020-01-01T00:00:00+00:00')"
            )
            conn.execute(
                """
                INSERT INTO scan_results (
                    id, run_id, ticker, timestamp, final_direction, stock_outcome_label,
                    outcome_last_updated_at
                ) VALUES (
                    1, 1, 'AAA', '2020-01-01T00:00:00+00:00', 'Bullish', 'PENDING',
                    '2020-01-01T00:00:00+00:00'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO scan_results (
                    id, run_id, ticker, timestamp, final_direction, stock_outcome_label,
                    outcome_last_updated_at
                ) VALUES (
                    2, 1, 'BBB', '2020-01-01T00:00:00+00:00', 'Bearish', 'WIN',
                    '2020-01-01T00:00:00+00:00'
                )
                """
            )
            _provenance(conn, 1)
            _provenance(conn, 2)
            conn.execute(
                """
                INSERT INTO feature_vectors (
                    recommendation_id, scan_id, ticker, timestamp, sector_name,
                    relative_volume, volume_spike_percent, options_volume_score
                ) VALUES (1, 1, 'AAA', '2020-01-01T00:00:00+00:00', NULL, 1, 1, 1)
                """
            )
            conn.execute(
                """
                INSERT INTO derived_builds (
                    build_id, artifact_type, status, started_at, built_at,
                    artifact_row_count, error_text
                ) VALUES (
                    'gate-alpha-failed', 'gate_alpha_metrics', 'FAILED',
                    '2026-09-29T00:00:00Z', NULL, NULL, 'stopped'
                )
                """
            )
            conn.execute(
                """
                INSERT INTO research_findings (id, title, severity, status)
                VALUES (8, 'Bearish signals underperforming', 'warning', 'open')
                """
            )
            conn.commit()
            conn.close()

            before = _hash(path)
            payload = build_overview_snapshot(path, fmp_key_present=False)
            self.assertEqual(_hash(path), before)
            codes = {item["code"] for item in payload["attention"]}
            self.assertTrue(codes <= ATTENTION_CODES)
            self.assertIn("derived_build_status", codes)
            self.assertIn("observation_freshness", codes)
            self.assertIn("outcome_freshness", codes)
            self.assertIn("stale_pending_outcomes", codes)
            self.assertIn("missing_feature_vectors", codes)
            self.assertIn("anomaly_monitor", codes)
            self.assertIn("research_finding", codes)
            self.assertIn("missing_fmp_key", codes)
            self.assertNotIn("strongestSignal", json.dumps(payload))

    def test_missing_table_stays_missing(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "partial.db"
            conn = sqlite3.connect(path)
            conn.execute("CREATE TABLE scan_results (id INTEGER PRIMARY KEY)")
            conn.commit()
            conn.close()
            before = _hash(path)
            payload = build_overview_snapshot(path, fmp_key_present=True)
            self.assertEqual(_hash(path), before)
            self.assertNotIn("pattern_intelligence", _tables(path))
            self.assertNotIn("derived_builds", _tables(path))
            self.assertIsNone(payload["horizon"]["patternCount"])
            self.assertIsNone(payload["system"]["derivedBuilds"])
            self.assertIsNone(payload["memory"]["eligibleObservations"])
            self.assertTrue({item["code"] for item in payload["attention"]} <= ATTENTION_CODES)

    def test_routes_and_get_overview_do_not_write(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "http.db"
            _count_fixture(path)
            before = _hash(path)
            previous = os.environ.get(RESEARCH_DB_PATH_ENV)
            os.environ[RESEARCH_DB_PATH_ENV] = str(path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
            port = server.server_address[1]
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                overview = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode("utf-8")
                scanner = urllib.request.urlopen(f"http://127.0.0.1:{port}/scanner").read().decode("utf-8")
                alias = urllib.request.urlopen(f"http://127.0.0.1:{port}/dashboard.html").read().decode("utf-8")
                api = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/overview").read().decode("utf-8"))
            finally:
                server.shutdown()
                thread.join(timeout=3)
                server.server_close()
                if previous is None:
                    os.environ.pop(RESEARCH_DB_PATH_ENV, None)
                else:
                    os.environ[RESEARCH_DB_PATH_ENV] = previous
            self.assertIn("<h1>", overview)
            self.assertIn("HORIZON-1", overview)
            self.assertIn("Scout Intelligence System", overview)
            self.assertIn("Live Scanner", scanner)
            self.assertIn('id="run-form"', scanner)
            self.assertIn("Live Scanner", alias)
            self.assertIn('id="run-form"', alias)
            self.assertNotIn('id="run-form"', overview)
            self.assertEqual(api["memory"]["eligibleObservations"], 2)
            self.assertEqual(_hash(path), before)


if __name__ == "__main__":
    unittest.main()
