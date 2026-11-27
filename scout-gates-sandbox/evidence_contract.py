#!/usr/bin/env python3
"""Local evidence-contract-1 types and validators.

This module does not open a database, call a vendor, read credentials, or
write the canonical research database. It is the local contract later phases
can serialize and test.

F3.1 corrections locked here:

- Publication is a chain of immutable objects. A run start, each checkpoint,
  and a terminal manifest are separate identities. Nothing is promoted by
  overwriting one manifest.
- Execution status, capture completeness, publication status, and research
  eligibility are separate fields. A finished run that qualified fewer names
  than a capacity limit is complete when every planned stage item was
  resolved. The same observed counts are incomplete when planned work was
  interrupted.
- Checkpoints may be declared after named stages. This module does not write
  them to a network. Production checkpoint frequency is not decided here.
- Run identities include role, pipeline, start time, attempt, and a nonce, so
  two executions in the same minute stay distinct.
- Acquisition role, capture origin, evidence derivation, methodology role,
  methodology id, source artifact, deployment verification, configuration,
  and universe identity are separate fields. An experimental methodology may
  directly capture evidence.
- The inspected SHA-256 243ce011… is the Python source file main.py. The zip
  archive that contains that file has a different digest. Neither digest is a
  verified deployment identity.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import (
    Any,
    ClassVar,
    Dict,
    Mapping,
    Optional,
    Sequence,
    Tuple,
)
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


EVIDENCE_SCHEMA_VERSION = "evidence-contract-1"

# Digest of the inspected Cloud Function source file main.py (175,770 bytes).
# It is not the digest of the surrounding zip archive.
INSPECTED_PYTHON_SOURCE_SHA256 = (
    "243ce011a4e8ad9fbade2622c0955d811c52db8045e2b23bd9d7517e599a4e51"
)
INSPECTED_PYTHON_SOURCE_LABEL = (
    "friday-scout-LIVE-00055-4kw-2026-11-26/main.py"
)
# Digest of friday-scout-LIVE-post-archive-2026-09-28.zip. The main.py member
# inside that zip matches INSPECTED_PYTHON_SOURCE_SHA256. The archive bytes
# do not.
INSPECTED_SOURCE_ARCHIVE_SHA256 = (
    "53ff6fe42913b1f8b8c1c364a4f72759688f394ae8452d03228b9cc23a94f204"
)
INSPECTED_SOURCE_ARCHIVE_LABEL = "friday-scout-LIVE-post-archive-2026-09-28.zip"

# Citation of the existing Horizon eligible population. This contract does not
# recompute or replace it.
CANONICAL_ELIGIBLE_POPULATION_HASH = (
    "eaa0e0ec2c10ff7bd5ac99fa8d9ed5cdaed763579e0a7aa4bb24e84292763fc8"
)
CANONICAL_ELIGIBLE_POPULATION_COUNT = 227
CANONICAL_CLASSIFIER_VERSION = "b0-2026-09-28"
CANONICAL_DERIVED_BUILD_IDS = frozenset(
    {
        "pattern-pattern-1-20260929T130814Z-bd3a61ed",
        "gate-alpha-gate-alpha-1-20260929T131618Z-fb09a847",
        "gate-intelligence-gate-intelligence-1-20260929T131948Z-3f2573be",
        "scoring-scoring-decomp-1-20261126T232822Z-ce315ae3",
    }
)

TRADING_SESSION_HORIZONS = frozenset({1, 3, 5, 10, 20})
CHECKPOINT_STAGES = (
    "run_started",
    "quotes_captured",
    "screening_resolved",
    "deep_evaluation",
    "ranking",
    "editorial",
)
DECISIVE_EVALUATION_STATES = frozenset({"evaluated_passed", "evaluated_failed"})
REDACTED = "[redacted]"

# Production checkpoint frequency is intentionally not a constant here.
# A later phase may emit any subset of CHECKPOINT_STAGES after measuring
# latency. This module performs no network I/O.

FUTURE_RESEARCH_UTILITY_METRICS = (
    "incremental_coverage",
    "effective_sample_size",
    "feature_redundancy",
    "statistical_uncertainty_reduction",
    "incremental_out_of_sample_predictive_performance",
    "calibration_improvement",
    "risk_adjusted_performance_improvement",
    "signal_decay",
    "marginal_acquisition_cost",
)

_SECRET_KEYS = frozenset(
    {
        "apikey",
        "api_key",
        "api-key",
        "token",
        "access_token",
        "auth",
        "authorization",
        "password",
        "passwd",
        "secret",
        "signature",
        "sig",
        "key",
        "x-api-key",
        "x-goog-signature",
        "x-goog-credential",
        "x-amz-signature",
        "x-amz-credential",
        "x-amz-security-token",
        "awsaccesskeyid",
        "credential",
        "sessiontoken",
        "smtp_password",
        "private_key",
    }
)
_DROPPED_KEYS = frozenset(
    {
        "authorization",
        "proxy-authorization",
        "cookie",
        "set-cookie",
        "x-api-key",
        "email",
        "to",
        "recipient",
        "subscriber",
        "bcc",
        "cc",
    }
)
_NONCE_RE = re.compile(r"^[A-Za-z0-9_-]{8,}$")


class EvidenceContractError(Exception):
    """The record is outside evidence-contract-1."""


class EvaluationState(str, Enum):
    EVALUATED_PASSED = "evaluated_passed"
    EVALUATED_FAILED = "evaluated_failed"
    EVALUATED_MISSING_INPUTS = "evaluated_missing_inputs"
    NOT_EVALUATED_EARLIER_FAILURE = "not_evaluated_earlier_failure"
    NOT_SELECTED_RANKING_TRUNCATION = "not_selected_ranking_truncation"
    NOT_REQUESTED = "not_requested"
    VENDOR_RETURNED_NO_DATA = "vendor_returned_no_data"
    VENDOR_REQUEST_FAILED = "vendor_request_failed"
    EVALUATION_INTERRUPTED = "evaluation_interrupted"
    NOT_APPLICABLE = "not_applicable"
    UNKNOWN = "unknown"


class VendorRequestState(str, Enum):
    SUCCESSFUL_RESPONSE = "successful_response"
    EMPTY_RESPONSE = "empty_response"
    HTTP_FAILURE = "http_failure"
    TIMEOUT = "timeout"
    PARSE_FAILURE = "parse_failure"
    CACHE_HIT = "cache_hit"
    BUDGET_BLOCKED = "budget_blocked"
    UNKNOWN = "unknown"


class ExecutionStatus(str, Enum):
    STARTED = "started"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    ABANDONED = "abandoned"


class CaptureCompleteness(str, Enum):
    COMPLETE = "complete"
    INCOMPLETE = "incomplete"
    NOT_ASSESSED = "not_assessed"


class PublicationStatus(str, Enum):
    UNPUBLISHED = "unpublished"
    CHECKPOINT_PUBLISHED = "checkpoint_published"
    TERMINALLY_PUBLISHED = "terminally_published"


class ResearchEligibility(str, Enum):
    NOT_ASSESSED = "not_assessed"
    ELIGIBLE_NEW_POPULATION = "eligible_new_population"
    EXCLUDED_FROM_CANONICAL = "excluded_from_canonical"
    HOLDOUT = "holdout"


class AcquisitionRole(str, Enum):
    PRODUCTION = "production"
    RESEARCH = "research"


class Pipeline(str, Enum):
    PRODUCTION_BATCH = "production_batch"
    PRODUCTION_SINGLE = "production_single"
    RESEARCH_ACQUISITION = "research_acquisition"


class CaptureOrigin(str, Enum):
    LIVE_EXECUTION = "live_execution"
    HISTORICAL_EXPORT = "historical_export"
    SYNTHETIC = "synthetic"
    CACHE_REPLAY = "cache_replay"


class EvidenceDerivation(str, Enum):
    CAPTURED = "captured"
    RECONSTRUCTED = "reconstructed"
    INFERRED = "inferred"
    EXPERIMENTAL = "experimental"


class MethodologyRole(str, Enum):
    SCORING = "scoring"
    GATE_EVALUATION = "gate_evaluation"
    FEATURE_EXTRACTION = "feature_extraction"
    STRATEGY_SELECTION = "strategy_selection"
    EDITORIAL = "editorial"
    OUTCOME = "outcome"
    SAMPLING = "sampling"
    EXPERIMENTAL = "experimental"
    ACQUISITION = "acquisition"


class SourceArtifactKind(str, Enum):
    PYTHON_SOURCE = "python_source"
    SOURCE_ARCHIVE = "source_archive"
    CONFIGURATION = "configuration"
    UNIVERSE_MANIFEST = "universe_manifest"
    UNKNOWN = "unknown"


class Direction(str, Enum):
    CALL = "CALL"
    PUT = "PUT"
    NONE = "NONE"


class InstrumentIdSource(str, Enum):
    VENDOR = "vendor"
    UNAVAILABLE = "unavailable"
    EXPLICIT = "explicit"


class InstrumentEvent(str, Enum):
    NONE = "none"
    TICKER_CHANGE = "ticker_change"
    MERGER = "merger"
    DELISTING = "delisting"
    EXCHANGE_DISTINCTION = "exchange_distinction"
    UNKNOWN = "unknown"


class TimeSource(str, Enum):
    VENDOR = "vendor"
    UNAVAILABLE = "unavailable"


class PayloadBytesKind(str, Enum):
    RAW_RESPONSE_BYTES = "raw_response_bytes"
    NORMALIZED_JSON = "normalized_json"
    PARSED_OBJECT_ONLY = "parsed_object_only"
    ABSENT = "absent"


class Freshness(str, Enum):
    FRESH = "fresh"
    STALE = "stale"
    UNKNOWN = "unknown"


class FreshnessBasis(str, Enum):
    VENDOR_EFFECTIVE = "vendor_effective"
    RECEIPT_TIME = "receipt_time"
    UNAVAILABLE = "unavailable"


class FeatureDerivation(str, Enum):
    VENDOR_RAW = "vendor_raw"
    CALCULATED = "calculated"
    STATIC_TABLE = "static_table"


class SamplingKind(str, Enum):
    REPRESENTATIVE = "representative"
    STRATIFIED = "stratified"
    MATCHED_CONTROL = "matched_control"
    HYPOTHESIS_DRIVEN = "hypothesis_driven"
    LONGITUDINAL = "longitudinal"
    EXPLORATORY = "exploratory"
    INDEPENDENT_HOLDOUT = "independent_holdout"
    CANONICAL_REFERENCE = "canonical_reference"


class HoldoutStatus(str, Enum):
    HOLDOUT = "holdout"
    DISCOVERY = "discovery"
    NOT_APPLICABLE = "not_applicable"


class PopulationRole(str, Enum):
    PRODUCTION_CAPTURE = "production_capture"
    RESEARCH_COHORT = "research_cohort"
    HOLDOUT = "holdout"
    CONTROL = "control"
    DISCOVERY = "discovery"


class OutcomeAnchor(str, Enum):
    QUOTE_OBSERVATION = "quote_observation"
    COMPLETED_QUANTITATIVE_EVALUATION = "completed_quantitative_evaluation"
    COMPLETED_RANKING = "completed_ranking"
    EDITORIAL_RECOMMENDATION = "editorial_recommendation"
    HYPOTHETICAL_EXECUTABLE_ENTRY = "hypothetical_executable_entry"
    ACTUAL_EXECUTION = "actual_execution"


class OutcomeKind(str, Enum):
    UNDERLYING_RETURN = "underlying_return"
    BENCHMARK_RELATIVE = "benchmark_relative"
    SECTOR_RELATIVE = "sector_relative"
    DIRECTION_ADJUSTED = "direction_adjusted"
    RISK_ADJUSTED = "risk_adjusted"
    MFE = "mfe"
    MAE = "mae"
    HYPOTHETICAL_STRATEGY = "hypothetical_strategy"
    OPTION_CONTRACT = "option_contract"


class EmailClassification(str, Enum):
    TOP = "top"
    SECONDARY = "secondary"
    WATCH = "watch"


class PricingModel(str, Enum):
    SUBSCRIPTION = "subscription"
    QUOTA = "quota"
    METERED = "metered"
    UNKNOWN = "unknown"


class CostProvenance(str, Enum):
    UNKNOWN = "unknown"
    EXPLICIT_ZERO_MARGINAL = "explicit_zero_marginal"
    SUPPLIED_NOT_INVENTED = "supplied_not_invented"
    SUBSCRIPTION_UNMETERED = "subscription_unmetered"


class BudgetDecisionStatus(str, Enum):
    ALLOWED = "allowed"
    BLOCKED_REQUEST_BUDGET = "blocked_request_budget"
    BLOCKED_QUOTA_BUDGET = "blocked_quota_budget"
    BLOCKED_SPENDING_BUDGET = "blocked_spending_budget"
    BLOCKED_UNKNOWN_COST = "blocked_unknown_cost"
    BLOCKED_DURATION_LIMIT = "blocked_duration_limit"
    BLOCKED_RETRY_LIMIT = "blocked_retry_limit"
    BLOCKED_UNAPPROVED_ENDPOINT = "blocked_unapproved_endpoint"


OUTCOME_METHODOLOGY_BY_KIND = {
    OutcomeKind.UNDERLYING_RETURN: "outcome-underlying-return-v1",
    OutcomeKind.BENCHMARK_RELATIVE: "outcome-benchmark-relative-v1",
    OutcomeKind.SECTOR_RELATIVE: "outcome-sector-relative-v1",
    OutcomeKind.DIRECTION_ADJUSTED: "outcome-direction-adjusted-v1",
    OutcomeKind.RISK_ADJUSTED: "outcome-risk-adjusted-v1",
    OutcomeKind.MFE: "outcome-mfe-v1",
    OutcomeKind.MAE: "outcome-mae-v1",
    OutcomeKind.HYPOTHETICAL_STRATEGY: "outcome-hypothetical-strategy-v1",
    OutcomeKind.OPTION_CONTRACT: "outcome-option-contract-v1",
}

DECISION_ANCHORS = frozenset(
    {
        OutcomeAnchor.COMPLETED_QUANTITATIVE_EVALUATION,
        OutcomeAnchor.COMPLETED_RANKING,
        OutcomeAnchor.EDITORIAL_RECOMMENDATION,
        OutcomeAnchor.HYPOTHETICAL_EXECUTABLE_ENTRY,
        OutcomeAnchor.ACTUAL_EXECUTION,
    }
)

ENTITY_PRIMARY_FIELDS = {
    "acquisition_run": "acquisition_run_id",
    "universe_manifest": "universe_manifest_id",
    "universe_membership": "membership_id",
    "vendor_request": "vendor_request_id",
    "security_observation": "observation_id",
    "directional_evaluation": "directional_evaluation_id",
    "screening_decision": "screening_decision_id",
    "gate_evaluation": "gate_evaluation_id",
    "feature_observation": "feature_observation_id",
    "score_evaluation": "score_evaluation_id",
    "ranking_decision": "ranking_decision_id",
    "strategy_evaluation": "strategy_evaluation_id",
    "editorial_decision": "editorial_decision_id",
    "market_outcome": "outcome_id",
    "research_population_manifest": "population_id",
    "derived_build_reference": "build_id",
    "run_start_record": "run_start_id",
    "evidence_checkpoint_manifest": "checkpoint_id",
    "terminal_publication_manifest": "terminal_id",
    "acquisition_telemetry": "telemetry_id",
    "acquisition_budget": "budget_id",
}

CONTRACT_ENTITIES = (
    "acquisition_run",
    "universe_manifest",
    "universe_membership",
    "vendor_request",
    "security_observation",
    "directional_evaluation",
    "screening_decision",
    "gate_evaluation",
    "feature_observation",
    "score_evaluation",
    "ranking_decision",
    "strategy_evaluation",
    "editorial_decision",
    "market_outcome",
    "research_population_manifest",
    "derived_build_reference",
    "evidence_checkpoint_manifest",
    "terminal_publication_manifest",
    "acquisition_telemetry",
    "acquisition_budget",
)


def _fail(message: str) -> None:
    raise EvidenceContractError(message)


def _schema(value: str) -> None:
    if value != EVIDENCE_SCHEMA_VERSION:
        _fail(
            "unsupported evidence schema version: {0}. Supported: {1}".format(
                value, EVIDENCE_SCHEMA_VERSION
            )
        )


def _text(name: str, value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("{0} must be a non-empty string".format(name))
    return value


def _optional_text(name: str, value: Any) -> None:
    if value is not None and (not isinstance(value, str) or not value.strip()):
        _fail("{0} must be a non-empty string when set".format(name))


def _int(name: str, value: Any, minimum: Optional[int] = None) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        _fail("{0} must be an integer".format(name))
    if minimum is not None and value < minimum:
        _fail("{0} must be >= {1}".format(name, minimum))
    return value


def _optional_int(name: str, value: Any, minimum: Optional[int] = None) -> None:
    if value is not None:
        _int(name, value, minimum)


def _number(name: str, value: Any) -> None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("{0} must be a finite number".format(name))
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        _fail("{0} must be a finite number".format(name))


def _optional_number(name: str, value: Any) -> None:
    if value is not None:
        _number(name, value)


def _aware(name: str, value: Any) -> datetime:
    if not isinstance(value, datetime):
        _fail("{0} must be a timezone-aware datetime".format(name))
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        _fail("{0} must be a timezone-aware datetime".format(name))
    return value


def _optional_aware(name: str, value: Any) -> None:
    if value is not None:
        _aware(name, value)


def _enum(name: str, value: Any, kind: type) -> Enum:
    if not isinstance(value, kind):
        _fail("{0} must be {1}".format(name, kind.__name__))
    return value


def _reason_map(pairs: Sequence[Tuple[str, str]]) -> Dict[str, str]:
    if not isinstance(pairs, tuple):
        _fail("missing_reasons must be a tuple of pairs")
    keys = []
    mapped: Dict[str, str] = {}
    for item in pairs:
        if not isinstance(item, tuple) or len(item) != 2:
            _fail("missing_reasons entries must be (field, reason) pairs")
        key, reason = item
        _text("missing_reasons field", key)
        _text("missing_reasons reason", reason)
        if key in mapped:
            _fail("duplicate missing reason for {0}".format(key))
        mapped[key] = reason
        keys.append(key)
    if keys != sorted(keys):
        _fail("missing_reasons must be sorted by field name")
    return mapped


def _require_reason(pairs: Sequence[Tuple[str, str]], field: str, value: Any) -> None:
    mapped = _reason_map(pairs)
    if value is None and field not in mapped:
        _fail("{0} is null and requires missing_reasons".format(field))
    if value is not None and field in mapped:
        _fail("{0} is present and must not have missing_reasons".format(field))


def _sha256_text(value: str) -> None:
    _text("sha256", value)
    if not re.fullmatch(r"[0-9a-f]{64}", value):
        _fail("sha256 must be 64 lowercase hex characters")


def canonical_json_bytes(value: Any) -> bytes:
    """Deterministic UTF-8 JSON. Object keys are sorted. No whitespace."""
    ready = _jsonable(value)
    return json.dumps(
        ready, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_canonical(value: Any) -> str:
    return sha256_bytes(canonical_json_bytes(value))


def _jsonable(value: Any) -> Any:
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(value).items()}
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, datetime):
        _aware("datetime", value)
        return value.isoformat()
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        _fail("non-finite numbers cannot be serialized")
    return value


def record_to_dict(record: Any) -> Dict[str, Any]:
    if not is_dataclass(record):
        _fail("record_to_dict requires a dataclass")
    return _jsonable(record)


def is_decisive_evaluation(state: EvaluationState) -> bool:
    """True only for an evaluation that actually passed or failed."""
    return state.value in DECISIVE_EVALUATION_STATES


def make_acquisition_run_id(
    *,
    acquisition_role: AcquisitionRole,
    pipeline: Pipeline,
    started_at: datetime,
    attempt_number: int,
    execution_nonce: str,
) -> str:
    """Collision-resistant run id.

    The UTC timestamp is minute-visible. attempt_number distinguishes a retry.
    execution_nonce distinguishes concurrent or repeated starts that share a
    clock second. Production and research roles cannot share an id.
    """
    _enum("acquisition_role", acquisition_role, AcquisitionRole)
    _enum("pipeline", pipeline, Pipeline)
    started = _aware("started_at", started_at).astimezone(timezone.utc)
    _int("attempt_number", attempt_number, 1)
    if not isinstance(execution_nonce, str) or _NONCE_RE.fullmatch(execution_nonce) is None:
        _fail("execution_nonce must be 8 or more URL-safe characters")
    stamp = started.strftime("%Y%m%dT%H%M%SZ")
    return "{role}:{pipeline}:{stamp}:attempt-{attempt}:{nonce}".format(
        role=acquisition_role.value,
        pipeline=pipeline.value,
        stamp=stamp,
        attempt=attempt_number,
        nonce=execution_nonce,
    )


def sanitize_url(url: str) -> str:
    """Drop URL credentials and redact signed-query secrets. Keep data params."""
    parts = urlsplit(url)
    if parts.username or parts.password:
        host = parts.hostname or ""
        if parts.port:
            host = "{0}:{1}".format(host, parts.port)
        netloc = host
    else:
        netloc = parts.netloc
    query = []
    for key, value in parse_qsl(parts.query, keep_blank_values=True):
        if key.lower() in _SECRET_KEYS or key.lower() in _DROPPED_KEYS:
            query.append((key, REDACTED))
        else:
            query.append((key, value))
    return urlunsplit((parts.scheme, netloc, parts.path, urlencode(query), ""))


def sanitize_params(params: Mapping[str, Any]) -> Dict[str, Any]:
    """Remove secrets before hashing or serialization.

    Authorization headers, cookies, and subscriber addresses are dropped.
    Secret query values are replaced with a redaction token so two data
    requests that differ only by an API key share a fingerprint, while two
    symbols do not.
    """
    if not isinstance(params, Mapping):
        _fail("params must be a mapping")
    return _scrub(params)


def _scrub(value: Any) -> Any:
    if isinstance(value, Mapping):
        cleaned: Dict[str, Any] = {}
        for key, item in value.items():
            name = str(key)
            lowered = name.lower()
            if lowered in _DROPPED_KEYS:
                continue
            if lowered in _SECRET_KEYS:
                cleaned[name] = REDACTED
                continue
            cleaned[name] = _scrub(item)
        return cleaned
    if isinstance(value, (list, tuple)):
        return [_scrub(item) for item in value]
    if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
        return sanitize_url(value)
    return value


def assert_text_free_of(text: str, forbidden: Sequence[str]) -> None:
    for secret in forbidden:
        if secret and secret in text:
            _fail("serialized evidence contains forbidden material")
    if "Bearer " in text or "Authorization:" in text:
        _fail("serialized evidence contains an authorization header")


def params_fingerprint(sanitized: Mapping[str, Any]) -> str:
    return sha256_canonical(sanitized)


def payload_identity(
    *,
    kind: PayloadBytesKind,
    raw_bytes: Optional[bytes] = None,
    parsed: Any = None,
) -> Tuple[str, bool]:
    """Return (sha256, byte_exact).

    Parsed or reserialized JSON is never reported as the vendor's raw bytes.
    """
    _enum("payload kind", kind, PayloadBytesKind)
    if kind == PayloadBytesKind.ABSENT:
        _fail("absent payloads have no content hash")
    if kind == PayloadBytesKind.RAW_RESPONSE_BYTES:
        if not isinstance(raw_bytes, (bytes, bytearray)):
            _fail("raw payload bytes are required for byte-exact identity")
        return sha256_bytes(bytes(raw_bytes)), True
    if parsed is None:
        _fail("parsed payload is required for a normalized hash")
    return sha256_canonical(parsed), False


@dataclass(frozen=True)
class StageAccount:
    """One stage's planned work, partitioned without overlap.

    expected_attempts = attempted + excluded_resolved + interrupted.
    succeeded + failed = attempted.
    capacity_limit is the maximum the methodology could have accepted. It is
    not the expected count. A stage that qualified 3 names against a capacity
    of 20 is complete when those 3 were finished and nothing was interrupted.
    """

    stage: str
    expected_attempts: int
    attempted: int
    succeeded: int
    failed: int
    excluded_resolved: int
    interrupted: int
    capacity_limit: Optional[int] = None

    def identity_holds(self) -> bool:
        numbers = (
            self.expected_attempts,
            self.attempted,
            self.succeeded,
            self.failed,
            self.excluded_resolved,
            self.interrupted,
        )
        if any(isinstance(item, bool) or not isinstance(item, int) or item < 0 for item in numbers):
            return False
        if self.succeeded + self.failed != self.attempted:
            return False
        if self.attempted + self.excluded_resolved + self.interrupted != self.expected_attempts:
            return False
        if self.capacity_limit is not None:
            if isinstance(self.capacity_limit, bool) or not isinstance(self.capacity_limit, int):
                return False
            if self.capacity_limit < 0 or self.expected_attempts > self.capacity_limit:
                return False
        return True

    def resolved(self) -> bool:
        return self.identity_holds() and self.interrupted == 0


def validate_stage_accounts(stages: Sequence[StageAccount]) -> None:
    seen = set()
    for stage in stages:
        if not isinstance(stage, StageAccount):
            _fail("stage account must be a StageAccount")
        _text("stage", stage.stage)
        if stage.stage in seen:
            _fail("duplicate stage account: {0}".format(stage.stage))
        seen.add(stage.stage)
        if not stage.identity_holds():
            _fail("stage accounting does not partition: {0}".format(stage.stage))
    by_name = {stage.stage: stage for stage in stages}
    if "screening" in by_name and "deep_evaluation" in by_name:
        screening = by_name["screening"]
        deep = by_name["deep_evaluation"]
        if deep.expected_attempts != screening.succeeded:
            _fail(
                "deep_evaluation expected_attempts must equal screening successes"
            )


def assess_capture_completeness(
    execution_status: ExecutionStatus,
    stages: Sequence[StageAccount],
) -> CaptureCompleteness:
    """Completeness follows the plan that was actually attempted.

    A completed execution whose stages are all resolved is complete, even when
    succeeded is below capacity_limit. Interrupted planned work is incomplete
    and cannot be labeled completed.
    """
    _enum("execution_status", execution_status, ExecutionStatus)
    validate_stage_accounts(stages)
    if execution_status == ExecutionStatus.STARTED and not stages:
        return CaptureCompleteness.NOT_ASSESSED
    if stages and all(stage.resolved() for stage in stages):
        if execution_status == ExecutionStatus.COMPLETED:
            return CaptureCompleteness.COMPLETE
        return CaptureCompleteness.INCOMPLETE
    return CaptureCompleteness.INCOMPLETE


def _source_rules(
    *,
    source_digest_sha256: str,
    source_artifact_kind: SourceArtifactKind,
    deployment_verified: bool,
) -> None:
    _sha256_text(source_digest_sha256)
    _enum("source_artifact_kind", source_artifact_kind, SourceArtifactKind)
    if not isinstance(deployment_verified, bool):
        _fail("deployment_verified must be a boolean")
    if source_digest_sha256 == INSPECTED_PYTHON_SOURCE_SHA256:
        if source_artifact_kind != SourceArtifactKind.PYTHON_SOURCE:
            _fail("inspected Python source digest cannot label another artifact")
        if deployment_verified:
            _fail("inspected Python source is not a verified deployment identity")
    if source_digest_sha256 == INSPECTED_SOURCE_ARCHIVE_SHA256:
        if source_artifact_kind != SourceArtifactKind.SOURCE_ARCHIVE:
            _fail("inspected archive digest cannot label another artifact")
        if deployment_verified:
            _fail("inspected source archive is not a verified deployment identity")


def _eligibility_for_pipeline(
    pipeline: Pipeline, eligibility: ResearchEligibility
) -> None:
    if pipeline in (Pipeline.PRODUCTION_BATCH, Pipeline.PRODUCTION_SINGLE):
        if eligibility != ResearchEligibility.EXCLUDED_FROM_CANONICAL:
            _fail(
                "production observations stay outside the canonical eligible population"
            )


@dataclass(frozen=True)
class AcquisitionRun:
    """One execution. Identity is acquisition_run_id."""

    evidence_schema_version: str
    acquisition_run_id: str
    execution_nonce: str
    attempt_number: int
    acquisition_role: AcquisitionRole
    pipeline: Pipeline
    capture_origin: CaptureOrigin
    execution_status: ExecutionStatus
    capture_completeness: CaptureCompleteness
    publication_status: PublicationStatus
    research_eligibility: ResearchEligibility
    methodology_role: MethodologyRole
    methodology_id: str
    evidence_derivation: EvidenceDerivation
    source_identity: str
    source_artifact_kind: SourceArtifactKind
    source_digest_sha256: str
    deployment_verified: bool
    universe_manifest_id: str
    started_at: datetime
    stage_accounts: Tuple[StageAccount, ...]
    deployment_revision: Optional[str] = None
    configuration_id: Optional[str] = None
    finished_at: Optional[datetime] = None

    uniqueness: ClassVar[Tuple[str, ...]] = ("acquisition_run_id",)


@dataclass(frozen=True)
class UniverseManifest:
    evidence_schema_version: str
    universe_manifest_id: str
    member_fingerprint_sha256: str
    symbol_count: int
    created_at: datetime
    source_identity: str
    frame_description: str
    configuration_id: Optional[str] = None

    uniqueness: ClassVar[Tuple[str, ...]] = ("universe_manifest_id",)


@dataclass(frozen=True)
class UniverseMembership:
    """Ticker as observed, plus an instrument id only when one was supplied."""

    evidence_schema_version: str
    membership_id: str
    universe_manifest_id: str
    acquisition_run_id: str
    symbol_as_observed: str
    instrument_id_source: InstrumentIdSource
    instrument_event: InstrumentEvent
    registration_ordinal: int
    sector_as_registered: str
    sector_resolved: str
    wind_resolved: int
    resolution_rule: str
    exchange: Optional[str] = None
    instrument_id: Optional[str] = None
    prior_symbol: Optional[str] = None
    duplicate_of_ordinal: Optional[int] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "universe_manifest_id",
        "symbol_as_observed",
        "exchange",
        "registration_ordinal",
    )


@dataclass(frozen=True)
class VendorRequest:
    evidence_schema_version: str
    vendor_request_id: str
    acquisition_run_id: str
    vendor: str
    endpoint: str
    sanitized_params: Dict[str, Any]
    params_sha256: str
    request_started_at: datetime
    request_state: VendorRequestState
    external_request: bool
    retry_ordinal: int
    payload_bytes_kind: PayloadBytesKind
    byte_exact: bool
    response_received_at: Optional[datetime] = None
    cache_retrieved_at: Optional[datetime] = None
    vendor_effective_at: Optional[datetime] = None
    vendor_effective_source: TimeSource = TimeSource.UNAVAILABLE
    http_status: Optional[int] = None
    payload_sha256: Optional[str] = None
    payload_ref: Optional[str] = None

    uniqueness: ClassVar[Tuple[str, ...]] = ("vendor_request_id",)


@dataclass(frozen=True)
class SecurityObservation:
    evidence_schema_version: str
    observation_id: str
    acquisition_run_id: str
    symbol_as_observed: str
    membership_id: str
    quote_state: VendorRequestState
    quote_as_of_source: TimeSource
    evidence_derivation: EvidenceDerivation
    methodology_id: str
    missing_reasons: Tuple[Tuple[str, str], ...]
    quote_request_id: Optional[str] = None
    price: Optional[float] = None
    year_high: Optional[float] = None
    year_low: Optional[float] = None
    volume: Optional[float] = None
    change_percentage: Optional[float] = None
    quote_as_of: Optional[datetime] = None
    response_received_at: Optional[datetime] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "acquisition_run_id",
        "symbol_as_observed",
    )


@dataclass(frozen=True)
class DirectionalEvaluation:
    evidence_schema_version: str
    directional_evaluation_id: str
    observation_id: str
    direction: Direction
    methodology_id: str
    methodology_role: MethodologyRole
    evidence_derivation: EvidenceDerivation
    evaluation_state: EvaluationState
    acquisition_role: AcquisitionRole

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "observation_id",
        "direction",
        "methodology_id",
    )


@dataclass(frozen=True)
class ScreeningDecision:
    evidence_schema_version: str
    screening_decision_id: str
    directional_evaluation_id: str
    stage: str
    decision_state: EvaluationState
    decided_at: datetime
    missing_reasons: Tuple[Tuple[str, str], ...] = ()
    reason_code: Optional[str] = None
    measured_value: Optional[float] = None
    threshold_value: Optional[float] = None
    rank_before_cut: Optional[int] = None
    cut_limit: Optional[int] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "directional_evaluation_id",
        "stage",
    )


@dataclass(frozen=True)
class GateEvaluation:
    evidence_schema_version: str
    gate_evaluation_id: str
    directional_evaluation_id: str
    gate_name: str
    evaluation_state: EvaluationState
    evidence_derivation: EvidenceDerivation
    missing_reasons: Tuple[Tuple[str, str], ...] = ()
    production_reported_state: Optional[str] = None
    score: Optional[float] = None
    clean: Optional[bool] = None
    penalty: Optional[float] = None
    evaluated_at: Optional[datetime] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "directional_evaluation_id",
        "gate_name",
        "evidence_derivation",
    )


@dataclass(frozen=True)
class FeatureObservation:
    evidence_schema_version: str
    feature_observation_id: str
    observation_id: str
    feature_name: str
    feature_derivation: FeatureDerivation
    evidence_derivation: EvidenceDerivation
    methodology_id: str
    source_request_ids: Tuple[str, ...]
    vendor_effective_source: TimeSource
    freshness: Freshness
    freshness_basis: FreshnessBasis
    missing_reasons: Tuple[Tuple[str, str], ...] = ()
    directional_evaluation_id: Optional[str] = None
    value_number: Optional[float] = None
    value_text: Optional[str] = None
    unit: Optional[str] = None
    calculated_at: Optional[datetime] = None
    vendor_effective_at: Optional[datetime] = None
    response_received_at: Optional[datetime] = None
    cache_retrieved_at: Optional[datetime] = None
    staleness_limit_seconds: Optional[int] = None
    supersedes_feature_observation_id: Optional[str] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "observation_id",
        "directional_evaluation_id",
        "feature_name",
        "methodology_id",
        "evidence_derivation",
    )


@dataclass(frozen=True)
class ScoreEvaluation:
    evidence_schema_version: str
    score_evaluation_id: str
    directional_evaluation_id: str
    methodology_id: str
    methodology_role: MethodologyRole
    component_name: str
    calculated_at: datetime
    evidence_derivation: EvidenceDerivation
    missing_reasons: Tuple[Tuple[str, str], ...] = ()
    raw_points: Optional[float] = None
    cap: Optional[float] = None
    applied_points: Optional[float] = None
    displayed_score: Optional[float] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "directional_evaluation_id",
        "methodology_id",
        "component_name",
        "evidence_derivation",
    )


@dataclass(frozen=True)
class RankingDecision:
    evidence_schema_version: str
    ranking_decision_id: str
    acquisition_run_id: str
    direction: Direction
    stage: str
    symbol_as_observed: str
    included: bool
    sort_key: str
    ranked_at: datetime
    decision_available_at: datetime
    rank: Optional[int] = None
    cut_limit: Optional[int] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "acquisition_run_id",
        "direction",
        "stage",
        "symbol_as_observed",
    )


@dataclass(frozen=True)
class StrategyEvaluation:
    evidence_schema_version: str
    strategy_evaluation_id: str
    directional_evaluation_id: str
    strategy_methodology_id: str
    is_formula_strike: bool
    contract_verified: bool
    chain_present: bool
    chain_establishes_execution: bool
    strategy_name: Optional[str] = None
    expiration_text: Optional[str] = None
    conviction: Optional[str] = None
    conservative_strike: Optional[float] = None
    risky_strike: Optional[float] = None
    contract_symbol: Optional[str] = None
    execution_evidence_id: Optional[str] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "directional_evaluation_id",
        "strategy_methodology_id",
    )


@dataclass(frozen=True)
class EditorialDecision:
    evidence_schema_version: str
    editorial_decision_id: str
    report_id: str
    symbol_as_observed: str
    email_classification: EmailClassification
    parser_version: str
    decided_at: datetime
    decision_available_at: datetime
    evidence_derivation: EvidenceDerivation
    missing_reasons: Tuple[Tuple[str, str], ...] = ()
    acquisition_run_id: Optional[str] = None
    direction: Optional[str] = None
    strategy_text: Optional[str] = None
    claude_model: Optional[str] = None
    prompt_digest_sha256: Optional[str] = None
    source_excerpt: Optional[str] = None

    uniqueness: ClassVar[Tuple[str, ...]] = ("editorial_decision_id",)


@dataclass(frozen=True)
class MarketOutcome:
    evidence_schema_version: str
    outcome_id: str
    subject_id: str
    anchor_type: OutcomeAnchor
    outcome_methodology_id: str
    outcome_kind: OutcomeKind
    horizon_sessions: int
    entry_at: datetime
    evidence_derivation: EvidenceDerivation
    formula_strike_used: bool
    contract_verified: bool
    chain_present: bool
    value: Optional[float] = None
    missing_reason: Optional[str] = None
    observation_at: Optional[datetime] = None
    decision_available_at: Optional[datetime] = None
    observed_at: Optional[datetime] = None
    price_source: Optional[str] = None
    contract_symbol: Optional[str] = None
    execution_evidence_id: Optional[str] = None

    uniqueness: ClassVar[Tuple[str, ...]] = (
        "subject_id",
        "outcome_methodology_id",
        "horizon_sessions",
        "outcome_kind",
        "anchor_type",
    )


@dataclass(frozen=True)
class PopulationMember:
    symbol_as_observed: str
    holdout_status: HoldoutStatus
    inclusion_reason: str
    sampling_methodology_id: str
    stratum_id: Optional[str] = None
    selection_probability: Optional[float] = None
    exclusion_reason: Optional[str] = None


@dataclass(frozen=True)
class ResearchPopulationManifest:
    evidence_schema_version: str
    population_id: str
    population_role: PopulationRole
    sampling_kind: SamplingKind
    sampling_methodology_id: str
    sampling_frame: str
    selection_timestamp: datetime
    population_fingerprint_sha256: str
    symbol_count: int
    methodology_id: str
    evidence_derivation: EvidenceDerivation
    capture_origin: CaptureOrigin
    provisional: bool
    approved_acquisition: bool
    permanent_definition: bool
    bound_to_production_drawdown_rule: bool
    members_embedded: bool
    members: Tuple[PopulationMember, ...]
    inclusion_reasons: Tuple[str, ...] = ()
    exclusion_reasons: Tuple[str, ...] = ()
    random_seed: Optional[str] = None
    hypothesis_id: Optional[str] = None
    parent_population_id: Optional[str] = None
    holdout_status: HoldoutStatus = HoldoutStatus.NOT_APPLICABLE

    uniqueness: ClassVar[Tuple[str, ...]] = ("population_id",)


@dataclass(frozen=True)
class DerivedBuildReference:
    """A pointer to an existing build. This type does not publish rows."""

    evidence_schema_version: str
    build_id: str
    population_fingerprint_sha256: str
    methodology_id: str
    artifact_type: str
    status: str
    publishes_new_rows: bool
    population_id: Optional[str] = None

    uniqueness: ClassVar[Tuple[str, ...]] = ("build_id",)


@dataclass(frozen=True)
class RunStartRecord:
    """Immutable start record. Later checkpoints cite run_start_id."""

    evidence_schema_version: str
    run_start_id: str
    acquisition_run_id: str
    acquisition_role: AcquisitionRole
    pipeline: Pipeline
    execution_nonce: str
    attempt_number: int
    started_at: datetime
    universe_manifest_id: str

    uniqueness: ClassVar[Tuple[str, ...]] = ("run_start_id",)


@dataclass(frozen=True)
class ChunkRef:
    entity: str
    object_key: str
    sha256: str
    row_count: int
    uncompressed_byte_length: int


@dataclass(frozen=True)
class EvidenceCheckpointManifest:
    """Immutable checkpoint. Sequence identity is checkpoint_id.

    Publishing a later stage creates a new checkpoint. It does not rewrite
    this object. Production writers are not implemented here.
    """

    evidence_schema_version: str
    checkpoint_id: str
    acquisition_run_id: str
    run_start_id: str
    sequence: int
    stage: str
    published_at: datetime
    execution_status: ExecutionStatus
    capture_completeness: CaptureCompleteness
    chunks: Tuple[ChunkRef, ...]
    previous_checkpoint_id: Optional[str] = None

    uniqueness: ClassVar[Tuple[str, ...]] = ("checkpoint_id",)


@dataclass(frozen=True)
class TerminalPublicationManifest:
    """Immutable terminal record. It cites checkpoints. It does not replace them."""

    evidence_schema_version: str
    terminal_id: str
    acquisition_run_id: str
    run_start_id: str
    checkpoint_ids: Tuple[str, ...]
    checkpoint_sha256: Tuple[Tuple[str, str], ...]
    execution_status: ExecutionStatus
    capture_completeness: CaptureCompleteness
    publication_status: PublicationStatus
    published_at: datetime
    catalog_object_key: str

    uniqueness: ClassVar[Tuple[str, ...]] = ("terminal_id",)


@dataclass(frozen=True)
class CatalogEntry:
    """One immutable catalog object per run. Not an appended JSONL log."""

    evidence_schema_version: str
    acquisition_run_id: str
    terminal_id: str
    catalog_object_key: str
    execution_status: ExecutionStatus
    published_at: datetime

    uniqueness: ClassVar[Tuple[str, ...]] = ("acquisition_run_id",)


@dataclass(frozen=True)
class LatestPointer:
    """Optional mutable pointer. Generation is the compare-and-swap token.

    Discovery of historical runs uses catalog objects and run-start objects.
    This pointer is not required to find them.
    """

    evidence_schema_version: str
    role: str
    generation: int
    acquisition_run_id: str
    catalog_object_key: str


def validate_acquisition_run(record: AcquisitionRun) -> None:
    _schema(record.evidence_schema_version)
    _text("acquisition_run_id", record.acquisition_run_id)
    if _NONCE_RE.fullmatch(record.execution_nonce) is None:
        _fail("execution_nonce must be 8 or more URL-safe characters")
    _int("attempt_number", record.attempt_number, 1)
    _enum("acquisition_role", record.acquisition_role, AcquisitionRole)
    _enum("pipeline", record.pipeline, Pipeline)
    _enum("capture_origin", record.capture_origin, CaptureOrigin)
    _enum("execution_status", record.execution_status, ExecutionStatus)
    _enum("capture_completeness", record.capture_completeness, CaptureCompleteness)
    _enum("publication_status", record.publication_status, PublicationStatus)
    _enum("research_eligibility", record.research_eligibility, ResearchEligibility)
    _enum("methodology_role", record.methodology_role, MethodologyRole)
    _text("methodology_id", record.methodology_id)
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    _text("source_identity", record.source_identity)
    _source_rules(
        source_digest_sha256=record.source_digest_sha256,
        source_artifact_kind=record.source_artifact_kind,
        deployment_verified=record.deployment_verified,
    )
    _optional_text("deployment_revision", record.deployment_revision)
    _optional_text("configuration_id", record.configuration_id)
    _text("universe_manifest_id", record.universe_manifest_id)
    started = _aware("started_at", record.started_at)
    if record.finished_at is not None:
        finished = _aware("finished_at", record.finished_at)
        if finished < started:
            _fail("finished_at precedes started_at")
    expected_id = make_acquisition_run_id(
        acquisition_role=record.acquisition_role,
        pipeline=record.pipeline,
        started_at=started,
        attempt_number=record.attempt_number,
        execution_nonce=record.execution_nonce,
    )
    if record.acquisition_run_id != expected_id:
        _fail("acquisition_run_id does not match its identity parts")
    if not isinstance(record.stage_accounts, tuple):
        _fail("stage_accounts must be a tuple")
    validate_stage_accounts(record.stage_accounts)
    assessed = assess_capture_completeness(
        record.execution_status, record.stage_accounts
    )
    if record.capture_completeness != assessed:
        _fail(
            "capture_completeness {0} does not match stage accounting {1}".format(
                record.capture_completeness.value, assessed.value
            )
        )
    if record.stage_accounts and all(stage.resolved() for stage in record.stage_accounts):
        if record.execution_status != ExecutionStatus.COMPLETED:
            _fail("resolved stages require execution_status completed")
    if record.execution_status == ExecutionStatus.COMPLETED and assessed != CaptureCompleteness.COMPLETE:
        _fail("a completed execution requires resolved stage accounting")
    if assessed == CaptureCompleteness.COMPLETE and record.execution_status != ExecutionStatus.COMPLETED:
        _fail("resolved stages require execution_status completed")
    terminal_states = {
        ExecutionStatus.COMPLETED,
        ExecutionStatus.FAILED,
        ExecutionStatus.TIMED_OUT,
        ExecutionStatus.ABANDONED,
    }
    if record.publication_status == PublicationStatus.TERMINALLY_PUBLISHED:
        if record.execution_status not in terminal_states:
            _fail("a terminal publication requires a terminal execution status")
    _eligibility_for_pipeline(record.pipeline, record.research_eligibility)


def validate_universe_manifest(record: UniverseManifest) -> None:
    _schema(record.evidence_schema_version)
    _text("universe_manifest_id", record.universe_manifest_id)
    _sha256_text(record.member_fingerprint_sha256)
    _int("symbol_count", record.symbol_count, 0)
    _aware("created_at", record.created_at)
    _text("source_identity", record.source_identity)
    _text("frame_description", record.frame_description)
    _optional_text("configuration_id", record.configuration_id)


def validate_universe_membership(record: UniverseMembership) -> None:
    _schema(record.evidence_schema_version)
    _text("membership_id", record.membership_id)
    _text("universe_manifest_id", record.universe_manifest_id)
    _text("acquisition_run_id", record.acquisition_run_id)
    _text("symbol_as_observed", record.symbol_as_observed)
    _enum("instrument_id_source", record.instrument_id_source, InstrumentIdSource)
    _enum("instrument_event", record.instrument_event, InstrumentEvent)
    _int("registration_ordinal", record.registration_ordinal, 1)
    _text("sector_as_registered", record.sector_as_registered)
    _text("sector_resolved", record.sector_resolved)
    _int("wind_resolved", record.wind_resolved)
    _text("resolution_rule", record.resolution_rule)
    _optional_text("exchange", record.exchange)
    _optional_text("instrument_id", record.instrument_id)
    _optional_text("prior_symbol", record.prior_symbol)
    _optional_int("duplicate_of_ordinal", record.duplicate_of_ordinal, 1)
    if record.instrument_id_source == InstrumentIdSource.UNAVAILABLE:
        if record.instrument_id is not None:
            _fail("unavailable instrument identity must be null")
    elif record.instrument_id is None:
        _fail("instrument_id is required when a source claims one")
    if record.instrument_event in (InstrumentEvent.TICKER_CHANGE, InstrumentEvent.MERGER):
        if record.prior_symbol is None:
            _fail("ticker changes and mergers require prior_symbol")
    if record.instrument_event == InstrumentEvent.EXCHANGE_DISTINCTION:
        if record.exchange is None:
            _fail("exchange distinctions require exchange")
    expected = "{0}:{1}:{2}".format(
        record.universe_manifest_id,
        record.symbol_as_observed,
        record.registration_ordinal,
    )
    if record.exchange:
        expected = "{0}:{1}".format(expected, record.exchange)
    if record.membership_id != expected:
        _fail("membership_id does not match symbol, ordinal, and manifest")


def validate_vendor_request(record: VendorRequest) -> None:
    _schema(record.evidence_schema_version)
    _text("vendor_request_id", record.vendor_request_id)
    _text("acquisition_run_id", record.acquisition_run_id)
    _text("vendor", record.vendor)
    _text("endpoint", record.endpoint)
    if not isinstance(record.sanitized_params, dict):
        _fail("sanitized_params must be a dict")
    scrubbed = sanitize_params(record.sanitized_params)
    if scrubbed != record.sanitized_params:
        _fail("sanitized_params still contain secret or address fields")
    serialized = canonical_json_bytes(record.sanitized_params).decode("utf-8")
    assert_text_free_of(serialized, ())
    fingerprint = params_fingerprint(record.sanitized_params)
    if record.params_sha256 != fingerprint:
        _fail("params_sha256 does not match sanitized params")
    _aware("request_started_at", record.request_started_at)
    _enum("request_state", record.request_state, VendorRequestState)
    if not isinstance(record.external_request, bool):
        _fail("external_request must be a boolean")
    _int("retry_ordinal", record.retry_ordinal, 0)
    _enum("payload_bytes_kind", record.payload_bytes_kind, PayloadBytesKind)
    if not isinstance(record.byte_exact, bool):
        _fail("byte_exact must be a boolean")
    _optional_aware("response_received_at", record.response_received_at)
    _optional_aware("cache_retrieved_at", record.cache_retrieved_at)
    _optional_aware("vendor_effective_at", record.vendor_effective_at)
    _enum("vendor_effective_source", record.vendor_effective_source, TimeSource)
    _optional_int("http_status", record.http_status, 0)
    if record.payload_sha256 is not None:
        _sha256_text(record.payload_sha256)
    _optional_text("payload_ref", record.payload_ref)
    if record.vendor_effective_source == TimeSource.UNAVAILABLE:
        if record.vendor_effective_at is not None:
            _fail("missing vendor effective time must stay null")
    elif record.vendor_effective_at is None:
        _fail("vendor effective time requires a timestamp")
    non_external = {VendorRequestState.CACHE_HIT, VendorRequestState.BUDGET_BLOCKED}
    if record.request_state in non_external:
        if record.external_request:
            _fail("cache hits and budget blocks are not external vendor requests")
    elif not record.external_request:
        _fail("this request state counts as an external vendor request")
    if record.request_state == VendorRequestState.CACHE_HIT:
        if record.response_received_at is None or record.cache_retrieved_at is None:
            _fail("a cache hit keeps original receipt time and retrieval time")
        if record.cache_retrieved_at < record.response_received_at:
            _fail("cache retrieval cannot precede the original acquisition")
    if record.byte_exact and record.payload_bytes_kind != PayloadBytesKind.RAW_RESPONSE_BYTES:
        _fail("only raw response bytes can be marked byte-exact")
    if record.payload_bytes_kind == PayloadBytesKind.PARSED_OBJECT_ONLY and record.byte_exact:
        _fail("a parsed object is not byte-exact vendor evidence")
    if record.payload_bytes_kind == PayloadBytesKind.ABSENT and record.payload_sha256 is not None:
        _fail("an absent payload has no content hash")
    if record.response_received_at is not None and record.response_received_at < record.request_started_at:
        _fail("response receipt precedes request start")


def validate_security_observation(record: SecurityObservation) -> None:
    _schema(record.evidence_schema_version)
    _text("observation_id", record.observation_id)
    _text("acquisition_run_id", record.acquisition_run_id)
    _text("symbol_as_observed", record.symbol_as_observed)
    _text("membership_id", record.membership_id)
    _enum("quote_state", record.quote_state, VendorRequestState)
    _enum("quote_as_of_source", record.quote_as_of_source, TimeSource)
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    _text("methodology_id", record.methodology_id)
    _optional_text("quote_request_id", record.quote_request_id)
    for name, value in (
        ("price", record.price),
        ("year_high", record.year_high),
        ("year_low", record.year_low),
        ("volume", record.volume),
        ("change_percentage", record.change_percentage),
    ):
        _optional_number(name, value)
        _require_reason(record.missing_reasons, name, value)
    _optional_aware("quote_as_of", record.quote_as_of)
    _optional_aware("response_received_at", record.response_received_at)
    _require_reason(record.missing_reasons, "quote_as_of", record.quote_as_of)
    if record.quote_as_of_source == TimeSource.UNAVAILABLE:
        if record.quote_as_of is not None:
            _fail("receipt time cannot be stored as vendor effective time")
    elif record.quote_as_of is None:
        _fail("vendor quote time requires quote_as_of")
    expected = "{0}:{1}".format(record.acquisition_run_id, record.symbol_as_observed)
    if record.observation_id != expected:
        _fail("observation_id does not match run and ticker")


def validate_directional_evaluation(record: DirectionalEvaluation) -> None:
    _schema(record.evidence_schema_version)
    _text("directional_evaluation_id", record.directional_evaluation_id)
    _text("observation_id", record.observation_id)
    _enum("direction", record.direction, Direction)
    _text("methodology_id", record.methodology_id)
    _enum("methodology_role", record.methodology_role, MethodologyRole)
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    _enum("evaluation_state", record.evaluation_state, EvaluationState)
    _enum("acquisition_role", record.acquisition_role, AcquisitionRole)
    expected = "{0}:{1}:{2}".format(
        record.observation_id, record.direction.value, record.methodology_id
    )
    if record.directional_evaluation_id != expected:
        _fail("directional_evaluation_id does not match observation, direction, and methodology")


def validate_screening_decision(record: ScreeningDecision) -> None:
    _schema(record.evidence_schema_version)
    _text("screening_decision_id", record.screening_decision_id)
    _text("directional_evaluation_id", record.directional_evaluation_id)
    _text("stage", record.stage)
    _enum("decision_state", record.decision_state, EvaluationState)
    _aware("decided_at", record.decided_at)
    _optional_text("reason_code", record.reason_code)
    _optional_number("measured_value", record.measured_value)
    _optional_number("threshold_value", record.threshold_value)
    _require_reason(record.missing_reasons, "measured_value", record.measured_value)
    _optional_int("rank_before_cut", record.rank_before_cut, 1)
    _optional_int("cut_limit", record.cut_limit, 1)
    if record.decision_state == EvaluationState.NOT_SELECTED_RANKING_TRUNCATION:
        if record.cut_limit is None or record.rank_before_cut is None:
            _fail("ranking truncation requires rank_before_cut and cut_limit")


def validate_gate_evaluation(record: GateEvaluation) -> None:
    _schema(record.evidence_schema_version)
    _text("gate_evaluation_id", record.gate_evaluation_id)
    _text("directional_evaluation_id", record.directional_evaluation_id)
    _text("gate_name", record.gate_name)
    _enum("evaluation_state", record.evaluation_state, EvaluationState)
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    _optional_text("production_reported_state", record.production_reported_state)
    _optional_number("score", record.score)
    _require_reason(record.missing_reasons, "score", record.score)
    if record.clean is not None and not isinstance(record.clean, bool):
        _fail("clean must be a boolean when set")
    _optional_number("penalty", record.penalty)
    _optional_aware("evaluated_at", record.evaluated_at)
    evaluated = {
        EvaluationState.EVALUATED_PASSED,
        EvaluationState.EVALUATED_FAILED,
        EvaluationState.EVALUATED_MISSING_INPUTS,
    }
    if record.evaluation_state in evaluated and record.evaluated_at is None:
        _fail("an evaluated gate requires evaluated_at")
    if record.evaluation_state not in evaluated and record.evaluated_at is not None:
        _fail("an unevaluated gate cannot carry evaluated_at")
    if record.production_reported_state == "clean_true_placeholder":
        if record.evaluation_state != EvaluationState.NOT_EVALUATED_EARLIER_FAILURE:
            _fail("a skipped Gate 4 placeholder cannot validate as a pass or fail")
        if record.clean is not False and record.clean is not None:
            _fail("a skipped Gate 4 placeholder is not a clean pass")
        if is_decisive_evaluation(record.evaluation_state):
            _fail("a skipped gate is not a pass or fail")
    if record.evaluation_state == EvaluationState.EVALUATED_MISSING_INPUTS:
        if not record.missing_reasons:
            _fail("missing gate inputs require missing_reasons")


def validate_feature_observation(record: FeatureObservation) -> None:
    _schema(record.evidence_schema_version)
    _text("feature_observation_id", record.feature_observation_id)
    _text("observation_id", record.observation_id)
    _text("feature_name", record.feature_name)
    _enum("feature_derivation", record.feature_derivation, FeatureDerivation)
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    _text("methodology_id", record.methodology_id)
    if not isinstance(record.source_request_ids, tuple):
        _fail("source_request_ids must be a tuple")
    if len(record.source_request_ids) != len(set(record.source_request_ids)):
        _fail("source_request_ids contains a duplicate")
    for request_id in record.source_request_ids:
        _text("source_request_id", request_id)
    _enum("vendor_effective_source", record.vendor_effective_source, TimeSource)
    _enum("freshness", record.freshness, Freshness)
    _enum("freshness_basis", record.freshness_basis, FreshnessBasis)
    _optional_text("directional_evaluation_id", record.directional_evaluation_id)
    _optional_number("value_number", record.value_number)
    _optional_text("value_text", record.value_text)
    _optional_text("unit", record.unit)
    _optional_aware("calculated_at", record.calculated_at)
    _optional_aware("vendor_effective_at", record.vendor_effective_at)
    _optional_aware("response_received_at", record.response_received_at)
    _optional_aware("cache_retrieved_at", record.cache_retrieved_at)
    _optional_int("staleness_limit_seconds", record.staleness_limit_seconds, 0)
    _optional_text("supersedes_feature_observation_id", record.supersedes_feature_observation_id)
    if record.value_number is None and record.value_text is None:
        _require_reason(record.missing_reasons, "value", None)
    else:
        _reason_map(record.missing_reasons)
        if "value" in _reason_map(record.missing_reasons):
            _fail("a present feature value must not have a missing reason")
    if record.vendor_effective_source == TimeSource.UNAVAILABLE:
        if record.vendor_effective_at is not None:
            _fail("missing vendor effective time must stay null")
    elif record.vendor_effective_at is None:
        _fail("vendor effective time requires a timestamp")
    if record.freshness == Freshness.FRESH:
        if record.freshness_basis != FreshnessBasis.VENDOR_EFFECTIVE:
            _fail("freshness cannot be claimed from receipt time")
        if record.vendor_effective_at is None:
            _fail("freshness requires a vendor effective time")
    if record.freshness_basis == FreshnessBasis.UNAVAILABLE and record.freshness == Freshness.FRESH:
        _fail("unavailable timing cannot be fresh")
    if (
        record.cache_retrieved_at is not None
        and record.response_received_at is not None
        and record.cache_retrieved_at < record.response_received_at
    ):
        _fail("cache retrieval cannot precede the original acquisition")
    if (
        record.supersedes_feature_observation_id is not None
        and record.supersedes_feature_observation_id == record.feature_observation_id
    ):
        _fail("a feature cannot supersede itself")


def assert_historically_available(
    feature: FeatureObservation, decision_available_at: datetime
) -> Dict[str, Any]:
    """Reject inputs acquired after the decision that would use them.

    Cache retrieval time does not become the historical acquisition time.
    A missing vendor effective time stays not vendor-effective-safe.
    """
    validate_feature_observation(feature)
    decision_at = _aware("decision_available_at", decision_available_at)
    if feature.response_received_at is None:
        _fail("historical use requires the original response receipt time")
    if feature.response_received_at > decision_at:
        _fail("feature was acquired after the decision that would use it")
    if feature.vendor_effective_at is not None and feature.vendor_effective_at > decision_at:
        _fail("vendor effective time is after the decision")
    return {
        "usable": True,
        "vendor_effective_safe": feature.vendor_effective_source == TimeSource.VENDOR
        and feature.vendor_effective_at is not None,
        "acquired_at": feature.response_received_at,
        "cache_retrieved_at": feature.cache_retrieved_at,
    }


def validate_score_evaluation(record: ScoreEvaluation) -> None:
    _schema(record.evidence_schema_version)
    _text("score_evaluation_id", record.score_evaluation_id)
    _text("directional_evaluation_id", record.directional_evaluation_id)
    _text("methodology_id", record.methodology_id)
    _enum("methodology_role", record.methodology_role, MethodologyRole)
    if record.methodology_role != MethodologyRole.SCORING:
        _fail("score evaluations use the scoring methodology role")
    _text("component_name", record.component_name)
    _aware("calculated_at", record.calculated_at)
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    for name, value in (
        ("raw_points", record.raw_points),
        ("cap", record.cap),
        ("applied_points", record.applied_points),
        ("displayed_score", record.displayed_score),
    ):
        _optional_number(name, value)
        _require_reason(record.missing_reasons, name, value)


def validate_ranking_decision(record: RankingDecision) -> None:
    _schema(record.evidence_schema_version)
    _text("ranking_decision_id", record.ranking_decision_id)
    _text("acquisition_run_id", record.acquisition_run_id)
    _enum("direction", record.direction, Direction)
    _text("stage", record.stage)
    _text("symbol_as_observed", record.symbol_as_observed)
    if not isinstance(record.included, bool):
        _fail("included must be a boolean")
    _text("sort_key", record.sort_key)
    ranked = _aware("ranked_at", record.ranked_at)
    available = _aware("decision_available_at", record.decision_available_at)
    if available < ranked:
        _fail("decision availability precedes ranking")
    _optional_int("rank", record.rank, 1)
    _optional_int("cut_limit", record.cut_limit, 1)
    if not record.included and record.cut_limit is None:
        _fail("an excluded rank requires cut_limit")


def validate_strategy_evaluation(record: StrategyEvaluation) -> None:
    _schema(record.evidence_schema_version)
    _text("strategy_evaluation_id", record.strategy_evaluation_id)
    _text("directional_evaluation_id", record.directional_evaluation_id)
    _text("strategy_methodology_id", record.strategy_methodology_id)
    for name in (
        "is_formula_strike",
        "contract_verified",
        "chain_present",
        "chain_establishes_execution",
    ):
        if not isinstance(getattr(record, name), bool):
            _fail("{0} must be a boolean".format(name))
    _optional_text("strategy_name", record.strategy_name)
    _optional_text("expiration_text", record.expiration_text)
    _optional_text("conviction", record.conviction)
    _optional_number("conservative_strike", record.conservative_strike)
    _optional_number("risky_strike", record.risky_strike)
    _optional_text("contract_symbol", record.contract_symbol)
    _optional_text("execution_evidence_id", record.execution_evidence_id)
    if record.is_formula_strike:
        if record.contract_verified or record.contract_symbol is not None:
            _fail("a formula strike is not a verified option contract")
        if record.chain_establishes_execution:
            _fail("a formula strike does not establish an execution")
    if record.chain_present and record.chain_establishes_execution:
        if record.execution_evidence_id is None:
            _fail("an option chain alone does not establish execution")
    if record.contract_verified and record.contract_symbol is None:
        _fail("a verified contract requires contract_symbol")


def validate_editorial_decision(record: EditorialDecision) -> None:
    _schema(record.evidence_schema_version)
    _text("editorial_decision_id", record.editorial_decision_id)
    _text("report_id", record.report_id)
    _text("symbol_as_observed", record.symbol_as_observed)
    _enum("email_classification", record.email_classification, EmailClassification)
    _text("parser_version", record.parser_version)
    decided = _aware("decided_at", record.decided_at)
    available = _aware("decision_available_at", record.decision_available_at)
    if available < decided:
        _fail("decision availability precedes the editorial decision")
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    _optional_text("acquisition_run_id", record.acquisition_run_id)
    _optional_text("direction", record.direction)
    _require_reason(record.missing_reasons, "direction", record.direction)
    _optional_text("strategy_text", record.strategy_text)
    _optional_text("claude_model", record.claude_model)
    if record.prompt_digest_sha256 is not None:
        _sha256_text(record.prompt_digest_sha256)
    _optional_text("source_excerpt", record.source_excerpt)
    expected = "{0}|{1}|{2}".format(
        record.report_id,
        record.email_classification.value,
        record.symbol_as_observed,
    )
    if record.editorial_decision_id != expected:
        _fail("editorial_decision_id does not match report, class, and ticker")


def validate_market_outcome(record: MarketOutcome) -> None:
    _schema(record.evidence_schema_version)
    _text("outcome_id", record.outcome_id)
    _text("subject_id", record.subject_id)
    _enum("anchor_type", record.anchor_type, OutcomeAnchor)
    _enum("outcome_kind", record.outcome_kind, OutcomeKind)
    expected_method = OUTCOME_METHODOLOGY_BY_KIND[record.outcome_kind]
    if record.outcome_methodology_id != expected_method:
        _fail("outcome methodology id does not match outcome kind")
    if record.horizon_sessions not in TRADING_SESSION_HORIZONS:
        _fail("horizon_sessions must be a trading-session horizon")
    entry = _aware("entry_at", record.entry_at)
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    for name in ("formula_strike_used", "contract_verified", "chain_present"):
        if not isinstance(getattr(record, name), bool):
            _fail("{0} must be a boolean".format(name))
    _optional_number("value", record.value)
    _optional_text("missing_reason", record.missing_reason)
    _optional_aware("observation_at", record.observation_at)
    _optional_aware("decision_available_at", record.decision_available_at)
    _optional_aware("observed_at", record.observed_at)
    _optional_text("price_source", record.price_source)
    _optional_text("contract_symbol", record.contract_symbol)
    _optional_text("execution_evidence_id", record.execution_evidence_id)
    if record.value is None and not record.missing_reason:
        _fail("a missing outcome requires missing_reason")
    if record.value is not None and record.missing_reason is not None:
        _fail("a present outcome value must not have missing_reason")
    if record.anchor_type == OutcomeAnchor.QUOTE_OBSERVATION:
        if record.observation_at is None:
            _fail("a quote-anchored outcome requires observation_at")
        if entry < record.observation_at:
            _fail("entry precedes the quote observation")
    if record.anchor_type in DECISION_ANCHORS:
        if record.decision_available_at is None:
            _fail("a decision-anchored outcome requires decision_available_at")
        if entry < record.decision_available_at:
            _fail("entry precedes decision availability")
    if record.anchor_type == OutcomeAnchor.ACTUAL_EXECUTION:
        if record.execution_evidence_id is None:
            _fail("actual execution requires execution_evidence_id")
    if record.formula_strike_used and record.outcome_kind == OutcomeKind.OPTION_CONTRACT:
        _fail("a formula strike is not an option-contract outcome")
    if record.formula_strike_used and record.contract_verified:
        _fail("a formula strike is not a verified contract")
    if record.outcome_kind == OutcomeKind.OPTION_CONTRACT and not record.contract_verified:
        if record.value is not None:
            _fail("an unverified option outcome cannot carry a return")
    if record.chain_present and not record.contract_verified and record.execution_evidence_id is None:
        if record.value is not None:
            _fail("an option chain alone does not establish a strategy return")
    if record.observed_at is not None and record.observed_at < entry:
        _fail("outcome observation precedes entry")


def _member_sort_key(member: PopulationMember) -> Tuple[str, str, str]:
    return (
        member.symbol_as_observed,
        member.stratum_id or "",
        member.holdout_status.value,
    )


def population_fingerprint(
    *,
    sampling_kind: SamplingKind,
    sampling_methodology_id: str,
    sampling_frame: str,
    selection_timestamp: datetime,
    members: Sequence[PopulationMember],
    methodology_id: str,
    random_seed: Optional[str],
    hypothesis_id: Optional[str],
    parent_population_id: Optional[str],
    approved_acquisition: bool,
    provisional: bool,
    permanent_definition: bool,
    bound_to_production_drawdown_rule: bool,
) -> str:
    """Deterministic sample identity. created_at is not part of the hash."""
    body = {
        "approved_acquisition": approved_acquisition,
        "bound_to_production_drawdown_rule": bound_to_production_drawdown_rule,
        "hypothesis_id": hypothesis_id,
        "members": [
            record_to_dict(member)
            for member in sorted(members, key=_member_sort_key)
        ],
        "methodology_id": methodology_id,
        "parent_population_id": parent_population_id,
        "permanent_definition": permanent_definition,
        "provisional": provisional,
        "random_seed": random_seed,
        "sampling_frame": sampling_frame,
        "sampling_kind": sampling_kind.value,
        "sampling_methodology_id": sampling_methodology_id,
        "selection_timestamp": _aware("selection_timestamp", selection_timestamp).isoformat(),
    }
    return sha256_canonical(body)


def discovery_members(
    manifest: "ResearchPopulationManifest",
) -> Tuple[PopulationMember, ...]:
    """Holdout members are not part of the discovery population."""
    return tuple(
        member
        for member in manifest.members
        if member.holdout_status != HoldoutStatus.HOLDOUT
    )


def validate_population_member(member: PopulationMember) -> None:
    if not isinstance(member, PopulationMember):
        _fail("population member must be a PopulationMember")
    _text("symbol_as_observed", member.symbol_as_observed)
    _enum("holdout_status", member.holdout_status, HoldoutStatus)
    _text("inclusion_reason", member.inclusion_reason)
    _text("sampling_methodology_id", member.sampling_methodology_id)
    _optional_text("stratum_id", member.stratum_id)
    _optional_text("exclusion_reason", member.exclusion_reason)
    if member.selection_probability is not None:
        _number("selection_probability", member.selection_probability)
        if not 0 < float(member.selection_probability) <= 1:
            _fail("selection_probability must be in (0, 1]")


def validate_research_population(record: ResearchPopulationManifest) -> None:
    _schema(record.evidence_schema_version)
    _text("population_id", record.population_id)
    _enum("population_role", record.population_role, PopulationRole)
    _enum("sampling_kind", record.sampling_kind, SamplingKind)
    _text("sampling_methodology_id", record.sampling_methodology_id)
    _text("sampling_frame", record.sampling_frame)
    _aware("selection_timestamp", record.selection_timestamp)
    _sha256_text(record.population_fingerprint_sha256)
    _int("symbol_count", record.symbol_count, 0)
    _text("methodology_id", record.methodology_id)
    _enum("evidence_derivation", record.evidence_derivation, EvidenceDerivation)
    _enum("capture_origin", record.capture_origin, CaptureOrigin)
    for name in (
        "provisional",
        "approved_acquisition",
        "permanent_definition",
        "bound_to_production_drawdown_rule",
        "members_embedded",
    ):
        if not isinstance(getattr(record, name), bool):
            _fail("{0} must be a boolean".format(name))
    if record.permanent_definition and record.bound_to_production_drawdown_rule:
        _fail(
            "the production drawdown threshold cannot permanently define research acquisition"
        )
    if record.approved_acquisition and record.provisional:
        _fail("a provisional population is not an approved acquisition")
    _optional_text("random_seed", record.random_seed)
    _optional_text("hypothesis_id", record.hypothesis_id)
    _optional_text("parent_population_id", record.parent_population_id)
    _enum("holdout_status", record.holdout_status, HoldoutStatus)
    if not isinstance(record.members, tuple):
        _fail("members must be a tuple")
    for member in record.members:
        validate_population_member(member)
    if record.sampling_kind == SamplingKind.CANONICAL_REFERENCE:
        if record.members or record.members_embedded:
            _fail("the canonical population is cited, not copied")
        if record.population_fingerprint_sha256 != CANONICAL_ELIGIBLE_POPULATION_HASH:
            _fail("canonical citation fingerprint does not match the eligible population")
        if record.symbol_count != CANONICAL_ELIGIBLE_POPULATION_COUNT:
            _fail("canonical citation count is not 227")
        if record.approved_acquisition:
            _fail("citing the canonical population does not approve a new acquisition")
        expected_id = "population:canonical:{0}".format(CANONICAL_ELIGIBLE_POPULATION_HASH)
        if record.population_id != expected_id:
            _fail("canonical population id is not the citation id")
        return
    if not record.members_embedded:
        _fail("a research population embeds its sample members")
    if record.symbol_count != len(record.members):
        _fail("symbol_count does not match embedded members")
    if record.sampling_kind == SamplingKind.STRATIFIED:
        if any(member.stratum_id is None for member in record.members):
            _fail("stratified members require stratum_id")
    if record.sampling_kind == SamplingKind.INDEPENDENT_HOLDOUT:
        if any(member.holdout_status != HoldoutStatus.HOLDOUT for member in record.members):
            _fail("an independent holdout contains only holdout members")
    fingerprint = population_fingerprint(
        sampling_kind=record.sampling_kind,
        sampling_methodology_id=record.sampling_methodology_id,
        sampling_frame=record.sampling_frame,
        selection_timestamp=record.selection_timestamp,
        members=record.members,
        methodology_id=record.methodology_id,
        random_seed=record.random_seed,
        hypothesis_id=record.hypothesis_id,
        parent_population_id=record.parent_population_id,
        approved_acquisition=record.approved_acquisition,
        provisional=record.provisional,
        permanent_definition=record.permanent_definition,
        bound_to_production_drawdown_rule=record.bound_to_production_drawdown_rule,
    )
    if record.population_fingerprint_sha256 != fingerprint:
        _fail("population fingerprint does not match the sample definition")
    if record.population_id != "population:{0}".format(fingerprint):
        _fail("population_id does not match the sample fingerprint")
    if record.population_fingerprint_sha256 == CANONICAL_ELIGIBLE_POPULATION_HASH:
        _fail("a research sample cannot reuse the canonical eligible fingerprint")


def cite_canonical_population(cited_at: datetime) -> ResearchPopulationManifest:
    """Cite the existing 227-row population without copying or rewriting it."""
    _aware("cited_at", cited_at)
    manifest = ResearchPopulationManifest(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        population_id="population:canonical:{0}".format(CANONICAL_ELIGIBLE_POPULATION_HASH),
        population_role=PopulationRole.PRODUCTION_CAPTURE,
        sampling_kind=SamplingKind.CANONICAL_REFERENCE,
        sampling_methodology_id=CANONICAL_CLASSIFIER_VERSION,
        sampling_frame="existing eligible population citation",
        selection_timestamp=cited_at,
        population_fingerprint_sha256=CANONICAL_ELIGIBLE_POPULATION_HASH,
        symbol_count=CANONICAL_ELIGIBLE_POPULATION_COUNT,
        methodology_id=CANONICAL_CLASSIFIER_VERSION,
        evidence_derivation=EvidenceDerivation.CAPTURED,
        capture_origin=CaptureOrigin.HISTORICAL_EXPORT,
        provisional=True,
        approved_acquisition=False,
        permanent_definition=False,
        bound_to_production_drawdown_rule=False,
        members_embedded=False,
        members=(),
        holdout_status=HoldoutStatus.NOT_APPLICABLE,
    )
    validate_research_population(manifest)
    return manifest


def build_population_manifest(
    *,
    population_role: PopulationRole,
    sampling_kind: SamplingKind,
    sampling_methodology_id: str,
    sampling_frame: str,
    selection_timestamp: datetime,
    members: Sequence[PopulationMember],
    methodology_id: str,
    random_seed: Optional[str] = None,
    hypothesis_id: Optional[str] = None,
    parent_population_id: Optional[str] = None,
    inclusion_reasons: Sequence[str] = (),
    exclusion_reasons: Sequence[str] = (),
    provisional: bool = True,
    approved_acquisition: bool = False,
    permanent_definition: bool = False,
    bound_to_production_drawdown_rule: bool = False,
    holdout_status: HoldoutStatus = HoldoutStatus.NOT_APPLICABLE,
    evidence_derivation: EvidenceDerivation = EvidenceDerivation.CAPTURED,
    capture_origin: CaptureOrigin = CaptureOrigin.SYNTHETIC,
) -> ResearchPopulationManifest:
    member_tuple = tuple(members)
    fingerprint = population_fingerprint(
        sampling_kind=sampling_kind,
        sampling_methodology_id=sampling_methodology_id,
        sampling_frame=sampling_frame,
        selection_timestamp=selection_timestamp,
        members=member_tuple,
        methodology_id=methodology_id,
        random_seed=random_seed,
        hypothesis_id=hypothesis_id,
        parent_population_id=parent_population_id,
        approved_acquisition=approved_acquisition,
        provisional=provisional,
        permanent_definition=permanent_definition,
        bound_to_production_drawdown_rule=bound_to_production_drawdown_rule,
    )
    manifest = ResearchPopulationManifest(
        evidence_schema_version=EVIDENCE_SCHEMA_VERSION,
        population_id="population:{0}".format(fingerprint),
        population_role=population_role,
        sampling_kind=sampling_kind,
        sampling_methodology_id=sampling_methodology_id,
        sampling_frame=sampling_frame,
        selection_timestamp=selection_timestamp,
        population_fingerprint_sha256=fingerprint,
        symbol_count=len(member_tuple),
        methodology_id=methodology_id,
        evidence_derivation=evidence_derivation,
        capture_origin=capture_origin,
        provisional=provisional,
        approved_acquisition=approved_acquisition,
        permanent_definition=permanent_definition,
        bound_to_production_drawdown_rule=bound_to_production_drawdown_rule,
        members_embedded=True,
        members=member_tuple,
        inclusion_reasons=tuple(inclusion_reasons),
        exclusion_reasons=tuple(exclusion_reasons),
        random_seed=random_seed,
        hypothesis_id=hypothesis_id,
        parent_population_id=parent_population_id,
        holdout_status=holdout_status,
    )
    validate_research_population(manifest)
    return manifest


def provisional_pilot_manifest(selection_timestamp: datetime) -> ResearchPopulationManifest:
    """The 20-name illustration is not an approved acquisition.

    Its frame is not the production drawdown rule, and it does not define
    research sampling permanently.
    """
    members = tuple(
        PopulationMember(
            symbol_as_observed="SYN{0:02d}".format(index),
            holdout_status=HoldoutStatus.DISCOVERY,
            inclusion_reason="provisional pilot slot",
            sampling_methodology_id="sampling-provisional-pilot-v0",
            stratum_id="pilot",
            selection_probability=0.05,
        )
        for index in range(20)
    )
    return build_population_manifest(
        population_role=PopulationRole.RESEARCH_COHORT,
        sampling_kind=SamplingKind.STRATIFIED,
        sampling_methodology_id="sampling-provisional-pilot-v0",
        sampling_frame="provisional pilot frame, not a production screen",
        selection_timestamp=selection_timestamp,
        members=members,
        methodology_id="horizon-research-pilot-v0",
        random_seed="pilot-not-approved",
        hypothesis_id="provisional-not-a-finding",
        provisional=True,
        approved_acquisition=False,
        permanent_definition=False,
        bound_to_production_drawdown_rule=False,
    )


def validate_derived_build_reference(record: DerivedBuildReference) -> None:
    _schema(record.evidence_schema_version)
    _text("build_id", record.build_id)
    _sha256_text(record.population_fingerprint_sha256)
    _text("methodology_id", record.methodology_id)
    _text("artifact_type", record.artifact_type)
    _text("status", record.status)
    if not isinstance(record.publishes_new_rows, bool):
        _fail("publishes_new_rows must be a boolean")
    _optional_text("population_id", record.population_id)
    if record.publishes_new_rows:
        _fail("evidence-contract-1 does not publish derived intelligence rows")
    if record.population_fingerprint_sha256 == CANONICAL_ELIGIBLE_POPULATION_HASH:
        if record.build_id not in CANONICAL_DERIVED_BUILD_IDS:
            _fail("unknown build id cannot attach to the canonical eligible population")


def validate_run_start(record: RunStartRecord) -> None:
    _schema(record.evidence_schema_version)
    _text("run_start_id", record.run_start_id)
    _text("acquisition_run_id", record.acquisition_run_id)
    _enum("acquisition_role", record.acquisition_role, AcquisitionRole)
    _enum("pipeline", record.pipeline, Pipeline)
    if _NONCE_RE.fullmatch(record.execution_nonce) is None:
        _fail("execution_nonce must be 8 or more URL-safe characters")
    _int("attempt_number", record.attempt_number, 1)
    _aware("started_at", record.started_at)
    _text("universe_manifest_id", record.universe_manifest_id)
    if record.run_start_id != record.acquisition_run_id + "/start":
        _fail("run_start_id must be the run id plus /start")


def validate_chunk_ref(record: ChunkRef) -> None:
    _text("entity", record.entity)
    if record.entity not in ENTITY_PRIMARY_FIELDS:
        _fail("unknown chunk entity: {0}".format(record.entity))
    _text("object_key", record.object_key)
    if record.object_key.startswith("/") or ".." in record.object_key.split("/"):
        _fail("object_key must be a relative key")
    _sha256_text(record.sha256)
    _int("row_count", record.row_count, 0)
    _int("uncompressed_byte_length", record.uncompressed_byte_length, 0)


def validate_checkpoint(record: EvidenceCheckpointManifest) -> None:
    _schema(record.evidence_schema_version)
    _text("checkpoint_id", record.checkpoint_id)
    _text("acquisition_run_id", record.acquisition_run_id)
    _text("run_start_id", record.run_start_id)
    _int("sequence", record.sequence, 1)
    if record.stage not in CHECKPOINT_STAGES:
        _fail("unknown checkpoint stage: {0}".format(record.stage))
    _aware("published_at", record.published_at)
    _enum("execution_status", record.execution_status, ExecutionStatus)
    _enum("capture_completeness", record.capture_completeness, CaptureCompleteness)
    if not isinstance(record.chunks, tuple):
        _fail("chunks must be a tuple")
    for chunk in record.chunks:
        validate_chunk_ref(chunk)
    expected = "{0}/checkpoint/{1:04d}".format(record.acquisition_run_id, record.sequence)
    if record.checkpoint_id != expected:
        _fail("checkpoint_id does not match run and sequence")
    if record.sequence == 1:
        if record.previous_checkpoint_id is not None:
            _fail("the first checkpoint has no predecessor")
    else:
        _text("previous_checkpoint_id", record.previous_checkpoint_id)
    if record.run_start_id != record.acquisition_run_id + "/start":
        _fail("checkpoint cites a mismatched run start")


def validate_terminal(record: TerminalPublicationManifest) -> None:
    _schema(record.evidence_schema_version)
    _text("terminal_id", record.terminal_id)
    _text("acquisition_run_id", record.acquisition_run_id)
    _text("run_start_id", record.run_start_id)
    if record.terminal_id != record.acquisition_run_id + "/terminal":
        _fail("terminal_id must be the run id plus /terminal")
    if record.run_start_id != record.acquisition_run_id + "/start":
        _fail("terminal cites a mismatched run start")
    if not isinstance(record.checkpoint_ids, tuple) or not record.checkpoint_ids:
        _fail("a terminal manifest cites at least one checkpoint")
    if not isinstance(record.checkpoint_sha256, tuple):
        _fail("checkpoint_sha256 must be a tuple of pairs")
    hashes = []
    for item in record.checkpoint_sha256:
        if not isinstance(item, tuple) or len(item) != 2:
            _fail("checkpoint hash entries must be pairs")
        checkpoint_id, digest = item
        _text("checkpoint_id", checkpoint_id)
        _sha256_text(digest)
        hashes.append(checkpoint_id)
    if hashes != list(record.checkpoint_ids):
        _fail("checkpoint hashes must follow checkpoint_ids")
    _enum("execution_status", record.execution_status, ExecutionStatus)
    if record.execution_status in (ExecutionStatus.STARTED, ExecutionStatus.RUNNING):
        _fail("a terminal manifest requires a terminal execution status")
    _enum("capture_completeness", record.capture_completeness, CaptureCompleteness)
    if record.publication_status != PublicationStatus.TERMINALLY_PUBLISHED:
        _fail("terminal publication_status must be terminally_published")
    _aware("published_at", record.published_at)
    _text("catalog_object_key", record.catalog_object_key)
    if record.catalog_object_key != "catalog/{0}.json".format(record.acquisition_run_id):
        _fail("catalog_object_key is not the per-run catalog object")


def validate_catalog_entry(record: CatalogEntry) -> None:
    _schema(record.evidence_schema_version)
    _text("acquisition_run_id", record.acquisition_run_id)
    _text("terminal_id", record.terminal_id)
    _text("catalog_object_key", record.catalog_object_key)
    _enum("execution_status", record.execution_status, ExecutionStatus)
    _aware("published_at", record.published_at)
    if record.catalog_object_key != "catalog/{0}.json".format(record.acquisition_run_id):
        _fail("catalog objects are one immutable file per run")


def logical_identity(record: Any) -> Tuple[Any, ...]:
    uniqueness = getattr(type(record), "uniqueness", None)
    if not uniqueness:
        _fail("record type has no uniqueness constraint")
    values = []
    for name in uniqueness:
        if not hasattr(record, name):
            _fail("uniqueness field {0} is not on the record".format(name))
        values.append(getattr(record, name))
    return tuple(values)


def primary_value(record: Any) -> str:
    mapping = {
        AcquisitionRun: "acquisition_run_id",
        UniverseManifest: "universe_manifest_id",
        UniverseMembership: "membership_id",
        VendorRequest: "vendor_request_id",
        SecurityObservation: "observation_id",
        DirectionalEvaluation: "directional_evaluation_id",
        ScreeningDecision: "screening_decision_id",
        GateEvaluation: "gate_evaluation_id",
        FeatureObservation: "feature_observation_id",
        ScoreEvaluation: "score_evaluation_id",
        RankingDecision: "ranking_decision_id",
        StrategyEvaluation: "strategy_evaluation_id",
        EditorialDecision: "editorial_decision_id",
        MarketOutcome: "outcome_id",
        ResearchPopulationManifest: "population_id",
        DerivedBuildReference: "build_id",
        RunStartRecord: "run_start_id",
        EvidenceCheckpointManifest: "checkpoint_id",
        TerminalPublicationManifest: "terminal_id",
        CatalogEntry: "acquisition_run_id",
    }
    field_name = mapping.get(type(record))
    if field_name is None:
        _fail("no primary identity for {0}".format(type(record).__name__))
    return getattr(record, field_name)


def assert_can_coexist(existing: Any, new: Any) -> None:
    """Identical records may be retried. Conflicts and silent overwrites may not.

    A reconstructed row cannot reuse the primary identity of a captured row.
    """
    if type(existing) is not type(new):
        _fail("evidence identities cannot cross entity types")
    if logical_identity(existing) == logical_identity(new):
        if record_to_dict(existing) != record_to_dict(new):
            _fail("conflicting evidence for the same logical identity")
        return
    if primary_value(existing) == primary_value(new):
        _fail("conflicting evidence for the same primary identity")
    existing_derivation = getattr(existing, "evidence_derivation", None)
    new_derivation = getattr(new, "evidence_derivation", None)
    if (
        existing_derivation == EvidenceDerivation.CAPTURED
        and new_derivation == EvidenceDerivation.RECONSTRUCTED
        and primary_value(existing) == primary_value(new)
    ):
        _fail("reconstructed evidence cannot overwrite captured evidence")


def predictive_roi_from_observations(*_args: Any, **_kwargs: Any) -> None:
    """Predictive ROI is not estimated in this contract."""
    _fail(
        "Predictive ROI is not estimated from observations in evidence-contract-1. "
        "Synthetic observations cannot support a research finding."
    )


VALIDATORS = {
    AcquisitionRun: validate_acquisition_run,
    UniverseManifest: validate_universe_manifest,
    UniverseMembership: validate_universe_membership,
    VendorRequest: validate_vendor_request,
    SecurityObservation: validate_security_observation,
    DirectionalEvaluation: validate_directional_evaluation,
    ScreeningDecision: validate_screening_decision,
    GateEvaluation: validate_gate_evaluation,
    FeatureObservation: validate_feature_observation,
    ScoreEvaluation: validate_score_evaluation,
    RankingDecision: validate_ranking_decision,
    StrategyEvaluation: validate_strategy_evaluation,
    EditorialDecision: validate_editorial_decision,
    MarketOutcome: validate_market_outcome,
    ResearchPopulationManifest: validate_research_population,
    DerivedBuildReference: validate_derived_build_reference,
    RunStartRecord: validate_run_start,
    EvidenceCheckpointManifest: validate_checkpoint,
    TerminalPublicationManifest: validate_terminal,
    CatalogEntry: validate_catalog_entry,
}


def validate(record: Any) -> None:
    validator = VALIDATORS.get(type(record))
    if validator is None:
        _fail("no validator for {0}".format(type(record).__name__))
    validator(record)


def contract_inventory() -> Tuple[str, ...]:
    """The 20 contract entities. Run-start and catalog entries are companions."""
    from evidence_economics import (  # local import avoids an import cycle
        AcquisitionBudget,
        AcquisitionTelemetry,
    )

    if "telemetry_id" not in AcquisitionTelemetry.__dataclass_fields__:
        _fail("acquisition telemetry is missing telemetry_id")
    if "budget_id" not in AcquisitionBudget.__dataclass_fields__:
        _fail("acquisition budget is missing budget_id")
    return CONTRACT_ENTITIES
