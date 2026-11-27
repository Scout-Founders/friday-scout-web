"""Production-shaped evidence fixtures. No production code and no market data.

The inputs copy the inspected batch shapes: a registration list, a quote dict
keyed by ticker, gate-0 predicates, a top-10 cut, a top-7 cut, and a Gate 4
``clean: True`` placeholder that means the gate did not run. Values are
synthetic. They are not observations from a live scan.
"""

import copy
import time
import tracemalloc
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple

from evidence_contract import (
    AcquisitionRole,
    AcquisitionRun,
    CaptureCompleteness,
    CaptureOrigin,
    ChunkRef,
    Direction,
    EditorialDecision,
    EmailClassification,
    EvidenceCheckpointManifest,
    EvidenceContractError,
    EvidenceDerivation,
    EVIDENCE_SCHEMA_VERSION,
    EvaluationState,
    ExecutionStatus,
    FeatureDerivation,
    FeatureObservation,
    Freshness,
    FreshnessBasis,
    GateEvaluation,
    INSPECTED_PYTHON_SOURCE_SHA256,
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
    TimeSource,
    UniverseManifest,
    UniverseMembership,
    VendorRequest,
    VendorRequestState,
    assert_can_coexist,
    build_population_manifest,
    canonical_json_bytes,
    discovery_members,
    make_acquisition_run_id,
    params_fingerprint,
    record_to_dict,
    security_observation_id,
    sha256_bytes,
    validate,
)
from evidence_publication import gzip_deterministic, jsonl_bytes

SCHEMA = EVIDENCE_SCHEMA_VERSION
METHODOLOGY = "scout-batch-synthetic-v0"
SOURCE_IDENTITY = "synthetic-production-shape"
STARTED = datetime(2026, 11, 26, 14, 49, tzinfo=timezone.utc)
DECIDED = STARTED + timedelta(minutes=2)
REPORT_ID = "synthetic-report-f33"
EXCHANGE = "XNAS"
UNIQUE_COUNT = 560
REGISTRATION_COUNT = 562

# Inspected Gate 0 thresholds. This module does not import production code.
CALL_MIN_PRICE = 10
CALL_MIN_VOLUME = 100000
CALL_MAX_DRAWDOWN = -15
PUT_MIN_RUNUP = 40
PUT_MAX_BELOW_HIGH = 15
TOP10 = 10
TOP7 = 7
PUBLISHED = 6


def _symbol(index: int) -> str:
    return "S{0:03d}".format(index)


def _kind(index: int) -> str:
    if index >= 550:
        return "timeout"
    if index >= 540:
        return "empty"
    if index < 30:
        return "low_price"
    if index < 50:
        return "low_volume"
    if index < 60:
        return "missing_high"
    if index < 70:
        return "missing_low"
    if index < 90:
        return "call_pool"
    if index < 110:
        return "put_pool"
    return "neutral"


def _quote(index: int) -> Optional[Dict[str, float]]:
    kind = _kind(index)
    if kind in ("timeout", "empty"):
        return None
    if kind == "low_price":
        return {"price": 8, "yearHigh": 20, "yearLow": 7, "volume": 500000, "changePercentage": -1}
    if kind == "low_volume":
        return {"price": 20, "yearHigh": 30, "yearLow": 10, "volume": 1000, "changePercentage": -1}
    if kind == "missing_high":
        return {"price": 20, "yearLow": 18, "volume": 500000, "changePercentage": 1}
    if kind == "missing_low":
        return {"price": 20, "yearHigh": 22, "volume": 500000, "changePercentage": -1}
    if kind == "call_pool":
        offset = index - 70
        high = 100.0
        price = high * (1 + (-40 + offset) / 100)
        return {
            "price": price,
            "yearHigh": high,
            "yearLow": price * 0.95,
            "volume": 1000000,
            "changePercentage": -2,
        }
    if kind == "put_pool":
        offset = index - 90
        price = 100.0
        run_up = 180 - offset
        low = price / (1 + run_up / 100)
        high = price / 0.98
        return {
            "price": price,
            "yearHigh": high,
            "yearLow": low,
            "volume": 1000000,
            "changePercentage": 3,
        }
    return {"price": 50, "yearHigh": 55, "yearLow": 45, "volume": 500000, "changePercentage": 0}


CALL_PLAN = {
    "S070": "early_exit",
    "S071": "wind_block",
    "S072": "gate2_fail",
    "S073": "gate2_fail",
    "S074": "passer",
    "S075": "passer",
    "S076": "passer",
    "S077": "passer",
    "S078": "passer",
    "S079": "top7_cut",
}
PUT_PLAN = {
    "S090": "early_exit",
    "S091": "wind_block",
    "S092": "gate2_fail",
    "S093": "gate2_fail",
    "S094": "passer",
    "S095": "passer",
    "S096": "passer",
    "S097": "passer",
    "S098": "passer",
    "S099": "top7_cut",
}
PASSER_SCORE = {
    "S074": 90,
    "S075": 88,
    "S076": 86,
    "S077": 80,
    "S078": 79,
    "S094": 85,
    "S095": 84,
    "S096": 83,
    "S097": 78,
    "S098": 77,
}


def synthetic_production_inputs() -> Dict[str, Any]:
    """Source-shaped objects. The mapper must not mutate this structure."""
    registrations = []
    quotes: Dict[str, Any] = {}
    sectors = ("SYN_TECH", "SYN_HEALTH", "SYN_FINANCE", "SYN_ENERGY")
    winds = {"SYN_TECH": 0, "SYN_HEALTH": 1, "SYN_FINANCE": -1, "SYN_ENERGY": 0}
    for index in range(UNIQUE_COUNT):
        symbol = _symbol(index)
        sector = sectors[index % 4]
        if symbol == "S071":
            sector = "SYNTHETIC_HEADWIND"
        elif symbol == "S091":
            sector = "SYNTHETIC_TAILWIND"
        wind = {"SYNTHETIC_HEADWIND": -3, "SYNTHETIC_TAILWIND": 2}.get(symbol and sector, winds.get(sector, 0))
        registrations.append(
            {
                "ticker": symbol,
                "exchange": EXCHANGE,
                "sector": sector,
                "wind": wind,
                "ordinal": 1,
            }
        )
        quote = _quote(index)
        if quote is not None:
            quotes[symbol] = quote
        elif _kind(index) == "timeout":
            quotes[symbol] = {"error": "timeout"}
        else:
            quotes[symbol] = {}
    for symbol, sector in (("S000", "CLOUD_PLATFORM"), ("S001", "CLOUD_PLATFORM")):
        registrations.append(
            {
                "ticker": symbol,
                "exchange": EXCHANGE,
                "sector": sector,
                "wind": -2,
                "ordinal": 2,
            }
        )
    return {
        "registrations": registrations,
        "quotes": quotes,
        "report_text": "SYNTHETIC REPORT\n" + ("S074 CALL synthetic passer\n" * 400),
        "claude_output": "Synthetic editorial text. Not a model response.",
    }


