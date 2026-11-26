#!/usr/bin/env python3
"""Pure decomposition of one inspected Scout single-scan payload.

This module does not open a database, call the network, run a scan, or write
an observation. It applies the single-scan formula from the local snapshot
inspected in the scoring audit, including that formula's earnings-day defect.

The snapshot hash identifies the inspected source. It is not a verified
identity of the deployed Cloud Function, and a historical row that is
compatible with the formula was not thereby shown to come from a verified
deployment.
"""

from __future__ import annotations

import enum
import re
from dataclasses import dataclass
from typing import Any, Mapping, Optional


INSPECTED_SOURCE_SHA256 = (
    "243ce011a4e8ad9fbade2622c0955d811c52db8045e2b23bd9d7517e599a4e51"
)
INSPECTED_SOURCE_LABEL = (
    "local snapshot friday-scout_function-source-deploy-candidate/main.py "
    "single_scan lines 3218-3232"
)
# Phase II recorded this prefix for the single_scan function body. It is a
# record of that inspection, not a hash recomputed by executing production code.
INSPECTED_SINGLE_SCAN_BODY_PREFIX = "e99f139892c7"
INSPECTED_SINGLE_SCAN_METHODOLOGY_ID = (
    f"scout-single-v1+{INSPECTED_SOURCE_SHA256[:16]}"
)
DEPLOYMENT_VERIFICATION_STATUS = "unverified"
VERIFIED_DEPLOYMENT_ID = None

BASE_SCORE = 50
SCORE_FLOOR = 0
SCORE_CEILING = 100

# News categories in scan_gate4. Each category is applied at most once.
# Insider-selling and priced-in flags clear Specter and add no points, so 0
# remains possible after a failed Specter check. The candidate domain for a
# missing penalty is the set of subset sums, not an arbitrary default.
SPECTER_CATEGORY_PENALTIES = (-10, -8, -5, -5, -15, -5, -3, -5)


class ScoringProvenanceError(Exception):
    """The payload is outside the single-scan decomposer."""


class EvidenceOrigin(str, enum.Enum):
    CAPTURED = "captured"
    NARRATIVE = "narrative"
    INVARIANT = "invariant"
    INFERRED = "inferred"
    AMBIGUOUS = "ambiguous"
    MISSING = "missing"
    CONFLICT = "conflict"


class ReconstructionClass(str, enum.Enum):
    """A–E classes from the Phase E1 contract.

    FORMULA_MISMATCH and EVIDENCE_CONFLICT are the two reasons inside class E.
    """

    EVIDENCE_DETERMINED = "A_evidence_determined"
    ALGEBRAICALLY_INFERRED = "B_algebraically_inferred"
    AMBIGUOUS = "C_ambiguous"
    INCOMPLETE = "D_incomplete"
    FORMULA_MISMATCH = "E_formula_mismatch"
    EVIDENCE_CONFLICT = "E_evidence_conflict"


class FormulaConfirmation(str, enum.Enum):
    INDEPENDENT = "independent"
    MATHEMATICAL_COMPATIBILITY = "mathematical_compatibility"
    NOT_UNIQUE = "not_unique"
    NOT_ASSESSED = "not_assessed"
    REJECTED = "rejected"


class DirectionStatus(str, enum.Enum):
    MATCH = "match"
    ROUNDING_AMBIGUOUS = "rounding_ambiguous"
    CONFLICT = "conflict"
    MISSING = "missing"


@dataclass(frozen=True)
class Evidenced:
    """One value plus the kind of evidence that produced it."""

    value: Any
    origin: EvidenceOrigin
    sources: tuple[str, ...] = ()
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "value": self.value,
            "origin": self.origin.value,
            "sources": list(self.sources),
            "detail": self.detail,
        }


@dataclass(frozen=True)
class Disagreement:
    field: str
    captured: Any
    narrative: Any

    def as_dict(self) -> dict[str, Any]:
        return {
            "field": self.field,
            "captured": self.captured,
            "narrative": self.narrative,
        }


@dataclass(frozen=True)
class MethodologyIdentity:
    inspected_methodology_id: str
    inspected_source_sha256: str
    inspected_source_label: str
    inspected_single_scan_body_prefix: str
    historical_methodology_id: None
    historical_compatibility: str
    verified_deployment_id: None
    deployment_verification: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "inspected_methodology_id": self.inspected_methodology_id,
            "inspected_source_sha256": self.inspected_source_sha256,
            "inspected_source_label": self.inspected_source_label,
            "inspected_single_scan_body_prefix": self.inspected_single_scan_body_prefix,
            "historical_methodology_id": self.historical_methodology_id,
            "historical_compatibility": self.historical_compatibility,
            "verified_deployment_id": self.verified_deployment_id,
            "deployment_verification": self.deployment_verification,
        }


@dataclass(frozen=True)
class InputCompleteness:
    present: tuple[str, ...]
    missing: tuple[str, ...]

    @property
    def complete(self) -> bool:
        return not self.missing

    def as_dict(self) -> dict[str, Any]:
        return {
            "complete": self.complete,
            "present": list(self.present),
            "missing": list(self.missing),
        }


@dataclass(frozen=True)
class PenaltyCandidate:
    penalty: int
    unclamped_score: int
    clamped_score: int

    def as_dict(self) -> dict[str, int]:
        return {
            "penalty": self.penalty,
            "unclamped_score": self.unclamped_score,
            "clamped_score": self.clamped_score,
        }


