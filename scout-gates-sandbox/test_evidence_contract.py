#!/usr/bin/env python3
"""Synthetic tests for evidence-contract-1. No production I/O."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import evidence_economics as economics
from evidence_contract import (
    CANONICAL_DERIVED_BUILD_IDS,
    CANONICAL_ELIGIBLE_POPULATION_COUNT,
    CANONICAL_ELIGIBLE_POPULATION_HASH,
    CONTRACT_ENTITIES,
    EVIDENCE_SCHEMA_VERSION,
    FUTURE_RESEARCH_UTILITY_METRICS,
    INSPECTED_PYTHON_SOURCE_SHA256,
    INSPECTED_SOURCE_ARCHIVE_SHA256,
    AcquisitionRole,
    AcquisitionRun,
    CaptureCompleteness,
    CaptureOrigin,
    ChunkRef,
    CostProvenance,
    DerivedBuildReference,
    Direction,
    DirectionalEvaluation,
    EditorialDecision,
    EmailClassification,
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
    HoldoutStatus,
    InstrumentEvent,
    InstrumentIdSource,
    MarketOutcome,
    MethodologyRole,
    OutcomeAnchor,
    OutcomeKind,
    OUTCOME_METHODOLOGY_BY_KIND,
    PayloadBytesKind,
    Pipeline,
    PopulationMember,
    PopulationRole,
    PricingModel,
    PublicationStatus,
    RankingDecision,
    ResearchEligibility,
    RunStartRecord,
    SamplingKind,
    ScoreEvaluation,
    ScreeningDecision,
    SecurityObservation,
    SourceArtifactKind,
    StageAccount,
    StrategyEvaluation,
    TerminalPublicationManifest,
    TimeSource,
    UniverseMembership,
    VendorRequest,
    VendorRequestState,
    assert_can_coexist,
    assert_historically_available,
    assert_text_free_of,
    build_population_manifest,
    canonical_json_bytes,
    cite_canonical_population,
    contract_inventory,
    discovery_members,
    is_decisive_evaluation,
    make_acquisition_run_id,
    params_fingerprint,
    payload_identity,
    predictive_roi_from_observations,
    provisional_pilot_manifest,
    record_to_dict,
    sanitize_params,
    sanitize_url,
    validate,
    validate_acquisition_run,
)
from evidence_economics import (
    RESERVATION_REQUIREMENT,
    SHARED_BUDGET_ENFORCEMENT_IMPLEMENTED,
    AcquisitionBudget,
    AcquisitionTelemetry,
    BudgetCounters,
    BudgetDecisionStatus,
    EntitlementPolicy,
    NamedAllowance,
    ProposedAcquisition,
    derive_acquisition_metrics,
    evaluate_budget,
    reservation_plan,
    summarize_vendor_requests,
)
from evidence_publication import (
    ImmutableConflict,
    GenerationPreconditionError,
    PublicationValidationError,
    LocalEvidenceStore,
    assert_safe_store_root,
    gzip_deterministic,
    gunzip_bytes,
    jsonl_bytes,
    sha256_bytes,
    validate_publication_set,
)
from migrate_derived_builds import EXPECTED_ELIGIBLE_HASH
from observation_evidence import population_stamp
from scoring_provenance import (
    DEPLOYMENT_VERIFICATION_STATUS,
    INSPECTED_SOURCE_SHA256,
)


T0 = datetime(2026, 11, 26, 14, 49, tzinfo=timezone.utc)
T1 = T0 + timedelta(seconds=2)
T2 = T0 + timedelta(minutes=5)
T3 = T0 + timedelta(minutes=6)
SYNTHETIC_DIGEST = "ab" * 32
API_KEY = "SYNTHETIC_API_KEY_f32"
BEARER = "SYNTHETIC_BEARER_TOKEN"
SIGNED_SECRET = "SYNTHETIC_SIGNED_URL_SECRET"
SUBSCRIBER = "subscriber@example.test"


def _complete_stages():
    return (
        StageAccount("quotes", 560, 560, 560, 0, 0, 0),
        StageAccount("screening", 560, 560, 3, 557, 0, 0),
        StageAccount("deep_evaluation", 3, 3, 3, 0, 0, 0, capacity_limit=20),
    )


def _partial_stages():
    return (
        StageAccount("quotes", 560, 560, 560, 0, 0, 0),
        StageAccount("screening", 560, 560, 20, 540, 0, 0),
        StageAccount("deep_evaluation", 20, 3, 3, 0, 0, 17, capacity_limit=20),
    )


def _run(
    nonce,
    stages,
    execution,
    completeness,
    *,
    role=AcquisitionRole.PRODUCTION,
    pipeline=Pipeline.PRODUCTION_BATCH,
    eligibility=ResearchEligibility.EXCLUDED_FROM_CANONICAL,
    attempt=1,
    started=T0,
    publication=PublicationStatus.UNPUBLISHED,
    digest=INSPECTED_PYTHON_SOURCE_SHA256,
    kind=SourceArtifactKind.PYTHON_SOURCE,
    verified=False,
    methodology_role=MethodologyRole.ACQUISITION,
    methodology_id="production-batch-v6-2026-09-28",
    derivation=EvidenceDerivation.CAPTURED,
):
    run_id = make_acquisition_run_id(
        acquisition_role=role,
        pipeline=pipeline,
        started_at=started,
        attempt_number=attempt,
        execution_nonce=nonce,
    )
    return AcquisitionRun(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        acquisition_run_id=run_id,
        execution_nonce=nonce,
        attempt_number=attempt,
        acquisition_role=role,
        pipeline=pipeline,
        capture_origin=CaptureOrigin.SYNTHETIC,
        execution_status=execution,
        capture_completeness=completeness,
        publication_status=publication,
        research_eligibility=eligibility,
        methodology_role=methodology_role,
        methodology_id=methodology_id,
        evidence_derivation=derivation,
        source_identity="synthetic-fixture",
        source_artifact_kind=kind,
        source_digest_sha256=digest,
        deployment_verified=verified,
        deployment_revision="friday-scout-00055-4kw",
        universe_manifest_id="universe-fixture",
        started_at=started,
        finished_at=T2 if execution == ExecutionStatus.COMPLETED else None,
        stage_accounts=stages,
    )


def _start_for(run):
    return RunStartRecord(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        run_start_id=run.acquisition_run_id + "/start",
        acquisition_run_id=run.acquisition_run_id,
        acquisition_role=run.acquisition_role,
        pipeline=run.pipeline,
        execution_nonce=run.execution_nonce,
        attempt_number=run.attempt_number,
        started_at=run.started_at,
        universe_manifest_id=run.universe_manifest_id,
    )


def _request(run_id, symbol, state, external, *, retry=0, payload=None, received=None, retrieved=None):
    params = {"symbol": symbol}
    return VendorRequest(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        vendor_request_id="{0}:fmp:quote:{1}:{2}".format(run_id, symbol, retry),
        acquisition_run_id=run_id,
        vendor="fmp",
        endpoint="quote",
        sanitized_params=params,
        params_sha256=params_fingerprint(params),
        request_started_at=T0,
        request_state=state,
        external_request=external,
        retry_ordinal=retry,
        payload_bytes_kind=PayloadBytesKind.PARSED_OBJECT_ONLY if payload else PayloadBytesKind.ABSENT,
        byte_exact=False,
        response_received_at=received,
        cache_retrieved_at=retrieved,
        payload_sha256=payload,
        payload_ref="payloads/{0}.json".format(payload) if payload else None,
        http_status=200 if state == VendorRequestState.SUCCESSFUL_RESPONSE else None,
    )


def _observation(run_id, symbol):
    return SecurityObservation(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        observation_id="{0}:{1}".format(run_id, symbol),
        acquisition_run_id=run_id,
        symbol_as_observed=symbol,
        membership_id="universe-fixture:{0}:1".format(symbol),
        quote_state=VendorRequestState.SUCCESSFUL_RESPONSE,
        quote_as_of_source=TimeSource.UNAVAILABLE,
        evidence_derivation=EvidenceDerivation.CAPTURED,
        methodology_id="production-batch-v6-2026-09-28",
        missing_reasons=(("quote_as_of", "vendor_effective_time_unavailable"),),
        quote_request_id="{0}:fmp:quote:{1}:0".format(run_id, symbol),
        price=100.0,
        year_high=120.0,
        year_low=80.0,
        volume=1000000.0,
        change_percentage=-1.5,
        quote_as_of=None,
        response_received_at=T1,
    )


def _direction(observation_id, direction, methodology, *, role=MethodologyRole.GATE_EVALUATION, derivation=EvidenceDerivation.CAPTURED):
    return DirectionalEvaluation(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        directional_evaluation_id="{0}:{1}:{2}".format(observation_id, direction.value, methodology),
        observation_id=observation_id,
        direction=direction,
        methodology_id=methodology,
        methodology_role=role,
        evidence_derivation=derivation,
        evaluation_state=EvaluationState.EVALUATED_PASSED,
        acquisition_role=AcquisitionRole.RESEARCH,
    )


def _feature(**overrides):
    values = dict(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        feature_observation_id="feature-rsi-captured",
        observation_id="run:AAPL",
        feature_name="rsi_14",
        feature_derivation=FeatureDerivation.VENDOR_RAW,
        evidence_derivation=EvidenceDerivation.CAPTURED,
        methodology_id="feature-extract-v1",
        source_request_ids=("req-rsi",),
        vendor_effective_source=TimeSource.UNAVAILABLE,
        freshness=Freshness.UNKNOWN,
        freshness_basis=FreshnessBasis.UNAVAILABLE,
        missing_reasons=(),
        value_number=28.0,
        response_received_at=T1,
    )
    values.update(overrides)
    return FeatureObservation(**values)


def _outcome(**overrides):
    kind = overrides.get("outcome_kind", OutcomeKind.UNDERLYING_RETURN)
    values = dict(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        outcome_id="outcome-1",
        subject_id="subject-1",
        anchor_type=OutcomeAnchor.COMPLETED_RANKING,
        outcome_methodology_id=OUTCOME_METHODOLOGY_BY_KIND[kind],
        outcome_kind=kind,
        horizon_sessions=5,
        entry_at=T2,
        evidence_derivation=EvidenceDerivation.CAPTURED,
        formula_strike_used=False,
        contract_verified=False,
        chain_present=False,
        value=None,
        missing_reason="not_calculated",
        decision_available_at=T2,
    )
    values.update(overrides)
    if "outcome_kind" in overrides and "outcome_methodology_id" not in overrides:
        values["outcome_methodology_id"] = OUTCOME_METHODOLOGY_BY_KIND[values["outcome_kind"]]
    return MarketOutcome(**values)


def _member(symbol, holdout, probability=0.25, stratum="liquidity-high"):
    return PopulationMember(
        symbol_as_observed=symbol,
        holdout_status=holdout,
        inclusion_reason="declared before requests",
        sampling_methodology_id="sampling-stratified-v1",
        stratum_id=stratum,
        selection_probability=probability,
    )


def _telemetry(**overrides):
    run_id = overrides.get("acquisition_run_id", "run-1")
    vendor = overrides.get("vendor", "fmp")
    endpoint = overrides.get("endpoint", "quote")
    values = dict(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        telemetry_id="{0}:{1}:{2}".format(run_id, vendor, endpoint),
        acquisition_run_id=run_id,
        endpoint=endpoint,
        vendor=vendor,
        external_request_count=4,
        cache_hit_count=1,
        cache_miss_count=4,
        retry_count=1,
        failed_request_count=0,
        billable_known=False,
        quota_known=False,
        cost_known=False,
        pricing_model=PricingModel.UNKNOWN,
        cost_provenance=CostProvenance.UNKNOWN,
        request_duration_ms=10,
        execution_duration_ms=20,
        payload_bytes=100,
        unique_securities=4,
        unique_usable_observations=4,
        completed_evaluations=2,
        redundant_acquisitions=1,
    )
    values.update(overrides)
    values["telemetry_id"] = "{0}:{1}:{2}".format(
        values["acquisition_run_id"], values["vendor"], values["endpoint"]
    )
    return AcquisitionTelemetry(**values)


def _proposal(**overrides):
    values = dict(
        endpoint="quote",
        vendor="fmp",
        cache_hit=False,
        retry=False,
        cost_known=False,
        duration_seconds=1,
    )
    values.update(overrides)
    return ProposedAcquisition(**values)


class IdentityAndProvenanceTests(unittest.TestCase):
    def test_contract_inventory_has_twenty_entities(self):
        self.assertEqual(contract_inventory(), CONTRACT_ENTITIES)
        self.assertEqual(len(CONTRACT_ENTITIES), 20)

    def test_separate_runs_in_the_same_minute(self):
        first = make_acquisition_run_id(
            acquisition_role=AcquisitionRole.PRODUCTION,
            pipeline=Pipeline.PRODUCTION_BATCH,
            started_at=T0,
            attempt_number=1,
            execution_nonce="nonceone1",
        )
        second = make_acquisition_run_id(
            acquisition_role=AcquisitionRole.PRODUCTION,
            pipeline=Pipeline.PRODUCTION_BATCH,
            started_at=T0,
            attempt_number=1,
            execution_nonce="noncetwo2",
        )
        retry = make_acquisition_run_id(
            acquisition_role=AcquisitionRole.PRODUCTION,
            pipeline=Pipeline.PRODUCTION_BATCH,
            started_at=T0,
            attempt_number=2,
            execution_nonce="nonceone1",
        )
        research = make_acquisition_run_id(
            acquisition_role=AcquisitionRole.RESEARCH,
            pipeline=Pipeline.RESEARCH_ACQUISITION,
            started_at=T0,
            attempt_number=1,
            execution_nonce="nonceone1",
        )
        self.assertNotEqual(first, second)
        self.assertNotEqual(first, retry)
        self.assertNotEqual(first, research)
        self.assertEqual(first, make_acquisition_run_id(
            acquisition_role=AcquisitionRole.PRODUCTION,
            pipeline=Pipeline.PRODUCTION_BATCH,
            started_at=T0,
            attempt_number=1,
            execution_nonce="nonceone1",
        ))

    def test_repeated_ticker_across_runs_stays_distinct(self):
        first = _run("nonceone1", _complete_stages(), ExecutionStatus.COMPLETED, CaptureCompleteness.COMPLETE)
        second = _run("noncetwo2", _complete_stages(), ExecutionStatus.COMPLETED, CaptureCompleteness.COMPLETE)
        validate(first)
        validate(second)
        left = _observation(first.acquisition_run_id, "AAPL")
        right = _observation(second.acquisition_run_id, "AAPL")
        validate(left)
        validate(right)
        self.assertNotEqual(left.observation_id, right.observation_id)
        self.assertEqual(left.symbol_as_observed, right.symbol_as_observed)

    def test_call_and_put_share_one_quote_payload(self):
        run = _run("nonceone1", _complete_stages(), ExecutionStatus.COMPLETED, CaptureCompleteness.COMPLETE)
        payload, exact = payload_identity(
            kind=PayloadBytesKind.PARSED_OBJECT_ONLY, parsed={"symbol": "AAPL", "price": 100}
        )
        self.assertFalse(exact)
        request = _request(
            run.acquisition_run_id,
            "AAPL",
            VendorRequestState.SUCCESSFUL_RESPONSE,
            True,
            payload=payload,
            received=T1,
        )
        validate(request)
        observation = _observation(run.acquisition_run_id, "AAPL")
        validate(observation)
        methodology = "production-batch-v6-2026-09-28"
        call = _direction(observation.observation_id, Direction.CALL, methodology)
        put = _direction(observation.observation_id, Direction.PUT, methodology)
        validate(call)
        validate(put)
        self.assertNotEqual(call.directional_evaluation_id, put.directional_evaluation_id)
        self.assertEqual(call.observation_id, put.observation_id)
        self.assertEqual(request.payload_sha256, payload)

    def test_unknown_instrument_identity_is_explicit(self):
        membership = UniverseMembership(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            membership_id="universe-fixture:AAPL:1",
            universe_manifest_id="universe-fixture",
            acquisition_run_id="ignored-by-membership-id",
            symbol_as_observed="AAPL",
            instrument_id_source=InstrumentIdSource.UNAVAILABLE,
            instrument_event=InstrumentEvent.NONE,
            registration_ordinal=1,
            sector_as_registered="TECH",
            sector_resolved="TECH",
            wind_resolved=0,
            resolution_rule="last_register_wins",
            instrument_id=None,
        )
        validate(membership)
        self.assertIsNone(membership.instrument_id)
        invented = UniverseMembership(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            membership_id="universe-fixture:AAPL:1",
            universe_manifest_id="universe-fixture",
            acquisition_run_id="ignored-by-membership-id",
            symbol_as_observed="AAPL",
            instrument_id_source=InstrumentIdSource.UNAVAILABLE,
            instrument_event=InstrumentEvent.NONE,
            registration_ordinal=1,
            sector_as_registered="TECH",
            sector_resolved="TECH",
            wind_resolved=0,
            resolution_rule="last_register_wins",
            instrument_id="invented",
        )
        with self.assertRaises(EvidenceContractError):
            validate(invented)

    def test_captured_evidence_under_experimental_methodology(self):
        observation = _observation("run-1", "AAPL")
        experimental = _direction(
            observation.observation_id,
            Direction.NONE,
            "experiment-direction-v0",
            role=MethodologyRole.EXPERIMENTAL,
            derivation=EvidenceDerivation.CAPTURED,
        )
        validate(experimental)
        self.assertEqual(experimental.evidence_derivation, EvidenceDerivation.CAPTURED)
        self.assertEqual(experimental.methodology_role, MethodologyRole.EXPERIMENTAL)

    def test_reconstructed_cannot_overwrite_captured(self):
        captured = _feature()
        reconstructed = _feature(
            feature_observation_id="feature-rsi-reconstructed",
            evidence_derivation=EvidenceDerivation.RECONSTRUCTED,
            value_number=30.0,
            supersedes_feature_observation_id=captured.feature_observation_id,
            feature_derivation=FeatureDerivation.CALCULATED,
        )
        validate(captured)
        validate(reconstructed)
        assert_can_coexist(captured, reconstructed)
        replacement = _feature(value_number=99.0)
        with self.assertRaises(EvidenceContractError):
            assert_can_coexist(captured, replacement)
        self.assertEqual(captured.value_number, 28.0)

    def test_unverified_deployment_stays_unverified(self):
        self.assertEqual(INSPECTED_SOURCE_SHA256, INSPECTED_PYTHON_SOURCE_SHA256)
        self.assertNotEqual(INSPECTED_PYTHON_SOURCE_SHA256, INSPECTED_SOURCE_ARCHIVE_SHA256)
        self.assertEqual(DEPLOYMENT_VERIFICATION_STATUS, "unverified")
        run = _run("nonceone1", _complete_stages(), ExecutionStatus.COMPLETED, CaptureCompleteness.COMPLETE)
        validate(run)
        self.assertFalse(run.deployment_verified)
        self.assertEqual(run.deployment_revision, "friday-scout-00055-4kw")
        with self.assertRaises(EvidenceContractError):
            validate(_run(
                "nonceone1",
                _complete_stages(),
                ExecutionStatus.COMPLETED,
                CaptureCompleteness.COMPLETE,
                verified=True,
            ))
        with self.assertRaises(EvidenceContractError):
            validate(_run(
                "nonceone1",
                _complete_stages(),
                ExecutionStatus.COMPLETED,
                CaptureCompleteness.COMPLETE,
                kind=SourceArtifactKind.SOURCE_ARCHIVE,
            ))
        with self.assertRaises(EvidenceContractError):
            validate(_run(
                "nonceone1",
                _complete_stages(),
                ExecutionStatus.COMPLETED,
                CaptureCompleteness.COMPLETE,
                digest=INSPECTED_SOURCE_ARCHIVE_SHA256,
                kind=SourceArtifactKind.PYTHON_SOURCE,
                pipeline=Pipeline.RESEARCH_ACQUISITION,
                role=AcquisitionRole.RESEARCH,
                eligibility=ResearchEligibility.NOT_ASSESSED,
            ))

    def test_naive_timestamp_is_rejected(self):
        with self.assertRaises(EvidenceContractError):
            validate(_run(
                "nonceone1",
                (),
                ExecutionStatus.STARTED,
                CaptureCompleteness.NOT_ASSESSED,
                role=AcquisitionRole.RESEARCH,
                pipeline=Pipeline.RESEARCH_ACQUISITION,
                eligibility=ResearchEligibility.NOT_ASSESSED,
                digest=SYNTHETIC_DIGEST,
                kind=SourceArtifactKind.UNKNOWN,
                started=datetime(2026, 11, 26, 14, 49),
            ))


class EvaluationSemanticsTests(unittest.TestCase):
    def _gate(self, state, **overrides):
        values = dict(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            gate_evaluation_id="gate-1",
            directional_evaluation_id="obs:CALL:method",
            gate_name="gate4",
            evaluation_state=state,
            evidence_derivation=EvidenceDerivation.CAPTURED,
            missing_reasons=(),
            score=1.0,
            evaluated_at=T1,
        )
        values.update(overrides)
        return GateEvaluation(**values)

    def test_pass_fail_missing_and_truncation(self):
        passed = self._gate(EvaluationState.EVALUATED_PASSED, clean=True)
        failed = self._gate(EvaluationState.EVALUATED_FAILED, gate_name="gate2", score=0.0)
        missing = self._gate(
            EvaluationState.EVALUATED_MISSING_INPUTS,
            score=None,
            missing_reasons=(("score", "rsi_missing"),),
        )
        truncated = ScreeningDecision(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            screening_decision_id="screen-1",
            directional_evaluation_id="obs:CALL:method",
            stage="top10_cut",
            decision_state=EvaluationState.NOT_SELECTED_RANKING_TRUNCATION,
            decided_at=T1,
            measured_value=11.0,
            rank_before_cut=11,
            cut_limit=10,
        )
        validate(passed)
        validate(failed)
        validate(missing)
        validate(truncated)
        self.assertTrue(is_decisive_evaluation(passed.evaluation_state))
        self.assertTrue(is_decisive_evaluation(failed.evaluation_state))
        self.assertFalse(is_decisive_evaluation(missing.evaluation_state))
        self.assertFalse(is_decisive_evaluation(truncated.decision_state))

    def test_earlier_failure_and_skipped_gate4_placeholder(self):
        earlier = self._gate(
            EvaluationState.NOT_EVALUATED_EARLIER_FAILURE,
            score=None,
            clean=None,
            evaluated_at=None,
            production_reported_state="clean_true_placeholder",
            missing_reasons=(("score", "not_evaluated"),),
        )
        validate(earlier)
        self.assertFalse(is_decisive_evaluation(earlier.evaluation_state))
        self.assertEqual(earlier.production_reported_state, "clean_true_placeholder")
        posed_as_pass = self._gate(
            EvaluationState.EVALUATED_PASSED,
            production_reported_state="clean_true_placeholder",
            clean=True,
        )
        with self.assertRaises(EvidenceContractError):
            validate(posed_as_pass)

    def test_vendor_failure_is_distinct_from_empty_response_and_cache_hit(self):
        run_id = "run-vendor"
        payload, _exact = payload_identity(
            kind=PayloadBytesKind.PARSED_OBJECT_ONLY, parsed={"symbol": "AAPL"}
        )
        failure = _request(
            run_id, "AAPL", VendorRequestState.HTTP_FAILURE, True, received=T1
        )
        empty = _request(
            run_id, "MSFT", VendorRequestState.EMPTY_RESPONSE, True, received=T1
        )
        cached = _request(
            run_id,
            "NVDA",
            VendorRequestState.CACHE_HIT,
            False,
            payload=payload,
            received=T1,
            retrieved=T2,
        )
        validate(failure)
        validate(empty)
        validate(cached)
        self.assertNotEqual(type(failure.request_state), EvaluationState)
        counts = summarize_vendor_requests((failure, empty, cached))
        self.assertEqual(counts["external_request_count"], 2)
        self.assertEqual(counts["cache_hit_count"], 1)
        self.assertEqual(counts["failed_request_count"], 1)
        self.assertNotIn(payload, ())


class CompletenessAndPublicationTests(unittest.TestCase):
    def test_three_deep_evaluations_can_be_complete(self):
        run = _run(
            "nonceone1",
            _complete_stages(),
            ExecutionStatus.COMPLETED,
            CaptureCompleteness.COMPLETE,
        )
        validate_acquisition_run(run)
        deep = run.stage_accounts[2]
        self.assertEqual(deep.succeeded, 3)
        self.assertEqual(deep.capacity_limit, 20)
        self.assertLess(deep.succeeded, deep.capacity_limit)
        self.assertEqual(run.capture_completeness, CaptureCompleteness.COMPLETE)

    def test_same_observed_counts_are_partial_when_interrupted(self):
        partial = _run(
            "nonceone1",
            _partial_stages(),
            ExecutionStatus.TIMED_OUT,
            CaptureCompleteness.INCOMPLETE,
        )
        validate(partial)
        self.assertEqual(partial.stage_accounts[0].succeeded, 560)
        self.assertEqual(partial.stage_accounts[2].succeeded, 3)
        self.assertEqual(partial.stage_accounts[2].interrupted, 17)
        self.assertEqual(partial.capture_completeness, CaptureCompleteness.INCOMPLETE)
        with self.assertRaises(EvidenceContractError):
            validate(_run(
                "nonceone1",
                _partial_stages(),
                ExecutionStatus.COMPLETED,
                CaptureCompleteness.COMPLETE,
            ))

    def test_started_run_has_no_terminal_and_checkpoints_do_not_overwrite(self):
        started = _run(
            "nonceone1",
            (),
            ExecutionStatus.STARTED,
            CaptureCompleteness.NOT_ASSESSED,
            role=AcquisitionRole.RESEARCH,
            pipeline=Pipeline.RESEARCH_ACQUISITION,
            eligibility=ResearchEligibility.NOT_ASSESSED,
            digest=SYNTHETIC_DIGEST,
            kind=SourceArtifactKind.UNKNOWN,
            methodology_id="horizon-research-v0",
        )
        validate(started)
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalEvidenceStore(Path(tmp))
            start = _start_for(started)
            first = store.put_run_start(start)
            second = store.put_run_start(start)
            self.assertTrue(first.created)
            self.assertTrue(second.identical_retry)
            self.assertIn(started.acquisition_run_id, store.discover_run_ids())
            self.assertNotIn(started.acquisition_run_id, store.list_catalog_run_ids())
            self.assertFalse(store.has_terminal(started.acquisition_run_id))
            self.assertFalse((store.root / "catalog" / "runs.jsonl").exists())

            observation = _observation(started.acquisition_run_id, "AAPL")
            chunk = store.put_chunk(
                started.acquisition_run_id,
                "security_observation",
                1,
                (observation,),
            )
            first_checkpoint = EvidenceCheckpointManifest(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                checkpoint_id=started.acquisition_run_id + "/checkpoint/0001",
                acquisition_run_id=started.acquisition_run_id,
                run_start_id=start.run_start_id,
                sequence=1,
                stage="quotes_captured",
                published_at=T1,
                execution_status=ExecutionStatus.RUNNING,
                capture_completeness=CaptureCompleteness.INCOMPLETE,
                chunks=(
                    ChunkRef(
                        entity="security_observation",
                        object_key=chunk["object_key"],
                        sha256=chunk["sha256"],
                        row_count=chunk["row_count"],
                        uncompressed_byte_length=chunk["uncompressed_byte_length"],
                    ),
                ),
            )
            second_checkpoint = EvidenceCheckpointManifest(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                checkpoint_id=started.acquisition_run_id + "/checkpoint/0002",
                acquisition_run_id=started.acquisition_run_id,
                run_start_id=start.run_start_id,
                sequence=2,
                stage="editorial",
                previous_checkpoint_id=first_checkpoint.checkpoint_id,
                published_at=T2,
                execution_status=ExecutionStatus.RUNNING,
                capture_completeness=CaptureCompleteness.INCOMPLETE,
                chunks=(),
            )
            store.put_checkpoint(first_checkpoint)
            store.put_checkpoint(second_checkpoint)
            retry = store.put_checkpoint(first_checkpoint)
            self.assertTrue(retry.identical_retry)
            bodies = {
                first_checkpoint.checkpoint_id: canonical_json_bytes(first_checkpoint),
                second_checkpoint.checkpoint_id: canonical_json_bytes(second_checkpoint),
            }
            terminal = TerminalPublicationManifest(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                terminal_id=started.acquisition_run_id + "/terminal",
                acquisition_run_id=started.acquisition_run_id,
                run_start_id=start.run_start_id,
                checkpoint_ids=(first_checkpoint.checkpoint_id, second_checkpoint.checkpoint_id),
                checkpoint_sha256=tuple(
                    (checkpoint_id, sha256_bytes(body))
                    for checkpoint_id, body in bodies.items()
                ),
                execution_status=ExecutionStatus.COMPLETED,
                capture_completeness=CaptureCompleteness.COMPLETE,
                publication_status=PublicationStatus.TERMINALLY_PUBLISHED,
                published_at=T3,
                catalog_object_key="catalog/{0}.json".format(started.acquisition_run_id),
            )
            validate_publication_set(
                start=start,
                checkpoints=(first_checkpoint, second_checkpoint),
                terminal=terminal,
                checkpoint_bodies=bodies,
                gzip_chunks={chunk["object_key"]: store.read_bytes(chunk["object_key"])},
            )
            store.put_terminal(terminal)
            store.put_terminal(terminal)
            self.assertTrue(store.has_terminal(started.acquisition_run_id))
            self.assertIn(started.acquisition_run_id, store.list_catalog_run_ids())
            self.assertFalse((store.root / "catalog" / "runs.jsonl").exists())

            pointer = store.put_pointer(
                "research",
                started.acquisition_run_id,
                terminal.catalog_object_key,
                0,
            )
            self.assertTrue(pointer.created)
            again = store.put_pointer(
                "research",
                started.acquisition_run_id,
                terminal.catalog_object_key,
                0,
            )
            self.assertTrue(again.identical_retry)
            with self.assertRaises(GenerationPreconditionError):
                store.put_pointer("research", "other-run", terminal.catalog_object_key, 0)
            store.put_pointer("research", "other-run", "catalog/other-run.json", 1)
            pointer_path = store.root / "pointers" / "latest-research.json"
            pointer_path.unlink()
            self.assertIn(started.acquisition_run_id, store.discover_run_ids())

            with self.assertRaisesRegex(PublicationValidationError, "missing chunk"):
                validate_publication_set(
                    start=start,
                    checkpoints=(first_checkpoint, second_checkpoint),
                    terminal=terminal,
                    checkpoint_bodies=bodies,
                    gzip_chunks={},
                )
            bad_checksum = ChunkRef(
                entity="security_observation",
                object_key=chunk["object_key"],
                sha256="c" * 64,
                row_count=chunk["row_count"],
                uncompressed_byte_length=chunk["uncompressed_byte_length"],
            )
            bad_checkpoint = EvidenceCheckpointManifest(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                checkpoint_id=started.acquisition_run_id + "/checkpoint/0001",
                acquisition_run_id=started.acquisition_run_id,
                run_start_id=start.run_start_id,
                sequence=1,
                stage="quotes_captured",
                published_at=T1,
                execution_status=ExecutionStatus.RUNNING,
                capture_completeness=CaptureCompleteness.INCOMPLETE,
                chunks=(bad_checksum,),
            )
            with self.assertRaises(ImmutableConflict):
                store.put_checkpoint(bad_checkpoint)
            with self.assertRaisesRegex(PublicationValidationError, "incorrect checksum"):
                validate_publication_set(
                    start=start,
                    checkpoints=(bad_checkpoint,),
                    terminal=TerminalPublicationManifest(
                        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                        terminal_id=started.acquisition_run_id + "/terminal",
                        acquisition_run_id=started.acquisition_run_id,
                        run_start_id=start.run_start_id,
                        checkpoint_ids=(bad_checkpoint.checkpoint_id,),
                        checkpoint_sha256=((
                            bad_checkpoint.checkpoint_id,
                            sha256_bytes(canonical_json_bytes(bad_checkpoint)),
                        ),),
                        execution_status=ExecutionStatus.FAILED,
                        capture_completeness=CaptureCompleteness.INCOMPLETE,
                        publication_status=PublicationStatus.TERMINALLY_PUBLISHED,
                        published_at=T3,
                        catalog_object_key=terminal.catalog_object_key,
                    ),
                    checkpoint_bodies={
                        bad_checkpoint.checkpoint_id: canonical_json_bytes(bad_checkpoint)
                    },
                    gzip_chunks={chunk["object_key"]: store.read_bytes(chunk["object_key"])},
                )
            wrong_count = ChunkRef(
                entity="security_observation",
                object_key=chunk["object_key"],
                sha256=chunk["sha256"],
                row_count=99,
                uncompressed_byte_length=chunk["uncompressed_byte_length"],
            )
            count_checkpoint = EvidenceCheckpointManifest(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                checkpoint_id=started.acquisition_run_id + "/checkpoint/0001",
                acquisition_run_id=started.acquisition_run_id,
                run_start_id=start.run_start_id,
                sequence=1,
                stage="quotes_captured",
                published_at=T1,
                execution_status=ExecutionStatus.RUNNING,
                capture_completeness=CaptureCompleteness.INCOMPLETE,
                chunks=(wrong_count,),
            )
            with self.assertRaisesRegex(PublicationValidationError, "incorrect row count"):
                validate_publication_set(
                    start=start,
                    checkpoints=(count_checkpoint,),
                    terminal=TerminalPublicationManifest(
                        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                        terminal_id=started.acquisition_run_id + "/terminal",
                        acquisition_run_id=started.acquisition_run_id,
                        run_start_id=start.run_start_id,
                        checkpoint_ids=(count_checkpoint.checkpoint_id,),
                        checkpoint_sha256=((
                            count_checkpoint.checkpoint_id,
                            sha256_bytes(canonical_json_bytes(count_checkpoint)),
                        ),),
                        execution_status=ExecutionStatus.FAILED,
                        capture_completeness=CaptureCompleteness.INCOMPLETE,
                        publication_status=PublicationStatus.TERMINALLY_PUBLISHED,
                        published_at=T3,
                        catalog_object_key=terminal.catalog_object_key,
                    ),
                    checkpoint_bodies={
                        count_checkpoint.checkpoint_id: canonical_json_bytes(count_checkpoint)
                    },
                    gzip_chunks={chunk["object_key"]: store.read_bytes(chunk["object_key"])},
                )
            with self.assertRaisesRegex(PublicationValidationError, "duplicate entity identity"):
                store.put_chunk(
                    started.acquisition_run_id,
                    "security_observation",
                    2,
                    (observation, observation),
                )
            payload = b'{"price":100}'
            store.put_bytes("payloads/shared.json", payload)
            shared = store.put_bytes("payloads/shared.json", payload)
            self.assertTrue(shared.identical_retry)
            with self.assertRaises(ImmutableConflict):
                store.put_bytes("payloads/shared.json", b'{"price":101}')
            self.assertEqual(store.read_bytes("payloads/shared.json"), payload)
            self.assertEqual(gzip_deterministic(b"abc"), gzip_deterministic(b"abc"))
            self.assertEqual(gunzip_bytes(gzip_deterministic(jsonl_bytes([{"a": 1}]))), jsonl_bytes([{"a": 1}]))

    def test_store_refuses_protected_directories(self):
        with self.assertRaises(EvidenceContractError):
            assert_safe_store_root(Path(__file__).resolve().parent)
        with self.assertRaises(EvidenceContractError):
            assert_safe_store_root(Path.home() / "Scout Evidence Archive")
        with self.assertRaises(EvidenceContractError):
            assert_safe_store_root(Path.home() / "Scout Evidence Archive" / "staging")


class PointInTimeTests(unittest.TestCase):
    def test_missing_vendor_effective_time_is_not_replaced(self):
        feature = _feature()
        validate(feature)
        self.assertIsNone(feature.vendor_effective_at)
        self.assertEqual(feature.vendor_effective_source, TimeSource.UNAVAILABLE)
        copied = _feature(vendor_effective_at=feature.response_received_at)
        with self.assertRaises(EvidenceContractError):
            validate(copied)
        receipt_fresh = _feature(
            freshness=Freshness.FRESH,
            freshness_basis=FreshnessBasis.RECEIPT_TIME,
        )
        with self.assertRaises(EvidenceContractError):
            validate(receipt_fresh)

    def test_cached_receipt_is_preserved_and_future_data_is_rejected(self):
        cached = _feature(cache_retrieved_at=T3)
        validate(cached)
        usable = assert_historically_available(cached, T2)
        self.assertTrue(usable["usable"])
        self.assertFalse(usable["vendor_effective_safe"])
        self.assertEqual(usable["acquired_at"], T1)
        self.assertEqual(usable["cache_retrieved_at"], T3)
        future = _feature(response_received_at=T3)
        with self.assertRaises(EvidenceContractError):
            assert_historically_available(future, T2)

    def test_outcome_entry_cannot_precede_decision_availability(self):
        with self.assertRaises(EvidenceContractError):
            validate(_outcome(entry_at=T0, decision_available_at=T2))
        quote = _outcome(
            anchor_type=OutcomeAnchor.QUOTE_OBSERVATION,
            observation_at=T1,
            entry_at=T0,
            decision_available_at=None,
        )
        with self.assertRaises(EvidenceContractError):
            validate(quote)
        allowed = _outcome()
        validate(allowed)

    def test_revised_feature_is_preserved_separately(self):
        original = _feature()
        revised = _feature(
            feature_observation_id="feature-rsi-revised",
            evidence_derivation=EvidenceDerivation.RECONSTRUCTED,
            feature_derivation=FeatureDerivation.CALCULATED,
            value_number=31.0,
            supersedes_feature_observation_id=original.feature_observation_id,
            source_request_ids=("req-rsi", "req-bars"),
        )
        validate(revised)
        with tempfile.TemporaryDirectory() as tmp:
            store = LocalEvidenceStore(Path(tmp))
            store.put_bytes("features/original.json", canonical_json_bytes(original))
            before = store.read_bytes("features/original.json")
            store.put_bytes("features/revised.json", canonical_json_bytes(revised))
            self.assertEqual(store.read_bytes("features/original.json"), before)
            self.assertNotEqual(before, store.read_bytes("features/revised.json"))


class SecurityTests(unittest.TestCase):
    def test_synthetic_secrets_are_redacted(self):
        params = sanitize_params(
            {
                "symbol": "AAPL",
                "apikey": API_KEY,
                "Authorization": "Bearer {0}".format(BEARER),
                "email": SUBSCRIBER,
                "url": "https://files.example.test/obj?symbol=AAPL&X-Goog-Signature={0}".format(
                    SIGNED_SECRET
                ),
            }
        )
        self.assertEqual(params["apikey"], "[redacted]")
        self.assertNotIn("Authorization", params)
        self.assertNotIn("email", params)
        self.assertNotIn(SIGNED_SECRET, params["url"])
        self.assertIn("symbol=AAPL", params["url"])
        signed = sanitize_url(
            "https://user:{0}@files.example.test/obj?symbol=MSFT&X-Goog-Credential={1}".format(
                API_KEY, SIGNED_SECRET
            )
        )
        self.assertNotIn(API_KEY, signed)
        self.assertNotIn(SIGNED_SECRET, signed)
        self.assertIn("symbol=MSFT", signed)
        apple = params_fingerprint(sanitize_params({"symbol": "AAPL", "apikey": API_KEY}))
        other_key = params_fingerprint(sanitize_params({"symbol": "AAPL", "apikey": "OTHER"}))
        microsoft = params_fingerprint(sanitize_params({"symbol": "MSFT", "apikey": API_KEY}))
        self.assertEqual(apple, other_key)
        self.assertNotEqual(apple, microsoft)
        request = _request(
            "run-secret",
            "AAPL",
            VendorRequestState.SUCCESSFUL_RESPONSE,
            True,
            received=T1,
        )
        request = VendorRequest(
            evidence_schema_version=request.evidence_schema_version,
            vendor_request_id=request.vendor_request_id,
            acquisition_run_id=request.acquisition_run_id,
            vendor=request.vendor,
            endpoint=request.endpoint,
            sanitized_params=sanitize_params({"symbol": "AAPL", "apikey": API_KEY}),
            params_sha256=params_fingerprint(sanitize_params({"symbol": "AAPL", "apikey": API_KEY})),
            request_started_at=request.request_started_at,
            request_state=request.request_state,
            external_request=True,
            retry_ordinal=0,
            payload_bytes_kind=PayloadBytesKind.ABSENT,
            byte_exact=False,
            response_received_at=T1,
        )
        validate(request)
        serialized = canonical_json_bytes(request).decode("utf-8")
        assert_text_free_of(serialized, (API_KEY, BEARER, SIGNED_SECRET, SUBSCRIBER))
        self.assertIn("AAPL", serialized)


class EconomicsTests(unittest.TestCase):
    def test_unknown_cost_stays_null_and_known_zero_stays_zero(self):
        unknown = _telemetry(unique_usable_observations=4, completed_evaluations=2)
        metrics = derive_acquisition_metrics(unknown)
        self.assertIsNone(unknown.actual_cost)
        self.assertIsNone(metrics.cost_per_usable_observation)
        self.assertIsNone(metrics.cost_per_completed_evaluation)
        self.assertIsNone(metrics.cost_per_matured_outcome)
        zero = _telemetry(
            cost_known=True,
            actual_cost=0,
            cost_currency="USD",
            cost_provenance=CostProvenance.EXPLICIT_ZERO_MARGINAL,
            pricing_model=PricingModel.SUBSCRIPTION,
            unique_usable_observations=4,
            completed_evaluations=2,
            matured_outcome_count=4,
        )
        zero_metrics = derive_acquisition_metrics(zero)
        self.assertEqual(zero_metrics.cost_per_usable_observation, 0.0)
        self.assertEqual(zero_metrics.cost_per_completed_evaluation, 0.0)
        self.assertEqual(zero_metrics.cost_per_matured_outcome, 0.0)
        empty = _telemetry(
            external_request_count=3,
            cache_hit_count=0,
            cache_miss_count=0,
            retry_count=0,
            unique_usable_observations=0,
            unique_securities=0,
            completed_evaluations=0,
        )
        empty_metrics = derive_acquisition_metrics(empty)
        self.assertIsNone(empty_metrics.external_requests_per_usable_observation)
        self.assertIsNone(empty_metrics.external_requests_per_completed_evaluation)
        self.assertIsNone(empty_metrics.cache_hit_rate)
        self.assertIsNone(empty_metrics.usable_observation_rate)
        self.assertFalse(hasattr(economics, "VENDOR_PRICES"))
        self.assertFalse(hasattr(economics, "PRICE_BOOK"))

    def test_budget_decisions(self):
        daily = AcquisitionBudget(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            budget_id="budget-daily",
            max_requests_per_day=1,
            max_requests_per_month=10,
        )
        blocked_day = evaluate_budget(
            daily, BudgetCounters(requests_today=1, requests_this_month=1), _proposal()
        )
        self.assertEqual(blocked_day.status, BudgetDecisionStatus.BLOCKED_REQUEST_BUDGET)
        self.assertEqual(blocked_day.detail, "max_requests_per_day")
        monthly = AcquisitionBudget(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            budget_id="budget-month",
            max_requests_per_month=2,
        )
        blocked_month = evaluate_budget(
            monthly, BudgetCounters(requests_this_month=2), _proposal()
        )
        self.assertEqual(blocked_month.status, BudgetDecisionStatus.BLOCKED_REQUEST_BUDGET)
        self.assertEqual(blocked_month.detail, "max_requests_per_month")
        capped = AcquisitionBudget(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            budget_id="budget-cap",
            max_requests_per_run=1,
            max_quota_units=1,
            approved_endpoints=("quote",),
        )
        cache_hit = evaluate_budget(
            capped,
            BudgetCounters(requests_this_run=1, quota_units_consumed=1),
            _proposal(cache_hit=True),
        )
        self.assertEqual(cache_hit.status, BudgetDecisionStatus.ALLOWED)
        self.assertEqual(cache_hit.external_request_increment, 0)
        self.assertEqual(cache_hit.quota_increment, 0)
        external = evaluate_budget(
            capped,
            BudgetCounters(requests_this_run=1),
            _proposal(),
        )
        self.assertEqual(external.status, BudgetDecisionStatus.BLOCKED_REQUEST_BUDGET)
        retry = evaluate_budget(
            capped,
            BudgetCounters(quota_units_consumed=0),
            _proposal(retry=True, quota_units=1),
        )
        self.assertEqual(retry.status, BudgetDecisionStatus.ALLOWED)
        self.assertEqual(retry.external_request_increment, 1)
        self.assertEqual(retry.quota_increment, 1)
        retry_blocked = evaluate_budget(
            capped,
            BudgetCounters(quota_units_consumed=1),
            _proposal(retry=True, quota_units=1),
        )
        self.assertEqual(retry_blocked.status, BudgetDecisionStatus.BLOCKED_QUOTA_BUDGET)
        spend = AcquisitionBudget(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            budget_id="budget-spend",
            max_actual_spend=10,
            spend_currency="USD",
            approved_entitlements=(
                EntitlementPolicy("entitlement-open", True),
                EntitlementPolicy("entitlement-closed", False),
            ),
            approved_endpoints=("quote",),
            vendor_allowances=(NamedAllowance("fmp", 100),),
        )
        unknown_cost = evaluate_budget(spend, BudgetCounters(), _proposal(cost_known=False))
        self.assertEqual(unknown_cost.status, BudgetDecisionStatus.BLOCKED_UNKNOWN_COST)
        known_zero = evaluate_budget(
            spend,
            BudgetCounters(),
            _proposal(cost_known=True, actual_cost=0),
        )
        self.assertEqual(known_zero.status, BudgetDecisionStatus.ALLOWED)
        permitted = evaluate_budget(
            spend,
            BudgetCounters(),
            _proposal(cost_known=False, entitlement_id="entitlement-open"),
        )
        self.assertEqual(permitted.status, BudgetDecisionStatus.ALLOWED)
        unapproved = evaluate_budget(
            spend,
            BudgetCounters(),
            _proposal(endpoint="insider-trading", cache_hit=True),
        )
        self.assertEqual(unapproved.status, BudgetDecisionStatus.BLOCKED_UNAPPROVED_ENDPOINT)
        plan = reservation_plan()
        self.assertFalse(SHARED_BUDGET_ENFORCEMENT_IMPLEMENTED)
        self.assertFalse(plan["implemented"])
        self.assertIn("Process-local", RESERVATION_REQUIREMENT)
        self.assertIn("atomic reservation", RESERVATION_REQUIREMENT)
        self.assertFalse(known_zero.reservation_implemented)
        self.assertEqual(known_zero.reservation_scope, "process_local")
        with self.assertRaises(EvidenceContractError):
            predictive_roi_from_observations([])
        self.assertIn(
            "incremental_out_of_sample_predictive_performance",
            FUTURE_RESEARCH_UTILITY_METRICS,
        )


class PopulationAndOutcomeTests(unittest.TestCase):
    def test_population_identity_holdout_and_canonical_boundary(self):
        selected = datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)
        members = (
            _member("AAA", HoldoutStatus.DISCOVERY, 0.5, "sector-a"),
            _member("BBB", HoldoutStatus.HOLDOUT, 0.5, "sector-a"),
        )
        manifest = build_population_manifest(
            population_role=PopulationRole.RESEARCH_COHORT,
            sampling_kind=SamplingKind.STRATIFIED,
            sampling_methodology_id="sampling-stratified-v1",
            sampling_frame="liquidity and sector frame",
            selection_timestamp=selected,
            members=members,
            methodology_id="horizon-research-v0",
            random_seed="seed-1",
            hypothesis_id="hypothesis-gate-value",
        )
        again = build_population_manifest(
            population_role=PopulationRole.RESEARCH_COHORT,
            sampling_kind=SamplingKind.STRATIFIED,
            sampling_methodology_id="sampling-stratified-v1",
            sampling_frame="liquidity and sector frame",
            selection_timestamp=selected,
            members=members,
            methodology_id="horizon-research-v0",
            random_seed="seed-1",
            hypothesis_id="hypothesis-gate-value",
        )
        self.assertEqual(manifest.population_id, again.population_id)
        other_method = build_population_manifest(
            population_role=PopulationRole.RESEARCH_COHORT,
            sampling_kind=SamplingKind.STRATIFIED,
            sampling_methodology_id="sampling-stratified-v1",
            sampling_frame="liquidity and sector frame",
            selection_timestamp=selected,
            members=members,
            methodology_id="another-methodology",
            random_seed="seed-1",
            hypothesis_id="hypothesis-gate-value",
        )
        self.assertNotEqual(manifest.population_id, other_method.population_id)
        self.assertNotEqual(
            manifest.population_fingerprint_sha256, CANONICAL_ELIGIBLE_POPULATION_HASH
        )
        discovered = discovery_members(manifest)
        self.assertEqual(tuple(member.symbol_as_observed for member in discovered), ("AAA",))
        serialized = record_to_dict(manifest)
        stored = serialized["members"]
        by_symbol = {member["symbol_as_observed"]: member for member in stored}
        self.assertEqual(by_symbol["BBB"]["stratum_id"], "sector-a")
        self.assertEqual(by_symbol["BBB"]["selection_probability"], 0.5)
        self.assertEqual(by_symbol["BBB"]["holdout_status"], "holdout")
        pilot = provisional_pilot_manifest(selected)
        self.assertTrue(pilot.provisional)
        self.assertFalse(pilot.approved_acquisition)
        self.assertFalse(pilot.permanent_definition)
        self.assertFalse(pilot.bound_to_production_drawdown_rule)
        self.assertEqual(pilot.symbol_count, 20)
        self.assertNotIn("drawdown", pilot.sampling_frame)
        with self.assertRaises(EvidenceContractError):
            build_population_manifest(
                population_role=PopulationRole.RESEARCH_COHORT,
                sampling_kind=SamplingKind.REPRESENTATIVE,
                sampling_methodology_id="sampling-v1",
                sampling_frame="production drawdown rule",
                selection_timestamp=selected,
                members=(_member("AAA", HoldoutStatus.DISCOVERY),),
                methodology_id="horizon-research-v0",
                provisional=False,
                approved_acquisition=True,
                permanent_definition=True,
                bound_to_production_drawdown_rule=True,
            )
        citation = cite_canonical_population(selected)
        self.assertEqual(citation.symbol_count, CANONICAL_ELIGIBLE_POPULATION_COUNT)
        self.assertEqual(citation.population_fingerprint_sha256, CANONICAL_ELIGIBLE_POPULATION_HASH)
        self.assertEqual(citation.members, ())
        self.assertFalse(citation.approved_acquisition)
        reference = DerivedBuildReference(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            build_id=sorted(CANONICAL_DERIVED_BUILD_IDS)[0],
            population_fingerprint_sha256=CANONICAL_ELIGIBLE_POPULATION_HASH,
            methodology_id="b0-2026-09-28",
            artifact_type="pattern_intelligence",
            status="COMPLETED",
            publishes_new_rows=False,
        )
        validate(reference)
        with self.assertRaises(EvidenceContractError):
            validate(DerivedBuildReference(
                evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
                build_id="new-build",
                population_fingerprint_sha256=CANONICAL_ELIGIBLE_POPULATION_HASH,
                methodology_id="b0-2026-09-28",
                artifact_type="pattern_intelligence",
                status="COMPLETED",
                publishes_new_rows=False,
            ))

    def test_outcome_kinds_horizons_and_formula_strikes(self):
        self.assertEqual(len(set(OUTCOME_METHODOLOGY_BY_KIND.values())), len(OUTCOME_METHODOLOGY_BY_KIND))
        for sessions in (1, 3, 5, 10, 20):
            validate(_outcome(horizon_sessions=sessions, outcome_id="outcome-{0}".format(sessions)))
        with self.assertRaises(EvidenceContractError):
            validate(_outcome(horizon_sessions=2))
        strategy = StrategyEvaluation(
            evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
            strategy_evaluation_id="strategy-1",
            directional_evaluation_id="obs:CALL:method",
            strategy_methodology_id="strategy-formula-v1",
            is_formula_strike=True,
            contract_verified=False,
            chain_present=False,
            chain_establishes_execution=False,
            conservative_strike=95.0,
            risky_strike=100.0,
        )
        validate(strategy)
        with self.assertRaises(EvidenceContractError):
            validate(_outcome(
                outcome_kind=OutcomeKind.OPTION_CONTRACT,
                formula_strike_used=True,
            ))
        with self.assertRaises(EvidenceContractError):
            validate(_outcome(
                outcome_kind=OutcomeKind.OPTION_CONTRACT,
                chain_present=True,
                value=0.2,
                missing_reason=None,
            ))
        missing_contract = _outcome(
            outcome_kind=OutcomeKind.OPTION_CONTRACT,
            chain_present=True,
            missing_reason="no_verified_contract",
        )
        validate(missing_contract)
        self.assertIsNone(missing_contract.value)

    def test_canonical_population_is_unchanged(self):
        self.assertEqual(EXPECTED_ELIGIBLE_HASH, CANONICAL_ELIGIBLE_POPULATION_HASH)
        database = Path(__file__).resolve().parent / "scout_memory.db"
        digest = hashlib.sha256(database.read_bytes()).hexdigest()
        self.assertEqual(
            digest,
            "9024d966956afb13a2aa9b8942001be2494ad6437f348f587dca26cd970be2b4",
        )
        connection = sqlite3.connect("file:{0}?mode=ro&immutable=1".format(database), uri=True)
        try:
            stamp = population_stamp(connection)
            eligible = connection.execute(
                "select count(*) from observation_provenance where research_eligible = 1"
            ).fetchone()[0]
            features = connection.execute("select count(*) from feature_vectors").fetchone()[0]
            scoring = connection.execute("select count(*) from scoring_decompositions").fetchone()[0]
            builds = connection.execute(
                "select build_id from derived_builds where status = 'COMPLETED'"
            ).fetchall()
        finally:
            connection.close()
        self.assertEqual(stamp.eligible_population_count, 227)
        self.assertEqual(stamp.eligible_population_hash, CANONICAL_ELIGIBLE_POPULATION_HASH)
        self.assertEqual(eligible, 227)
        self.assertEqual(features, 273)
        self.assertEqual(scoring, 227)
        self.assertEqual({row[0] for row in builds}, set(CANONICAL_DERIVED_BUILD_IDS))
        self.assertEqual(
            hashlib.sha256(database.read_bytes()).hexdigest(),
            digest,
        )


class ArchiveUntouchedTests(unittest.TestCase):
    def test_historical_archives_unchanged(self):
        expected = {
            Path.home() / "Scout Evidence Archive/backfill-exports/scout-daily-reports.zip":
                "57d508e5e69805382b71d93d6391bf721459d2174c5d556e3eb70fc0bbb10fe4",
            Path.home() / "Scout Evidence Archive/staging/scout_historical_reports_f24_20261127T015034Z.db":
                "f3454018c7d029f0a60e7c2913d8d440078c1445b6a065672a18cca549d9c9eb",
            Path.home() / "Scout Evidence Archive/staging/scout_historical_reports_f25_parser3.db":
                "bb313ce8dd1fc0690455d08bed9a7d2246e0413cb4e05982a7ea1957a9116e62",
        }
        for path, digest in expected.items():
            actual = hashlib.sha256(path.read_bytes()).hexdigest()
            self.assertEqual(actual, digest)


if __name__ == "__main__":
    unittest.main()
