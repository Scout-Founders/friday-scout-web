#!/usr/bin/env python3
"""Scoring decomposition schema and publication. Temporary databases only.

The canonical scout_memory.db is opened read-only, and only to copy it or to
prove that --apply refuses it. No test migrates or publishes that file.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from collections import Counter
from pathlib import Path
from unittest.mock import patch

from derived_build_manifest import get_build, start_build
from migrate_derived_builds import (
    CANONICAL_DB,
    CREATE_INDEX_SQL,
    CREATE_TABLE_SQL,
    EXPECTED_ELIGIBLE_HASH,
    apply_schema as apply_manifest_schema,
    schema_status as manifest_schema_status,
    sha256_file,
)
from migrate_scoring_decompositions import (
    ARTIFACT_TYPE,
    BACKUP_TABLE,
    LIVE_TABLE,
    MigrationAbort,
    apply_schema,
    main,
    schema_status,
)
from observation_evidence import PopulationStamp, population_stamp
from scoring_decomposition_store import (
    ScoringPublicationError,
    ambiguous_observations,
    current_scoring_generation,
    evidence_origin_distribution,
    get_scoring_decomposition,
    missing_or_conflicting_inputs,
    publish_scoring_decompositions,
    reconstruction_class_distribution,
    residual_distribution,
)
from scoring_provenance import (
    DECOMPOSER_VERSION,
    INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
    SCORING_DECOMPOSITION_SCHEMA_VERSION,
    ScoringProvenanceError,
    decompose_single_scan,
)
from test_scoring_provenance import payload


INTELLIGENCE_TABLES = (
    "pattern_intelligence",
    "gate_alpha_metrics",
    "gate_intelligence_metrics",
)


def base_db() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:", isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(
        """
        CREATE TABLE scan_results (
            id INTEGER PRIMARY KEY,
            raw_result_json TEXT,
            scout_score INTEGER,
            engine_version TEXT
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
    return conn


def add_observation(
    conn: sqlite3.Connection,
    scan_id: int,
    body: dict,
    *,
    origin: str = "production",
    eligible: int = 1,
    parent: str | None = None,
    classifier_version: str = "b0-2026-09-28",
    engine_version: str | None = "3.0.4+test",
    score: int | None = None,
) -> str:
    uid = f"sr:{scan_id}"
    conn.execute(
        """
        INSERT INTO scan_results (
            id, raw_result_json, scout_score, engine_version
        ) VALUES (?, ?, ?, ?)
        """,
        (scan_id, json.dumps(body), score, engine_version),
    )
    conn.execute(
        """
        INSERT INTO observation_provenance (
            observation_uid, scan_result_id, origin, record_class,
            parent_observation_uid, research_eligible, classification_reason,
            classified_at, classifier_version
        ) VALUES (?, ?, ?, 'raw', ?, ?, 'test', '2026-09-28T00:00:00Z', ?)
        """,
        (uid, scan_id, origin, parent, eligible, classifier_version),
    )
    return uid


def table_digest(conn: sqlite3.Connection, table: str) -> str:
    hasher = hashlib.sha256()
    columns = [row[1] for row in conn.execute(f"PRAGMA table_info({table})")]
    quoted = ", ".join(columns)
    for row in conn.execute(f"SELECT {quoted} FROM {table} ORDER BY 1"):
        hasher.update(repr(tuple(row)).encode("utf-8"))
        hasher.update(b"\n")
    return hasher.hexdigest()


class SchemaTests(unittest.TestCase):
    def test_migration_is_idempotent_and_preserves_manifest_rows(self) -> None:
        conn = base_db()
        stamp = PopulationStamp(
            classifier_version="b0-2026-09-28",
            eligible_population_count=1,
            eligible_population_hash="a" * 64,
            generated_at="2026-09-28T00:00:00Z",
        )
        existing = start_build(
            conn,
            artifact_type="pattern_intelligence",
            builder_version="pattern-1",
            stamp=stamp,
        )
        self.assertEqual(schema_status(conn), "missing")
        self.assertEqual(apply_schema(conn), "applied")
        self.assertEqual(schema_status(conn), "already_applied")
        self.assertEqual(apply_schema(conn), "already_applied")
        self.assertEqual(manifest_schema_status(conn), "already_applied")
        self.assertEqual(apply_manifest_schema(conn), "already_applied")
        self.assertEqual(schema_status(conn), "already_applied")
        stored = get_build(conn, existing["build_id"])
        self.assertEqual(stored["artifact_type"], "pattern_intelligence")
        self.assertEqual(stored["status"], "STARTED")
        self.assertEqual(
            int(conn.execute(f"SELECT COUNT(*) FROM {LIVE_TABLE}").fetchone()[0]),
            0,
        )
        self.assertEqual(
            int(conn.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0]),
            0,
        )
        conn.close()

    def test_partial_schema_is_a_conflict(self) -> None:
        conn = base_db()
        conn.execute(
            f"CREATE TABLE {LIVE_TABLE} (observation_uid TEXT)"
        )
        self.assertEqual(schema_status(conn), "conflict")
        with self.assertRaises(MigrationAbort):
            apply_schema(conn)
        self.assertIsNotNone(
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'derived_builds'"
            ).fetchone()
        )
        conn.close()

    def test_apply_refuses_the_canonical_database(self) -> None:
        before = sha256_file(CANONICAL_DB)
        code = main(["--database", str(CANONICAL_DB), "--apply"])
        self.assertEqual(code, 2)
        self.assertEqual(sha256_file(CANONICAL_DB), before)

    def test_publish_command_refuses_the_canonical_database(self) -> None:
        from scoring_decomposition_store import main as publish_main

        before = sha256_file(CANONICAL_DB)
        code = publish_main(["--database", str(CANONICAL_DB), "--publish"])
        self.assertEqual(code, 2)
        self.assertEqual(sha256_file(CANONICAL_DB), before)
        self.assertEqual(publish_main(["--database", str(CANONICAL_DB)]), 0)

    def test_foreign_key_requires_a_real_observation(self) -> None:
        conn = base_db()
        apply_schema(conn)
        uid = add_observation(conn, 1, payload(score=72))
        published = publish_scoring_decompositions(conn)
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                f"""
                INSERT INTO {LIVE_TABLE} (
                    observation_uid, scan_result_id, methodology_id,
                    source_snapshot_sha256, deployment_verification,
                    historical_compatibility, decomposer_version, schema_version,
                    reconstruction_class, formula_confirmation, direction_status,
                    contributions_json, direction_candidates_json,
                    input_completeness_json, warnings_json, disagreements_json,
                    build_id, eligible_population_hash,
                    scoring_input_fingerprint, source_provenance_fingerprint
                )
                SELECT 'missing-observation', 1, methodology_id,
                       source_snapshot_sha256, deployment_verification,
                       historical_compatibility, decomposer_version, schema_version,
                       reconstruction_class, formula_confirmation, direction_status,
                       contributions_json, direction_candidates_json,
                       input_completeness_json, warnings_json, disagreements_json,
                       build_id, eligible_population_hash,
                       scoring_input_fingerprint, source_provenance_fingerprint
                FROM {LIVE_TABLE}
                WHERE observation_uid = ?
                """,
                (uid,),
            )
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "DELETE FROM observation_provenance WHERE observation_uid = ?",
                (uid,),
            )
        self.assertEqual(published["row_count"], 1)
        conn.close()


class PublicationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = base_db()
        apply_schema(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def _population(self) -> None:
        add_observation(self.conn, 1, payload(score=72), score=72)
        add_observation(self.conn, 2, payload(clean=False, score=67), score=67)
        add_observation(
            self.conn,
            3,
            payload(
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
            ),
            score=100,
        )
        add_observation(self.conn, 4, payload(omit_catalyst=True, score=72), score=72)
        add_observation(self.conn, 5, payload(score=72, narrative_wind=3), score=72)
        add_observation(self.conn, 6, payload(score=80), score=80)
        add_observation(
            self.conn,
            7,
            payload(score=72),
            origin="reconstructed",
            eligible=0,
            parent="sr:1",
            score=72,
        )

    def test_publication_round_trip_preserves_contract(self) -> None:
        self._population()
        before_scans = table_digest(self.conn, "scan_results")
        before_provenance = table_digest(self.conn, "observation_provenance")
        result = publish_scoring_decompositions(self.conn)
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["row_count"], 6)
        self.assertEqual(result["methodology_id"], INSPECTED_SINGLE_SCAN_METHODOLOGY_ID)
        self.assertEqual(result["decomposer_version"], DECOMPOSER_VERSION)
        classes = reconstruction_class_distribution(self.conn)
        self.assertEqual(classes["A_evidence_determined"], 1)
        self.assertEqual(classes["B_algebraically_inferred"], 1)
        self.assertEqual(classes["C_ambiguous"], 1)
        self.assertEqual(classes["D_incomplete"], 1)
        self.assertEqual(classes["E_evidence_conflict"], 1)
        self.assertEqual(classes["E_formula_mismatch"], 1)
        self.assertNotIn("sr:7", {
            row["observation_uid"]
            for row in self.conn.execute(f"SELECT observation_uid FROM {LIVE_TABLE}")
        })

        determined = get_scoring_decomposition(self.conn, "sr:1")
        fresh = decompose_single_scan(payload(score=72))
        self.assertEqual(determined["methodology_id"], fresh.methodology.inspected_methodology_id)
        self.assertIsNone(determined["historical_methodology_id"])
        self.assertEqual(determined["historical_compatibility"], fresh.methodology.historical_compatibility)
        self.assertEqual(determined["deployment_verification"], "unverified")
        self.assertIsNone(determined["verified_deployment_id"])
        self.assertEqual(determined["decomposer_version"], DECOMPOSER_VERSION)
        self.assertEqual(determined["schema_version"], SCORING_DECOMPOSITION_SCHEMA_VERSION)
        self.assertEqual(determined["sandbox_engine_version"], "3.0.4+test")
        self.assertEqual(determined["captured_scout_score"], 72)
        self.assertEqual(determined["unclamped_score"], 72)
        self.assertEqual(determined["clamped_score"], 72)
        self.assertEqual(determined["reconstruction_error"], 0)
        self.assertEqual(determined["formula_confirmation"], "independent")
        for field in (
            "base_score",
            "atlas_raw",
            "atlas_contribution",
            "oracle_raw",
            "oracle_contribution",
            "phantom_raw",
            "phantom_contribution",
            "catalyst_raw",
            "catalyst_contribution",
            "specter_clean_bonus",
            "specter_penalty",
            "wind_contribution",
            "alignment_bonus",
            "volatility_bonus",
            "earnings_penalty",
        ):
            self.assertEqual(determined["contributions"][field], getattr(fresh, field).as_dict())
            self.assertIn("origin", determined["contributions"][field])
            self.assertIn("sources", determined["contributions"][field])
        self.assertIsNone(determined["penalty_candidates"])
        self.assertEqual(determined["eligible_population_hash"], result["eligible_population_hash"])

        inferred = get_scoring_decomposition(self.conn, "sr:2")
        self.assertEqual(inferred["formula_confirmation"], "mathematical_compatibility")
        self.assertEqual(inferred["contributions"]["specter_penalty"]["origin"], "inferred")
        self.assertEqual(inferred["reconstruction_error"], 0)

        ambiguous = ambiguous_observations(self.conn)
        self.assertEqual(len(ambiguous), 1)
        self.assertIsNone(ambiguous[0]["unclamped_score"])
        self.assertIsNone(ambiguous[0]["clamped_score"])
        self.assertIsNone(ambiguous[0]["reconstruction_error"])
        self.assertGreaterEqual(len(ambiguous[0]["penalty_candidates"]), 2)
        self.assertIn(0, [item["penalty"] for item in ambiguous[0]["penalty_candidates"]])
        self.assertIn(-3, [item["penalty"] for item in ambiguous[0]["penalty_candidates"]])

        incomplete = get_scoring_decomposition(self.conn, "sr:4")
        self.assertIsNone(incomplete["unclamped_score"])
        self.assertIsNone(incomplete["contributions"]["catalyst_raw"]["value"])
        self.assertIn("catalyst_raw", incomplete["input_completeness"]["missing"])
        self.assertTrue(incomplete["warnings"])

        problems = missing_or_conflicting_inputs(self.conn)
        self.assertEqual(
            [row["reconstruction_class"] for row in problems],
            ["D_incomplete", "E_evidence_conflict"],
        )
        self.assertTrue(any(item["field"] == "wind" for item in problems[1]["disagreements"]))

        residuals = residual_distribution(self.conn)
        self.assertEqual(residuals["independent"]["0"], 1)
        self.assertEqual(residuals["mathematical_compatibility"]["0"], 1)
        self.assertNotIn("not_unique", residuals)

        origins = evidence_origin_distribution(self.conn)
        self.assertGreater(origins.get("specter_penalty:invariant", 0), 0)
        self.assertGreater(origins.get("specter_penalty:inferred", 0), 0)
        self.assertGreater(origins.get("specter_penalty:ambiguous", 0), 0)

        generation = current_scoring_generation(self.conn)
        self.assertEqual(generation["build"]["build_id"], result["build_id"])
        self.assertEqual(generation["build"]["artifact_row_count"], 6)
        self.assertEqual(generation["live_row_count"], 6)
        self.assertEqual(generation["scoring_input_fingerprint"], result["scoring_input_fingerprint"])
        self.assertEqual(len(result["scoring_input_fingerprint"]), 64)
        self.assertEqual(
            determined["scoring_input_fingerprint"],
            result["scoring_input_fingerprint"],
        )
        self.assertEqual(
            determined["source_provenance_fingerprint"],
            result["source_provenance_fingerprint"],
        )
        self.assertEqual(table_digest(self.conn, "scan_results"), before_scans)
        self.assertEqual(table_digest(self.conn, "observation_provenance"), before_provenance)
        self.assertEqual(
            self.conn.execute("SELECT scout_score FROM scan_results WHERE id = 6").fetchone()[0],
            80,
        )

    def test_versions_stay_independent(self) -> None:
        add_observation(self.conn, 1, payload(score=72), score=72)
        with patch(
            "scoring_decomposition_store.DECOMPOSER_VERSION",
            "scoring-decomp-2",
        ), patch(
            "scoring_decomposition_store.BUILDER_VERSION",
            "scoring-decomp-2",
        ):
            publish_scoring_decompositions(self.conn)
        row = get_scoring_decomposition(self.conn, "sr:1")
        self.assertEqual(row["decomposer_version"], "scoring-decomp-2")
        self.assertEqual(row["methodology_id"], INSPECTED_SINGLE_SCAN_METHODOLOGY_ID)
        self.assertEqual(row["schema_version"], 1)
        self.assertEqual(row["deployment_verification"], "unverified")
        self.assertEqual(row["sandbox_engine_version"], "3.0.4+test")

    def test_failure_before_publication_writes_no_rows(self) -> None:
        add_observation(self.conn, 1, payload(score=72), score=72)

        def probe(stage: str) -> None:
            if stage == "before_calculate":
                raise RuntimeError("calculation stopped")

        with self.assertRaises(RuntimeError):
            publish_scoring_decompositions(self.conn, _probe=probe)
        self.assertEqual(
            int(self.conn.execute(f"SELECT COUNT(*) FROM {LIVE_TABLE}").fetchone()[0]),
            0,
        )
        failed = self.conn.execute(
            "SELECT status, error_text FROM derived_builds WHERE artifact_type = ?",
            (ARTIFACT_TYPE,),
        ).fetchone()
        self.assertEqual(failed["status"], "FAILED")
        self.assertIn("calculation stopped", failed["error_text"])
        self.assertIsNone(current_scoring_generation(self.conn))

    def test_decomposer_exception_is_visible_and_not_stored_as_a_score(self) -> None:
        add_observation(self.conn, 1, payload(score=72), score=72)
        with patch(
            "scoring_decomposition_store.decompose_single_scan",
            side_effect=ScoringProvenanceError("payload rejected"),
        ):
            with self.assertRaises(ScoringProvenanceError):
                publish_scoring_decompositions(self.conn)
        self.assertEqual(
            int(self.conn.execute(f"SELECT COUNT(*) FROM {LIVE_TABLE}").fetchone()[0]),
            0,
        )
        failed = self.conn.execute(
            "SELECT status, error_text FROM derived_builds WHERE artifact_type = ?",
            (ARTIFACT_TYPE,),
        ).fetchone()
        self.assertEqual(failed["status"], "FAILED")
        self.assertIn("payload rejected", failed["error_text"])

    def test_failure_during_publication_preserves_the_previous_generation(self) -> None:
        add_observation(self.conn, 1, payload(score=72), score=72)
        first = publish_scoring_decompositions(self.conn)

        def probe(stage: str) -> None:
            if stage == "after_delete":
                raise RuntimeError("replace failed")

        with self.assertRaises(RuntimeError):
            publish_scoring_decompositions(self.conn, _probe=probe)
        live = self.conn.execute(
            f"SELECT build_id FROM {LIVE_TABLE}"
        ).fetchall()
        self.assertEqual([row["build_id"] for row in live], [first["build_id"]])
        self.assertEqual(
            current_scoring_generation(self.conn)["build"]["build_id"],
            first["build_id"],
        )
        failed = self.conn.execute(
            """
            SELECT status FROM derived_builds
            WHERE artifact_type = ? AND status = 'FAILED'
            """,
            (ARTIFACT_TYPE,),
        ).fetchall()
        self.assertEqual(len(failed), 1)

    def test_population_change_does_not_replace_the_generation(self) -> None:
        add_observation(self.conn, 1, payload(score=72), score=72)
        first = publish_scoring_decompositions(self.conn)
        stamp = population_stamp(self.conn)

        def probe(stage: str) -> None:
            if stage == "before_lock":
                add_observation(self.conn, 8, payload(score=72), score=72)

        with self.assertRaises(ScoringPublicationError):
            publish_scoring_decompositions(self.conn, _probe=probe)
        live = [
            row["build_id"]
            for row in self.conn.execute(f"SELECT build_id FROM {LIVE_TABLE}")
        ]
        self.assertEqual(live, [first["build_id"]])
        self.assertEqual(population_stamp(self.conn).eligible_population_count, stamp.eligible_population_count + 1)
        self.assertEqual(
            self.conn.execute(
                "SELECT eligible_population_hash FROM derived_builds WHERE build_id = ?",
                (first["build_id"],),
            ).fetchone()[0],
            first["eligible_population_hash"],
        )

    def test_second_publication_keeps_the_prior_generation_in_backup(self) -> None:
        add_observation(self.conn, 1, payload(score=72), score=72)
        first = publish_scoring_decompositions(self.conn)
        second = publish_scoring_decompositions(self.conn)
        live_ids = {
            row["build_id"]
            for row in self.conn.execute(f"SELECT DISTINCT build_id FROM {LIVE_TABLE}")
        }
        backup_ids = {
            row["build_id"]
            for row in self.conn.execute(f"SELECT DISTINCT build_id FROM {BACKUP_TABLE}")
        }
        self.assertEqual(live_ids, {second["build_id"]})
        self.assertEqual(backup_ids, {first["build_id"]})
        self.assertEqual(
            self.conn.execute(f"SELECT COUNT(*) FROM {LIVE_TABLE}").fetchone()[0],
            self.conn.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0],
        )
        self.assertEqual(
            self.conn.execute(
                f"SELECT COUNT(*) FROM {LIVE_TABLE} GROUP BY observation_uid HAVING COUNT(*) > 1"
            ).fetchall(),
            [],
        )

    def test_publish_is_not_wired_into_capture_or_outcome_refresh(self) -> None:
        root = Path(__file__).resolve().parent
        for name in ("memory_store.py", "pattern_engine.py"):
            text = (root / name).read_text(encoding="utf-8")
            self.assertNotIn("publish_scoring_decompositions", text)
            self.assertNotIn("scoring_decomposition_store", text)


class IdentityTriggerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = base_db()
        apply_schema(self.conn)
        add_observation(self.conn, 1, payload(score=72), score=72)
        add_observation(self.conn, 2, payload(clean=False, score=67), score=67)
        self.published = publish_scoring_decompositions(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def _clone(self, table: str, **changes: object) -> None:
        columns = [row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")]
        source = list(
            self.conn.execute(f"SELECT {', '.join(columns)} FROM {LIVE_TABLE} LIMIT 1").fetchone()
        )
        for name, value in changes.items():
            source[columns.index(name)] = value
        placeholders = ", ".join("?" for _ in columns)
        self.conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            source,
        )

    def test_matching_pair_is_accepted_on_both_tables(self) -> None:
        self._clone(LIVE_TABLE, methodology_id="baseline-copy-check")
        self._clone(BACKUP_TABLE, methodology_id="baseline-copy-check")
        self.assertEqual(
            self.conn.execute(
                f"SELECT COUNT(*) FROM {LIVE_TABLE} WHERE methodology_id = 'baseline-copy-check'"
            ).fetchone()[0],
            1,
        )

    def test_mismatched_and_missing_pairs_are_rejected_with_and_without_foreign_keys(self) -> None:
        for enabled in (1, 0):
            self.conn.execute(f"PRAGMA foreign_keys = {enabled}")
            for table in (LIVE_TABLE, BACKUP_TABLE):
                for uid, scan_id in (("sr:1", 2), ("missing-observation", 1)):
                    with self.assertRaises(sqlite3.IntegrityError):
                        self._clone(table, observation_uid=uid, scan_result_id=scan_id, methodology_id=f"bad-{enabled}-{table}-{uid}-{scan_id}")
        self.conn.execute("PRAGMA foreign_keys = ON")

    def test_update_cannot_retarget_the_pair(self) -> None:
        for enabled in (1, 0):
            self.conn.execute(f"PRAGMA foreign_keys = {enabled}")
            for table in (LIVE_TABLE, BACKUP_TABLE):
                self._clone(table, methodology_id=f"editable-{enabled}-{table}")
                with self.assertRaises(sqlite3.IntegrityError):
                    self.conn.execute(
                        f"UPDATE {table} SET scan_result_id = 2 WHERE methodology_id = ?",
                        (f"editable-{enabled}-{table}",),
                    )
                self.conn.execute(
                    f"UPDATE {table} SET captured_scout_score = 72 WHERE methodology_id = ?",
                    (f"editable-{enabled}-{table}",),
                )
        self.conn.execute("PRAGMA foreign_keys = ON")

    def test_publication_rejects_an_inconsistent_proposed_identity(self) -> None:
        from scoring_decomposition_store import _storage_row

        def flipped(*args, **kwargs):
            row = _storage_row(*args, **kwargs)
            if row["observation_uid"] == "sr:1":
                row["scan_result_id"] = 2
            return row

        before = [
            tuple(row)
            for row in self.conn.execute(
                f"SELECT observation_uid, scan_result_id, methodology_id FROM {LIVE_TABLE} ORDER BY 1, 3"
            )
        ]
        with patch("scoring_decomposition_store._storage_row", flipped):
            with self.assertRaises(ScoringPublicationError) as caught:
                publish_scoring_decompositions(self.conn)
        self.assertIn("sr:1 does not belong to scan_result_id 2", str(caught.exception))
        after = [
            tuple(row)
            for row in self.conn.execute(
                f"SELECT observation_uid, scan_result_id, methodology_id FROM {LIVE_TABLE} ORDER BY 1, 3"
            )
        ]
        self.assertEqual(after, before)
        failed = self.conn.execute(
            "SELECT status, error_text FROM derived_builds WHERE artifact_type = ? AND status = 'FAILED'",
            (ARTIFACT_TYPE,),
        ).fetchone()
        self.assertIn("does not belong", failed["error_text"])
        self.assertEqual(
            current_scoring_generation(self.conn)["build"]["build_id"],
            self.published["build_id"],
        )


class FingerprintTests(unittest.TestCase):
    def test_fingerprint_is_deterministic_and_distinguishes_null(self) -> None:
        from scoring_decomposition_store import (
            scoring_input_fingerprint,
            source_provenance_fingerprint,
        )

        rows = [("sr:2", 2, "b"), ("sr:1", 1, "a")]
        again = [("sr:1", 1, "a"), ("sr:2", 2, "b")]
        self.assertEqual(scoring_input_fingerprint(rows), scoring_input_fingerprint(again))
        self.assertEqual(scoring_input_fingerprint(rows), scoring_input_fingerprint(list(reversed(rows))))
        self.assertNotEqual(
            scoring_input_fingerprint([("sr:1", 1, None)]),
            scoring_input_fingerprint([("sr:1", 1, "")]),
        )
        self.assertNotEqual(
            source_provenance_fingerprint([("sr:1", 1, None)]),
            source_provenance_fingerprint([("sr:1", 1, "")]),
        )
        self.assertNotEqual(
            scoring_input_fingerprint([("sr:1", 1, "SCOUT SCORE: 72")]),
            scoring_input_fingerprint([("sr:1", 1, "SCOUT SCORE: 11")]),
        )
        self.assertNotEqual(
            scoring_input_fingerprint([("sr:1", 1, "Direction: NEUTRAL")]),
            scoring_input_fingerprint([("sr:1", 1, "Direction: BULLISH")]),
        )
        self.assertNotEqual(
            scoring_input_fingerprint([("sr:1", 1, "Score: 2")]),
            scoring_input_fingerprint([("sr:1", 1, "Score: 3")]),
        )

    def test_payload_change_preserves_the_previous_generation(self) -> None:
        conn = base_db()
        apply_schema(conn)
        add_observation(conn, 1, payload(score=72), score=72)
        first = publish_scoring_decompositions(conn)
        original = conn.execute("SELECT raw_result_json FROM scan_results WHERE id = 1").fetchone()[0]

        def change(stage: str) -> None:
            if stage == "before_lock":
                body = json.loads(original)
                body["raw"]["scout_score"] = 11
                body["raw"]["direction"] = "BULLISH"
                body["raw"]["raw_output"] = body["raw"]["raw_output"].replace(
                    "SCOUT SCORE: 72/100", "SCOUT SCORE: 11/100"
                ).replace("Direction: NEUTRAL", "Direction: BULLISH")
                conn.execute(
                    "UPDATE scan_results SET raw_result_json = ? WHERE id = 1",
                    (json.dumps(body),),
                )

        with self.assertRaises(ScoringPublicationError) as caught:
            publish_scoring_decompositions(conn, _probe=change)
        self.assertIn("scoring-input fingerprint", str(caught.exception))
        live = [row["build_id"] for row in conn.execute(f"SELECT build_id FROM {LIVE_TABLE}")]
        self.assertEqual(live, [first["build_id"]])
        self.assertEqual(
            conn.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0],
            0,
        )
        conn.close()

    def test_outcome_update_does_not_change_the_scoring_fingerprint(self) -> None:
        conn = base_db()
        conn.execute("ALTER TABLE scan_results ADD COLUMN return_1d REAL")
        conn.execute("ALTER TABLE scan_results ADD COLUMN stock_outcome_label TEXT")
        apply_schema(conn)
        add_observation(conn, 1, payload(score=72), score=72)
        first = publish_scoring_decompositions(conn)

        def change(stage: str) -> None:
            if stage == "before_lock":
                conn.execute(
                    "UPDATE scan_results SET return_1d = 4.5, stock_outcome_label = 'WIN', scout_score = 11 WHERE id = 1"
                )

        second = publish_scoring_decompositions(conn, _probe=change)
        self.assertEqual(second["scoring_input_fingerprint"], first["scoring_input_fingerprint"])
        stored = get_scoring_decomposition(conn, "sr:1")
        self.assertEqual(stored["captured_scout_score"], 72)
        self.assertEqual(conn.execute("SELECT scout_score, return_1d FROM scan_results").fetchone()[0], 11)
        conn.close()

    def test_engine_version_change_is_a_provenance_mismatch(self) -> None:
        conn = base_db()
        apply_schema(conn)
        add_observation(conn, 1, payload(score=72), score=72)
        first = publish_scoring_decompositions(conn)

        def change(stage: str) -> None:
            if stage == "before_lock":
                conn.execute("UPDATE scan_results SET engine_version = '3.0.9+other' WHERE id = 1")

        with self.assertRaises(ScoringPublicationError) as caught:
            publish_scoring_decompositions(conn, _probe=change)
        self.assertIn("source provenance fingerprint", str(caught.exception))
        self.assertEqual(
            [row["build_id"] for row in conn.execute(f"SELECT build_id FROM {LIVE_TABLE}")],
            [first["build_id"]],
        )
        self.assertEqual(get_scoring_decomposition(conn, "sr:1")["sandbox_engine_version"], "3.0.4+test")
        conn.close()


class MethodologyGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = base_db()
        apply_schema(self.conn)
        add_observation(self.conn, 1, payload(score=72), score=72)

    def tearDown(self) -> None:
        self.conn.close()

    def _snapshot(self) -> dict[str, list[tuple]]:
        columns = [row[1] for row in self.conn.execute(f"PRAGMA table_info({LIVE_TABLE})")]
        listed = ", ".join(columns)
        return {
            table: [
                tuple(row)
                for row in self.conn.execute(f"SELECT {listed} FROM {table} ORDER BY methodology_id, observation_uid")
            ]
            for table in (LIVE_TABLE, BACKUP_TABLE)
        }

    def _plant(self, table: str, methodology_id: str) -> None:
        columns = [row[1] for row in self.conn.execute(f"PRAGMA table_info({table})")]
        source = self.conn.execute(f"SELECT {', '.join(columns)} FROM {LIVE_TABLE} LIMIT 1").fetchone()
        if source is None:
            source = self.conn.execute(f"SELECT {', '.join(columns)} FROM {BACKUP_TABLE} LIMIT 1").fetchone()
        values = list(source)
        values[columns.index("methodology_id")] = methodology_id
        placeholders = ", ".join("?" for _ in columns)
        self.conn.execute(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
            values,
        )

    def _expect_rejected(self, label: str) -> None:
        before = self._snapshot()
        with self.assertRaises(ScoringPublicationError) as caught:
            publish_scoring_decompositions(self.conn)
        self.assertIn(label, str(caught.exception))
        self.assertEqual(self._snapshot(), before)
        failed = self.conn.execute(
            """
            SELECT status, error_text FROM derived_builds
            WHERE artifact_type = ? AND status = 'FAILED'
            ORDER BY started_at DESC LIMIT 1
            """,
            (ARTIFACT_TYPE,),
        ).fetchone()
        self.assertIn(label, failed["error_text"])

    def test_empty_and_baseline_tables_publish(self) -> None:
        first = publish_scoring_decompositions(self.conn)
        self.assertEqual(first["status"], "COMPLETED")
        second = publish_scoring_decompositions(self.conn)
        self.assertEqual(second["status"], "COMPLETED")
        self.assertEqual(
            {
                row["methodology_id"]
                for row in self.conn.execute(f"SELECT methodology_id FROM {LIVE_TABLE}")
            },
            {"scout-single-v1+243ce011a4e8ad9f"},
        )

    def test_unexpected_methodology_in_live_backup_or_both_is_preserved(self) -> None:
        publish_scoring_decompositions(self.conn)
        self._plant(LIVE_TABLE, "experiment-live")
        self._expect_rejected("experiment-live")
        self.conn.execute(f"DELETE FROM {LIVE_TABLE} WHERE methodology_id = 'experiment-live'")
        self._plant(BACKUP_TABLE, "experiment-backup")
        self._expect_rejected("experiment-backup")
        self._plant(LIVE_TABLE, "experiment-both")
        self._expect_rejected("experiment-backup")
        self._expect_rejected("experiment-both")