@dataclass(frozen=True)
class SingleScanDecomposition:
    methodology: MethodologyIdentity
    classification: ReconstructionClass
    formula_confirmation: FormulaConfirmation
    captured_scout_score: Evidenced
    base_score: Evidenced
    atlas_raw: Evidenced
    atlas_contribution: Evidenced
    oracle_raw: Evidenced
    oracle_contribution: Evidenced
    phantom_raw: Evidenced
    phantom_contribution: Evidenced
    catalyst_raw: Evidenced
    catalyst_contribution: Evidenced
    specter_clean_bonus: Evidenced
    specter_penalty: Evidenced
    penalty_candidates: tuple[PenaltyCandidate, ...]
    wind_contribution: Evidenced
    alignment_bonus: Evidenced
    volatility_bonus: Evidenced
    earnings_penalty: Evidenced
    unclamped_score: Evidenced
    clamped_score: Evidenced
    reconstruction_error: Evidenced
    direction: Evidenced
    direction_status: DirectionStatus
    direction_candidates: tuple[str, ...]
    direction_argument: Evidenced
    input_completeness: InputCompleteness
    disagreements: tuple[Disagreement, ...]
    warnings: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "methodology": self.methodology.as_dict(),
            "classification": self.classification.value,
            "formula_confirmation": self.formula_confirmation.value,
            "captured_scout_score": self.captured_scout_score.as_dict(),
            "base_score": self.base_score.as_dict(),
            "atlas_raw": self.atlas_raw.as_dict(),
            "atlas_contribution": self.atlas_contribution.as_dict(),
            "oracle_raw": self.oracle_raw.as_dict(),
            "oracle_contribution": self.oracle_contribution.as_dict(),
            "phantom_raw": self.phantom_raw.as_dict(),
            "phantom_contribution": self.phantom_contribution.as_dict(),
            "catalyst_raw": self.catalyst_raw.as_dict(),
            "catalyst_contribution": self.catalyst_contribution.as_dict(),
            "specter_clean_bonus": self.specter_clean_bonus.as_dict(),
            "specter_penalty": self.specter_penalty.as_dict(),
            "penalty_candidates": [item.as_dict() for item in self.penalty_candidates],
            "wind_contribution": self.wind_contribution.as_dict(),
            "alignment_bonus": self.alignment_bonus.as_dict(),
            "volatility_bonus": self.volatility_bonus.as_dict(),
            "earnings_penalty": self.earnings_penalty.as_dict(),
            "unclamped_score": self.unclamped_score.as_dict(),
            "clamped_score": self.clamped_score.as_dict(),
            "reconstruction_error": self.reconstruction_error.as_dict(),
            "direction": self.direction.as_dict(),
            "direction_status": self.direction_status.value,
            "direction_candidates": list(self.direction_candidates),
            "direction_argument": self.direction_argument.as_dict(),
            "input_completeness": self.input_completeness.as_dict(),
            "disagreements": [item.as_dict() for item in self.disagreements],
            "warnings": list(self.warnings),
        }


def specter_penalty_domain() -> tuple[int, ...]:
    """Subset sums of the inspected news penalties, including 0."""
    totals = {0}
    for penalty in SPECTER_CATEGORY_PENALTIES:
        totals |= {existing + penalty for existing in totals}
    return tuple(sorted(totals))


def upside_cap(raw_score: int, multiplier: int, cap: int) -> int:
    """The inspected ``min(score * weight, cap)`` rule.

    The cap limits the positive side only. A negative product passes through.
    """
    return min(raw_score * multiplier, cap)


def earnings_penalty_points(earnings_days: Any) -> int:
    """Inspected if/elif. Day 0 is falsy and takes no penalty."""
    if earnings_days and earnings_days <= 7:
        return -10
    if earnings_days and earnings_days <= 14:
        return -5
    return 0


def clamp_score(total: int) -> int:
    return max(SCORE_FLOOR, min(SCORE_CEILING, total))


def decompose_single_scan(
    payload: Mapping[str, Any],
    *,
    methodology_id: str = INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
) -> SingleScanDecomposition:
    """Decompose one single-scan payload with the inspected formula.

    ``methodology_id`` must be the inspected single-scan id. Batch payloads
    and any other methodology raise ``ScoringProvenanceError``. The payload
    is not mutated.
    """
    if methodology_id != INSPECTED_SINGLE_SCAN_METHODOLOGY_ID:
        raise ScoringProvenanceError(
            "Unsupported scoring methodology "
            f"{methodology_id!r}. This decomposer implements only "
            f"{INSPECTED_SINGLE_SCAN_METHODOLOGY_ID}, and that id is the "
            "inspected local snapshot, not a verified deployment."
        )
    if not isinstance(payload, Mapping):
        raise ScoringProvenanceError("Observation payload must be a mapping.")
    scan = _scan_mapping(payload)
    text = scan.get("raw_output")
    if not isinstance(text, str):
        raise ScoringProvenanceError(
            "Unrecognized single-scan payload: raw_output is missing."
        )
    if _is_batch_narrative(text):
        raise ScoringProvenanceError(
            "Batch email payload rejected. decompose_single_scan does not "
            "apply the single-scan formula to run_full_scan output."
        )
    if not _is_single_scan_narrative(text):
        raise ScoringProvenanceError(
            "Unrecognized narrative format. Refusing to guess a score."
        )
    return _decompose_recognized_scan(scan, text)


