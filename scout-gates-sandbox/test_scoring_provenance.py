#!/usr/bin/env python3
"""Isolated fixtures for single-scan scoring decomposition.

These tests never open scout_memory.db.
"""

from __future__ import annotations

import json
import unittest

from scoring_provenance import (
    DEPLOYMENT_VERIFICATION_STATUS,
    INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
    DirectionStatus,
    EvidenceOrigin,
    FormulaConfirmation,
    ReconstructionClass,
    ScoringProvenanceError,
    decompose_single_scan,
)


def _compass(trend: str) -> str:
    if trend == "UPTREND":
        return "  ✅ UPTREND — $10.00 above 20-day SMA $9.00"
    if trend == "DOWNTREND":
        return "  ⚠ DOWNTREND — $10.00 below 20-day SMA $11.00"
    return "  No SMA data available"


def _pulse(iv_elevated: bool) -> str:
    if iv_elevated:
        return "  ⚠ Stock moved 6.0% today — elevated volatility"
    return "  ✅ Normal volatility. Options reasonably priced."


def _aegis(earnings) -> str:
    if earnings is None:
        return "  ℹ No confirmed earnings date found. Check manually before trading."
    if earnings == 0:
        return "  🚨 EARNINGS IN 0 DAYS (2026-05-20) — EXTREME RISK for options"
    return f"  ✅ Earnings in {earnings} days (2026-05-20) — noted"


def _narrative(
    *,
    atlas,
    oracle,
    phantom,
    catalyst,
    clean: bool,
    penalty,
    wind: int,
    trend: str,
    direction: str,
    iv_elevated: bool,
    earnings,
    score: int,
    runup: str,
    omit_catalyst: bool = False,
    malformed_atlas: bool = False,
    narrative_wind: int | None = None,
    narrative_score: int | None = None,
) -> str:
    shown_wind = wind if narrative_wind is None else narrative_wind
    shown_score = score if narrative_score is None else narrative_score
    atlas_line = "  Score: n/a" if malformed_atlas else f"  Score: {atlas}"
    specter = []
    if penalty is not None:
        specter.append(f"  Scout Score penalty: {penalty} points")
    specter.append(
        "  ✅ CLEAN — no threats detected"
        if clean
        else "  ❌ RED FLAGS DETECTED — review before trading"
    )
    catalyst_block = ""
    if not omit_catalyst:
        catalyst_block = (
            "\nGATE 5 — CATALYST (Event Trigger)\n"
            f"  Score: {catalyst}\n"
        )
    return (
        "SCOUT SINGLE SCAN — TEST\n"
        f"Sector: TEST (Wind: {shown_wind:+d})\n"
        "Drawdown from high: -5.0%\n"
        f"Runup from low: {runup}%\n"
        "GATE 2 — ATLAS (Core Strength)\n"
        f"{atlas_line}\n"
        "GATE 3 — ORACLE (Forward Vision)\n"
        f"  Score: {oracle}\n"
        "GATE 4 — PHANTOM (Smart Money)\n"
        f"  Score: {phantom}\n"
        f"{catalyst_block}"
        "GATE 6 — SPECTER (Threat Scan)\n"
        + "\n".join(specter)
        + "\n"
        "GATE 8 — AEGIS (Earnings Shield)\n"
        f"{_aegis(earnings)}\n"
        "GATE 9 — COMPASS (Trend Lock)\n"
        f"{_compass(trend)}\n"
        "GATE 10 — PULSE (Volatility Read)\n"
        f"{_pulse(iv_elevated)}\n"
        f"Direction: {direction}\n"
        f"SCOUT SCORE: {shown_score}/100\n"
    )