def _stage(
    name: str,
    expected: int,
    attempted: int,
    succeeded: int,
    failed: int,
    excluded: int = 0,
    interrupted: int = 0,
    capacity: Optional[int] = None,
) -> StageAccount:
    return StageAccount(
        stage=name,
        expected_attempts=expected,
        attempted=attempted,
        succeeded=succeeded,
        failed=failed,
        excluded_resolved=excluded,
        interrupted=interrupted,
        capacity_limit=capacity if capacity is not None else expected,
    )


def _run(
    nonce: str,
    stages: Sequence[StageAccount],
    execution: ExecutionStatus,
    publication: PublicationStatus,
    finished: Optional[datetime],
) -> AcquisitionRun:
    run_id = make_acquisition_run_id(
        acquisition_role=AcquisitionRole.PRODUCTION,
        pipeline=Pipeline.PRODUCTION_BATCH,
        started_at=STARTED,
        attempt_number=1,
        execution_nonce=nonce,
    )
    assessed_stages = tuple(stages)
    from evidence_contract import assess_capture_completeness

    completeness = assess_capture_completeness(execution, assessed_stages)
    run = AcquisitionRun(
        evidence_schema_version=SCHEMA,
        acquisition_run_id=run_id,
        execution_nonce=nonce,
        attempt_number=1,
        acquisition_role=AcquisitionRole.PRODUCTION,
        pipeline=Pipeline.PRODUCTION_BATCH,
        capture_origin=CaptureOrigin.SYNTHETIC,
        execution_status=execution,
        capture_completeness=completeness,
        publication_status=publication,
        research_eligibility=ResearchEligibility.EXCLUDED_FROM_CANONICAL,
        methodology_role=MethodologyRole.ACQUISITION,
        methodology_id=METHODOLOGY,
        evidence_derivation=EvidenceDerivation.CAPTURED,
        source_identity=SOURCE_IDENTITY,
        source_artifact_kind=SourceArtifactKind.PYTHON_SOURCE,
        source_digest_sha256=INSPECTED_PYTHON_SOURCE_SHA256,
        deployment_verified=False,
        deployment_revision="friday-scout-00055-4kw",
        universe_manifest_id="universe:synthetic-f33",
        started_at=STARTED,
        stage_accounts=assessed_stages,
        finished_at=finished,
    )
    validate(run)
    return run


def full_stage_accounts() -> Tuple[StageAccount, ...]:
    """562 quote slots, 20 names entering deep, 10 passers, 6 published."""
    return (
        _stage("quotes", 562, 562, 542, 20, capacity=562),
        _stage("screening", 540, 540, 20, 520, capacity=540),
        _stage("deep_evaluation", 20, 18, 10, 8, excluded=2, capacity=20),
        _stage("ranking", 10, 10, 6, 4, capacity=10),
        _stage("editorial", 4, 4, 4, 0, capacity=4),
    )


@dataclass
class MappedRun:
    run: AcquisitionRun
    run_start: RunStartRecord
    universe: UniverseManifest
    memberships: Tuple[UniverseMembership, ...]
    requests: Tuple[VendorRequest, ...]
    observations: Tuple[SecurityObservation, ...]
    directions: Tuple[Any, ...]
    screening: Tuple[ScreeningDecision, ...]
    gates: Tuple[GateEvaluation, ...]
    features: Tuple[FeatureObservation, ...]
    scores: Tuple[ScoreEvaluation, ...]
    rankings: Tuple[RankingDecision, ...]
    strategies: Tuple[StrategyEvaluation, ...]
    editorial: Tuple[EditorialDecision, ...]
    outcomes: Tuple[MarketOutcome, ...]
    payloads: Dict[str, bytes]
    reconciliation: Dict[str, Any]
    claude_input: str
    claude_output: str
    inputs_unchanged: bool


def _membership_id(manifest_id: str, symbol: str, ordinal: int, exchange: str) -> str:
    base = "{0}:{1}:{2}".format(manifest_id, symbol, ordinal)
    if exchange:
        return "{0}:{1}".format(base, exchange)
    return base


def _screen_call(quote: Mapping[str, Any]) -> Dict[str, Any]:
    price = quote.get("price", 0) or 0
    high = quote.get("yearHigh", 0) or 0
    volume = quote.get("volume", 0) or 0
    if price < CALL_MIN_PRICE:
        return {"state": EvaluationState.EVALUATED_FAILED, "reason": "low_price", "measured": price, "threshold": CALL_MIN_PRICE}
    if volume < CALL_MIN_VOLUME:
        return {"state": EvaluationState.EVALUATED_FAILED, "reason": "low_volume", "measured": volume, "threshold": CALL_MIN_VOLUME}
    if not quote.get("yearHigh"):
        return {"state": EvaluationState.EVALUATED_MISSING_INPUTS, "reason": "no_data", "missing": ("yearHigh", "vendor_field_absent")}
    drawdown = ((price - high) / high) * 100
    if drawdown > CALL_MAX_DRAWDOWN:
        return {"state": EvaluationState.EVALUATED_FAILED, "reason": "small_drawdown", "measured": drawdown, "threshold": CALL_MAX_DRAWDOWN}
    return {"state": EvaluationState.EVALUATED_PASSED, "reason": "gate0_pass", "measured": drawdown, "threshold": CALL_MAX_DRAWDOWN}


def _screen_put(quote: Mapping[str, Any]) -> Dict[str, Any]:
    price = quote.get("price", 0) or 0
    high = quote.get("yearHigh", 0) or 0
    low = quote.get("yearLow", 0) or 0
    volume = quote.get("volume", 0) or 0
    if price < CALL_MIN_PRICE:
        return {"state": EvaluationState.EVALUATED_FAILED, "reason": "low_price", "measured": price, "threshold": CALL_MIN_PRICE}
    if volume < CALL_MIN_VOLUME:
        return {"state": EvaluationState.EVALUATED_FAILED, "reason": "low_volume", "measured": volume, "threshold": CALL_MIN_VOLUME}
    if not quote.get("yearLow"):
        return {"state": EvaluationState.EVALUATED_MISSING_INPUTS, "reason": "no_data", "missing": ("yearLow", "vendor_field_absent")}
    run_up = ((price - low) / low) * 100
    if run_up < PUT_MIN_RUNUP:
        return {"state": EvaluationState.EVALUATED_FAILED, "reason": "small_runup", "measured": run_up, "threshold": PUT_MIN_RUNUP}
    if high:
        below = ((high - price) / high) * 100
        if below > PUT_MAX_BELOW_HIGH:
            return {"state": EvaluationState.EVALUATED_FAILED, "reason": "not_near_high", "measured": below, "threshold": PUT_MAX_BELOW_HIGH}
    return {"state": EvaluationState.EVALUATED_PASSED, "reason": "gate0_pass", "measured": run_up, "threshold": PUT_MIN_RUNUP}