def _scan_mapping(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    raw = payload.get("raw")
    if isinstance(raw, Mapping) and "raw_output" in raw:
        return raw
    if "raw_output" in payload:
        return payload
    raise ScoringProvenanceError(
        "Unrecognized observation payload: no single-scan raw_output."
    )


def _is_batch_narrative(text: str) -> bool:
    if "SCOUT SINGLE SCAN" in text:
        return False
    markers = ("SCOUT v6", "FINAL RANKINGS", "TOP PICK:")
    return any(marker in text for marker in markers)


def _is_single_scan_narrative(text: str) -> bool:
    return "SCOUT SINGLE SCAN" in text


def _decompose_recognized_scan(
    scan: Mapping[str, Any],
    text: str,
) -> SingleScanDecomposition:
    blocks = _gate_blocks(text)
    warnings = [
        "Deployment of this methodology is unverified. "
        "Compatibility with the inspected formula is not a deployment identity."
    ]
    disagreements: list[Disagreement] = []

    captured_score = _captured_int(scan.get("scout_score"), "scout_score")
    narrative_score = _narrative_scout_score(text)
    _record_disagreement(disagreements, "scout_score", captured_score, narrative_score)

    atlas_raw = _last_score(blocks.get("ATLAS", ""))
    oracle_raw = _last_score(blocks.get("ORACLE", ""))
    phantom_raw = _last_score(blocks.get("PHANTOM", ""))
    catalyst_raw = _last_score(blocks.get("CATALYST", ""))
    for name, block in (
        ("ATLAS", blocks.get("ATLAS", "")),
        ("ORACLE", blocks.get("ORACLE", "")),
        ("PHANTOM", blocks.get("PHANTOM", "")),
        ("CATALYST", blocks.get("CATALYST", "")),
    ):
        if block and _has_unrecognized_score_line(block) and _last_score(block) is None:
            warnings.append(f"Unrecognized score format in {name}.")

    wind = _captured_int(scan.get("wind"), "wind")
    narrative_wind = _narrative_wind(text)
    _record_disagreement(disagreements, "wind", wind, narrative_wind)

    trend = _captured_token(scan.get("trend"))
    narrative_trend = _narrative_trend(blocks.get("COMPASS", ""))
    _record_disagreement(disagreements, "trend", trend, narrative_trend)

    direction = _captured_direction(scan.get("direction"))
    narrative_direction = _narrative_direction(text)
    _record_disagreement(disagreements, "direction", direction, narrative_direction)

    iv_elevated = _captured_bool(scan.get("iv_elevated"))
    narrative_iv = _narrative_iv(blocks.get("PULSE", ""))
    _record_disagreement(disagreements, "iv_elevated", iv_elevated, narrative_iv)

    earnings_present = "earnings_days" in scan
    earnings_days = scan.get("earnings_days") if earnings_present else _MISSING
    earnings_number = _captured_earnings(earnings_days)
    narrative_earnings = _narrative_earnings(blocks.get("AEGIS", ""))
    _record_disagreement(
        disagreements, "earnings_days", earnings_number, narrative_earnings
    )

    gates = scan.get("gates") if isinstance(scan.get("gates"), Mapping) else {}
    specter_captured = _captured_bool(gates.get("specter")) if "specter" in gates else None
    specter_narrative = _narrative_specter(blocks.get("SPECTER", ""))
    _record_disagreement(
        disagreements, "specter", specter_captured, specter_narrative
    )
    explicit_penalty = _narrative_penalty(text)

    _record_gate_score_disagreement(disagreements, "atlas", atlas_raw, gates.get("atlas"), 2)
    _record_gate_score_disagreement(disagreements, "oracle", oracle_raw, gates.get("oracle"), 1)
    _record_gate_score_disagreement(disagreements, "phantom", phantom_raw, gates.get("phantom"), 0)
    _record_gate_score_disagreement(
        disagreements, "catalyst", catalyst_raw, gates.get("catalyst"), 1
    )
    if (
        captured_score is not None
        and "fortress" in gates
        and isinstance(gates.get("fortress"), bool)
        and gates["fortress"] != (captured_score >= 40)
    ):
        disagreements.append(
            Disagreement(
                "fortress",
                gates["fortress"],
                captured_score >= 40,
            )
        )
    if earnings_present and earnings_number is _MISSING:
        earnings_present = False
        warnings.append("earnings_days is present but is not a usable count.")
    elif (
        earnings_number is not _MISSING
        and "aegis" in gates
        and isinstance(gates.get("aegis"), bool)
    ):
        # Match the inspected falsy check, including day 0.
        expected_aegis = not bool(earnings_number) or (
            earnings_number is not None and earnings_number > 14
        )
        if gates["aegis"] != expected_aegis:
            disagreements.append(
                Disagreement("aegis", gates["aegis"], expected_aegis)
            )

    direction_status, direction_candidates, direction_warning = _direction_status(
        scan, text, direction
    )
    if direction_warning:
        warnings.append(direction_warning)

    if earnings_number == 0:
        warnings.append(
            "Earnings day 0 takes no penalty under the inspected falsy check. "
            "This baseline does not correct that defect."
        )

    missing = _missing_required(
        atlas_raw,
        oracle_raw,
        phantom_raw,
        catalyst_raw,
        specter_captured if specter_captured is not None else specter_narrative,
        wind,
        trend,
        direction,
        iv_elevated,
        earnings_present,
    )
    present = tuple(
        name
        for name, present_flag in (
            ("atlas_raw", atlas_raw is not None),
            ("oracle_raw", oracle_raw is not None),
            ("phantom_raw", phantom_raw is not None),
            ("catalyst_raw", catalyst_raw is not None),
            ("specter", (specter_captured is not None) or (specter_narrative is not None)),
            ("wind", wind is not None),
            ("trend", trend is not None),
            ("direction", direction is not None),
            ("iv_elevated", iv_elevated is not None),
            ("earnings_days", earnings_present),
            ("captured_scout_score", captured_score is not None),
        )
        if present_flag
    )
    completeness = InputCompleteness(present=present, missing=missing)

    identity_kwargs = {
        "inspected_methodology_id": INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
        "inspected_source_sha256": INSPECTED_SOURCE_SHA256,
        "inspected_source_label": INSPECTED_SOURCE_LABEL,
        "inspected_single_scan_body_prefix": INSPECTED_SINGLE_SCAN_BODY_PREFIX,
        "historical_methodology_id": None,
        "verified_deployment_id": VERIFIED_DEPLOYMENT_ID,
        "deployment_verification": DEPLOYMENT_VERIFICATION_STATUS,
    }

    def evidenced_score(raw: Optional[int], gate: str) -> Evidenced:
        if raw is None:
            return Evidenced(None, EvidenceOrigin.MISSING, (f"narrative:{gate}",), "Score line absent or unrecognized.")
        return Evidenced(raw, EvidenceOrigin.NARRATIVE, (f"narrative:{gate}.score",))

    def contribution(raw: Optional[int], gate: str, multiplier: int, cap: int) -> Evidenced:
        if raw is None:
            return Evidenced(None, EvidenceOrigin.MISSING, (f"narrative:{gate}.score", "formula:upside_cap"))
        return Evidenced(
            upside_cap(raw, multiplier, cap),
            EvidenceOrigin.NARRATIVE,
            (f"narrative:{gate}.score", f"formula:min(score*{multiplier},{cap})"),
        )

    atlas_value = evidenced_score(atlas_raw, "atlas")
    oracle_value = evidenced_score(oracle_raw, "oracle")
    phantom_value = evidenced_score(phantom_raw, "phantom")
    catalyst_value = evidenced_score(catalyst_raw, "catalyst")
    direction_argument = Evidenced(
        "CALL",
        EvidenceOrigin.INVARIANT,
        ("inspected_single_scan.scan_gate2", "inspected_single_scan.scan_gate4"),
        "single_scan calls Phantom and Specter with CALL before direction is chosen. "
        "The argument is not stored on the observation.",
    )
    direction_value = (
        Evidenced(direction, EvidenceOrigin.CAPTURED, ("payload.direction",))
        if direction is not None
        else Evidenced(None, EvidenceOrigin.MISSING, ("payload.direction",))
    )
    captured_value = (
        Evidenced(captured_score, EvidenceOrigin.CAPTURED, ("payload.scout_score",))
        if captured_score is not None
        else Evidenced(None, EvidenceOrigin.MISSING, ("payload.scout_score",))
    )

    if disagreements:
        warnings.append(
            "Structured and narrative evidence disagree. No score was selected."
        )
        return _unresolved(
            identity_kwargs,
            "inconsistent",
            ReconstructionClass.EVIDENCE_CONFLICT,
            FormulaConfirmation.REJECTED,
            captured_value,
            atlas_value,
            contribution(atlas_raw, "atlas", 5, 15),
            oracle_value,
            contribution(oracle_raw, "oracle", 4, 12),
            phantom_value,
            contribution(phantom_raw, "phantom", 3, 9),
            catalyst_value,
            contribution(catalyst_raw, "catalyst", 3, 9),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("payload.gates.specter", "narrative:specter")),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("narrative:specter_penalty",)),
            (),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("payload.wind",)),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("payload.trend", "payload.direction")),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("payload.iv_elevated",)),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("payload.earnings_days",)),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("formula:unclamped",)),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("formula:clamp",)),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("formula:residual",), "Not computed from contradictory inputs."),
            direction_value,
            direction_status,
            direction_candidates,
            direction_argument,
            completeness,
            tuple(disagreements),
            tuple(warnings),
        )

    if missing or captured_score is None:
        warnings.append("Required scoring evidence is missing. No substitute zero was inserted.")
        return _unresolved(
            identity_kwargs,
            "not_established",
            ReconstructionClass.INCOMPLETE,
            FormulaConfirmation.NOT_ASSESSED,
            captured_value,
            atlas_value,
            contribution(atlas_raw, "atlas", 5, 15),
            oracle_value,
            contribution(oracle_raw, "oracle", 4, 12),
            phantom_value,
            contribution(phantom_raw, "phantom", 3, 9),
            catalyst_value,
            contribution(catalyst_raw, "catalyst", 3, 9),
            _specter_bonus(specter_captured, specter_narrative),
            _explicit_or_missing_penalty(explicit_penalty),
            (),
            _wind_contribution(wind),
            _alignment(trend, direction),
            _volatility(iv_elevated),
            _earnings(earnings_number if earnings_present else None, earnings_present),
            Evidenced(None, EvidenceOrigin.MISSING, ("formula:unclamped",)),
            Evidenced(None, EvidenceOrigin.MISSING, ("formula:clamp",)),
            Evidenced(None, EvidenceOrigin.MISSING, ("formula:residual",)),
            direction_value,
            direction_status,
            direction_candidates,
            direction_argument,
            completeness,
            (),
            tuple(warnings),
        )

    assert atlas_raw is not None and oracle_raw is not None
    assert phantom_raw is not None and catalyst_raw is not None
    assert wind is not None and trend is not None and direction is not None
    assert iv_elevated is not None and earnings_present
    clean = specter_captured if specter_captured is not None else specter_narrative
    assert clean is not None
    parts = _formula_parts(
        atlas_raw, oracle_raw, phantom_raw, catalyst_raw, clean, wind, trend, direction, iv_elevated, earnings_number
    )
    if explicit_penalty is not None and clean is True and explicit_penalty != 0:
        warnings.append(
            "A clean Specter flag and a nonzero penalty sentence cannot both be true "
            "under the inspected code."
        )
        disagreements.append(Disagreement("specter_penalty", 0, explicit_penalty))
        return _unresolved(
            identity_kwargs,
            "inconsistent",
            ReconstructionClass.EVIDENCE_CONFLICT,
            FormulaConfirmation.REJECTED,
            captured_value,
            atlas_value,
            contribution(atlas_raw, "atlas", 5, 15),
            oracle_value,
            contribution(oracle_raw, "oracle", 4, 12),
            phantom_value,
            contribution(phantom_raw, "phantom", 3, 9),
            catalyst_value,
            contribution(catalyst_raw, "catalyst", 3, 9),
            _specter_bonus(specter_captured, specter_narrative),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("code:clean_implies_zero", "narrative:specter_penalty")),
            (),
            _wind_contribution(wind),
            _alignment(trend, direction),
            _volatility(iv_elevated),
            _earnings(earnings_number, True),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("formula:unclamped",)),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("formula:clamp",)),
            Evidenced(None, EvidenceOrigin.CONFLICT, ("formula:residual",)),
            direction_value,
            direction_status,
            direction_candidates,
            direction_argument,
            completeness,
            tuple(disagreements),
            tuple(warnings),
        )

    if explicit_penalty is not None:
        penalty_value = Evidenced(
            explicit_penalty,
            EvidenceOrigin.NARRATIVE,
            ("narrative:specter_penalty",),
        )
        confirmation_origin = EvidenceOrigin.NARRATIVE
        chosen = (PenaltyCandidate(explicit_penalty, * _with_penalty(parts, explicit_penalty)),)
        return _from_choice(
            identity_kwargs,
            captured_score,
            captured_value,
            atlas_value,
            oracle_value,
            phantom_value,
            catalyst_value,
            atlas_raw,
            oracle_raw,
            phantom_raw,
            catalyst_raw,
            clean,
            specter_captured,
            specter_narrative,
            penalty_value,
            chosen,
            wind,
            trend,
            direction,
            iv_elevated,
            earnings_number,
            direction_value,
            direction_status,
            direction_candidates,
            direction_argument,
            completeness,
            warnings,
            algebraic=False,
            confirmation_origin=confirmation_origin,
        )

    if clean is True:
        penalty_value = Evidenced(
            0,
            EvidenceOrigin.INVARIANT,
            ("code:penalty_sets_clean_false", "payload.gates.specter"),
            "A clean Specter result has penalty 0 because every penalty path sets clean false.",
        )
        chosen = (PenaltyCandidate(0, *_with_penalty(parts, 0)),)
        return _from_choice(
            identity_kwargs,
            captured_score,
            captured_value,
            atlas_value,
            oracle_value,
            phantom_value,
            catalyst_value,
            atlas_raw,
            oracle_raw,
            phantom_raw,
            catalyst_raw,
            clean,
            specter_captured,
            specter_narrative,
            penalty_value,
            chosen,
            wind,
            trend,
            direction,
            iv_elevated,
            earnings_number,
            direction_value,
            direction_status,
            direction_candidates,
            direction_argument,
            completeness,
            warnings,
            algebraic=False,
            confirmation_origin=EvidenceOrigin.INVARIANT,
        )

    candidates = []
    for penalty in specter_penalty_domain():
        unclamped, clamped = _with_penalty(parts, penalty)
        if clamped == captured_score:
            candidates.append(PenaltyCandidate(penalty, unclamped, clamped))
    if len(candidates) == 1:
        only = candidates[0]
        warnings.append(
            "Specter failed and no penalty sentence was stored. "
            f"Penalty {only.penalty} is the only value in the inspected category-sum "
            "domain that reproduces the captured clamped score. "
            "This is mathematical compatibility, not independent confirmation of the penalty."
        )
        penalty_value = Evidenced(
            only.penalty,
            EvidenceOrigin.INFERRED,
            ("algebra:unique_penalty_in_specter_domain", "payload.scout_score"),
            "Inferred from the captured clamped score. Not an independently stored penalty.",
        )
        return _from_choice(
            identity_kwargs,
            captured_score,
            captured_value,
            atlas_value,
            oracle_value,
            phantom_value,
            catalyst_value,
            atlas_raw,
            oracle_raw,
            phantom_raw,
            catalyst_raw,
            clean,
            specter_captured,
            specter_narrative,
            penalty_value,
            tuple(candidates),
            wind,
            trend,
            direction,
            iv_elevated,
            earnings_number,
            direction_value,
            direction_status,
            direction_candidates,
            direction_argument,
            completeness,
            warnings,
            algebraic=True,
            confirmation_origin=EvidenceOrigin.INFERRED,
        )
    if len(candidates) > 1:
        warnings.append(
            "Several inspected Specter penalties clamp to the captured score. "
            "No penalty was selected and no single raw total is returned. "
            "The candidate domain is the subset sums of "
            f"{SPECTER_CATEGORY_PENALTIES}, including 0 for a flag that adds no points."
        )
        return _ambiguous(
            identity_kwargs,
            captured_value,
            atlas_value,
            oracle_value,
            phantom_value,
            catalyst_value,
            atlas_raw,
            oracle_raw,
            phantom_raw,
            catalyst_raw,
            specter_captured,
            specter_narrative,
            tuple(candidates),
            wind,
            trend,
            direction,
            iv_elevated,
            earnings_number,
            direction_value,
            direction_status,
            direction_candidates,
            direction_argument,
            completeness,
            warnings,
        )

    unclamped_without, _clamped_without = _with_penalty(parts, 0)
    warnings.append(
        "No penalty in the inspected category-sum domain reproduces the captured score. "
        f"With penalty 0 the unclamped total would be {unclamped_without}, "
        "and that value was not substituted."
    )
    return _from_choice(
        identity_kwargs,
        captured_score,
        captured_value,
        atlas_value,
        oracle_value,
        phantom_value,
        catalyst_value,
        atlas_raw,
        oracle_raw,
        phantom_raw,
        catalyst_raw,
        clean,
        specter_captured,
        specter_narrative,
        Evidenced(None, EvidenceOrigin.MISSING, ("narrative:specter_penalty",), "No domain penalty fits."),
        (),
        wind,
        trend,
        direction,
        iv_elevated,
        earnings_number,
        direction_value,
        direction_status,
        direction_candidates,
        direction_argument,
        completeness,
        warnings,
        algebraic=False,
        confirmation_origin=EvidenceOrigin.MISSING,
        force_mismatch=True,
    )


