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
        self.assertEqual(result["mode"], "unavailable")
        self.assertIsNone(result["earnings_score"])

    def test_awaiting_provider_when_report_passed_without_actuals(self) -> None:
        result = build_earnings_intelligence(
            {
                "ticker": "NVDA",
                "earnings_days_since": 1,
                "earnings_report_date": "2026-05-20",
                "eps_actual": None,
                "revenue_actual": None,
                "eps_estimate": 1.76,
                "revenue_estimate": 78423370000,
                "earnings_data_source": "fmp_stable_earnings",
            }
        )
        self.assertEqual(result["mode"], "awaiting_provider")
        self.assertFalse(result["active"])
        self.assertIn("not yet available from provider", result["status_message"])

    def test_pre_earnings_nvda_like_payload(self) -> None:
        result = build_earnings_intelligence(
            {
                "ticker": "NVDA",
                "earnings_days": 0,
                "change": -0.77,
                "raw_output": "GATE 8 — AEGIS (Earnings Shield)\n  EARNINGS IN 0 DAYS (2026-05-20) — EXTREME RISK",
            }
        )
        self.assertFalse(result["active"])
        self.assertEqual(result["mode"], "pre_earnings")
        self.assertTrue(result["visible"])
        self.assertIsNone(result["earnings_score"])
        self.assertEqual(result["inputs"]["earnings_days_until"], 0.0)

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


class ResearchMemoryHistoryTests(unittest.TestCase):
    def test_history_where_clause_pending(self) -> None:
        from memory_store import HistoryFilters, history_where_clause

        sql, params = history_where_clause(HistoryFilters(outcome="PENDING"))
        self.assertIn("stock_outcome_label", sql)
        self.assertEqual(params, [])

    def test_history_where_clause_ticker_and_score(self) -> None:
        from memory_store import HistoryFilters, history_where_clause

        sql, params = history_where_clause(
            HistoryFilters(ticker="NVDA", min_score=70.0, max_score=90.0)
        )
        self.assertIn("ticker LIKE ?", sql)
        self.assertIn("scout_score >= ?", sql)
        self.assertIn("scout_score <= ?", sql)
        self.assertEqual(params, ["%NVDA%", 70.0, 90.0])

    def test_normalize_history_filters_drops_accidental_zero_scores(self) -> None:
        from memory_store import HistoryFilters, count_scan_results, normalize_history_filters

        normalized = normalize_history_filters(HistoryFilters(min_score=0, max_score=0))
        self.assertIsNone(normalized.min_score)
        self.assertIsNone(normalized.max_score)
        self.assertEqual(count_scan_results(filters=normalized), count_scan_results())

    def test_normalize_history_filters_drops_placeholder_failed_gate(self) -> None:
        from memory_store import HistoryFilters, normalize_history_filters

        normalized = normalize_history_filters(HistoryFilters(failed_gate="Threat Scan"))
        self.assertIsNone(normalized.failed_gate)


class OutcomeAnalyticsTests(unittest.TestCase):
    def test_bullish_directional_accuracy_uses_forward_return_sign(self) -> None:
        from memory_store import directional_accuracy_stats

        rows = [
            {"final_direction": "Bullish", "return_5d": 4.0},
            {"final_direction": "Bullish", "return_20d": -2.0},
            {"final_direction": "Bullish", "return_1d": 0.5},
        ]
        stats = directional_accuracy_stats(rows, "Bullish")
        self.assertEqual(stats["completed"], 3)
        self.assertEqual(stats["wins"], 2)
        self.assertEqual(stats["losses"], 1)
        self.assertAlmostEqual(stats["directional_accuracy"], 66.7, places=1)
        self.assertAlmostEqual(stats["avg_return"], (4.0 - 2.0 + 0.5) / 3, places=2)

    def test_bearish_directional_accuracy_uses_negative_return(self) -> None:
        from memory_store import directional_accuracy_stats

        rows = [
            {"final_direction": "Bearish", "return_10d": -3.0},
            {"final_direction": "Bearish", "return_5d": 2.0},
        ]
        stats = directional_accuracy_stats(rows, "Bearish")
        self.assertEqual(stats["wins"], 1)
        self.assertEqual(stats["losses"], 1)
        self.assertEqual(stats["directional_accuracy"], 50.0)


class PerformanceTrackerTests(unittest.TestCase):
    def test_trading_day_price_after_resolves(self) -> None:
        from datetime import date

        from performance_tracker import trading_day_price_after

        prices = {
            date(2026, 4, 1): 100.0,
            date(2026, 4, 2): 101.0,
            date(2026, 4, 3): 102.0,
            date(2026, 4, 6): 103.0,
        }
        target_date, target_price, meta = trading_day_price_after(
            prices,
            date(2026, 4, 1),
            2,
            as_of=date(2026, 4, 6),
        )
        self.assertEqual(meta["status"], "resolved")
        self.assertEqual(target_date, date(2026, 4, 3))
        self.assertEqual(target_price, 102.0)

    def test_trading_day_price_after_pending(self) -> None:
        from datetime import date

        from performance_tracker import trading_day_price_after

        prices = {
            date(2026, 4, 1): 100.0,
            date(2026, 4, 2): 101.0,
        }
        _, target_price, meta = trading_day_price_after(
            prices,
            date(2026, 4, 1),
            5,
            as_of=date(2026, 4, 2),
        )
        self.assertIsNone(target_price)
        self.assertEqual(meta["status"], "pending")
        self.assertIn("trading day", meta["reason"].lower())

    def test_stock_label_uses_longest_filled_horizon(self) -> None:
        from performance_tracker import stock_label

        label = stock_label("Bullish", {1: -2.0, 20: 12.0})
        self.assertEqual(label, "WIN")


if __name__ == "__main__":
    unittest.main()