def _formula_strikes(direction: Direction, price: float) -> Tuple[str, float, float]:
    """Percent of spot on a $5 grid. Matches the inspected moderate branch."""
    if direction == Direction.PUT:
        cons_pct, risky_pct, conviction = 0.99, 0.95, "MODERATE"
    else:
        cons_pct, risky_pct, conviction = 1.005, 1.04, "MODERATE"
    return (
        conviction,
        round(price * cons_pct / 5) * 5,
        round(price * risky_pct / 5) * 5,
    )


def map_production_shaped(inputs: Mapping[str, Any], nonce: str = "f33full01") -> MappedRun:
    """Map source-shaped fixtures into evidence records. Does not call vendors."""
    original = canonical_json_bytes(inputs)
    working = copy.deepcopy(dict(inputs))
    registrations = working["registrations"]
    quotes = working["quotes"]
    if len(registrations) != REGISTRATION_COUNT:
        raise EvidenceContractError("expected 562 registrations")
    run = _run(
        nonce,
        full_stage_accounts(),
        ExecutionStatus.COMPLETED,
        PublicationStatus.UNPUBLISHED,
        DECIDED,
    )
    manifest_id = run.universe_manifest_id
    by_symbol: Dict[str, List[Mapping[str, Any]]] = {}
    for item in registrations:
        by_symbol.setdefault(item["ticker"], []).append(item)
    if len(by_symbol) != UNIQUE_COUNT:
        raise EvidenceContractError("expected 560 unique symbols")

    memberships: List[UniverseMembership] = []
    resolved: Dict[str, Mapping[str, Any]] = {}
    for symbol, rows in by_symbol.items():
        ordered = sorted(rows, key=lambda row: row["ordinal"])
        winner = ordered[-1]
        resolved[symbol] = winner
        for row in ordered:
            memberships.append(
                UniverseMembership(
                    evidence_schema_version=SCHEMA,
                    membership_id=_membership_id(manifest_id, symbol, row["ordinal"], row["exchange"]),
                    universe_manifest_id=manifest_id,
                    acquisition_run_id=run.acquisition_run_id,
                    symbol_as_observed=symbol,
                    instrument_id_source=InstrumentIdSource.UNAVAILABLE,
                    instrument_event=InstrumentEvent.NONE,
                    registration_ordinal=row["ordinal"],
                    sector_as_registered=row["sector"],
                    sector_resolved=winner["sector"],
                    wind_resolved=winner["wind"],
                    resolution_rule="last_registration_wins",
                    exchange=row["exchange"],
                    instrument_id=None,
                    duplicate_of_ordinal=1 if row["ordinal"] > 1 else None,
                )
            )
    fingerprint_lines = [
        "{0}|{1}|{2}".format(item.symbol_as_observed, item.registration_ordinal, item.exchange or "")
        for item in memberships
    ]
    universe = UniverseManifest(
        evidence_schema_version=SCHEMA,
        universe_manifest_id=manifest_id,
        member_fingerprint_sha256=sha256_bytes(("\n".join(sorted(fingerprint_lines)) + "\n").encode("utf-8")),
        symbol_count=UNIQUE_COUNT,
        created_at=STARTED,
        source_identity=SOURCE_IDENTITY,
        frame_description="synthetic 560-symbol universe with two duplicate registrations",
    )

    requests: List[VendorRequest] = []
    payloads: Dict[str, bytes] = {}
    primary_request: Dict[str, VendorRequest] = {}
    for row in registrations:
        symbol = row["ticker"]
        raw = quotes.get(symbol)
        params = {"symbol": symbol, "ordinal": row["ordinal"]}
        request_id = "req:{0}:{1}:{2}".format(run.acquisition_run_id, symbol, row["ordinal"])
        if isinstance(raw, dict) and raw.get("error") == "timeout":
            state = VendorRequestState.TIMEOUT
            payload_kind = PayloadBytesKind.ABSENT
            payload_sha = None
            http_status = None
            received = None
        elif isinstance(raw, dict) and raw.get("price") is not None:
            state = VendorRequestState.SUCCESSFUL_RESPONSE
            stored = {
                key: raw[key]
                for key in ("price", "yearHigh", "yearLow", "volume", "changePercentage")
                if key in raw
            }
            stored["symbol"] = symbol
            body = canonical_json_bytes(stored)
            payload_sha = sha256_bytes(body)
            payloads.setdefault(payload_sha, body)
            payload_kind = PayloadBytesKind.PARSED_OBJECT_ONLY
            http_status = 200
            received = STARTED + timedelta(seconds=1)
        else:
            state = VendorRequestState.EMPTY_RESPONSE
            payload_kind = PayloadBytesKind.ABSENT
            payload_sha = None
            http_status = 200
            received = STARTED + timedelta(seconds=1)
        request = VendorRequest(
            evidence_schema_version=SCHEMA,
            vendor_request_id=request_id,
            acquisition_run_id=run.acquisition_run_id,
            vendor="synthetic-quote",
            endpoint="quote",
            sanitized_params=params,
            params_sha256=params_fingerprint(params),
            request_started_at=STARTED,
            request_state=state,
            external_request=True,
            retry_ordinal=0,
            payload_bytes_kind=payload_kind,
            byte_exact=False,
            response_received_at=received,
            http_status=http_status,
            payload_sha256=payload_sha,
            payload_ref=None if payload_sha is None else "payloads/{0}.json".format(payload_sha),
        )
        requests.append(request)
        if row["ordinal"] == 1:
            primary_request[symbol] = request

    observations: List[SecurityObservation] = []
    directions = []
    screening: List[ScreeningDecision] = []
    from evidence_contract import DirectionalEvaluation

    call_pass = []
    put_pass = []
    for symbol in sorted(by_symbol):
        request = primary_request[symbol]
        winner = resolved[symbol]
        first = next(row for row in by_symbol[symbol] if row["ordinal"] == 1)
        membership_id = _membership_id(manifest_id, symbol, first["ordinal"], first["exchange"])
        raw = quotes.get(symbol)
        usable = isinstance(raw, dict) and raw.get("price") is not None
        if request.request_state == VendorRequestState.TIMEOUT:
            quote_state = VendorRequestState.TIMEOUT
            directional_state = EvaluationState.VENDOR_REQUEST_FAILED
            missing = tuple(sorted((
                ("price", "vendor_request_failed"),
                ("year_high", "vendor_request_failed"),
                ("year_low", "vendor_request_failed"),
                ("volume", "vendor_request_failed"),
                ("change_percentage", "vendor_request_failed"),
                ("quote_as_of", "vendor_effective_time_unavailable"),
            )))
            price = high = low = volume = change = None
        elif not usable:
            quote_state = VendorRequestState.EMPTY_RESPONSE
            directional_state = EvaluationState.VENDOR_RETURNED_NO_DATA
            missing = tuple(sorted((
                ("price", "vendor_returned_no_data"),
                ("year_high", "vendor_returned_no_data"),
                ("year_low", "vendor_returned_no_data"),
                ("volume", "vendor_returned_no_data"),
                ("change_percentage", "vendor_returned_no_data"),
                ("quote_as_of", "vendor_effective_time_unavailable"),
            )))
            price = high = low = volume = change = None
        else:
            quote_state = VendorRequestState.SUCCESSFUL_RESPONSE
            directional_state = None
            price = raw.get("price")
            high = raw.get("yearHigh")
            low = raw.get("yearLow")
            volume = raw.get("volume")
            change = raw.get("changePercentage")
            absent = []
            for field_name, field_value in (
                ("price", price),
                ("year_high", high),
                ("year_low", low),
                ("volume", volume),
                ("change_percentage", change),
            ):
                if field_value is None:
                    absent.append((field_name, "vendor_field_absent"))
            absent.append(("quote_as_of", "vendor_effective_time_unavailable"))
            missing = tuple(sorted(absent))
        observation = SecurityObservation(
            evidence_schema_version=SCHEMA,
            observation_id=security_observation_id(run.acquisition_run_id, symbol, EXCHANGE),
            acquisition_run_id=run.acquisition_run_id,
            symbol_as_observed=symbol,
            membership_id=membership_id,
            quote_state=quote_state,
            quote_as_of_source=TimeSource.UNAVAILABLE,
            evidence_derivation=EvidenceDerivation.CAPTURED,
            methodology_id=METHODOLOGY,
            missing_reasons=missing,
            quote_request_id=request.vendor_request_id,
            exchange=EXCHANGE,
            price=price,
            year_high=high,
            year_low=low,
            volume=volume,
            change_percentage=change,
            quote_as_of=None,
            response_received_at=request.response_received_at,
        )
        observations.append(observation)
        for direction, screener, passed in (
            (Direction.CALL, _screen_call, call_pass),
            (Direction.PUT, _screen_put, put_pass),
        ):
            directional_id = "{0}:{1}:{2}".format(observation.observation_id, direction.value, METHODOLOGY)
            if directional_state is not None:
                state = directional_state
                decision = None
            else:
                decision = screener(raw)
                state = decision["state"]
            directions.append(
                DirectionalEvaluation(
                    evidence_schema_version=SCHEMA,
                    directional_evaluation_id=directional_id,
                    observation_id=observation.observation_id,
                    direction=direction,
                    methodology_id=METHODOLOGY,
                    methodology_role=MethodologyRole.GATE_EVALUATION,
                    evidence_derivation=EvidenceDerivation.CAPTURED,
                    evaluation_state=state if decision is None or state != EvaluationState.EVALUATED_PASSED else EvaluationState.EVALUATED_PASSED,
                    acquisition_role=AcquisitionRole.PRODUCTION,
                )
            )
            if decision is None:
                screening.append(
                    _screening(directional_id, "gate0", state, reason="vendor", measured=None)
                )
            else:
                screening.append(
                    _screening(
                        directional_id,
                        "gate0",
                        decision["state"],
                        reason=decision["reason"],
                        measured=decision.get("measured"),
                        threshold=decision.get("threshold"),
                        missing=decision.get("missing"),
                    )
                )
                if decision["state"] == EvaluationState.EVALUATED_PASSED:
                    passed.append((symbol, decision.get("measured"), directional_id, observation, raw))

    call_ranked = sorted(call_pass, key=lambda item: item[1])
    put_ranked = sorted(put_pass, key=lambda item: item[1], reverse=True)
    selected_call = {item[0] for item in call_ranked[:TOP10]}
    selected_put = {item[0] for item in put_ranked[:TOP10]}
    for rank, item in enumerate(call_ranked, start=1):
        included = rank <= TOP10
        screening.append(
            _screening(
                item[2],
                "top10",
                EvaluationState.EVALUATED_PASSED if included else EvaluationState.NOT_SELECTED_RANKING_TRUNCATION,
                reason="top10",
                measured=item[1],
                rank=rank,
                cut=TOP10,
            )
        )
    for rank, item in enumerate(put_ranked, start=1):
        included = rank <= TOP10
        screening.append(
            _screening(
                item[2],
                "top10",
                EvaluationState.EVALUATED_PASSED if included else EvaluationState.NOT_SELECTED_RANKING_TRUNCATION,
                reason="top10",
                measured=item[1],
                rank=rank,
                cut=TOP10,
            )
        )

    gates: List[GateEvaluation] = []
    features: List[FeatureObservation] = []
    scores: List[ScoreEvaluation] = []
    rankings: List[RankingDecision] = []
    strategies: List[StrategyEvaluation] = []
    editorial: List[EditorialDecision] = []
    outcomes: List[MarketOutcome] = []
    passers = []

    def _add_rank(symbol: str, direction: Direction, stage: str, included: bool, rank: int, cut: int, sort_key: str) -> None:
        rankings.append(
            RankingDecision(
                evidence_schema_version=SCHEMA,
                ranking_decision_id="{0}|{1}|{2}|{3}".format(run.acquisition_run_id, stage, direction.value, symbol),
                acquisition_run_id=run.acquisition_run_id,
                direction=direction,
                stage=stage,
                symbol_as_observed=symbol,
                included=included,
                sort_key=sort_key,
                ranked_at=DECIDED,
                decision_available_at=DECIDED,
                rank=rank,
                cut_limit=cut,
                exchange=EXCHANGE,
            )
        )

    for rank, item in enumerate(call_ranked, start=1):
        _add_rank(item[0], Direction.CALL, "top10", rank <= TOP10, rank, TOP10, "drawdown_ascending")
    for rank, item in enumerate(put_ranked, start=1):
        _add_rank(item[0], Direction.PUT, "top10", rank <= TOP10, rank, TOP10, "runup_descending")

    sequential_call = [item for item in call_ranked[:TOP10] if CALL_PLAN[item[0]] not in ("early_exit", "wind_block")]
    sequential_put = [item for item in put_ranked[:TOP10] if PUT_PLAN[item[0]] not in ("early_exit", "wind_block")]
    for rank, item in enumerate(sequential_call, start=1):
        _add_rank(item[0], Direction.CALL, "top7", rank <= TOP7, rank, TOP7, "deep_order")
    for rank, item in enumerate(sequential_put, start=1):
        _add_rank(item[0], Direction.PUT, "top7", rank <= TOP7, rank, TOP7, "deep_order")

    def _gate(directional_id: str, name: str, state: EvaluationState, score: Optional[float] = None, reported: Optional[str] = None, clean: Optional[bool] = None) -> None:
        reasons = () if score is not None else (("score", "not_scored"),)
        evaluated_at = None if state in (
            EvaluationState.NOT_EVALUATED_EARLIER_FAILURE,
            EvaluationState.NOT_REQUESTED,
            EvaluationState.NOT_SELECTED_RANKING_TRUNCATION,
        ) else DECIDED
        gates.append(
            GateEvaluation(
                evidence_schema_version=SCHEMA,
                gate_evaluation_id="{0}|{1}|captured".format(directional_id, name),
                directional_evaluation_id=directional_id,
                gate_name=name,
                evaluation_state=state,
                evidence_derivation=EvidenceDerivation.CAPTURED,
                missing_reasons=reasons,
                production_reported_state=reported,
                score=score,
                clean=clean,
                evaluated_at=evaluated_at,
            )
        )

    def _feature(observation, name: str, value: float, request_id: str, consumed: bool = True) -> None:
        features.append(
            FeatureObservation(
                evidence_schema_version=SCHEMA,
                feature_observation_id="{0}|{1}|captured|0".format(observation.observation_id, name),
                observation_id=observation.observation_id,
                feature_name=name,
                feature_derivation=FeatureDerivation.CALCULATED if name != "price" else FeatureDerivation.VENDOR_RAW,
                evidence_derivation=EvidenceDerivation.CAPTURED,
                methodology_id=METHODOLOGY,
                source_request_ids=(request_id,),
                vendor_effective_source=TimeSource.UNAVAILABLE,
                freshness=Freshness.UNKNOWN,
                freshness_basis=FreshnessBasis.RECEIPT_TIME,
                value_number=value,
                unit="score" if "score" in name else "usd",
                calculated_at=DECIDED if name != "price" else None,
                response_received_at=STARTED + timedelta(seconds=1),
                revision_sequence=0,
                consumed_by_decision=consumed,
            )
        )

    def _evaluate_deep(item, direction: Direction, plan: str) -> None:
        symbol, measured, directional_id, observation, raw = item
        request_id = observation.quote_request_id
        if plan == "top7_cut":
            return
        if plan == "early_exit":
            _gate(directional_id, "gate1", EvaluationState.EVALUATED_FAILED, score=2)
            _feature(observation, "basic_score", 2, request_id)
            return
        if plan == "wind_block":
            _gate(directional_id, "gate1", EvaluationState.EVALUATED_FAILED, score=4)
            _feature(observation, "basic_score", 4, request_id)
            return
        _gate(directional_id, "gate1", EvaluationState.EVALUATED_PASSED, score=6)
        _feature(observation, "gate1_score", 6, request_id)
        if plan == "gate2_fail":
            _gate(directional_id, "gate2", EvaluationState.EVALUATED_FAILED, score=-3)
            _gate(directional_id, "gate3", EvaluationState.NOT_EVALUATED_EARLIER_FAILURE)
            _gate(
                directional_id,
                "gate4",
                EvaluationState.NOT_EVALUATED_EARLIER_FAILURE,
                reported="clean_true_placeholder",
                clean=None,
            )
            return
        _gate(directional_id, "gate2", EvaluationState.EVALUATED_PASSED, score=2)
        _gate(directional_id, "gate3", EvaluationState.EVALUATED_PASSED, score=1)
        _gate(directional_id, "gate4", EvaluationState.EVALUATED_PASSED, score=0, clean=True)
        _feature(observation, "price", raw["price"], request_id)
        metric = "drawdown" if direction == Direction.CALL else "run_up"
        _feature(observation, metric, measured, request_id)
        displayed = PASSER_SCORE[symbol]
        for component, applied in (("base", 50), ("gate1", 15), ("displayed", displayed)):
            scores.append(
                ScoreEvaluation(
                    evidence_schema_version=SCHEMA,
                    score_evaluation_id="{0}|{1}|{2}|captured".format(directional_id, METHODOLOGY, component),
                    directional_evaluation_id=directional_id,
                    methodology_id=METHODOLOGY,
                    methodology_role=MethodologyRole.SCORING,
                    component_name=component,
                    calculated_at=DECIDED,
                    evidence_derivation=EvidenceDerivation.CAPTURED,
                    raw_points=applied,
                    cap=applied,
                    applied_points=applied,
                    displayed_score=displayed,
                )
            )
        conviction, conservative, risky = _formula_strikes(direction, raw["price"])
        strategies.append(
            StrategyEvaluation(
                evidence_schema_version=SCHEMA,
                strategy_evaluation_id="{0}|formula-strike-v1".format(directional_id),
                directional_evaluation_id=directional_id,
                strategy_methodology_id="formula-strike-v1",
                is_formula_strike=True,
                contract_verified=False,
                chain_present=False,
                chain_establishes_execution=False,
                strategy_name="BULL CALL SPREAD" if direction == Direction.CALL else "BEAR PUT SPREAD",
                expiration_text="6-10 weeks",
                conviction=conviction,
                conservative_strike=float(conservative),
                risky_strike=float(risky),
                contract_symbol=None,
            )
        )
        outcomes.append(
            MarketOutcome(
                evidence_schema_version=SCHEMA,
                outcome_id="outcome:{0}:h5".format(directional_id),
                subject_id=directional_id,
                anchor_type=OutcomeAnchor.COMPLETED_RANKING,
                outcome_methodology_id=OUTCOME_METHODOLOGY_BY_KIND[OutcomeKind.UNDERLYING_RETURN],
                outcome_kind=OutcomeKind.UNDERLYING_RETURN,
                horizon_sessions=5,
                entry_at=DECIDED,
                evidence_derivation=EvidenceDerivation.CAPTURED,
                formula_strike_used=False,
                contract_verified=False,
                chain_present=False,
                value=None,
                missing_reason="not_calculated",
                decision_available_at=DECIDED,
            )
        )
        passers.append((symbol, direction, directional_id, displayed))

    for item in call_ranked[:TOP10]:
        _evaluate_deep(item, Direction.CALL, CALL_PLAN[item[0]])
    for item in put_ranked[:TOP10]:
        _evaluate_deep(item, Direction.PUT, PUT_PLAN[item[0]])

    # Directional state for names that passed gate 0 and then stopped later.
    rewritten = []
    later = {}
    for symbol, plan in list(CALL_PLAN.items()) + list(PUT_PLAN.items()):
        later[(symbol, Direction.CALL if symbol in CALL_PLAN else Direction.PUT)] = plan
    for directional in directions:
        symbol = directional.observation_id.rsplit("|", 1)[-1]
        plan = later.get((symbol, directional.direction))
        state = directional.evaluation_state
        if plan == "passer":
            state = EvaluationState.EVALUATED_PASSED
        elif plan in ("early_exit", "wind_block", "gate2_fail"):
            state = EvaluationState.EVALUATED_FAILED
        elif plan == "top7_cut":
            state = EvaluationState.NOT_SELECTED_RANKING_TRUNCATION
        elif state == EvaluationState.EVALUATED_PASSED and plan is None:
            state = EvaluationState.NOT_SELECTED_RANKING_TRUNCATION
        rewritten.append(replace(directional, evaluation_state=state))
    directions = rewritten

    ordered_passers = sorted(passers, key=lambda item: (-item[3], item[0]))
    published = ordered_passers[:PUBLISHED]
    for rank, item in enumerate(ordered_passers, start=1):
        _add_rank(item[0], item[1], "published_top6", rank <= PUBLISHED, rank, PUBLISHED, "displayed_score")
    editorial_plan = (
        (published[0], EmailClassification.TOP),
        (published[3], EmailClassification.SECONDARY),
        (published[1], EmailClassification.WATCH),
        (published[4], EmailClassification.WATCH),
    )
    report = working["report_text"]
    claude_input = report[:8000]
    claude_output = working["claude_output"]
    prompt_digest = sha256_bytes(claude_input.encode("utf-8"))
    for item, classification in editorial_plan:
        editorial.append(
            EditorialDecision(
                evidence_schema_version=SCHEMA,
                editorial_decision_id="{0}|{1}|{2}".format(REPORT_ID, classification.value, item[0]),
                report_id=REPORT_ID,
                symbol_as_observed=item[0],
                email_classification=classification,
                parser_version="synthetic-editorial-v0",
                decided_at=DECIDED,
                decision_available_at=DECIDED,
                evidence_derivation=EvidenceDerivation.CAPTURED,
                acquisition_run_id=run.acquisition_run_id,
                direction=item[1].value,
                strategy_text="synthetic strategy label",
                claude_model="synthetic-not-a-model",
                prompt_digest_sha256=prompt_digest,
                source_excerpt="synthetic excerpt",
            )
        )

    records = (
        [run, universe]
        + memberships
        + requests
        + observations
        + directions
        + screening
        + gates
        + features
        + scores
        + rankings
        + strategies
        + editorial
        + outcomes
    )
    for record in records:
        validate(record)

    run_start = RunStartRecord(
        evidence_schema_version=SCHEMA,
        run_start_id=run.acquisition_run_id + "/start",
        acquisition_run_id=run.acquisition_run_id,
        acquisition_role=AcquisitionRole.PRODUCTION,
        pipeline=Pipeline.PRODUCTION_BATCH,
        execution_nonce=nonce,
        attempt_number=1,
        started_at=STARTED,
        universe_manifest_id=manifest_id,
    )
    validate(run_start)
    reconciliation = _reconcile(
        memberships, requests, observations, directions, screening, gates, rankings, strategies, editorial, outcomes, payloads, passers, published
    )
    unchanged = canonical_json_bytes(inputs) == original
    return MappedRun(
        run=run,
        run_start=run_start,
        universe=universe,
        memberships=tuple(memberships),
        requests=tuple(requests),
        observations=tuple(observations),
        directions=tuple(directions),
        screening=tuple(screening),
        gates=tuple(gates),
        features=tuple(features),
        scores=tuple(scores),
        rankings=tuple(rankings),
        strategies=tuple(strategies),
        editorial=tuple(editorial),
        outcomes=tuple(outcomes),
        payloads=payloads,
        reconciliation=reconciliation,
        claude_input=claude_input,
        claude_output=claude_output,
        inputs_unchanged=unchanged,
    )