_MISSING = object()


def _from_choice(
    identity_kwargs: dict[str, Any],
    captured_score: int,
    captured_value: Evidenced,
    atlas_value: Evidenced,
    oracle_value: Evidenced,
    phantom_value: Evidenced,
    catalyst_value: Evidenced,
    atlas_raw: int,
    oracle_raw: int,
    phantom_raw: int,
    catalyst_raw: int,
    clean: bool,
    specter_captured: Optional[bool],
    specter_narrative: Optional[bool],
    penalty_value: Evidenced,
    chosen: tuple[PenaltyCandidate, ...],
    wind: int,
    trend: str,
    direction: str,
    iv_elevated: bool,
    earnings_number: Any,
    direction_value: Evidenced,
    direction_status: DirectionStatus,
    direction_candidates: tuple[str, ...],
    direction_argument: Evidenced,
    completeness: InputCompleteness,
    warnings: list[str],
    *,
    algebraic: bool,
    confirmation_origin: EvidenceOrigin,
    force_mismatch: bool = False,
) -> SingleScanDecomposition:
    if force_mismatch or not chosen or penalty_value.value is None:
        unclamped = Evidenced(None, EvidenceOrigin.MISSING, ("formula:unclamped",))
        clamped = Evidenced(None, EvidenceOrigin.MISSING, ("formula:clamp",))
        error = Evidenced(
            None,
            EvidenceOrigin.CONFLICT,
            ("payload.scout_score", "formula:domain"),
            "Fully specified inputs do not reproduce the captured score.",
        )
        classification = ReconstructionClass.FORMULA_MISMATCH
        confirmation = FormulaConfirmation.REJECTED
        compatibility = "inconsistent"
        shown_penalty = penalty_value
        shown_candidates: tuple[PenaltyCandidate, ...] = ()
    else:
        candidate = chosen[0]
        if algebraic:
            origin = EvidenceOrigin.INFERRED
            detail = "Depends on an inferred penalty. Not an independent formula confirmation."
            classification = ReconstructionClass.ALGEBRAICALLY_INFERRED
            confirmation = FormulaConfirmation.MATHEMATICAL_COMPATIBILITY
            compatibility = "mathematically_compatible_penalty_not_captured"
        else:
            # Gate scores are narrative. A clean-Specter zero is an invariant,
            # and that lineage stays on specter_penalty rather than promoting
            # the whole total to an invariant.
            origin = EvidenceOrigin.NARRATIVE
            detail = f"Penalty evidence is {confirmation_origin.value}."
            classification = ReconstructionClass.EVIDENCE_DETERMINED
            confirmation = FormulaConfirmation.INDEPENDENT
            compatibility = "consistent_with_inspected_formula"
        mismatch = candidate.clamped_score != captured_score
        if mismatch:
            classification = ReconstructionClass.FORMULA_MISMATCH
            confirmation = FormulaConfirmation.REJECTED
            compatibility = "inconsistent"
            origin = EvidenceOrigin.CONFLICT
            detail = "Formula result was kept. The captured score was not written back onto it."
            warnings.append(
                "Captured score and the inspected formula disagree. "
                "The reconstructed total was not forced to the captured score."
            )
        unclamped = Evidenced(candidate.unclamped_score, origin, ("formula:unclamped",), detail)
        clamped = Evidenced(candidate.clamped_score, origin, ("formula:clamp(0,100)",), detail)
        error = Evidenced(
            candidate.clamped_score - captured_score,
            origin,
            ("payload.scout_score", "formula:clamp"),
            detail,
        )
        shown_penalty = penalty_value
        shown_candidates = chosen
    return SingleScanDecomposition(
        methodology=MethodologyIdentity(**identity_kwargs, historical_compatibility=compatibility),
        classification=classification,
        formula_confirmation=confirmation,
        captured_scout_score=captured_value,
        base_score=Evidenced(BASE_SCORE, EvidenceOrigin.INVARIANT, ("formula:base",)),
        atlas_raw=atlas_value,
        atlas_contribution=Evidenced(
            upside_cap(atlas_raw, 5, 15),
            EvidenceOrigin.NARRATIVE,
            ("narrative:atlas.score", "formula:min(score*5,15)"),
        ),
        oracle_raw=oracle_value,
        oracle_contribution=Evidenced(
            upside_cap(oracle_raw, 4, 12),
            EvidenceOrigin.NARRATIVE,
            ("narrative:oracle.score", "formula:min(score*4,12)"),
        ),
        phantom_raw=phantom_value,
        phantom_contribution=Evidenced(
            upside_cap(phantom_raw, 3, 9),
            EvidenceOrigin.NARRATIVE,
            ("narrative:phantom.score", "formula:min(score*3,9)"),
        ),
        catalyst_raw=catalyst_value,
        catalyst_contribution=Evidenced(
            upside_cap(catalyst_raw, 3, 9),
            EvidenceOrigin.NARRATIVE,
            ("narrative:catalyst.score", "formula:min(score*3,9)"),
        ),
        specter_clean_bonus=_specter_bonus(specter_captured, specter_narrative),
        specter_penalty=shown_penalty,
        penalty_candidates=shown_candidates,
        wind_contribution=_wind_contribution(wind),
        alignment_bonus=_alignment(trend, direction),
        volatility_bonus=_volatility(iv_elevated),
        earnings_penalty=_earnings(earnings_number, True),
        unclamped_score=unclamped,
        clamped_score=clamped,
        reconstruction_error=error,
        direction=direction_value,
        direction_status=direction_status,
        direction_candidates=direction_candidates,
        direction_argument=direction_argument,
        input_completeness=completeness,
        disagreements=(),
        warnings=tuple(warnings),
    )


