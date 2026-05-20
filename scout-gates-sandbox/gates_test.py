#!/usr/bin/env python3
"""Unit tests for sandbox gate intelligence helpers."""

from __future__ import annotations

import unittest

from earnings_intelligence import (
    GUIDANCE_INLINE,
    GUIDANCE_LOWERED,
    GUIDANCE_RAISED,
    GUIDANCE_STRONGLY_RAISED,
    apply_scout_score_adjustment,
    attach_adjusted_scout_score,
    build_earnings_intelligence,
    compute_conviction_adjustment,
    recency_multiplier,
    score_eps_surprise,
    score_guidance,
    score_market_reaction,
    score_quality_modifier,
    score_revenue_surprise,
    secondary_gate_weight,
)


class EarningsIntelligenceTests(unittest.TestCase):
    def test_eps_surprise_tiers(self) -> None:
        self.assertEqual(score_eps_surprise(25), 15)
        self.assertEqual(score_eps_surprise(15), 12)
        self.assertEqual(score_eps_surprise(5), 8)
        self.assertEqual(score_eps_surprise(0), 4)
        self.assertEqual(score_eps_surprise(-2), -4)
        self.assertEqual(score_eps_surprise(-5), -8)
        self.assertEqual(score_eps_surprise(-15), -12)
        self.assertEqual(score_eps_surprise(-25), -15)
        self.assertEqual(score_eps_surprise(None), 0)

    def test_revenue_surprise_tiers(self) -> None:
        self.assertEqual(score_revenue_surprise(12), 15)
        self.assertEqual(score_revenue_surprise(7), 12)
        self.assertEqual(score_revenue_surprise(3), 8)
        self.assertEqual(score_revenue_surprise(0), 4)
        self.assertEqual(score_revenue_surprise(-1), -4)
        self.assertEqual(score_revenue_surprise(-3), -8)
        self.assertEqual(score_revenue_surprise(-7), -12)
        self.assertEqual(score_revenue_surprise(-12), -15)

    def test_guidance_and_reaction_tiers(self) -> None:
        self.assertEqual(score_guidance(GUIDANCE_STRONGLY_RAISED), 25)
        self.assertEqual(score_guidance(GUIDANCE_RAISED), 18)
        self.assertEqual(score_guidance(GUIDANCE_INLINE), 5)
        self.assertEqual(score_guidance("unknown"), 0)
        self.assertEqual(score_market_reaction(9), 15)
        self.assertEqual(score_market_reaction(0), 0)
        self.assertEqual(score_market_reaction(-9), -15)

    def test_quality_modifier_rules(self) -> None:
        self.assertEqual(
            score_quality_modifier(5, 4, GUIDANCE_RAISED, 2),
            10,
        )
        self.assertEqual(
            score_quality_modifier(5, 4, GUIDANCE_LOWERED, 2),
            -15,
        )
        self.assertEqual(
            score_quality_modifier(-5, -4, GUIDANCE_LOWERED, -2),
            -12,
        )
        self.assertEqual(
            score_quality_modifier(5, 4, GUIDANCE_RAISED, -9),
            -8,
        )
        self.assertEqual(
            score_quality_modifier(-5, -4, GUIDANCE_LOWERED, 9),
            0,
        )

    def test_inactive_when_no_post_earnings_data(self) -> None:
        result = build_earnings_intelligence({"earnings_days": 12})
        self.assertFalse(result["active"])
        self.assertIsNone(result["earnings_score"])

    def test_active_baseline_and_label(self) -> None:
        result = build_earnings_intelligence(
            {
                "eps_surprise_pct": 12,
                "revenue_surprise_pct": 6,
                "guidance": "raised",
                "market_reaction_pct": 5,
            }
        )
        self.assertTrue(result["active"])
        self.assertEqual(result["earnings_score"], 100)
        self.assertEqual(result["label"], "Elite bullish earnings outcome")
        self.assertEqual(sum(result["components"].values()), 12 + 12 + 18 + 10 + 10)
        self.assertEqual(result["conviction_adjustment"], 8)

    def test_recency_decay_reduces_score_and_conviction(self) -> None:
        fresh = build_earnings_intelligence(
            {"eps_surprise_pct": 10, "earnings_days_since": 1}
        )
        stale = build_earnings_intelligence(
            {"eps_surprise_pct": 10, "earnings_days_since": 14}
        )
        self.assertGreater(fresh["earnings_score"], stale["earnings_score"])
        self.assertEqual(fresh["recency_multiplier"], 1.0)
        self.assertEqual(stale["recency_multiplier"], 0.25)
        self.assertGreater(
            abs(fresh["conviction_adjustment"]),
            abs(stale["conviction_adjustment"]),
        )

    def test_conviction_adjustment_is_capped(self) -> None:
        result = build_earnings_intelligence(
            {
                "eps_surprise_pct": 25,
                "revenue_surprise_pct": 12,
                "guidance": "strongly_raised",
                "market_reaction_pct": 10,
            }
        )
        self.assertLessEqual(abs(result["conviction_adjustment"]), result["conviction_cap"])

    def test_secondary_gate_weight_dampening(self) -> None:
        context = {
            "primary_interpreter_active": True,
            "secondary_gate_multiplier": 0.35,
            "recent_post_earnings_window": True,
        }
        self.assertEqual(
            secondary_gate_weight("Event Trigger", "earnings beat headline", context),
            0.35,
        )
        self.assertEqual(
            secondary_gate_weight("Event Trigger", "product launch", context),
            1.0,
        )

    def test_adjusted_scout_score_attachment(self) -> None:
        payload = attach_adjusted_scout_score(
            {"score": 72},
            build_earnings_intelligence({"eps_surprise_pct": 12}),
        )
        self.assertEqual(payload["scoutScoreBase"], 72)
        self.assertEqual(payload["adjustedScoutScore"], apply_scout_score_adjustment(72, payload["earningsConvictionAdjustment"]))

    def test_unavailable_components_do_not_penalize(self) -> None:
        result = build_earnings_intelligence({"eps_surprise_pct": 5})
        self.assertTrue(result["active"])
        self.assertEqual(result["components"]["eps_surprise"], 8)
        self.assertEqual(result["components"]["revenue_surprise"], 0)
        self.assertEqual(result["components"]["guidance"], 0)
        self.assertEqual(result["components"]["market_reaction"], 0)
        self.assertEqual(result["earnings_score"], 58)

    def test_recency_multiplier_steps(self) -> None:
        self.assertEqual(recency_multiplier(0.5), 1.0)
        self.assertEqual(recency_multiplier(2), 0.8)
        self.assertEqual(recency_multiplier(5), 0.5)
        self.assertEqual(recency_multiplier(30), 0.25)

    def test_conviction_helper_cap(self) -> None:
        self.assertEqual(compute_conviction_adjustment(100, 1.0, 8), 8)
        self.assertEqual(compute_conviction_adjustment(0, 1.0, 8), -8)


if __name__ == "__main__":
    unittest.main()