class MigrationIntegrityTests(unittest.TestCase):
    def test_tables_without_triggers_are_not_already_migrated(self) -> None:
        from migrate_scoring_decompositions import (
            CREATE_BACKUP_SQL,
            CREATE_CLASS_INDEX_SQL,
            CREATE_LIVE_SQL,
        )

        conn = base_db()
        conn.execute(CREATE_LIVE_SQL)
        conn.execute(CREATE_BACKUP_SQL)
        conn.execute(CREATE_CLASS_INDEX_SQL)
        self.assertEqual(schema_status(conn), "conflict")
        with self.assertRaises(MigrationAbort):
            apply_schema(conn)
        self.assertEqual(
            conn.execute(
                "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'scoring_%'"
            ).fetchone()[0],
            0,
        )
        conn.close()

    def test_dropped_trigger_is_not_silently_repaired(self) -> None:
        conn = base_db()
        self.assertEqual(apply_schema(conn), "applied")
        conn.execute("DROP TRIGGER scoring_decompositions_identity_insert")
        self.assertEqual(schema_status(conn), "conflict")
        with self.assertRaises(MigrationAbort):
            apply_schema(conn)
        self.assertIsNone(
            conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'trigger' AND name = 'scoring_decompositions_identity_insert'"
            ).fetchone()
        )
        conn.close()

    def test_failed_migration_restores_the_manifest(self) -> None:
        conn = base_db()
        conn.execute(
            """
            INSERT INTO derived_builds (
                build_id, artifact_type, classifier_version,
                eligible_population_count, eligible_population_hash,
                builder_version, status, started_at, built_at, artifact_row_count
            ) VALUES (
                'pattern-keep', 'pattern_intelligence', 'b0-2026-09-28',
                1, ?, 'pattern-1', 'COMPLETED',
                '2026-09-28T00:00:00Z', '2026-09-28T00:00:01Z', 36
            )
            """,
            ("d" * 64,),
        )
        conn.execute("CREATE TABLE derived_builds_scoring_widen (blocked INTEGER)")
        before = [
            tuple(row)
            for row in conn.execute("SELECT type, name FROM sqlite_master ORDER BY 1, 2")
        ]
        with self.assertRaises(sqlite3.OperationalError):
            apply_schema(conn)
        after = [
            tuple(row)
            for row in conn.execute("SELECT type, name FROM sqlite_master ORDER BY 1, 2")
        ]
        self.assertEqual(after, before)
        self.assertEqual(
            tuple(conn.execute("SELECT build_id, status, artifact_row_count FROM derived_builds").fetchone()),
            ("pattern-keep", "COMPLETED", 36),
        )
        conn.close()