def _ambiguous(
    identity_kwargs: dict[str, Any],
    captured_value: Evidenced,
    atlas_value: Evidenced,
    oracle_value: Evidenced,
    phantom_value: Evidenced,
    catalyst_value: Evidenced,
    atlas_raw: int,
    oracle_raw: int,
    phantom_raw: int,
    catalyst_raw: int,
    specter_captured: Optional[bool],
    specter_narrative: Optional[bool],
    candidates: tuple[PenaltyCandidate, ...],
    wind: int,
    trend: str,
    direction: str,
    iv_elevated: bool,
    earnings_number: Any,
    direction_value: Evidenced,
    direction_status: DirectionStatus,
    direction_candidates: tuple[str, ...],
    direction_argument: Evidenced,
    completeness: InputCompleteness,
    warnings: list[str],
) -> SingleScanDecomposition:
    penalties = tuple(item.penalty for item in candidates)
    raw_totals = tuple(item.unclamped_score for item in candidates)
    detail = (
        f"Possible penalties {penalties}. Possible unclamped totals {raw_totals}. "
        "None of these is selected."
    )
    return SingleScanDecomposition(
        methodology=MethodologyIdentity(
            **identity_kwargs,
            historical_compatibility="clamp_compatible_vector_ambiguous",
        ),
        classification=ReconstructionClass.AMBIGUOUS,
        formula_confirmation=FormulaConfirmation.NOT_UNIQUE,
        captured_scout_score=captured_value,
        base_score=Evidenced(BASE_SCORE, EvidenceOrigin.INVARIANT, ("formula:base",)),
        atlas_raw=atlas_value,
        atlas_contribution=Evidenced(
            upside_cap(atlas_raw, 5, 15), EvidenceOrigin.NARRATIVE,
            ("narrative:atlas.score", "formula:min(score*5,15)"),
        ),
        oracle_raw=oracle_value,
        oracle_contribution=Evidenced(
            upside_cap(oracle_raw, 4, 12), EvidenceOrigin.NARRATIVE,
            ("narrative:oracle.score", "formula:min(score*4,12)"),
        ),
        phantom_raw=phantom_value,
        phantom_contribution=Evidenced(
            upside_cap(phantom_raw, 3, 9), EvidenceOrigin.NARRATIVE,
            ("narrative:phantom.score", "formula:min(score*3,9)"),
        ),
        catalyst_raw=catalyst_value,
        catalyst_contribution=Evidenced(
            upside_cap(catalyst_raw, 3, 9), EvidenceOrigin.NARRATIVE,
            ("narrative:catalyst.score", "formula:min(score*3,9)"),
        ),
        specter_clean_bonus=_specter_bonus(specter_captured, specter_narrative),
        specter_penalty=Evidenced(None, EvidenceOrigin.AMBIGUOUS, ("algebra:specter_domain",), detail),
        penalty_candidates=candidates,
        wind_contribution=_wind_contribution(wind),
        alignment_bonus=_alignment(trend, direction),
        volatility_bonus=_volatility(iv_elevated),
        earnings_penalty=_earnings(earnings_number, True),
        unclamped_score=Evidenced(None, EvidenceOrigin.AMBIGUOUS, ("formula:unclamped",), detail),
        clamped_score=Evidenced(
            None,
            EvidenceOrigin.AMBIGUOUS,
            ("formula:clamp(0,100)",),
            "Every candidate clamps to the captured score. That shared ceiling is not a unique vector.",
        ),
        reconstruction_error=Evidenced(
            None,
            EvidenceOrigin.AMBIGUOUS,
            ("formula:residual",),
            "A residual would require selecting one candidate.",
        ),
        direction=direction_value,
        direction_status=direction_status,
        direction_candidates=direction_candidates,
        direction_argument=direction_argument,
        input_completeness=completeness,
        disagreements=(),
        warnings=tuple(warnings),
    )


