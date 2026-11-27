#!/usr/bin/env python3
"""Read-only scoring intelligence. Temporary databases, plus one canonical read."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
import unittest
import urllib.error
import urllib.request
from collections import Counter
from http.server import ThreadingHTTPServer
from pathlib import Path

from dashboard import DashboardHandler
from memory_store import RESEARCH_DB_PATH_ENV
from migrate_derived_builds import CANONICAL_DB, CREATE_INDEX_SQL, CREATE_TABLE_SQL, sha256_file
from migrate_scoring_decompositions import apply_schema
from scoring_decomposition_store import publish_scoring_decompositions
from scoring_intelligence_read_model import (
    ScoringReadError,
    build_scoring_intelligence_summary,
    get_scoring_observation,
    list_ambiguous_scoring_observations,
    list_scoring_observations,
)
from scoring_provenance import decompose_single_scan
from test_scoring_decomposition_publication import add_observation
from test_scoring_provenance import payload


BACKUP_DB = CANONICAL_DB.parent / "scout_memory_pre_scoring_e2_20261126T225551Z.db"
EXPECTED_DB_SHA256 = "9024d966956afb13a2aa9b8942001be2494ad6437f348f587dca26cd970be2b4"
EXPECTED_BACKUP_SHA256 = "8dfb129ddd674a0b76e8c12a12c7a0b50f8899824f6392ef53ddbf251814396c"
EXPECTED_BUILD = "scoring-scoring-decomp-1-20261126T232822Z-ce315ae3"


def _file_db(directory: Path) -> tuple[Path, sqlite3.Connection]:
    path = directory / "research.db"
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY,
            raw_result_json TEXT,
            scout_score INTEGER,
            engine_version TEXT,
            ticker TEXT,
            timestamp TEXT,
            final_direction TEXT
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
    apply_schema(conn)
    return path, conn


def _add(conn: sqlite3.Connection, scan_id: int, body: dict, ticker: str) -> str:
    uid = add_observation(conn, scan_id, body)
    conn.execute(
        """
        UPDATE scan_results
        SET ticker = ?, timestamp = ?, final_direction = ?
        WHERE id = ?
        """,
        (ticker, f"2026-09-{scan_id:02d}T00:00:00Z", "Neutral", scan_id),
    )
    return uid


def _publish(conn: sqlite3.Connection, rows: list[tuple[int, dict, str]]) -> None:
    for scan_id, body, ticker in rows:
        _add(conn, scan_id, body, ticker)
    publish_scoring_decompositions(conn)


class ScoringIntelligenceReadTests(unittest.TestCase):
    def test_published_summary_separates_residuals_and_is_current(self) -> None:
        with self._temporary() as (path, conn):
            _publish(
                conn,
                [
                    (1, payload(score=72), "AAA"),
                    (2, payload(clean=False, score=67), "BBB"),
                    (3, payload(score=80), "CCC"),
                ],
            )
            conn.close()
            before = sha256_file(path)
            summary = build_scoring_intelligence_summary(path)
            self.assertEqual(sha256_file(path), before)
            self.assertEqual(summary["generation_status"], "current")
            self.assertEqual(summary["counts"]["published_rows"], 3)
            classes = summary["distributions"]["reconstruction_class"]
            self.assertEqual(classes["A_evidence_determined"], 1)
            self.assertEqual(classes["B_algebraically_inferred"], 1)
            self.assertEqual(classes["E_formula_mismatch"], 1)
            self.assertEqual(summary["distributions"]["independent_residual"], {"0": 1})
            self.assertEqual(
                summary["distributions"]["mathematical_compatibility_residual"],
                {"0": 1},
            )
            self.assertEqual(summary["published"]["deployment_verification"], "unverified")
            self.assertIsNone(summary["published"]["verified_deployment_id"])

    def test_missing_schema_is_reported_without_creating_it(self) -> None:
        with self._temporary(apply=False) as (path, conn):
            conn.close()
            summary = build_scoring_intelligence_summary(path)
            self.assertEqual(summary["generation_status"], "missing_schema")
            self.assertIsNone(summary["published"])
            check = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            names = {row[0] for row in check.execute("SELECT name FROM sqlite_master")}
            check.close()
            self.assertNotIn("scoring_decompositions", names)

    def test_missing_completed_generation_is_reported(self) -> None:
        with self._temporary() as (path, conn):
            _add(conn, 1, payload(score=72), "AAA")
            conn.close()
            summary = build_scoring_intelligence_summary(path)
            self.assertEqual(summary["generation_status"], "missing_generation")
            self.assertEqual(summary["counts"]["published_rows"], 0)

    def test_changed_population_hash_is_stale_and_not_rebuilt(self) -> None:
        with self._temporary() as (path, conn):
            _publish(conn, [(1, payload(score=72), "AAA")])
            build_id = conn.execute(
                "SELECT build_id FROM derived_builds WHERE artifact_type = 'scoring_decompositions'"
            ).fetchone()[0]
            conn.execute(
                "UPDATE derived_builds SET eligible_population_hash = ? WHERE build_id = ?",
                ("b" * 64, build_id),
            )
            conn.close()
            summary = build_scoring_intelligence_summary(path)
            self.assertEqual(summary["generation_status"], "stale_population")
            check = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            stored = check.execute(
                "SELECT eligible_population_hash FROM derived_builds WHERE build_id = ?",
                (build_id,),
            ).fetchone()[0]
            check.close()
            self.assertEqual(stored, "b" * 64)

    def test_changed_scoring_input_is_stale(self) -> None:
        with self._temporary() as (path, conn):
            _publish(conn, [(1, payload(score=72), "AAA")])
            conn.execute(
                "UPDATE scan_results SET raw_result_json = ? WHERE id = 1",
                (json.dumps(payload(score=10)),),
            )
            conn.close()
            summary = build_scoring_intelligence_summary(path)
            self.assertEqual(summary["generation_status"], "stale_inputs")
            page = list_scoring_observations(path, limit=10, offset=0)
            self.assertEqual(page["observations"][0]["captured_scout_score"], 72)

    def test_individual_observation_keeps_origins_and_nulls(self) -> None:
        ambiguous = payload(
            atlas=3,
            oracle=2,
            phantom=4,
            catalyst=3,
            clean=False,
            wind=3,
            trend="UPTREND",
            direction="BEARISH",
            iv_elevated=False,
            score=100,
            runup="70.0",
        )
        with self._temporary() as (path, conn):
            _publish(
                conn,
                [
                    (1, payload(clean=False, score=67), "INF"),
                    (2, ambiguous, "AMB"),
                    (3, payload(omit_catalyst=True, score=72), "MISS"),
                    (4, payload(score=72, narrative_wind=3), "CONF"),
                ],
            )
            conn.close()
            inferred = get_scoring_observation("sr:1", path)
            self.assertEqual(inferred["reconstruction_class"], "B_algebraically_inferred")
            self.assertEqual(inferred["contributions"]["specter_penalty"]["origin"], "inferred")
            self.assertNotEqual(inferred["contributions"]["specter_penalty"]["origin"], "captured")
            self.assertEqual(inferred["reconstruction_error"], 0)
            ambiguous_row = get_scoring_observation("sr:2", path)
            self.assertIsNone(ambiguous_row["unclamped_score"])
            self.assertIsNone(ambiguous_row["clamped_score"])
            self.assertIsNone(ambiguous_row["reconstruction_error"])
            self.assertGreaterEqual(len(ambiguous_row["penalty_candidates"]), 2)
            self.assertIsNone(ambiguous_row["contributions"]["specter_penalty"]["value"])
            missing = get_scoring_observation("sr:3", path)
            self.assertEqual(missing["contributions"]["catalyst_raw"]["origin"], "missing")
            self.assertIsNone(missing["contributions"]["catalyst_raw"]["value"])
            conflict = get_scoring_observation("sr:4", path)
            self.assertEqual(conflict["contributions"]["wind_contribution"]["origin"], "conflict")
            self.assertTrue(conflict["disagreements"])
            listed = list_ambiguous_scoring_observations(path)
            self.assertEqual(len(listed["observations"]), 1)
            self.assertIsNone(listed["observations"][0]["unclamped_score"])
            self.assertEqual(listed["observations"][0]["captured_scout_score"], 100)
            summary = build_scoring_intelligence_summary(path)
            self.assertEqual(summary["counts"]["missing_or_conflicting"], 2)
            self.assertEqual(summary["counts"]["ambiguous"], 1)

    def test_filters_and_pagination_are_bounded(self) -> None:
        with self._temporary() as (path, conn):
            rows = [(index, payload(score=72), ticker) for index, ticker in enumerate(("AAA", "BBB", "CCC"), start=1)]
            rows.append((4, payload(clean=False, score=67, direction="BULLISH"), "BBB"))
            _publish(conn, rows)
            conn.close()
            first = list_scoring_observations(path, limit=2, offset=0)
            second = list_scoring_observations(path, offset=2, limit=2)
            self.assertEqual(first["total"], 4)
            self.assertEqual(len(first["observations"]), 2)
            self.assertEqual(len(second["observations"]), 2)
            self.assertEqual(
                [row["scan_result_id"] for row in first["observations"] + second["observations"]],
                [1, 2, 3, 4],
            )
            ticker = list_scoring_observations(path, ticker="bbb", limit=10, offset=0)
            self.assertEqual({row["scan_result_id"] for row in ticker["observations"]}, {2, 4})
            direction = list_scoring_observations(path, direction="bullish", limit=10, offset=0)
            self.assertEqual([row["scan_result_id"] for row in direction["observations"]], [4])
            scored = list_scoring_observations(path, score_min=70, score_max=72, limit=10, offset=0)
            self.assertEqual({row["captured_scout_score"] for row in scored["observations"]}, {72})
            classified = list_scoring_observations(
                path,
                reconstruction_class="B_algebraically_inferred",
                limit=10,
                offset=0,
            )
            self.assertEqual(len(classified["observations"]), 1)
            with self.assertRaises(ScoringReadError):
                list_scoring_observations(path, limit=51, offset=0)
            with self.assertRaises(ScoringReadError):
                list_scoring_observations(path, reconstruction_class="not-a-class", limit=10, offset=0)

    def test_invalid_observation_identifiers_are_rejected(self) -> None:
        for value in ("", "sr/1", "a\nb", "x" * 201):
            with self.subTest(value=value):
                with self.assertRaises(ScoringReadError):
                    get_scoring_observation(value, Path("/nonexistent-scoring.db"))

    def test_reads_do_not_change_a_database_file(self) -> None:
        with self._temporary() as (path, conn):
            _publish(conn, [(1, payload(score=72), "AAA")])
            conn.close()
            before = sha256_file(path)
            build_scoring_intelligence_summary(path)
            list_scoring_observations(path, limit=25, offset=0)
            get_scoring_observation("sr:1", path)
            list_ambiguous_scoring_observations(path)
            self.assertEqual(sha256_file(path), before)
            readonly = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            with self.assertRaises(sqlite3.OperationalError):
                readonly.execute("UPDATE scoring_decompositions SET captured_scout_score = 0")
            readonly.close()
            self.assertEqual(sha256_file(path), before)

    def _temporary(self, apply: bool = True):
        return _TemporaryDatabase(apply)


class _TemporaryDatabase:
    def __init__(self, apply: bool) -> None:
        self.apply = apply

    def __enter__(self):
        import tempfile
        self.directory = tempfile.TemporaryDirectory()
        root = Path(self.directory.name)
        if self.apply:
            return _file_db(root)
        path = root / "research.db"
        conn = sqlite3.connect(path, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.executescript(CREATE_TABLE_SQL + ";\n" + CREATE_INDEX_SQL + ";")
        return path, conn

    def __exit__(self, exc_type, exc, traceback) -> None:
        self.directory.cleanup()


class ScoringInterfaceTests(unittest.TestCase):
    def test_invalid_identifier_route_does_not_require_the_database(self) -> None:
        server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            url = f"http://127.0.0.1:{server.server_address[1]}/api/scoring-intelligence/observations/sr%2F1"
            with urllib.request.urlopen(url) as response:
                self.fail(f"expected rejection, got {response.status}")
        except urllib.error.HTTPError as exc:
            self.assertEqual(exc.code, 400)
            body = json.loads(exc.read().decode("utf-8"))
            self.assertIn("not valid", body["message"])
        finally:
            server.shutdown()

    def test_page_and_filtered_api_use_the_supplied_database(self) -> None:
        with _TemporaryDatabase(True) as (path, conn):
            _publish(conn, [(1, payload(score=72), "AAA"), (2, payload(score=80), "BBB")])
            conn.close()
            previous = os.environ.get(RESEARCH_DB_PATH_ENV)
            os.environ[RESEARCH_DB_PATH_ENV] = str(path)
            server = ThreadingHTTPServer(("127.0.0.1", 0), DashboardHandler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{server.server_address[1]}"
            try:
                with urllib.request.urlopen(base + "/scoring-intelligence") as response:
                    page = response.read().decode("utf-8")
                self.assertIn("Scoring", page)
                self.assertIn("does not rescore", page)
                with urllib.request.urlopen(
                    base + "/api/scoring-intelligence/observations?ticker=AAA&limit=25&offset=0"
                ) as response:
                    payload_body = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload_body["total"], 1)
                self.assertEqual(payload_body["observations"][0]["ticker"], "AAA")
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    urllib.request.urlopen(base + "/api/scoring-intelligence/observations/sr:9")
                self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown()
                if previous is None:
                    os.environ.pop(RESEARCH_DB_PATH_ENV, None)
                else:
                    os.environ[RESEARCH_DB_PATH_ENV] = previous


class CanonicalScoringReadTests(unittest.TestCase):
    def test_canonical_generation_is_unchanged(self) -> None:
        self.assertEqual(sha256_file(CANONICAL_DB), EXPECTED_DB_SHA256)
        self.assertEqual(sha256_file(BACKUP_DB), EXPECTED_BACKUP_SHA256)
        summary = build_scoring_intelligence_summary(CANONICAL_DB)
        page = list_scoring_observations(CANONICAL_DB, limit=50, offset=0)
        ambiguous = list_ambiguous_scoring_observations(CANONICAL_DB)
        self.assertEqual(sha256_file(CANONICAL_DB), EXPECTED_DB_SHA256)
        self.assertEqual(sha256_file(BACKUP_DB), EXPECTED_BACKUP_SHA256)
        self.assertEqual(summary["generation_status"], "current")
        self.assertEqual(summary["published"]["build_id"], EXPECTED_BUILD)
        self.assertEqual(summary["counts"]["published_rows"], 227)
        classes = summary["distributions"]["reconstruction_class"]
        self.assertEqual(classes["A_evidence_determined"], 151)
        self.assertEqual(classes["B_algebraically_inferred"], 69)
        self.assertEqual(classes["C_ambiguous"], 7)
        self.assertEqual(summary["distributions"]["independent_residual"], {"0": 151})
        self.assertEqual(
            summary["distributions"]["mathematical_compatibility_residual"],
            {"0": 69},
        )
        self.assertEqual(summary["counts"]["ambiguous"], 7)
        self.assertEqual(summary["counts"]["missing_or_conflicting"], 0)
        self.assertEqual(page["total"], 227)
        self.assertEqual(len(page["observations"]), 50)
        conn = sqlite3.connect(f"file:{CANONICAL_DB}?mode=ro", uri=True)
        conn.row_factory = sqlite3.Row
        examples = {
            row["scan_result_id"]: row["observation_uid"]
            for row in conn.execute(
                """
                SELECT scan_result_id, observation_uid
                FROM scoring_decompositions
                WHERE scan_result_id IN (153, 167, 210)
                """
            )
        }
        prior = {
            row["build_id"]: (row["artifact_type"], row["artifact_row_count"])
            for row in conn.execute(
                """
                SELECT build_id, artifact_type, artifact_row_count
                FROM derived_builds
                WHERE artifact_type != 'scoring_decompositions'
                """
            )
        }
        evidence_count = conn.execute("SELECT COUNT(*) FROM observation_provenance").fetchone()[0]
        conn.close()
        bsx = get_scoring_observation(examples[210], CANONICAL_DB)
        nvda = get_scoring_observation(examples[153], CANONICAL_DB)
        tsm = get_scoring_observation(examples[167], CANONICAL_DB)
        self.assertEqual(bsx["ticker"], "BSX")
        self.assertEqual(bsx["captured_scout_score"], 100)
        self.assertEqual(bsx["unclamped_score"], 101)
        self.assertEqual(bsx["clamped_score"], 100)
        self.assertEqual(bsx["reconstruction_error"], 0)
        self.assertEqual(nvda["ticker"], "NVDA")
        self.assertEqual(nvda["captured_scout_score"], 100)
        self.assertIsNone(nvda["unclamped_score"])
        self.assertEqual(len(nvda["penalty_candidates"]), 2)
        self.assertEqual(tsm["ticker"], "TSM")
        self.assertIsNone(tsm["unclamped_score"])
        self.assertEqual(len(tsm["penalty_candidates"]), 3)
        self.assertEqual(len(ambiguous["observations"]), 7)
        self.assertEqual(
            prior["pattern-pattern-1-20260929T130814Z-bd3a61ed"],
            ("pattern_intelligence", 36),
        )
        self.assertEqual(
            prior["gate-alpha-gate-alpha-1-20260929T131618Z-fb09a847"],
            ("gate_alpha_metrics", 350),
        )
        self.assertEqual(
            prior["gate-intelligence-gate-intelligence-1-20260929T131948Z-3f2573be"],
            ("gate_intelligence_metrics", 14),
        )
        self.assertEqual(evidence_count, 274)
        fresh = Counter()
        check = sqlite3.connect(f"file:{CANONICAL_DB}?mode=ro", uri=True)
        for (raw,) in check.execute(
            """
            SELECT sr.raw_result_json
            FROM scan_results AS sr
            JOIN observation_provenance AS op ON op.scan_result_id = sr.id
            WHERE op.research_eligible = 1
            """
        ):
            fresh[decompose_single_scan(json.loads(raw)).classification.value] += 1
        check.close()
        self.assertEqual(dict(fresh), classes)
        self.assertEqual(sha256_file(CANONICAL_DB), EXPECTED_DB_SHA256)


if __name__ == "__main__":
    unittest.main()