class DisposableCopyCensusTests(unittest.TestCase):
    def test_copy_matches_a_fresh_decomposition_and_preserves_prior_artifacts(self) -> None:
        before_hash = sha256_file(CANONICAL_DB)
        with tempfile.TemporaryDirectory() as tmp:
            copy_path = Path(tmp) / "scout_memory_copy.db"
            source = sqlite3.connect(f"file:{CANONICAL_DB.resolve()}?mode=ro", uri=True)
            copied = sqlite3.connect(copy_path, isolation_level=None)
            try:
                source.backup(copied)
            finally:
                source.close()
            copied.row_factory = sqlite3.Row
            copied.execute("PRAGMA foreign_keys = ON")
            before = _research_snapshot(copied)
            self.assertEqual(before["eligible_hash"], EXPECTED_ELIGIBLE_HASH)
            schema_result = apply_schema(copied)
            self.assertIn(schema_result, ("applied", "already_applied"))
            self.assertEqual(_research_snapshot(copied), before)
            first = publish_scoring_decompositions(copied)
            recomputed = _recompute_census(copied)
            stored = reconstruction_class_distribution(copied)
            represented = int(copied.execute(f"SELECT COUNT(*) FROM {LIVE_TABLE}").fetchone()[0])
            self.assertEqual(represented, before["eligible_count"])
            self.assertEqual(represented, sum(stored.values()))
            self.assertEqual(stored, recomputed["classes"])
            self.assertEqual(stored.get("E_formula_mismatch", 0), recomputed["classes"].get("E_formula_mismatch", 0))
            self.assertEqual(recomputed["classes"].get("E_formula_mismatch", 0), 0)
            self.assertEqual(stored.get("E_evidence_conflict", 0), recomputed["classes"].get("E_evidence_conflict", 0))
            self.assertEqual(recomputed["classes"].get("E_evidence_conflict", 0), 0)
            self.assertEqual(len(recomputed["omitted"]), 0)
            self.assertEqual(
                copied.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM {LIVE_TABLE} AS scoring
                    JOIN observation_provenance AS op
                      ON op.observation_uid = scoring.observation_uid
                    WHERE op.scan_result_id != scoring.scan_result_id
                    """
                ).fetchone()[0],
                0,
            )
            self.assertEqual(
                [
                    row[0]
                    for row in copied.execute(f"SELECT DISTINCT methodology_id FROM {LIVE_TABLE}")
                ],
                [first["methodology_id"]],
            )
            fresh_rows = [
                (row["observation_uid"], int(row["scan_result_id"]), row["raw_result_json"], row["engine_version"])
                for row in copied.execute(
                    """
                    SELECT op.observation_uid, sr.id AS scan_result_id,
                           sr.raw_result_json, sr.engine_version
                    FROM scan_results AS sr
                    JOIN observation_provenance AS op ON op.scan_result_id = sr.id
                    WHERE op.research_eligible = 1
                    ORDER BY sr.id
                    """
                )
            ]
            from scoring_decomposition_store import (
                scoring_input_fingerprint,
                source_provenance_fingerprint,
            )
            self.assertEqual(
                first["scoring_input_fingerprint"],
                scoring_input_fingerprint([(uid, scan_id, raw) for uid, scan_id, raw, _engine in fresh_rows]),
            )
            self.assertEqual(
                first["source_provenance_fingerprint"],
                source_provenance_fingerprint([(uid, scan_id, engine) for uid, scan_id, _raw, engine in fresh_rows]),
            )
            self.assertEqual(
                current_scoring_generation(copied)["scoring_input_fingerprint"],
                first["scoring_input_fingerprint"],
            )
            ambiguous = ambiguous_observations(copied)
            self.assertEqual(len(ambiguous), recomputed["classes"].get("C_ambiguous", 0))
            for row in ambiguous:
                fresh = recomputed["ambiguous"][row["observation_uid"]]
                self.assertIsNone(row["unclamped_score"])
                self.assertIsNone(row["clamped_score"])
                self.assertIsNone(row["reconstruction_error"])
                self.assertEqual(row["penalty_candidates"], fresh)
                self.assertGreaterEqual(len(row["penalty_candidates"]), 2)
            after_first = _research_snapshot(copied)
            self.assertEqual(after_first, before)
            second = publish_scoring_decompositions(copied)
            backup_ids = {
                row["build_id"]
                for row in copied.execute(f"SELECT DISTINCT build_id FROM {BACKUP_TABLE}")
            }
            live_ids = {
                row["build_id"]
                for row in copied.execute(f"SELECT DISTINCT build_id FROM {LIVE_TABLE}")
            }
            self.assertEqual(backup_ids, {first["build_id"]})
            self.assertEqual(live_ids, {second["build_id"]})
            self.assertEqual(
                copied.execute(f"SELECT COUNT(*) FROM {BACKUP_TABLE}").fetchone()[0],
                before["eligible_count"],
            )
            self.assertEqual(_research_snapshot(copied), before)
            self.assertEqual(population_stamp(copied).eligible_population_hash, before["eligible_hash"])
            copied.close()
        self.assertEqual(sha256_file(CANONICAL_DB), before_hash)


def _research_snapshot(conn: sqlite3.Connection) -> dict:
    stamp = population_stamp(conn)
    snapshot = {
        "eligible_count": stamp.eligible_population_count,
        "eligible_hash": stamp.eligible_population_hash,
        "classifier": stamp.classifier_version,
        "scan_results": table_digest(conn, "scan_results"),
        "observation_provenance": table_digest(conn, "observation_provenance"),
    }
    for table in INTELLIGENCE_TABLES:
        snapshot[table] = table_digest(conn, table)
        snapshot[table + "_manifests"] = [
            tuple(row)
            for row in conn.execute(
                """
                SELECT build_id, artifact_type, classifier_version,
                       eligible_population_count, eligible_population_hash,
                       builder_version, status, started_at, built_at,
                       artifact_row_count, error_text
                FROM derived_builds
                WHERE artifact_type = ?
                ORDER BY build_id
                """,
                (table,),
            )
        ]
    return snapshot


def _recompute_census(conn: sqlite3.Connection) -> dict:
    classes: Counter[str] = Counter()
    ambiguous = {}
    stored_uids = {
        row["observation_uid"]
        for row in conn.execute(f"SELECT observation_uid FROM {LIVE_TABLE}")
    }
    seen = set()
    for row in conn.execute(
        """
        SELECT sr.id, sr.raw_result_json, op.observation_uid
        FROM scan_results AS sr
        JOIN observation_provenance AS op ON op.scan_result_id = sr.id
        WHERE op.research_eligible = 1
        ORDER BY sr.id
        """
    ):
        seen.add(row["observation_uid"])
        decomposition = decompose_single_scan(json.loads(row["raw_result_json"]))
        classes[decomposition.classification.value] += 1
        if decomposition.classification.value == "C_ambiguous":
            ambiguous[row["observation_uid"]] = [
                item.as_dict() for item in decomposition.penalty_candidates
            ]
    return {
        "classes": dict(classes),
        "ambiguous": ambiguous,
        "omitted": sorted(seen - stored_uids),
    }


if __name__ == "__main__":
    unittest.main()
