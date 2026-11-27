#!/usr/bin/env python3
"""Acquisition telemetry, derived ratios, and process-local budget decisions.

No vendor price table lives here. Unknown cost stays null. A known zero
marginal cost stays zero. Cache hits do not increment external request
counts. This module does not send requests and does not enforce a budget
across processes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Optional, Sequence, Tuple

from evidence_contract import (
    EVIDENCE_SCHEMA_VERSION,
    BudgetDecisionStatus,
    CostProvenance,
    EvidenceContractError,
    PricingModel,
    VendorRequest,
    VendorRequestState,
    _aware,
    _enum,
    _fail,
    _int,
    _number,
    _optional_int,
    _optional_number,
    _optional_text,
    _schema,
    _text,
)


RESERVATION_REQUIREMENT = (
    "Process-local counters cannot enforce a budget shared by concurrent jobs. "
    "Shared enforcement requires an atomic reservation before a vendor request "
    "is sent and a release of unused units. evidence-contract-1 does not "
    "implement distributed budget enforcement."
)
SHARED_BUDGET_ENFORCEMENT_IMPLEMENTED = False
RESERVATION_SCOPE = "process_local"


@dataclass(frozen=True)
class AcquisitionTelemetry:
    """One endpoint's accounting for one run.

    actual_cost is null when cost_known is false. That is not zero. A
    subscription with an explicit zero marginal charge uses cost_known true
    and actual_cost 0.
    """

    evidence_schema_version: str
    telemetry_id: str
    acquisition_run_id: str
    endpoint: str
    vendor: str
    external_request_count: int
    cache_hit_count: int
    cache_miss_count: int
    retry_count: int
    failed_request_count: int
    billable_known: bool
    quota_known: bool
    cost_known: bool
    pricing_model: PricingModel
    cost_provenance: CostProvenance
    request_duration_ms: int
    execution_duration_ms: int
    payload_bytes: int
    unique_securities: int
    unique_usable_observations: int
    completed_evaluations: int
    redundant_acquisitions: int
    billable_request_count: Optional[int] = None
    quota_units_consumed: Optional[int] = None
    research_population_coverage: Optional[float] = None
    matured_outcome_coverage: Optional[float] = None
    matured_outcome_count: Optional[int] = None
    estimated_cost: Optional[float] = None
    actual_cost: Optional[float] = None
    cost_currency: Optional[str] = None
    subscription_entitlement_id: Optional[str] = None

    uniqueness = ("telemetry_id",)


@dataclass(frozen=True)
class DerivedAcquisitionMetrics:
    external_requests_per_usable_observation: Optional[float]
    external_requests_per_completed_evaluation: Optional[float]
    cache_hit_rate: Optional[float]
    acquisition_failure_rate: Optional[float]
    usable_observation_rate: Optional[float]
    cost_per_usable_observation: Optional[float]
    cost_per_completed_evaluation: Optional[float]
    cost_per_matured_outcome: Optional[float]


@dataclass(frozen=True)
class EntitlementPolicy:
    entitlement_id: str
    permit_unknown_cost: bool


@dataclass(frozen=True)
class NamedAllowance:
    name: str
    max_requests: int


@dataclass(frozen=True)
class AcquisitionBudget:
    evidence_schema_version: str
    budget_id: str
    shared_enforcement_implemented: bool = False
    max_requests_per_run: Optional[int] = None
    max_requests_per_day: Optional[int] = None
    max_requests_per_month: Optional[int] = None
    max_quota_units: Optional[int] = None
    max_deep_securities: Optional[int] = None
    max_execution_duration_seconds: Optional[float] = None
    max_estimated_spend: Optional[float] = None
    max_actual_spend: Optional[float] = None
    max_retry_count: Optional[int] = None
    vendor_allowances: Tuple[NamedAllowance, ...] = ()
    endpoint_allowances: Tuple[NamedAllowance, ...] = ()
    approved_endpoints: Optional[Tuple[str, ...]] = None
    approved_entitlements: Tuple[EntitlementPolicy, ...] = ()
    spend_currency: Optional[str] = None

    uniqueness = ("budget_id",)


@dataclass(frozen=True)
class BudgetCounters:
    requests_this_run: int = 0
    requests_today: int = 0
    requests_this_month: int = 0
    requests_by_vendor: Tuple[Tuple[str, int], ...] = ()
    requests_by_endpoint: Tuple[Tuple[str, int], ...] = ()
    quota_units_consumed: int = 0
    quota_known: bool = True
    deep_securities: int = 0
    elapsed_seconds: float = 0
    retries: int = 0
    estimated_spend: float = 0
    actual_spend: float = 0


@dataclass(frozen=True)
class ProposedAcquisition:
    endpoint: str
    vendor: str
    cache_hit: bool
    retry: bool
    cost_known: bool
    duration_seconds: float
    quota_units: Optional[int] = None
    estimated_cost: Optional[float] = None
    actual_cost: Optional[float] = None
    counts_as_deep_security: bool = False
    entitlement_id: Optional[str] = None


@dataclass(frozen=True)
class BudgetDecision:
    status: BudgetDecisionStatus
    detail: str
    external_request_increment: int
    quota_increment: int
    shared_enforcement_implemented: bool
    reservation_scope: str
    reservation_implemented: bool


def _ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return float(numerator) / float(denominator)


def _cost_ratio(telemetry: AcquisitionTelemetry, denominator: Optional[int]) -> Optional[float]:
    if not telemetry.cost_known or telemetry.actual_cost is None:
        return None
    if denominator is None or denominator == 0:
        return None
    return float(telemetry.actual_cost) / float(denominator)


def derive_acquisition_metrics(telemetry: AcquisitionTelemetry) -> DerivedAcquisitionMetrics:
    validate_telemetry(telemetry)
    cache_denominator = telemetry.cache_hit_count + telemetry.cache_miss_count
    return DerivedAcquisitionMetrics(
        external_requests_per_usable_observation=_ratio(
            telemetry.external_request_count, telemetry.unique_usable_observations
        ),
        external_requests_per_completed_evaluation=_ratio(
            telemetry.external_request_count, telemetry.completed_evaluations
        ),
        cache_hit_rate=_ratio(telemetry.cache_hit_count, cache_denominator),
        acquisition_failure_rate=_ratio(
            telemetry.failed_request_count, telemetry.external_request_count
        ),
        usable_observation_rate=_ratio(
            telemetry.unique_usable_observations, telemetry.unique_securities
        ),
        cost_per_usable_observation=_cost_ratio(
            telemetry, telemetry.unique_usable_observations
        ),
        cost_per_completed_evaluation=_cost_ratio(
            telemetry, telemetry.completed_evaluations
        ),
        cost_per_matured_outcome=_cost_ratio(
            telemetry, telemetry.matured_outcome_count
        ),
    )


def validate_telemetry(record: AcquisitionTelemetry) -> None:
    _schema(record.evidence_schema_version)
    _text("telemetry_id", record.telemetry_id)
    _text("acquisition_run_id", record.acquisition_run_id)
    _text("endpoint", record.endpoint)
    _text("vendor", record.vendor)
    for name in (
        "external_request_count",
        "cache_hit_count",
        "cache_miss_count",
        "retry_count",
        "failed_request_count",
        "request_duration_ms",
        "execution_duration_ms",
        "payload_bytes",
        "unique_securities",
        "unique_usable_observations",
        "completed_evaluations",
        "redundant_acquisitions",
    ):
        _int(name, getattr(record, name), 0)
    for name in ("billable_known", "quota_known", "cost_known"):
        if not isinstance(getattr(record, name), bool):
            _fail("{0} must be a boolean".format(name))
    _enum("pricing_model", record.pricing_model, PricingModel)
    _enum("cost_provenance", record.cost_provenance, CostProvenance)
    _known_count("billable_request_count", record.billable_known, record.billable_request_count)
    _known_count("quota_units_consumed", record.quota_known, record.quota_units_consumed)
    _optional_number("research_population_coverage", record.research_population_coverage)
    _optional_number("matured_outcome_coverage", record.matured_outcome_coverage)
    for name in ("research_population_coverage", "matured_outcome_coverage"):
        value = getattr(record, name)
        if value is not None and not 0 <= float(value) <= 1:
            _fail("{0} must be between 0 and 1".format(name))
    _optional_int("matured_outcome_count", record.matured_outcome_count, 0)
    _optional_number("estimated_cost", record.estimated_cost)
    _optional_number("actual_cost", record.actual_cost)
    _optional_text("cost_currency", record.cost_currency)
    _optional_text("subscription_entitlement_id", record.subscription_entitlement_id)
    if record.retry_count > record.external_request_count:
        _fail("retries are a subset of external requests")
    if record.failed_request_count > record.external_request_count:
        _fail("failures are a subset of external requests")
    if record.cache_miss_count > record.external_request_count:
        _fail("cache misses cannot exceed external requests")
    if record.billable_known and record.billable_request_count > record.external_request_count:
        _fail("billable requests cannot exceed external requests")
    if record.cost_known:
        if record.actual_cost is None or record.cost_currency is None:
            _fail("known cost requires actual_cost and cost_currency")
        if record.cost_provenance == CostProvenance.UNKNOWN:
            _fail("known cost cannot use unknown provenance")
        if record.actual_cost == 0 and record.cost_provenance not in (
            CostProvenance.EXPLICIT_ZERO_MARGINAL,
            CostProvenance.SUBSCRIPTION_UNMETERED,
        ):
            _fail("a zero cost requires explicit zero-marginal provenance")
    else:
        if record.actual_cost is not None or record.estimated_cost is not None:
            _fail("unknown cost must stay null")
        if record.cost_provenance != CostProvenance.UNKNOWN:
            _fail("unknown cost uses unknown provenance")
    if record.telemetry_id != "{0}:{1}:{2}".format(
        record.acquisition_run_id, record.vendor, record.endpoint
    ):
        _fail("telemetry_id does not match run, vendor, and endpoint")


def _known_count(name: str, known: bool, value: Optional[int]) -> None:
    if known:
        _int(name, value, 0)
    elif value is not None:
        _fail("{0} is unknown and must stay null".format(name))


def summarize_vendor_requests(requests: Sequence[VendorRequest]) -> Dict[str, int]:
    """Count external calls separately from cache hits."""
    external = 0
    cache_hits = 0
    failures = 0
    retries = 0
    for request in requests:
        from evidence_contract import validate_vendor_request

        validate_vendor_request(request)
        if request.request_state == VendorRequestState.CACHE_HIT:
            cache_hits += 1
            continue
        if request.request_state == VendorRequestState.BUDGET_BLOCKED:
            continue
        if request.external_request:
            external += 1
            if request.retry_ordinal > 0:
                retries += 1
            if request.request_state in (
                VendorRequestState.HTTP_FAILURE,
                VendorRequestState.TIMEOUT,
                VendorRequestState.PARSE_FAILURE,
            ):
                failures += 1
    return {
        "external_request_count": external,
        "cache_hit_count": cache_hits,
        "retry_count": retries,
        "failed_request_count": failures,
    }


def reservation_plan() -> Dict[str, object]:
    return {
        "implemented": False,
        "scope": RESERVATION_SCOPE,
        "requirement": RESERVATION_REQUIREMENT,
        "shared_enforcement_implemented": SHARED_BUDGET_ENFORCEMENT_IMPLEMENTED,
    }


def validate_budget(record: AcquisitionBudget) -> None:
    _schema(record.evidence_schema_version)
    _text("budget_id", record.budget_id)
    if record.shared_enforcement_implemented is not False:
        _fail("distributed budget enforcement is not implemented")
    for name in (
        "max_requests_per_run",
        "max_requests_per_day",
        "max_requests_per_month",
        "max_quota_units",
        "max_deep_securities",
        "max_retry_count",
    ):
        _optional_int(name, getattr(record, name), 0)
    _optional_number("max_execution_duration_seconds", record.max_execution_duration_seconds)
    _optional_number("max_estimated_spend", record.max_estimated_spend)
    _optional_number("max_actual_spend", record.max_actual_spend)
    if record.max_execution_duration_seconds is not None and record.max_execution_duration_seconds < 0:
        _fail("max_execution_duration_seconds must be >= 0")
    for name in ("max_estimated_spend", "max_actual_spend"):
        value = getattr(record, name)
        if value is not None and value < 0:
            _fail("{0} must be >= 0".format(name))
    _optional_text("spend_currency", record.spend_currency)
    spend_configured = (
        record.max_estimated_spend is not None or record.max_actual_spend is not None
    )
    if spend_configured and record.spend_currency is None:
        _fail("a spending cap requires spend_currency")
    if record.approved_endpoints is not None and not isinstance(record.approved_endpoints, tuple):
        _fail("approved_endpoints must be a tuple when set")
    for allowance in record.vendor_allowances + record.endpoint_allowances:
        _text("allowance name", allowance.name)
        _int("allowance max", allowance.max_requests, 0)
    for policy in record.approved_entitlements:
        _text("entitlement_id", policy.entitlement_id)
        if not isinstance(policy.permit_unknown_cost, bool):
            _fail("permit_unknown_cost must be a boolean")


def _counter(pairs: Sequence[Tuple[str, int]], name: str) -> int:
    total = 0
    for key, count in pairs:
        if key == name:
            total += count
    return total


def _decision(
    status: BudgetDecisionStatus,
    detail: str,
    external_increment: int,
    quota_increment: int,
) -> BudgetDecision:
    return BudgetDecision(
        status=status,
        detail=detail,
        external_request_increment=external_increment,
        quota_increment=quota_increment,
        shared_enforcement_implemented=False,
        reservation_scope=RESERVATION_SCOPE,
        reservation_implemented=False,
    )


def evaluate_budget(
    budget: AcquisitionBudget,
    counters: BudgetCounters,
    proposal: ProposedAcquisition,
) -> BudgetDecision:
    """Decide a single proposed acquisition against process-local counters.

    The returned decision is not an atomic reservation. Concurrent jobs need
    a shared store that this function does not provide.
    """
    validate_budget(budget)
    if proposal.cache_hit and proposal.retry:
        raise EvidenceContractError("a cache hit is not a retry")
    _number("duration_seconds", proposal.duration_seconds)
    if proposal.duration_seconds < 0:
        _fail("duration_seconds must be >= 0")
    _number("elapsed_seconds", counters.elapsed_seconds)
    external_increment = 0 if proposal.cache_hit else 1
    if proposal.cache_hit or proposal.quota_units is None:
        quota_increment = 0
        quota_unknown = proposal.quota_units is None and not proposal.cache_hit
    else:
        _int("quota_units", proposal.quota_units, 0)
        quota_increment = proposal.quota_units
        quota_unknown = False

    if budget.approved_endpoints is not None and proposal.endpoint not in budget.approved_endpoints:
        return _decision(
            BudgetDecisionStatus.BLOCKED_UNAPPROVED_ENDPOINT,
            "endpoint is not approved",
            external_increment,
            quota_increment,
        )
    if proposal.retry and budget.max_retry_count is not None:
        if counters.retries + 1 > budget.max_retry_count:
            return _decision(
                BudgetDecisionStatus.BLOCKED_RETRY_LIMIT,
                "retry limit",
                external_increment,
                quota_increment,
            )
    if budget.max_execution_duration_seconds is not None:
        if counters.elapsed_seconds + proposal.duration_seconds > budget.max_execution_duration_seconds:
            return _decision(
                BudgetDecisionStatus.BLOCKED_DURATION_LIMIT,
                "duration limit",
                external_increment,
                quota_increment,
            )
    request_checks = (
        ("max_requests_per_run", counters.requests_this_run, budget.max_requests_per_run),
        ("max_requests_per_day", counters.requests_today, budget.max_requests_per_day),
        ("max_requests_per_month", counters.requests_this_month, budget.max_requests_per_month),
    )
    for label, used, limit in request_checks:
        if limit is not None and used + external_increment > limit:
            return _decision(
                BudgetDecisionStatus.BLOCKED_REQUEST_BUDGET,
                label,
                external_increment,
                quota_increment,
            )
    for allowance in budget.vendor_allowances:
        if allowance.name == proposal.vendor:
            used = _counter(counters.requests_by_vendor, proposal.vendor)
            if used + external_increment > allowance.max_requests:
                return _decision(
                    BudgetDecisionStatus.BLOCKED_REQUEST_BUDGET,
                    "vendor allowance",
                    external_increment,
                    quota_increment,
                )
    for allowance in budget.endpoint_allowances:
        if allowance.name == proposal.endpoint:
            used = _counter(counters.requests_by_endpoint, proposal.endpoint)
            if used + external_increment > allowance.max_requests:
                return _decision(
                    BudgetDecisionStatus.BLOCKED_REQUEST_BUDGET,
                    "endpoint allowance",
                    external_increment,
                    quota_increment,
                )
    if budget.max_quota_units is not None and not proposal.cache_hit:
        if quota_unknown or not counters.quota_known:
            return _decision(
                BudgetDecisionStatus.BLOCKED_QUOTA_BUDGET,
                "quota_unknown",
                external_increment,
                0,
            )
        if counters.quota_units_consumed + quota_increment > budget.max_quota_units:
            return _decision(
                BudgetDecisionStatus.BLOCKED_QUOTA_BUDGET,
                "quota limit",
                external_increment,
                quota_increment,
            )
    if proposal.counts_as_deep_security and budget.max_deep_securities is not None:
        if counters.deep_securities + 1 > budget.max_deep_securities:
            return _decision(
                BudgetDecisionStatus.BLOCKED_REQUEST_BUDGET,
                "max_deep_securities",
                external_increment,
                quota_increment,
            )
    spend_cap = budget.max_actual_spend is not None or budget.max_estimated_spend is not None
    if spend_cap and not proposal.cost_known:
        if not _unknown_cost_permitted(budget, proposal.entitlement_id):
            return _decision(
                BudgetDecisionStatus.BLOCKED_UNKNOWN_COST,
                "spending cap requires a known cost",
                external_increment,
                quota_increment,
            )
    if proposal.cost_known:
        if proposal.actual_cost is None:
            _fail("known cost requires actual_cost")
        if budget.max_actual_spend is not None:
            if counters.actual_spend + proposal.actual_cost > budget.max_actual_spend:
                return _decision(
                    BudgetDecisionStatus.BLOCKED_SPENDING_BUDGET,
                    "actual spend",
                    external_increment,
                    quota_increment,
                )
        if budget.max_estimated_spend is not None:
            if proposal.estimated_cost is None:
                return _decision(
                    BudgetDecisionStatus.BLOCKED_UNKNOWN_COST,
                    "estimated spend is unknown",
                    external_increment,
                    quota_increment,
                )
            if counters.estimated_spend + proposal.estimated_cost > budget.max_estimated_spend:
                return _decision(
                    BudgetDecisionStatus.BLOCKED_SPENDING_BUDGET,
                    "estimated spend",
                    external_increment,
                    quota_increment,
                )
    return _decision(
        BudgetDecisionStatus.ALLOWED,
        "allowed",
        external_increment,
        quota_increment if not quota_unknown else 0,
    )


def _unknown_cost_permitted(budget: AcquisitionBudget, entitlement_id: Optional[str]) -> bool:
    if not entitlement_id:
        return False
    for policy in budget.approved_entitlements:
        if policy.entitlement_id == entitlement_id and policy.permit_unknown_cost:
            return True
    return False