def _unresolved(
    identity_kwargs: dict[str, Any],
    compatibility: str,
    classification: ReconstructionClass,
    confirmation: FormulaConfirmation,
    captured_value: Evidenced,
    atlas_value: Evidenced,
    atlas_contribution: Evidenced,
    oracle_value: Evidenced,
    oracle_contribution: Evidenced,
    phantom_value: Evidenced,
    phantom_contribution: Evidenced,
    catalyst_value: Evidenced,
    catalyst_contribution: Evidenced,
    specter_bonus: Evidenced,
    specter_penalty: Evidenced,
    candidates: tuple[PenaltyCandidate, ...],
    wind_contribution: Evidenced,
    alignment_bonus: Evidenced,
    volatility_bonus: Evidenced,
    earnings_penalty: Evidenced,
    unclamped: Evidenced,
    clamped: Evidenced,
    error: Evidenced,
    direction_value: Evidenced,
    direction_status: DirectionStatus,
    direction_candidates: tuple[str, ...],
    direction_argument: Evidenced,
    completeness: InputCompleteness,
    disagreements: tuple[Disagreement, ...],
    warnings: tuple[str, ...],
) -> SingleScanDecomposition:
    return SingleScanDecomposition(
        methodology=MethodologyIdentity(**identity_kwargs, historical_compatibility=compatibility),
        classification=classification,
        formula_confirmation=confirmation,
        captured_scout_score=captured_value,
        base_score=Evidenced(BASE_SCORE, EvidenceOrigin.INVARIANT, ("formula:base",)),
        atlas_raw=atlas_value,
        atlas_contribution=atlas_contribution,
        oracle_raw=oracle_value,
        oracle_contribution=oracle_contribution,
        phantom_raw=phantom_value,
        phantom_contribution=phantom_contribution,
        catalyst_raw=catalyst_value,
        catalyst_contribution=catalyst_contribution,
        specter_clean_bonus=specter_bonus,
        specter_penalty=specter_penalty,
        penalty_candidates=candidates,
        wind_contribution=wind_contribution,
        alignment_bonus=alignment_bonus,
        volatility_bonus=volatility_bonus,
        earnings_penalty=earnings_penalty,
        unclamped_score=unclamped,
        clamped_score=clamped,
        reconstruction_error=error,
        direction=direction_value,
        direction_status=direction_status,
        direction_candidates=direction_candidates,
        direction_argument=direction_argument,
        input_completeness=completeness,
        disagreements=disagreements,
        warnings=warnings,
    )


