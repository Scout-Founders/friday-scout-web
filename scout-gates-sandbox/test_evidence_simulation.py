"""F3.3 simulation, publication integrity, and acquisition-safeguard tests.

Synthetic fixtures only. No production import, vendor call, or canonical write.
"""

import tempfile
import threading
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

from evidence_contract import (
    AcquisitionRole,
    BudgetDecisionStatus,
    CANONICAL_ELIGIBLE_POPULATION_COUNT,
    CANONICAL_ELIGIBLE_POPULATION_HASH,
    CaptureCompleteness,
    CaptureOrigin,
    ChunkRef,
    Direction,
    EVIDENCE_SCHEMA_VERSION,
    EvaluationState,
    EvidenceCheckpointManifest,
    EvidenceContractError,
    EvidenceDerivation,
    ExecutionStatus,
    FeatureDerivation,
    FeatureObservation,
    Freshness,
    FreshnessBasis,
    GateEvaluation,
    InstrumentEvent,
    InstrumentIdSource,
    MarketOutcome,
    MethodologyRole,
    OutcomeAnchor,
    OutcomeKind,
    OUTCOME_METHODOLOGY_BY_KIND,
    OutcomeRealization,
    Pipeline,
    PublicationStatus,
    RankingDecision,
    ResearchEligibility,
    SecurityObservation,
    TerminalPublicationManifest,
    TimeSource,
    UniverseMembership,
    VendorRequestState,
    assert_can_coexist,
    assert_historically_available,
    canonical_json_bytes,
    make_acquisition_run_id,
    security_observation_id,
    sha256_bytes,
    validate,
)
from evidence_economics import (
    AcquisitionBudget,
    BudgetCounters,
    EntitlementPolicy,
    LocalBudgetLedger,
    ProposedAcquisition,
    committed_external_requests,
    evaluate_budget,
)
from evidence_publication import (
    CLOUD_ADAPTER_REQUIREMENTS,
    GenerationPreconditionError,
    ImmutableConflict,
    LocalEvidenceStore,
    PublicationValidationError,
    gzip_deterministic,
    validate_publication_set,
)
from evidence_simulation import (
    accounted_chunk_rows,
    build_production_shaped_run,
    measure_local_publication,
    repeated_request_keeps_payload,
    research_population_examples,
    reuse_views,
    scenario_runs,
)

T0 = datetime(2026, 11, 26, 14, 0, tzinfo=timezone.utc)
T1 = T0 + timedelta(minutes=1)
T2 = T0 + timedelta(minutes=5)
T3 = T0 + timedelta(days=1)


def _observation(run_id, symbol, exchange=None):
    return SecurityObservation(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        observation_id=security_observation_id(run_id, symbol, exchange),
        acquisition_run_id=run_id,
        symbol_as_observed=symbol,
        membership_id="membership:{0}".format(symbol),
        quote_state=VendorRequestState.SUCCESSFUL_RESPONSE,
        quote_as_of_source=TimeSource.UNAVAILABLE,
        evidence_derivation=EvidenceDerivation.CAPTURED,
        methodology_id="fixture",
        missing_reasons=(("quote_as_of", "vendor_effective_time_unavailable"),),
        exchange=exchange,
        price=10,
        year_high=12,
        year_low=8,
        volume=1000000,
        change_percentage=1,
        response_received_at=T0,
    )


def _feature(**overrides):
    values = dict(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        feature_observation_id="feature-price-0",
        observation_id="obs",
        feature_name="price",
        feature_derivation=FeatureDerivation.VENDOR_RAW,
        evidence_derivation=EvidenceDerivation.CAPTURED,
        methodology_id="fixture",
        source_request_ids=("req-1",),
        vendor_effective_source=TimeSource.UNAVAILABLE,
        freshness=Freshness.UNKNOWN,
        freshness_basis=FreshnessBasis.RECEIPT_TIME,
        value_number=10,
        response_received_at=T0,
        revision_sequence=0,
        consumed_by_decision=False,
    )
    values.update(overrides)
    return FeatureObservation(**values)


def _run_id(nonce):
    return make_acquisition_run_id(
        acquisition_role=AcquisitionRole.RESEARCH,
        pipeline=Pipeline.RESEARCH_ACQUISITION,
        started_at=T0,
        attempt_number=1,
        execution_nonce=nonce,
    )