def _screening(
    directional_id: str,
    stage: str,
    state: EvaluationState,
    reason: str,
    measured: Optional[float] = None,
    threshold: Optional[float] = None,
    missing: Optional[Tuple[str, str]] = None,
    rank: Optional[int] = None,
    cut: Optional[int] = None,
) -> ScreeningDecision:
    reasons: Tuple[Tuple[str, str], ...] = ()
    if missing:
        reasons = (missing,)
    if measured is None:
        reasons = tuple(sorted(reasons + (("measured_value", reason or "not_measured"),)))
    return ScreeningDecision(
        evidence_schema_version=SCHEMA,
        screening_decision_id="{0}|{1}".format(directional_id, stage),
        directional_evaluation_id=directional_id,
        stage=stage,
        decision_state=state,
        decided_at=DECIDED,
        missing_reasons=reasons,
        reason_code=reason,
        measured_value=measured,
        threshold_value=threshold,
        rank_before_cut=rank,
        cut_limit=cut,
    )


def _count_states(records: Sequence[Any], field: str) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for record in records:
        value = getattr(record, field)
        key = value.value if hasattr(value, "value") else str(value)
        counts[key] = counts.get(key, 0) + 1
    return counts


def _reconcile(memberships, requests, observations, directions, screening, gates, rankings, strategies, editorial, outcomes, payloads, passers, published) -> Dict[str, Any]:
    shared = {}
    for request in requests:
        if request.payload_sha256:
            shared[request.payload_sha256] = shared.get(request.payload_sha256, 0) + 1
    return {
        "registrations": len(memberships),
        "unique_symbols": len({item.symbol_as_observed for item in memberships}),
        "resolved_cloud_platform": sorted(
            item.symbol_as_observed
            for item in memberships
            if item.sector_resolved == "CLOUD_PLATFORM" and item.registration_ordinal == 2
        ),
        "quote_attempts": len(requests),
        "quote_states": _count_states(requests, "request_state"),
        "unique_payloads": len(payloads),
        "payload_references": sum(shared.values()),
        "duplicate_payload_references": sum(count - 1 for count in shared.values() if count > 1),
        "observations": len(observations),
        "directional_evaluations": len(directions),
        "directional_states": _count_states(directions, "evaluation_state"),
        "screening_states": _count_states(screening, "decision_state"),
        "gate_states": _count_states(gates, "evaluation_state"),
        "gate4_placeholders": sum(
            1 for gate in gates if gate.production_reported_state == "clean_true_placeholder" and gate.clean is not True
        ),
        "deep_gate_rows": len(gates),
        "rejected_directional": sum(
            1 for item in directions if item.evaluation_state != EvaluationState.EVALUATED_PASSED
        ),
        "complete_passers": len(passers),
        "published_candidates": len(published),
        "editorial": len(editorial),
        "editorial_classes": _count_states(editorial, "email_classification"),
        "formula_strikes": len(strategies),
        "verified_contracts": sum(1 for item in strategies if item.contract_verified),
        "outcomes_without_value": sum(1 for item in outcomes if item.value is None),
        "instrument_ids_invented": sum(1 for item in memberships if item.instrument_id is not None),
        "missing_fields": (
            "quote_as_of vendor effective time is unavailable on the quote object",
            "instrument_id is unavailable and was not invented",
            "formula strikes are not verified contracts",
            "outcome values were not calculated and no prices were fetched",
            "Gate 4 clean:true on an earlier failure is a placeholder, not a pass",
            "production underlying_data_timestamp and source_scan_run_id are absent",
        ),
    }