def _formula_parts(
    atlas_raw: int,
    oracle_raw: int,
    phantom_raw: int,
    catalyst_raw: int,
    clean: bool,
    wind: int,
    trend: str,
    direction: str,
    iv_elevated: bool,
    earnings_number: Any,
) -> int:
    """Score before the Specter penalty and before the clamp."""
    total = BASE_SCORE
    total += upside_cap(atlas_raw, 5, 15)
    total += upside_cap(oracle_raw, 4, 12)
    total += upside_cap(phantom_raw, 3, 9)
    total += upside_cap(catalyst_raw, 3, 9)
    total += 5 if clean else 0
    total += wind * 3
    total += _alignment_points(trend, direction)
    total += 0 if iv_elevated else 3
    total += earnings_penalty_points(earnings_number)
    return total


def _with_penalty(parts_before_penalty: int, penalty: int) -> tuple[int, int]:
    unclamped = parts_before_penalty + penalty
    return unclamped, clamp_score(unclamped)


def _alignment_points(trend: str, direction: str) -> int:
    if trend == "UPTREND" and direction == "BULLISH":
        return 5
    if trend == "DOWNTREND" and direction == "BEARISH":
        return 5
    return 0


def _specter_bonus(
    specter_captured: Optional[bool],
    specter_narrative: Optional[bool],
) -> Evidenced:
    clean = specter_captured if specter_captured is not None else specter_narrative
    if clean is None:
        return Evidenced(None, EvidenceOrigin.MISSING, ("payload.gates.specter",))
    origin = EvidenceOrigin.CAPTURED if specter_captured is not None else EvidenceOrigin.NARRATIVE
    return Evidenced(5 if clean else 0, origin, ("payload.gates.specter", "formula:clean_bonus"))


def _explicit_or_missing_penalty(explicit_penalty: Optional[int]) -> Evidenced:
    if explicit_penalty is None:
        return Evidenced(None, EvidenceOrigin.MISSING, ("narrative:specter_penalty",))
    return Evidenced(explicit_penalty, EvidenceOrigin.NARRATIVE, ("narrative:specter_penalty",))


def _wind_contribution(wind: Optional[int]) -> Evidenced:
    if wind is None:
        return Evidenced(None, EvidenceOrigin.MISSING, ("payload.wind",))
    return Evidenced(wind * 3, EvidenceOrigin.CAPTURED, ("payload.wind", "formula:wind*3"))


def _alignment(trend: Optional[str], direction: Optional[str]) -> Evidenced:
    if trend is None or direction is None:
        return Evidenced(None, EvidenceOrigin.MISSING, ("payload.trend", "payload.direction"))
    return Evidenced(
        _alignment_points(trend, direction),
        EvidenceOrigin.CAPTURED,
        ("payload.trend", "payload.direction", "formula:alignment"),
    )


def _volatility(iv_elevated: Optional[bool]) -> Evidenced:
    if iv_elevated is None:
        return Evidenced(None, EvidenceOrigin.MISSING, ("payload.iv_elevated",))
    return Evidenced(
        0 if iv_elevated else 3,
        EvidenceOrigin.CAPTURED,
        ("payload.iv_elevated", "formula:quiet_iv_bonus"),
    )


def _earnings(earnings_number: Any, present: bool) -> Evidenced:
    if not present:
        return Evidenced(None, EvidenceOrigin.MISSING, ("payload.earnings_days",))
    detail = ""
    if earnings_number == 0:
        detail = "Day 0 is falsy in the inspected if/elif, so the penalty is 0."
    return Evidenced(
        earnings_penalty_points(earnings_number),
        EvidenceOrigin.CAPTURED,
        ("payload.earnings_days", "formula:earnings_if_elif"),
        detail,
    )


def _missing_required(
    atlas_raw: Optional[int],
    oracle_raw: Optional[int],
    phantom_raw: Optional[int],
    catalyst_raw: Optional[int],
    specter: Optional[bool],
    wind: Optional[int],
    trend: Optional[str],
    direction: Optional[str],
    iv_elevated: Optional[bool],
    earnings_present: bool,
) -> tuple[str, ...]:
    missing = []
    for name, value in (
        ("atlas_raw", atlas_raw),
        ("oracle_raw", oracle_raw),
        ("phantom_raw", phantom_raw),
        ("catalyst_raw", catalyst_raw),
        ("specter", specter),
        ("wind", wind),
        ("trend", trend),
        ("direction", direction),
        ("iv_elevated", iv_elevated),
    ):
        if value is None:
            missing.append(name)
    if not earnings_present:
        missing.append("earnings_days")
    return tuple(missing)