class IdentityTests(unittest.TestCase):
    def test_same_ticker_on_two_exchanges_does_not_collide(self):
        run_id = _run_id("ident0001")
        nasdaq = _observation(run_id, "S000", "XNAS")
        nyse = _observation(run_id, "S000", "XASE")
        validate(nasdaq)
        validate(nyse)
        assert_can_coexist(nasdaq, nyse)
        self.assertNotEqual(nasdaq.observation_id, nyse.observation_id)
        self.assertIsNone(nasdaq.instrument_id if hasattr(nasdaq, "instrument_id") else None)

    def test_missing_exchange_stays_distinct_from_a_named_exchange(self):
        run_id = _run_id("ident0002")
        unnamed = _observation(run_id, "S000", None)
        named = _observation(run_id, "S000", "XNAS")
        assert_can_coexist(unnamed, named)

    def test_repeated_observation_across_runs_is_a_new_row(self):
        first = _observation(_run_id("ident0003"), "S000", "XNAS")
        second = _observation(_run_id("ident0004"), "S000", "XNAS")
        assert_can_coexist(first, second)

    def test_instrument_id_is_kept_only_when_supplied(self):
        supplied = UniverseMembership(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            membership_id="universe:S000:1:XNAS",
            universe_manifest_id="universe",
            acquisition_run_id="run",
            symbol_as_observed="S000",
            instrument_id_source=InstrumentIdSource.VENDOR,
            instrument_event=InstrumentEvent.NONE,
            registration_ordinal=1,
            sector_as_registered="SYN_TECH",
            sector_resolved="SYN_TECH",
            wind_resolved=0,
            resolution_rule="last_registration_wins",
            exchange="XNAS",
            instrument_id="vendor-instrument-1",
        )
        unknown = replace(
            supplied,
            membership_id="universe:S001:1:XNAS",
            symbol_as_observed="S001",
            instrument_id_source=InstrumentIdSource.UNAVAILABLE,
            instrument_id=None,
        )
        changed = replace(
            supplied,
            membership_id="universe:S002:1:XNAS",
            symbol_as_observed="S002",
            instrument_event=InstrumentEvent.TICKER_CHANGE,
            prior_symbol="SOLD",
            instrument_id_source=InstrumentIdSource.UNAVAILABLE,
            instrument_id=None,
        )
        validate(supplied)
        validate(unknown)
        validate(changed)
        self.assertEqual(supplied.instrument_id, "vendor-instrument-1")
        self.assertIsNone(unknown.instrument_id)

    def test_same_symbol_ranks_on_two_exchanges(self):
        left = RankingDecision(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            ranking_decision_id="rank-xnas",
            acquisition_run_id="run",
            direction=Direction.CALL,
            stage="top10",
            symbol_as_observed="S000",
            included=True,
            sort_key="drawdown",
            ranked_at=T1,
            decision_available_at=T1,
            rank=1,
            cut_limit=10,
            exchange="XNAS",
        )
        right = replace(left, ranking_decision_id="rank-xase", exchange="XASE")
        validate(left)
        validate(right)
        assert_can_coexist(left, right)


class RevisionAndTimingTests(unittest.TestCase):
    def test_revisions_coexist_and_do_not_overwrite_the_captured_row(self):
        captured = _feature(
            feature_observation_id="price-captured-0",
            consumed_by_decision=True,
            value_number=10,
        )
        reconstructed = _feature(
            feature_observation_id="price-reconstructed-1",
            evidence_derivation=EvidenceDerivation.RECONSTRUCTED,
            revision_sequence=1,
            supersedes_feature_observation_id=captured.feature_observation_id,
            consumed_by_decision=False,
            value_number=10.5,
            calculated_at=T2,
        )
        reconstructed_again = _feature(
            feature_observation_id="price-reconstructed-2",
            evidence_derivation=EvidenceDerivation.RECONSTRUCTED,
            revision_sequence=2,
            supersedes_feature_observation_id=reconstructed.feature_observation_id,
            consumed_by_decision=False,
            value_number=11,
            calculated_at=T3,
        )
        revised_vendor = _feature(
            feature_observation_id="price-captured-1",
            evidence_derivation=EvidenceDerivation.CAPTURED,
            revision_sequence=1,
            supersedes_feature_observation_id=captured.feature_observation_id,
            consumed_by_decision=False,
            value_number=12,
        )
        experimental = _feature(
            feature_observation_id="price-experimental-0",
            evidence_derivation=EvidenceDerivation.EXPERIMENTAL,
            methodology_id="experimental-scoring-v0",
            consumed_by_decision=False,
            value_number=99,
            calculated_at=T3,
        )
        for record in (captured, reconstructed, reconstructed_again, revised_vendor, experimental):
            validate(record)
        assert_can_coexist(captured, reconstructed)
        assert_can_coexist(reconstructed, reconstructed_again)
        assert_can_coexist(captured, revised_vendor)
        assert_can_coexist(captured, experimental)
        overwritten = replace(captured, value_number=999)
        with self.assertRaises(EvidenceContractError):
            assert_can_coexist(captured, overwritten)

    def test_retrospective_calculation_is_not_a_production_input(self):
        consumed = _feature(
            feature_observation_id="consumed",
            consumed_by_decision=True,
            calculated_at=T1,
        )
        available = assert_historically_available(consumed, T2)
        self.assertTrue(available["usable"])
        self.assertTrue(available["consumed_by_decision"])
        self.assertFalse(available["retrospective"])
        retrospective = _feature(
            feature_observation_id="retro",
            evidence_derivation=EvidenceDerivation.RECONSTRUCTED,
            revision_sequence=1,
            supersedes_feature_observation_id="consumed",
            consumed_by_decision=False,
            calculated_at=T3,
        )
        later = assert_historically_available(retrospective, T2)
        self.assertTrue(later["retrospective"])
        self.assertFalse(later["consumed_by_decision"])
        claimed = replace(retrospective, consumed_by_decision=True, evidence_derivation=EvidenceDerivation.CAPTURED, revision_sequence=0, supersedes_feature_observation_id=None)
        with self.assertRaises(EvidenceContractError):
            assert_historically_available(claimed, T2)

    def test_inputs_acquired_after_the_decision_are_rejected(self):
        late = _feature(response_received_at=T3)
        with self.assertRaises(EvidenceContractError):
            assert_historically_available(late, T1)