def build_production_shaped_run(nonce: str = "f33full01") -> MappedRun:
    inputs = synthetic_production_inputs()
    mapped = map_production_shaped(inputs, nonce=nonce)
    if not mapped.inputs_unchanged:
        raise EvidenceContractError("mapper mutated the source-shaped fixture")
    return mapped


def scenario_runs() -> Dict[str, AcquisitionRun]:
    """Six status distinctions. Vendor failure is not incomplete capture."""
    finished = DECIDED
    three = (
        _stage("quotes", 560, 560, 560, 0, capacity=560),
        _stage("screening", 560, 560, 3, 557, capacity=560),
        _stage("deep_evaluation", 3, 3, 3, 0, capacity=20),
    )
    after_screen = (
        _stage("quotes", 560, 560, 560, 0, capacity=560),
        _stage("screening", 560, 560, 20, 540, capacity=560),
        _stage("deep_evaluation", 20, 0, 0, 0, interrupted=20, capacity=20),
    )
    during_deep = (
        _stage("quotes", 560, 560, 560, 0, capacity=560),
        _stage("screening", 560, 560, 20, 540, capacity=560),
        _stage("deep_evaluation", 20, 3, 1, 2, interrupted=17, capacity=20),
    )
    before_editorial = (
        _stage("quotes", 562, 562, 542, 20, capacity=562),
        _stage("screening", 540, 540, 20, 520, capacity=540),
        _stage("deep_evaluation", 20, 18, 10, 8, excluded=2, capacity=20),
        _stage("ranking", 10, 10, 6, 4, capacity=10),
        _stage("editorial", 4, 0, 0, 0, interrupted=4, capacity=4),
    )
    return {
        "complete_three_deep": _run("f33three1", three, ExecutionStatus.COMPLETED, PublicationStatus.TERMINALLY_PUBLISHED, finished),
        "interrupted_after_screening": _run("f33after1", after_screen, ExecutionStatus.ABANDONED, PublicationStatus.CHECKPOINT_PUBLISHED, finished),
        "interrupted_during_deep": _run("f33during1", during_deep, ExecutionStatus.TIMED_OUT, PublicationStatus.CHECKPOINT_PUBLISHED, finished),
        "interrupted_before_editorial": _run("f33edit01", before_editorial, ExecutionStatus.ABANDONED, PublicationStatus.CHECKPOINT_PUBLISHED, finished),
        "vendor_failures_complete": _run("f33vend01", full_stage_accounts(), ExecutionStatus.COMPLETED, PublicationStatus.TERMINALLY_PUBLISHED, finished),
        "publication_failed": _run("f33pubf01", full_stage_accounts(), ExecutionStatus.COMPLETED, PublicationStatus.UNPUBLISHED, finished),
    }