def payload(
    *,
    atlas: int = 2,
    oracle: int = 1,
    phantom: int = 0,
    catalyst: int = 1,
    clean: bool = True,
    penalty=None,
    wind: int = 0,
    trend: str = "UNKNOWN",
    direction: str = "NEUTRAL",
    iv_elevated: bool = True,
    earnings=None,
    score: int = 67,
    drawdown: float = -5.0,
    rsi: float = 50.0,
    dcf_gap: float = 0.0,
    runup: str = "10.0",
    omit_catalyst: bool = False,
    malformed_atlas: bool = False,
    narrative_wind: int | None = None,
    narrative_score: int | None = None,
    gates: dict | None = None,
) -> dict:
    if gates is None:
        gates = {
            "atlas": atlas >= 2,
            "oracle": oracle >= 1,
            "phantom": phantom >= 0,
            "catalyst": catalyst >= 1,
            "specter": clean,
            "aegis": not (earnings and earnings <= 14),
            "fortress": score >= 40,
        }
    raw = {
        "ticker": "TEST",
        "scout_score": score,
        "direction": direction,
        "wind": wind,
        "trend": trend,
        "iv_elevated": iv_elevated,
        "earnings_days": earnings,
        "drawdown": drawdown,
        "rsi": rsi,
        "dcf_gap": dcf_gap,
        "gates": gates,
        "raw_output": _narrative(
            atlas=atlas,
            oracle=oracle,
            phantom=phantom,
            catalyst=catalyst,
            clean=clean,
            penalty=penalty,
            wind=wind,
            trend=trend,
            direction=direction,
            iv_elevated=iv_elevated,
            earnings=earnings,
            score=score,
            runup=runup,
            omit_catalyst=omit_catalyst,
            malformed_atlas=malformed_atlas,
            narrative_wind=narrative_wind,
            narrative_score=narrative_score,
        ),
    }
    return {"raw": raw}