class GateTimestampTests(unittest.TestCase):
    def _gate(self, state, evaluated_at, reasons=(("score", "not_scored"),)):
        return GateEvaluation(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            gate_evaluation_id="gate-1",
            directional_evaluation_id="directional-1",
            gate_name="gate4",
            evaluation_state=state,
            evidence_derivation=EvidenceDerivation.CAPTURED,
            missing_reasons=reasons,
            evaluated_at=evaluated_at,
        )

    def test_missing_inputs_and_interruption_may_carry_a_timestamp(self):
        missing = self._gate(
            EvaluationState.EVALUATED_MISSING_INPUTS,
            T1,
            (("score", "missing_input"),),
        )
        interrupted = self._gate(EvaluationState.EVALUATION_INTERRUPTED, T1)
        validate(missing)
        validate(interrupted)

    def test_a_gate_that_never_executed_has_no_timestamp(self):
        skipped = self._gate(EvaluationState.NOT_EVALUATED_EARLIER_FAILURE, T1)
        with self.assertRaises(EvidenceContractError):
            validate(skipped)
        placeholder = replace(
            self._gate(EvaluationState.NOT_EVALUATED_EARLIER_FAILURE, None),
            production_reported_state="clean_true_placeholder",
            clean=None,
        )
        validate(placeholder)


class OutcomeRealizationTests(unittest.TestCase):
    def _contract_outcome(self, **overrides):
        kind = overrides.get("outcome_kind", OutcomeKind.OPTION_CONTRACT)
        values = dict(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            outcome_id="outcome-contract",
            subject_id="subject",
            anchor_type=OutcomeAnchor.HYPOTHETICAL_EXECUTABLE_ENTRY,
            outcome_methodology_id=OUTCOME_METHODOLOGY_BY_KIND[kind],
            outcome_kind=kind,
            horizon_sessions=5,
            entry_at=T2,
            evidence_derivation=EvidenceDerivation.CAPTURED,
            formula_strike_used=False,
            contract_verified=True,
            chain_present=True,
            outcome_realization=OutcomeRealization.HYPOTHETICAL,
            value=0.1,
            decision_available_at=T1,
            contract_symbol="SYNTHETIC240101C00100000",
        )
        values.update(overrides)
        return MarketOutcome(**values)

    def test_hypothetical_observed_and_realized_stay_distinct(self):
        hypothetical = self._contract_outcome()
        observed = self._contract_outcome(
            outcome_id="outcome-observed",
            outcome_realization=OutcomeRealization.OBSERVED_CONTRACT_PRICE,
            value=1.25,
        )
        realized = self._contract_outcome(
            outcome_id="outcome-realized",
            anchor_type=OutcomeAnchor.ACTUAL_EXECUTION,
            outcome_realization=OutcomeRealization.REALIZED_EXECUTION,
            execution_evidence_id="execution-1",
            value=0.2,
        )
        validate(hypothetical)
        validate(observed)
        validate(realized)
        assert_can_coexist(hypothetical, observed)
        self.assertIsNone(hypothetical.execution_evidence_id)
        formula = self._contract_outcome(formula_strike_used=True, contract_verified=False, contract_symbol=None, chain_present=False, value=None, missing_reason="formula_strike_not_a_contract", outcome_realization=OutcomeRealization.OBSERVED_CONTRACT_PRICE)
        with self.assertRaises(EvidenceContractError):
            validate(formula)


class ProductionShapedSimulationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapped = build_production_shaped_run()

    def test_reconciliation_explains_every_reduction(self):
        report = self.mapped.reconciliation
        self.assertTrue(self.mapped.inputs_unchanged)
        self.assertEqual(report["registrations"], 562)
        self.assertEqual(report["unique_symbols"], 560)
        self.assertEqual(report["observations"], 560)
        self.assertEqual(report["quote_attempts"], 562)
        self.assertEqual(report["quote_states"]["successful_response"], 542)
        self.assertEqual(report["quote_states"]["empty_response"], 10)
        self.assertEqual(report["quote_states"]["timeout"], 10)
        self.assertEqual(report["unique_payloads"], 540)
        self.assertEqual(report["payload_references"], 542)
        self.assertEqual(report["duplicate_payload_references"], 2)
        self.assertEqual(report["directional_evaluations"], 1120)
        self.assertEqual(report["complete_passers"], 10)
        self.assertEqual(report["published_candidates"], 6)
        self.assertEqual(report["editorial"], 4)
        self.assertEqual(report["editorial_classes"], {"top": 1, "secondary": 1, "watch": 2})
        self.assertEqual(report["gate4_placeholders"], 4)
        self.assertEqual(report["verified_contracts"], 0)
        self.assertEqual(report["instrument_ids_invented"], 0)
        self.assertEqual(report["outcomes_without_value"], 10)
        self.assertEqual(report["resolved_cloud_platform"], ["S000", "S001"])
        states = report["directional_states"]
        self.assertEqual(sum(states.values()), 1120)
        self.assertEqual(states["evaluated_passed"], 10)
        self.assertEqual(states["vendor_request_failed"] + states["vendor_returned_no_data"], 40)

    def test_duplicate_registration_resolves_without_a_second_observation(self):
        symbols = [item.symbol_as_observed for item in self.mapped.observations]
        self.assertEqual(len(symbols), len(set(symbols)))
        cloud = [item for item in self.mapped.memberships if item.symbol_as_observed == "S000"]
        self.assertEqual(len(cloud), 2)
        self.assertTrue(all(item.sector_resolved == "CLOUD_PLATFORM" for item in cloud))
        self.assertTrue(all(item.wind_resolved == -2 for item in cloud))
        self.assertTrue(all(item.instrument_id is None for item in cloud))

    def test_call_and_put_share_one_quote_payload(self):
        reuse = reuse_views(self.mapped)
        self.assertTrue(reuse["call_and_put_share_observation"])
        self.assertEqual(reuse["new_external_requests"], 0)
        self.assertGreater(reuse["duplicate_payload_bytes_avoided"], 0)
        self.assertEqual(reuse["unique_payloads"], 540)
        repeated = repeated_request_keeps_payload(self.mapped)
        self.assertEqual(
            repeated.payload_sha256,
            next(item.payload_sha256 for item in self.mapped.requests if item.payload_sha256),
        )

    def test_placeholder_gate_is_not_a_pass(self):
        placeholders = [
            gate for gate in self.mapped.gates if gate.production_reported_state == "clean_true_placeholder"
        ]
        self.assertEqual(len(placeholders), 4)
        for gate in placeholders:
            self.assertEqual(gate.evaluation_state, EvaluationState.NOT_EVALUATED_EARLIER_FAILURE)
            self.assertIsNot(gate.clean, True)
            self.assertIsNone(gate.evaluated_at)

    def test_records_stay_inside_one_run(self):
        observation_ids = {item.observation_id for item in self.mapped.observations}
        direction_ids = {item.directional_evaluation_id for item in self.mapped.directions}
        self.assertEqual(len(observation_ids), len(self.mapped.observations))
        self.assertEqual(len(direction_ids), len(self.mapped.directions))
        for directional in self.mapped.directions:
            self.assertIn(directional.observation_id, observation_ids)
        for decision in self.mapped.screening:
            self.assertIn(decision.directional_evaluation_id, direction_ids)
        for gate in self.mapped.gates:
            self.assertIn(gate.directional_evaluation_id, direction_ids)
        import evidence_simulation
        self.assertNotIn("import main", Path(evidence_simulation.__file__).read_text())