def accounted_chunk_rows(checkpoints: Sequence[EvidenceCheckpointManifest]) -> int:
    """Count each object once. A cumulative checkpoint replaces that entity.

    An incremental checkpoint that repeats an object key is a reference to
    the earlier object. Identical bytes are not a second copy of the rows.
    A repeated key with a different hash or row count is a conflict.
    """
    incremental: Dict[str, ChunkRef] = {}
    cumulative: Dict[str, Tuple[ChunkRef, ...]] = {}
    for checkpoint in sorted(checkpoints, key=lambda item: item.sequence):
        if checkpoint.evidence_accounting == "cumulative":
            grouped: Dict[str, List[ChunkRef]] = {}
            for chunk in checkpoint.chunks:
                grouped.setdefault(chunk.entity, []).append(chunk)
            for entity, chunks in grouped.items():
                cumulative[entity] = tuple(chunks)
                incremental = {
                    key: value for key, value in incremental.items() if value.entity != entity
                }
            continue
        for chunk in checkpoint.chunks:
            prior = incremental.get(chunk.object_key)
            if prior is not None:
                if prior.sha256 != chunk.sha256 or prior.row_count != chunk.row_count:
                    raise EvidenceContractError("repeated chunk key conflicts")
                continue
            cumulative.pop(chunk.entity, None)
            incremental[chunk.object_key] = chunk
    total = sum(chunk.row_count for chunk in incremental.values())
    for chunks in cumulative.values():
        total += sum(chunk.row_count for chunk in chunks)
    return total