def _direction_status(
    scan: Mapping[str, Any],
    text: str,
    direction: Optional[str],
) -> tuple[DirectionStatus, tuple[str, ...], str]:
    drawdown = _optional_float(scan.get("drawdown"))
    rsi = _optional_float(scan.get("rsi"))
    gap = scan.get("dcf_gap") if "dcf_gap" in scan else None
    if gap is not None:
        gap = _optional_float(gap)
    runup_text = _narrative_runup(text)
    if drawdown is None or rsi is None or runup_text is None or direction is None:
        return DirectionStatus.MISSING, (), "Direction inputs are incomplete."
    printed, decimals = runup_text
    low, high = _rounding_interval(printed, decimals)
    threshold = 60.0
    straddles = low < threshold < high
    if not straddles:
        predicted = _classify_direction(drawdown, printed, gap, rsi)
        if predicted == direction:
            return DirectionStatus.MATCH, (predicted,), ""
        return (
            DirectionStatus.CONFLICT,
            (predicted,),
            f"Direction rule on the stored inputs yields {predicted}; the payload says {direction}.",
        )
    below = _classify_direction(drawdown, min(printed, threshold) - 0.001, gap, rsi)
    above = _classify_direction(drawdown, max(printed, threshold), gap, rsi)
    candidates = tuple(dict.fromkeys((below, above)))
    if len(candidates) == 1:
        if candidates[0] == direction:
            return DirectionStatus.MATCH, candidates, ""
        return (
            DirectionStatus.CONFLICT,
            candidates,
            f"Direction rule yields {candidates[0]}; the payload says {direction}.",
        )
    if direction in candidates:
        return (
            DirectionStatus.ROUNDING_AMBIGUOUS,
            candidates,
            "Run-up is printed to one decimal on the 60 percent bearish boundary. "
            f"The pre-round interval supports {candidates}. "
            f"The stored direction {direction} is inside that interval. "
            "This is rounding ambiguity, not a demonstrated formula defect.",
        )
    return (
        DirectionStatus.CONFLICT,
        candidates,
        "Run-up rounding cannot explain the stored direction.",
    )


def _classify_direction(
    drawdown: float,
    runup: float,
    dcf_gap: Optional[float],
    rsi: float,
) -> str:
    call_case = drawdown <= -20 or bool(dcf_gap and dcf_gap > 20) or bool(rsi and rsi < 40)
    put_case = runup >= 60 or bool(dcf_gap and dcf_gap < -30) or bool(rsi and rsi > 75)
    if call_case and not put_case:
        return "BULLISH"
    if put_case and not call_case:
        return "BEARISH"
    return "NEUTRAL"


def _rounding_interval(printed: float, decimals: int) -> tuple[float, float]:
    quantum = 10 ** (-decimals)
    return printed - quantum / 2, printed + quantum / 2


def _gate_blocks(text: str) -> dict[str, str]:
    parts = re.split(r"(?=GATE\s+\d+\s+[—-]\s+[A-Z]+)", text)
    blocks: dict[str, str] = {}
    for part in parts:
        match = re.search(r"GATE\s+\d+\s+[—-]\s+([A-Z]+)", part)
        if match:
            blocks[match.group(1)] = part
    return blocks


def _last_score(block: str) -> Optional[int]:
    found = re.findall(r"(?m)^\s*Score:\s*(-?\d+)\s*$", block)
    if not found:
        return None
    return int(found[-1])


def _has_unrecognized_score_line(block: str) -> bool:
    return re.search(r"(?m)^\s*Score:\s*(?!-?\d+\s*$)\S+", block) is not None


def _narrative_scout_score(text: str) -> Optional[int]:
    match = re.search(r"SCOUT SCORE:\s*(-?\d+)\s*/\s*100", text)
    return int(match.group(1)) if match else None


def _narrative_wind(text: str) -> Optional[int]:
    match = re.search(r"Wind:\s*([+-]?\d+)", text)
    return int(match.group(1)) if match else None


def _narrative_trend(block: str) -> Optional[str]:
    if "No SMA data" in block:
        return "UNKNOWN"
    if "DOWNTREND" in block:
        return "DOWNTREND"
    if "UPTREND" in block:
        return "UPTREND"
    return None


def _narrative_direction(text: str) -> Optional[str]:
    match = re.search(r"(?m)^\s*Direction:\s*(BULLISH|BEARISH|NEUTRAL)\s*$", text)
    return match.group(1) if match else None


def _narrative_iv(block: str) -> Optional[bool]:
    if "Normal volatility" in block:
        return False
    if any(token in block for token in ("elevated volatility", "IV spiking", "IV premium", "IV ELEVATED")):
        return True
    return None


def _narrative_earnings(block: str) -> Any:
    if "No confirmed earnings date" in block:
        return None
    match = re.search(r"EARNINGS IN\s+(\d+)\s+DAYS", block, re.I)
    if not match:
        match = re.search(r"Earnings in\s+(\d+)\s+days", block)
    if match:
        return int(match.group(1))
    return _MISSING


def _narrative_specter(block: str) -> Optional[bool]:
    clean = "CLEAN — no threats detected" in block or "CLEAN - no threats detected" in block
    flagged = "RED FLAGS DETECTED" in block
    if clean and flagged:
        return None
    if clean:
        return True
    if flagged:
        return False
    return None


def _narrative_penalty(text: str) -> Optional[int]:
    match = re.search(r"Scout Score penalty:\s*(-?\d+)\s*points", text, re.I)
    return int(match.group(1)) if match else None


def _narrative_runup(text: str) -> Optional[tuple[float, int]]:
    match = re.search(r"Runup from low:\s*(-?\d+(?:\.(\d+))?)%", text)
    if not match:
        return None
    decimals = len(match.group(2) or "")
    return float(match.group(1)), decimals


def _record_disagreement(
    disagreements: list[Disagreement],
    field: str,
    captured: Any,
    narrative: Any,
) -> None:
    if narrative is _MISSING or captured is _MISSING:
        return
    if captured is None and narrative is None:
        return
    if captured != narrative:
        disagreements.append(Disagreement(field, captured, narrative))


def _record_gate_score_disagreement(
    disagreements: list[Disagreement],
    field: str,
    raw_score: Optional[int],
    gate_value: Any,
    threshold: int,
) -> None:
    if raw_score is None or not isinstance(gate_value, bool):
        return
    expected = raw_score >= threshold
    if gate_value != expected:
        disagreements.append(Disagreement(field, gate_value, expected))


def _captured_int(value: Any, _name: str) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return None


def _captured_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    return None


def _captured_token(value: Any) -> Optional[str]:
    if isinstance(value, str) and value:
        return value.strip().upper()
    return None


def _captured_direction(value: Any) -> Optional[str]:
    token = _captured_token(value)
    if token in {"BULLISH", "BEARISH", "NEUTRAL"}:
        return token
    return None


def _captured_earnings(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, bool):
        return _MISSING
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    return _MISSING


def _optional_float(value: Any) -> Optional[float]:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None