class ScenarioStatusTests(unittest.TestCase):
    def test_six_runs_keep_status_dimensions_separate(self):
        runs = scenario_runs()
        three = runs["complete_three_deep"]
        self.assertEqual(three.execution_status, ExecutionStatus.COMPLETED)
        self.assertEqual(three.capture_completeness, CaptureCompleteness.COMPLETE)
        self.assertEqual(three.stage_accounts[2].expected_attempts, 3)
        self.assertEqual(three.stage_accounts[2].capacity_limit, 20)
        self.assertEqual(three.research_eligibility, ResearchEligibility.EXCLUDED_FROM_CANONICAL)
        after = runs["interrupted_after_screening"]
        self.assertEqual(after.execution_status, ExecutionStatus.ABANDONED)
        self.assertEqual(after.capture_completeness, CaptureCompleteness.INCOMPLETE)
        self.assertEqual(after.stage_accounts[2].interrupted, 20)
        during = runs["interrupted_during_deep"]
        self.assertEqual(during.execution_status, ExecutionStatus.TIMED_OUT)
        self.assertEqual(during.stage_accounts[2].attempted, 3)
        self.assertEqual(during.stage_accounts[2].interrupted, 17)
        editorial = runs["interrupted_before_editorial"]
        self.assertEqual(editorial.stage_accounts[3].succeeded, 6)
        self.assertEqual(editorial.stage_accounts[4].interrupted, 4)
        self.assertEqual(editorial.capture_completeness, CaptureCompleteness.INCOMPLETE)
        vendor = runs["vendor_failures_complete"]
        self.assertEqual(vendor.capture_completeness, CaptureCompleteness.COMPLETE)
        self.assertEqual(vendor.stage_accounts[0].failed, 20)
        unpublished = runs["publication_failed"]
        self.assertEqual(unpublished.execution_status, ExecutionStatus.COMPLETED)
        self.assertEqual(unpublished.capture_completeness, CaptureCompleteness.COMPLETE)
        self.assertEqual(unpublished.publication_status, PublicationStatus.UNPUBLISHED)


class ManifestAccountingTests(unittest.TestCase):
    def _checkpoint(self, run_id, sequence, previous, chunks, accounting="incremental"):
        return EvidenceCheckpointManifest(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            checkpoint_id="{0}/checkpoint/{1:04d}".format(run_id, sequence),
            acquisition_run_id=run_id,
            run_start_id=run_id + "/start",
            sequence=sequence,
            stage="quotes_captured",
            published_at=datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc),
            execution_status=ExecutionStatus.RUNNING,
            capture_completeness=CaptureCompleteness.INCOMPLETE,
            chunks=tuple(chunks),
            previous_checkpoint_id=previous,
            evidence_accounting=accounting,
        )

    def _chunk(self, key, rows, digest="a" * 64):
        return ChunkRef(
            entity="security_observation",
            object_key=key,
            sha256=digest,
            row_count=rows,
            uncompressed_byte_length=rows,
        )

    def test_repeated_chunk_is_not_double_counted(self):
        run_id = "run"
        chunk = self._chunk("runs/run/chunks/security_observation-0001.jsonl.gz", 560)
        first = self._checkpoint(run_id, 1, None, (chunk,))
        second = self._checkpoint(run_id, 2, first.checkpoint_id, (chunk,))
        self.assertEqual(accounted_chunk_rows((first, second)), 560)
        conflict = self._checkpoint(run_id, 2, first.checkpoint_id, (self._chunk(chunk.object_key, 10, "b" * 64),))
        with self.assertRaises(EvidenceContractError):
            accounted_chunk_rows((first, conflict))

    def test_cumulative_checkpoint_replaces_the_entity(self):
        run_id = "run"
        first = self._checkpoint(run_id, 1, None, (self._chunk("runs/run/a.jsonl.gz", 10),))
        replacement = self._chunk("runs/run/b.jsonl.gz", 12, "c" * 64)
        second = self._checkpoint(run_id, 2, first.checkpoint_id, (replacement,), accounting="cumulative")
        self.assertEqual(accounted_chunk_rows((first, second)), 12)

    def test_missing_chunk_rejects_a_self_consistent_terminal(self):
        mapped = build_production_shaped_run("f33acct1")
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalEvidenceStore(Path(tmp))
            store.put_run_start(mapped.run_start)
            chunk = store.put_chunk(
                mapped.run.acquisition_run_id,
                "security_observation",
                1,
                mapped.observations[:2],
            )
            checkpoint = self._checkpoint(
                mapped.run.acquisition_run_id,
                1,
                None,
                (
                    ChunkRef(
                        entity="security_observation",
                        object_key=chunk["object_key"],
                        sha256=chunk["sha256"],
                        row_count=chunk["row_count"],
                        uncompressed_byte_length=chunk["uncompressed_byte_length"],
                    ),
                ),
            )
            checkpoint = replace(
                checkpoint,
                acquisition_run_id=mapped.run.acquisition_run_id,
                run_start_id=mapped.run_start.run_start_id,
                checkpoint_id=mapped.run.acquisition_run_id + "/checkpoint/0001",
            )
            body = canonical_json_bytes(checkpoint)
            terminal = TerminalPublicationManifest(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                terminal_id=mapped.run.acquisition_run_id + "/terminal",
                acquisition_run_id=mapped.run.acquisition_run_id,
                run_start_id=mapped.run_start.run_start_id,
                checkpoint_ids=(checkpoint.checkpoint_id,),
                checkpoint_sha256=((checkpoint.checkpoint_id, sha256_bytes(body)),),
                execution_status=ExecutionStatus.COMPLETED,
                capture_completeness=CaptureCompleteness.COMPLETE,
                publication_status=PublicationStatus.TERMINALLY_PUBLISHED,
                published_at=datetime(2026, 11, 26, 16, 10, tzinfo=timezone.utc),
                catalog_object_key="catalog/{0}.json".format(mapped.run.acquisition_run_id),
            )
            with self.assertRaisesRegex(PublicationValidationError, "missing chunk"):
                validate_publication_set(
                    start=mapped.run_start,
                    checkpoints=(checkpoint,),
                    terminal=terminal,
                    checkpoint_bodies={checkpoint.checkpoint_id: body},
                    gzip_chunks={},
                )