def reuse_views(mapped: MappedRun) -> Dict[str, Any]:
    """Several methodologies read one captured population. No new requests."""
    request_ids = tuple(request.vendor_request_id for request in mapped.requests[:1])
    observation = mapped.observations[0]
    experimental = FeatureObservation(
        evidence_schema_version=SCHEMA,
        feature_observation_id=observation.observation_id + "|drawdown|experimental|0",
        observation_id=observation.observation_id,
        feature_name="drawdown",
        feature_derivation=FeatureDerivation.CALCULATED,
        evidence_derivation=EvidenceDerivation.EXPERIMENTAL,
        methodology_id="experimental-scoring-v0",
        source_request_ids=(observation.quote_request_id,),
        vendor_effective_source=TimeSource.UNAVAILABLE,
        freshness=Freshness.UNKNOWN,
        freshness_basis=FreshnessBasis.RECEIPT_TIME,
        directional_evaluation_id=None,
        value_number=-1,
        response_received_at=STARTED + timedelta(seconds=1),
        calculated_at=DECIDED + timedelta(days=1),
        consumed_by_decision=False,
    )
    validate(experimental)
    payload_hashes = {request.payload_sha256 for request in mapped.requests if request.payload_sha256}
    reference_count = sum(1 for request in mapped.requests if request.payload_sha256)
    unique_bytes = sum(len(body) for body in mapped.payloads.values())
    referenced_bytes = sum(
        len(mapped.payloads[request.payload_sha256])
        for request in mapped.requests
        if request.payload_sha256
    )
    return {
        "gate_rows": len(mapped.gates),
        "rejected_rows": mapped.reconciliation["rejected_directional"],
        "experimental_feature_id": experimental.feature_observation_id,
        "experimental_consumed_by_decision": experimental.consumed_by_decision,
        "experimental_reuses_request": experimental.source_request_ids[0] in {item.vendor_request_id for item in mapped.requests},
        "call_and_put_share_observation": _shared_observation(mapped),
        "unique_payloads": len(payload_hashes),
        "payload_references": reference_count,
        "duplicate_payload_bytes_avoided": referenced_bytes - unique_bytes,
        "new_external_requests": 0,
        "sample_request_ids": request_ids,
    }


def _shared_observation(mapped: MappedRun) -> bool:
    by_observation: Dict[str, set] = {}
    for directional in mapped.directions:
        by_observation.setdefault(directional.observation_id, set()).add(directional.direction)
    return any(Direction.CALL in directions and Direction.PUT in directions for directions in by_observation.values())