class ScoringProvenanceTests(unittest.TestCase):
    def test_clean_specter_uses_invariant_zero_penalty(self) -> None:
        # 50+10+4+0+3+5 = 72. Elevated IV adds nothing. No earnings penalty.
        result = decompose_single_scan(payload(score=72))
        self.assertEqual(result.classification, ReconstructionClass.EVIDENCE_DETERMINED)
        self.assertEqual(result.formula_confirmation, FormulaConfirmation.INDEPENDENT)
        self.assertEqual(result.specter_penalty.value, 0)
        self.assertEqual(result.specter_penalty.origin, EvidenceOrigin.INVARIANT)
        self.assertEqual(result.specter_clean_bonus.value, 5)
        self.assertEqual(result.unclamped_score.value, 72)
        self.assertEqual(result.clamped_score.value, 72)
        self.assertEqual(result.reconstruction_error.value, 0)
        self.assertIsNone(result.methodology.historical_methodology_id)
        self.assertEqual(
            result.methodology.deployment_verification,
            DEPLOYMENT_VERIFICATION_STATUS,
        )
        self.assertIsNone(result.methodology.verified_deployment_id)
        self.assertEqual(
            result.methodology.inspected_methodology_id,
            INSPECTED_SINGLE_SCAN_METHODOLOGY_ID,
        )
        self.assertEqual(result.direction_argument.value, "CALL")
        self.assertEqual(result.direction_argument.origin, EvidenceOrigin.INVARIANT)

    def test_explicit_news_penalty_is_narrative_evidence(self) -> None:
        # Same components as the clean case, but Specter fails and the text
        # states -20. 72 - 5 clean bonus - 20 = 47.
        result = decompose_single_scan(
            payload(clean=False, penalty=-20, score=47)
        )
        self.assertEqual(result.classification, ReconstructionClass.EVIDENCE_DETERMINED)
        self.assertEqual(result.specter_penalty.value, -20)
        self.assertEqual(result.specter_penalty.origin, EvidenceOrigin.NARRATIVE)
        self.assertEqual(result.specter_clean_bonus.value, 0)
        self.assertEqual(result.reconstruction_error.value, 0)

    def test_failed_specter_unique_zero_is_not_independent_confirmation(self) -> None:
        # 50+10+4+0+3 = 67 with a failed Specter and no penalty sentence.
        # Every negative category sum misses 67, so 0 is algebraically unique.
        result = decompose_single_scan(payload(clean=False, score=67))
        self.assertEqual(result.classification, ReconstructionClass.ALGEBRAICALLY_INFERRED)
        self.assertEqual(
            result.formula_confirmation,
            FormulaConfirmation.MATHEMATICAL_COMPATIBILITY,
        )
        self.assertNotEqual(result.formula_confirmation, FormulaConfirmation.INDEPENDENT)
        self.assertEqual(result.specter_penalty.value, 0)
        self.assertEqual(result.specter_penalty.origin, EvidenceOrigin.INFERRED)
        self.assertEqual(result.unclamped_score.origin, EvidenceOrigin.INFERRED)
        self.assertEqual(result.reconstruction_error.value, 0)

    def test_saturated_score_keeps_every_possible_penalty(self) -> None:
        # Unclamped total is 103 at penalty 0. Penalties 0 and -3 both clamp
        # to 100. The decomposer must not pick one.
        result = decompose_single_scan(
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
            )
        )
        self.assertEqual(result.classification, ReconstructionClass.AMBIGUOUS)
        self.assertEqual(result.formula_confirmation, FormulaConfirmation.NOT_UNIQUE)
        self.assertIsNone(result.specter_penalty.value)
        self.assertEqual(result.specter_penalty.origin, EvidenceOrigin.AMBIGUOUS)
        self.assertIsNone(result.unclamped_score.value)
        self.assertIsNone(result.clamped_score.value)
        self.assertIsNone(result.reconstruction_error.value)
        penalties = tuple(item.penalty for item in result.penalty_candidates)
        self.assertGreater(len(penalties), 1)
        self.assertIn(0, penalties)
        self.assertIn(-3, penalties)
        self.assertTrue(all(item.clamped_score == 100 for item in result.penalty_candidates))

    def test_earnings_day_zero_is_not_corrected(self) -> None:
        # Clean total 72. Day 0 does not subtract 10.
        result = decompose_single_scan(payload(earnings=0, score=72))
        self.assertEqual(result.earnings_penalty.value, 0)
        self.assertEqual(result.classification, ReconstructionClass.EVIDENCE_DETERMINED)
        self.assertTrue(any("day 0" in warning.lower() for warning in result.warnings))

    def test_earnings_windows_follow_the_inspected_if_elif(self) -> None:
        expected = {1: -10, 7: -10, 8: -5, 14: -5, 15: 0}
        for days, penalty in expected.items():
            with self.subTest(days=days):
                result = decompose_single_scan(
                    payload(earnings=days, score=72 + penalty)
                )
                self.assertEqual(result.earnings_penalty.value, penalty)
                self.assertEqual(result.reconstruction_error.value, 0)
                self.assertEqual(
                    result.classification,
                    ReconstructionClass.EVIDENCE_DETERMINED,
                )

    def test_positive_contributions_hit_the_upside_cap(self) -> None:
        result = decompose_single_scan(
            payload(atlas=6, oracle=4, phantom=4, catalyst=4, score=72)
        )
        self.assertEqual(result.atlas_raw.value, 6)
        self.assertEqual(result.atlas_contribution.value, 15)
        self.assertEqual(result.oracle_contribution.value, 12)
        self.assertEqual(result.phantom_contribution.value, 9)
        self.assertEqual(result.catalyst_contribution.value, 9)
        self.assertLess(result.atlas_contribution.value, 6 * 5)

    def test_negative_component_passes_through_the_upside_cap(self) -> None:
        # Catalyst -2 contributes -6, not 0 and not a capped positive.
        # 72 - 3 (the usual +1 catalyst) - 6 = 63, and the gate fails.
        result = decompose_single_scan(payload(catalyst=-2, score=63))
        self.assertEqual(result.catalyst_raw.value, -2)
        self.assertEqual(result.catalyst_contribution.value, -6)
        self.assertEqual(result.reconstruction_error.value, 0)

    def test_upper_and_lower_clamps(self) -> None:
        high = decompose_single_scan(
            payload(
                atlas=4,
                oracle=3,
                phantom=4,
                catalyst=4,
                wind=3,
                trend="UPTREND",
                direction="BULLISH",
                iv_elevated=False,
                score=100,
                drawdown=-25.0,
            )
        )
        self.assertEqual(high.classification, ReconstructionClass.EVIDENCE_DETERMINED)
        self.assertGreater(high.unclamped_score.value, 100)
        self.assertEqual(high.clamped_score.value, 100)
        self.assertEqual(high.reconstruction_error.value, 0)

        low = decompose_single_scan(
            payload(
                atlas=-4,
                oracle=-1,
                phantom=-6,
                catalyst=-2,
                clean=False,
                penalty=-56,
                wind=-3,
                iv_elevated=True,
                earnings=1,
                score=0,
            )
        )
        self.assertLess(low.unclamped_score.value, 0)
        self.assertEqual(low.clamped_score.value, 0)
        self.assertEqual(low.reconstruction_error.value, 0)
        self.assertEqual(low.classification, ReconstructionClass.EVIDENCE_DETERMINED)

    def test_missing_component_does_not_become_zero(self) -> None:
        result = decompose_single_scan(payload(omit_catalyst=True, score=72))
        self.assertEqual(result.classification, ReconstructionClass.INCOMPLETE)
        self.assertEqual(result.catalyst_raw.origin, EvidenceOrigin.MISSING)
        self.assertIsNone(result.catalyst_raw.value)
        self.assertIsNone(result.catalyst_contribution.value)
        self.assertIsNone(result.unclamped_score.value)
        self.assertIn("catalyst_raw", result.input_completeness.missing)

    def test_malformed_score_line_is_not_guessed(self) -> None:
        result = decompose_single_scan(payload(malformed_atlas=True, score=72))
        self.assertEqual(result.classification, ReconstructionClass.INCOMPLETE)
        self.assertEqual(result.atlas_raw.origin, EvidenceOrigin.MISSING)
        self.assertTrue(any("Unrecognized score format" in warning for warning in result.warnings))

    def test_structured_and_narrative_wind_conflict(self) -> None:
        result = decompose_single_scan(payload(score=72, narrative_wind=3))
        self.assertEqual(result.classification, ReconstructionClass.EVIDENCE_CONFLICT)
        self.assertEqual(result.formula_confirmation, FormulaConfirmation.REJECTED)
        self.assertIsNone(result.unclamped_score.value)
        self.assertTrue(any(item.field == "wind" for item in result.disagreements))
        self.assertEqual(result.wind_contribution.origin, EvidenceOrigin.CONFLICT)

    def test_formula_mismatch_keeps_the_formula_result(self) -> None:
        result = decompose_single_scan(payload(score=80))
        self.assertEqual(result.classification, ReconstructionClass.FORMULA_MISMATCH)
        self.assertEqual(result.clamped_score.value, 72)
        self.assertEqual(result.reconstruction_error.value, 72 - 80)
        self.assertNotEqual(result.clamped_score.value, 80)

    def test_rounded_runup_boundary_is_not_a_formula_defect(self) -> None:
        result = decompose_single_scan(
            payload(
                score=72,
                drawdown=-8.25,
                rsi=64.24,
                dcf_gap=-23.23,
                runup="60.0",
                direction="NEUTRAL",
            )
        )
        self.assertEqual(result.classification, ReconstructionClass.EVIDENCE_DETERMINED)
        self.assertEqual(result.direction_status, DirectionStatus.ROUNDING_AMBIGUOUS)
        self.assertIn("NEUTRAL", result.direction_candidates)
        self.assertIn("BEARISH", result.direction_candidates)
        self.assertNotEqual(result.classification, ReconstructionClass.FORMULA_MISMATCH)
        self.assertTrue(any("rounding" in warning.lower() for warning in result.warnings))

    def test_input_is_not_mutated(self) -> None:
        body = payload(score=72)
        gates = body["raw"]["gates"]
        before = json.dumps(body, sort_keys=True)
        decompose_single_scan(body)
        self.assertEqual(json.dumps(body, sort_keys=True), before)
        self.assertIs(body["raw"]["gates"], gates)

    def test_repeated_execution_is_identical(self) -> None:
        body = payload(clean=False, score=67)
        first = decompose_single_scan(body)
        second = decompose_single_scan(body)
        self.assertEqual(first, second)
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_unsupported_methodology_is_rejected(self) -> None:
        with self.assertRaises(ScoringProvenanceError):
            decompose_single_scan(payload(score=72), methodology_id="scout-batch-v1+deadbeef")

    def test_batch_payload_is_rejected(self) -> None:
        batch = {
            "raw": {
                "raw_output": "SCOUT v6 — 05/19/2026\nFINAL RANKINGS\nTOP PICK: AAA (CALL)\n",
                "scout_score": 80,
            }
        }
        with self.assertRaises(ScoringProvenanceError) as raised:
            decompose_single_scan(batch)
        self.assertIn("Batch", str(raised.exception))


if __name__ == "__main__":
    unittest.main()