class PublicationFailureTests(unittest.TestCase):
    def test_identical_and_conflicting_publishers(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalEvidenceStore(Path(tmp))
            errors = []

            def publish(payload):
                try:
                    store.put_bytes("runs/shared/start.json", payload)
                except ImmutableConflict:
                    errors.append("conflict")

            threads = [
                threading.Thread(target=publish, args=(b"same",)),
                threading.Thread(target=publish, args=(b"same",)),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join()
            self.assertEqual(errors, [])
            self.assertEqual(store.read_bytes("runs/shared/start.json"), b"same")
            with self.assertRaises(ImmutableConflict):
                store.put_bytes("runs/shared/start.json", b"other")

    def test_partial_file_is_not_promoted(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalEvidenceStore(Path(tmp))
            store.write_partial("runs/crash/object.json", b"partial")
            self.assertTrue(store.list_partials())
            self.assertFalse((store.root / "runs" / "crash" / "object.json").exists())
            with self.assertRaises(EvidenceContractError):
                store.put_bytes("runs/crash/object.json", b"finished")
            self.assertEqual(store.discard_partials(), 1)
            created = store.put_bytes("runs/crash/object.json", b"finished")
            self.assertTrue(created.created)
            self.assertEqual(store.list_partials(), [])

    def test_corrupt_gzip_and_terminal_without_catalog(self):
        mapped = build_production_shaped_run("f33pub01")
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalEvidenceStore(Path(tmp))
            store.put_run_start(mapped.run_start)
            key = "runs/{0}/chunks/security_observation-0001.jsonl.gz".format(mapped.run.acquisition_run_id)
            store.put_bytes(key, b"not-gzip")
            checkpoint = EvidenceCheckpointManifest(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                checkpoint_id=mapped.run.acquisition_run_id + "/checkpoint/0001",
                acquisition_run_id=mapped.run.acquisition_run_id,
                run_start_id=mapped.run_start.run_start_id,
                sequence=1,
                stage="quotes_captured",
                published_at=datetime(2026, 11, 26, 16, 0, tzinfo=timezone.utc),
                execution_status=ExecutionStatus.COMPLETED,
                capture_completeness=CaptureCompleteness.COMPLETE,
                chunks=(
                    ChunkRef(
                        entity="security_observation",
                        object_key=key,
                        sha256="d" * 64,
                        row_count=1,
                        uncompressed_byte_length=1,
                    ),
                ),
            )
            body = canonical_json_bytes(checkpoint)
            terminal = TerminalPublicationManifest(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                terminal_id=mapped.run.acquisition_run_id + "/terminal",
                acquisition_run_id=mapped.run.acquisition_run_id,
                run_start_id=mapped.run_start.run_start_id,
                checkpoint_ids=(checkpoint.checkpoint_id,),
                checkpoint_sha256=((checkpoint.checkpoint_id, sha256_bytes(body)),),
                execution_status=ExecutionStatus.COMPLETED,
                capture_completeness=CaptureCompleteness.COMPLETE,
                publication_status=PublicationStatus.TERMINALLY_PUBLISHED,
                published_at=T3,
                catalog_object_key="catalog/{0}.json".format(mapped.run.acquisition_run_id),
            )
            with self.assertRaisesRegex(PublicationValidationError, "chunk is not gzip"):
                validate_publication_set(
                    start=mapped.run_start,
                    checkpoints=(checkpoint,),
                    terminal=terminal,
                    checkpoint_bodies={checkpoint.checkpoint_id: body},
                    gzip_chunks={key: store.read_bytes(key)},
                )
            store.put_terminal(terminal, publish_catalog=False)
            self.assertTrue(store.has_terminal(mapped.run.acquisition_run_id))
            self.assertNotIn(mapped.run.acquisition_run_id, store.list_catalog_run_ids())
            store.put_terminal(terminal)
            self.assertIn(mapped.run.acquisition_run_id, store.list_catalog_run_ids())
            self.assertIn(mapped.run.acquisition_run_id, store.discover_run_ids())

    def test_history_survives_without_the_latest_pointer(self):
        mapped = build_production_shaped_run("f33ptr01")
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalEvidenceStore(Path(tmp))
            store.put_run_start(mapped.run_start)
            catalog_key = "catalog/{0}.json".format(mapped.run.acquisition_run_id)
            store.put_bytes(catalog_key, b"{}")
            store.put_pointer(
                mapped.run.acquisition_role.value,
                mapped.run.acquisition_run_id,
                catalog_key,
                0,
            )
            pointer = store.root / "pointers" / "latest-production.json"
            pointer.unlink()
            self.assertIn(mapped.run.acquisition_run_id, store.discover_run_ids())
            with self.assertRaises(GenerationPreconditionError):
                store.put_pointer(
                    mapped.run.acquisition_role.value,
                    "other-run",
                    catalog_key,
                    5,
                )
            self.assertTrue(any("ifGenerationMatch=0" in item for item in CLOUD_ADAPTER_REQUIREMENTS))

    def test_checkpoint_sequence_gaps_are_rejected(self):
        mapped = build_production_shaped_run("f33seq01")
        first = EvidenceCheckpointManifest(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            checkpoint_id=mapped.run.acquisition_run_id + "/checkpoint/0001",
            acquisition_run_id=mapped.run.acquisition_run_id,
            run_start_id=mapped.run_start.run_start_id,
            sequence=1,
            stage="run_started",
            published_at=T1,
            execution_status=ExecutionStatus.RUNNING,
            capture_completeness=CaptureCompleteness.INCOMPLETE,
            chunks=(),
        )
        skipped = replace(
            first,
            checkpoint_id=mapped.run.acquisition_run_id + "/checkpoint/0003",
            sequence=3,
            previous_checkpoint_id=first.checkpoint_id,
            published_at=T2,
        )
        terminal = TerminalPublicationManifest(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            terminal_id=mapped.run.acquisition_run_id + "/terminal",
            acquisition_run_id=mapped.run.acquisition_run_id,
            run_start_id=mapped.run_start.run_start_id,
            checkpoint_ids=(first.checkpoint_id, skipped.checkpoint_id),
            checkpoint_sha256=(
                (first.checkpoint_id, sha256_bytes(canonical_json_bytes(first))),
                (skipped.checkpoint_id, sha256_bytes(canonical_json_bytes(skipped))),
            ),
            execution_status=ExecutionStatus.ABANDONED,
            capture_completeness=CaptureCompleteness.INCOMPLETE,
            publication_status=PublicationStatus.TERMINALLY_PUBLISHED,
            published_at=T3,
            catalog_object_key="catalog/{0}.json".format(mapped.run.acquisition_run_id),
        )
        with self.assertRaisesRegex(PublicationValidationError, "contiguous"):
            validate_publication_set(
                start=mapped.run_start,
                checkpoints=(skipped, first),
                terminal=terminal,
                checkpoint_bodies={
                    first.checkpoint_id: canonical_json_bytes(first),
                    skipped.checkpoint_id: canonical_json_bytes(skipped),
                },
                gzip_chunks={},
            )


class BudgetAndPopulationTests(unittest.TestCase):
    def _budget(self, **overrides):
        values = dict(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            budget_id="budget-f33",
            shared_enforcement_implemented=False,
            max_requests_per_run=2,
            max_requests_per_day=5,
            max_requests_per_month=20,
            spend_currency="USD",
        )
        values.update(overrides)
        return AcquisitionBudget(**values)

    def _proposal(self, **overrides):
        values = dict(
            endpoint="quote",
            vendor="synthetic-quote",
            cache_hit=False,
            retry=False,
            cost_known=True,
            duration_seconds=0.1,
            actual_cost=0,
            cost_currency="USD",
            external_requests=1,
        )
        values.update(overrides)
        return ProposedAcquisition(**values)

    def test_caps_fail_closed_and_blocked_requests_are_not_acquisitions(self):
        budget = self._budget(max_quota_units=10, max_actual_spend=1, max_retry_count=1, max_deep_securities=1, max_execution_duration_seconds=1, approved_endpoints=("quote",))
        counters = BudgetCounters(requests_this_run=1, quota_known=True, quota_units_consumed=9)
        oversized = evaluate_budget(budget, counters, self._proposal(external_requests=5))
        self.assertEqual(oversized.status, BudgetDecisionStatus.BLOCKED_REQUEST_BUDGET)
        self.assertEqual(committed_external_requests(oversized), 0)
        unknown_quota = evaluate_budget(
            budget,
            BudgetCounters(quota_known=False),
            self._proposal(quota_units=1, cost_known=True),
        )
        self.assertEqual(unknown_quota.detail, "quota_unknown")
        money = self._budget(max_actual_spend=1)
        mismatch = evaluate_budget(money, BudgetCounters(), self._proposal(cost_currency="EUR", quota_units=1))
        self.assertEqual(mismatch.detail, "currency_mismatch")
        missing_currency = evaluate_budget(money, BudgetCounters(), self._proposal(cost_currency=None, quota_units=1))
        self.assertEqual(missing_currency.detail, "currency_missing")
        expired = self._budget(
            max_actual_spend=1,
            approved_entitlements=(EntitlementPolicy("ent-1", True, expires_at=T0),),
        )
        blocked = evaluate_budget(
            expired,
            BudgetCounters(),
            self._proposal(cost_known=False, actual_cost=None, entitlement_id="ent-1", cost_currency=None),
            as_of=T1,
        )
        self.assertEqual(blocked.status, BudgetDecisionStatus.BLOCKED_UNKNOWN_COST)
        cache = evaluate_budget(budget, counters, self._proposal(cache_hit=True, external_requests=5))
        self.assertEqual(cache.external_request_increment, 0)
        retry = evaluate_budget(
            self._budget(max_retry_count=0),
            BudgetCounters(),
            self._proposal(retry=True),
        )
        self.assertEqual(retry.status, BudgetDecisionStatus.BLOCKED_RETRY_LIMIT)
        self.assertEqual(committed_external_requests(retry), 0)

    def test_local_ledger_is_process_local(self):
        ledger = LocalBudgetLedger(self._budget(max_requests_per_run=1), BudgetCounters())
        self.assertFalse(ledger.shared_enforcement_implemented)
        results = []

        def acquire():
            results.append(ledger.try_acquire(self._proposal()).status)

        threads = [threading.Thread(target=acquire) for _ in range(2)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(item.value for item in results), ["allowed", "blocked_request_budget"])

    def test_research_populations_stay_isolated_from_the_canonical_fingerprint(self):
        first = research_population_examples(T0)
        second = research_population_examples(T0)
        self.assertEqual(first["fingerprints"], second["fingerprints"])
        self.assertEqual(len(set(first["fingerprints"].values())), len(first["fingerprints"]))
        self.assertEqual(first["holdout_discovery_symbols"], ())
        self.assertNotIn("S007", first["holdout_discovery_symbols"])
        self.assertNotIn("S008", first["holdout_discovery_symbols"])
        for manifest in first["manifests"].values():
            self.assertFalse(manifest.bound_to_production_drawdown_rule)
            self.assertFalse(manifest.approved_acquisition)
            self.assertNotIn("drawdown", manifest.sampling_frame)
            self.assertNotEqual(manifest.population_fingerprint_sha256, CANONICAL_ELIGIBLE_POPULATION_HASH)
        self.assertEqual(CANONICAL_ELIGIBLE_POPULATION_COUNT, 227)

    def test_local_fixture_measurement_is_labeled(self):
        mapped = build_production_shaped_run("f33time1")
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalEvidenceStore(Path(tmp))
            measured = measure_local_publication(mapped, store)
        self.assertEqual(measured["label"], "local_fixture_not_production_latency")
        self.assertTrue(measured["recovered_matches"])
        self.assertGreater(measured["uncompressed_bytes"], measured["compressed_bytes"])
        self.assertEqual(measured["observation_rows"], 560)
        print("LOCAL_FIXTURE_MEASUREMENT {0}".format(measured))


class EvidenceModuleBoundaryTests(unittest.TestCase):
    def test_simulation_does_not_reference_production_entrypoints(self):
        text = Path(__file__).with_name("evidence_simulation.py").read_text()
        for forbidden in ("import main", "fmp_get", "finnhub_get", "send_alert", "firestore"):
            self.assertNotIn(forbidden, text)


if __name__ == "__main__":
    unittest.main()