def research_population_examples(selection_timestamp: datetime) -> Dict[str, Any]:
    """Deterministic synthetic cohorts. This does not run the 20-name pilot."""
    frame = "synthetic research frame, independent of the production screen"
    symbols = tuple(_symbol(index) for index in range(12))

    def member(symbol: str, holdout, reason: str, methodology: str, stratum: Optional[str] = None, probability: Optional[float] = None, exclusion: Optional[str] = None) -> PopulationMember:
        return PopulationMember(
            symbol_as_observed=symbol,
            holdout_status=holdout,
            inclusion_reason=reason,
            sampling_methodology_id=methodology,
            stratum_id=stratum,
            selection_probability=probability,
            exclusion_reason=exclusion,
        )

    from evidence_contract import HoldoutStatus

    representative = build_population_manifest(
        population_role=PopulationRole.DISCOVERY,
        sampling_kind=SamplingKind.REPRESENTATIVE,
        sampling_methodology_id="sampling-representative-v0",
        sampling_frame=frame,
        selection_timestamp=selection_timestamp,
        members=tuple(
            member(symbol, HoldoutStatus.DISCOVERY, "frame member", "sampling-representative-v0", probability=1.0)
            for symbol in symbols[:4]
        ),
        methodology_id="research-representative-v0",
        random_seed="seed-representative",
        inclusion_reasons=("frame member",),
    )
    stratified = build_population_manifest(
        population_role=PopulationRole.RESEARCH_COHORT,
        sampling_kind=SamplingKind.STRATIFIED,
        sampling_methodology_id="sampling-stratified-v0",
        sampling_frame=frame,
        selection_timestamp=selection_timestamp,
        members=(
            member(symbols[0], HoldoutStatus.DISCOVERY, "stratum A", "sampling-stratified-v0", "A", 0.5),
            member(symbols[1], HoldoutStatus.DISCOVERY, "stratum B", "sampling-stratified-v0", "B", 0.5),
        ),
        methodology_id="research-stratified-v0",
        random_seed="seed-stratified",
    )
    matched = build_population_manifest(
        population_role=PopulationRole.CONTROL,
        sampling_kind=SamplingKind.MATCHED_CONTROL,
        sampling_methodology_id="sampling-matched-v0",
        sampling_frame=frame,
        selection_timestamp=selection_timestamp,
        members=(
            member(symbols[2], HoldoutStatus.DISCOVERY, "case", "sampling-matched-v0", "case"),
            member(symbols[3], HoldoutStatus.DISCOVERY, "control", "sampling-matched-v0", "control"),
        ),
        methodology_id="research-matched-v0",
        parent_population_id=representative.population_id,
    )
    hypothesis = build_population_manifest(
        population_role=PopulationRole.RESEARCH_COHORT,
        sampling_kind=SamplingKind.HYPOTHESIS_DRIVEN,
        sampling_methodology_id="sampling-hypothesis-v0",
        sampling_frame=frame,
        selection_timestamp=selection_timestamp,
        members=(member(symbols[4], HoldoutStatus.DISCOVERY, "hypothesis member", "sampling-hypothesis-v0"),),
        methodology_id="research-hypothesis-v0",
        hypothesis_id="hypothesis-synthetic-1",
    )
    longitudinal = build_population_manifest(
        population_role=PopulationRole.RESEARCH_COHORT,
        sampling_kind=SamplingKind.LONGITUDINAL,
        sampling_methodology_id="sampling-longitudinal-v0",
        sampling_frame=frame,
        selection_timestamp=selection_timestamp,
        members=(member(symbols[5], HoldoutStatus.DISCOVERY, "repeat observation", "sampling-longitudinal-v0"),),
        methodology_id="research-longitudinal-v0",
        parent_population_id=representative.population_id,
    )
    exploratory = build_population_manifest(
        population_role=PopulationRole.RESEARCH_COHORT,
        sampling_kind=SamplingKind.EXPLORATORY,
        sampling_methodology_id="sampling-exploratory-v0",
        sampling_frame=frame,
        selection_timestamp=selection_timestamp,
        members=(member(symbols[6], HoldoutStatus.DISCOVERY, "exploratory inclusion", "sampling-exploratory-v0"),),
        methodology_id="research-exploratory-v0",
    )
    holdout = build_population_manifest(
        population_role=PopulationRole.HOLDOUT,
        sampling_kind=SamplingKind.INDEPENDENT_HOLDOUT,
        sampling_methodology_id="sampling-holdout-v0",
        sampling_frame=frame,
        selection_timestamp=selection_timestamp,
        members=(
            member(symbols[7], HoldoutStatus.HOLDOUT, "held out", "sampling-holdout-v0"),
            member(symbols[8], HoldoutStatus.HOLDOUT, "held out", "sampling-holdout-v0"),
        ),
        methodology_id="research-holdout-v0",
        holdout_status=HoldoutStatus.HOLDOUT,
        exclusion_reasons=("held out of discovery",),
    )
    manifests = {
        "representative": representative,
        "stratified": stratified,
        "matched_control": matched,
        "hypothesis_driven": hypothesis,
        "longitudinal": longitudinal,
        "exploratory": exploratory,
        "independent_holdout": holdout,
    }
    return {
        "manifests": manifests,
        "holdout_discovery_symbols": tuple(item.symbol_as_observed for item in discovery_members(holdout)),
        "fingerprints": {name: item.population_fingerprint_sha256 for name, item in manifests.items()},
    }


def measure_local_publication(mapped: MappedRun, store) -> Dict[str, Any]:
    """Local fixture timings. These are not production latency."""
    tracemalloc.start()
    started = time.perf_counter()
    rows = [record_to_dict(record) for record in mapped.observations]
    serialize_seconds = time.perf_counter() - started
    raw = jsonl_bytes(rows)
    compressed = gzip_deterministic(raw)
    current, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    publish_started = time.perf_counter()
    store.put_run_start(mapped.run_start)
    store.put_bytes("payloads/observations.jsonl.gz", compressed)
    publish_seconds = time.perf_counter() - publish_started
    recover_started = time.perf_counter()
    recovered = store.read_bytes("payloads/observations.jsonl.gz")
    recover_seconds = time.perf_counter() - recover_started
    return {
        "label": "local_fixture_not_production_latency",
        "observation_rows": len(rows),
        "uncompressed_bytes": len(raw),
        "compressed_bytes": len(compressed),
        "serialization_seconds": serialize_seconds,
        "publication_seconds": publish_seconds,
        "recovery_seconds": recover_seconds,
        "peak_memory_bytes": peak,
        "recovered_matches": recovered == compressed,
        "current_memory_bytes": current,
    }


def repeated_request_keeps_payload(mapped: MappedRun) -> VendorRequest:
    """A later run can cite the stored payload without a second body."""
    first = next(request for request in mapped.requests if request.payload_sha256)
    second = replace(first, vendor_request_id=first.vendor_request_id + ":later-run")
    assert_can_coexist(first, second)
    return second
