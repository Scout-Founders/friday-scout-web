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


class ReportExportTests(unittest.TestCase):
    def sample_scan_payload(self) -> dict:
        return {
            "ok": True,
            "runTimestamp": "2026-05-19T12:00:00+00:00",
            "universeMode": "custom",
            "pickMode": "gate_runner",
            "candidates": ["AAPL", "MSFT"],
            "results": [
                {
                    "ticker": "AAPL",
                    "score": 78,
                    "adjustedScoutScore": 81,
                    "sector": "Technology",
                    "passedAllGates": True,
                    "gates": [
                        {"index": 1, "key": "sentinel", "code": "SENTINEL", "name": "Market Filter", "passed": True},
                    ],
                    "explanation": {
                        "summary": "AAPL was the final winner and passed every gate.",
                        "status": "winner",
                        "gates": [
                            {
                                "gate_key": "sentinel",
                                "gate_name": "Market Filter",
                                "status": "PASS",
                                "actual_value": "score: 82; volume healthy",
                                "explanation": "AAPL passed Market Filter.",
                            }
                        ],
                    },
                    "directionBreakdown": {
                        "direction": "Bullish",
                        "bullConviction": 70,
                        "bearConviction": 30,
                        "netDirectionalEdge": 40,
                    },
                    "scoringBreakdown": {
                        "peerRiskAdjustedEdge": 1.24,
                        "sharpeRatio": 1.08,
                        "tStat": 2.31,
                        "peerRiskAdjustedEdgeBreakdown": {
                            "rawEdge": 1.8,
                            "riskAdjustment": -0.2,
                            "peerAdjustment": -0.36,
                            "finalEdge": 1.24,
                        },
                    },
                },
                {
                    "ticker": "MSFT",
                    "score": 72,
                    "sector": "Technology",
                    "passedAllGates": False,
                    "gates": [],
                    "explanation": {"summary": "MSFT rejected.", "status": "rejected", "gates": []},
                },
            ],
            "finalPick": {
                "ticker": "AAPL",
                "score": 78,
                "adjustedScoutScore": 81,
                "sector": "Technology",
                "passedAllGates": True,
                "gates": [
                    {"index": 1, "key": "sentinel", "code": "SENTINEL", "name": "Market Filter", "passed": True},
                ],
                "explanation": {
                    "summary": "AAPL was the final winner and passed every gate.",
                    "status": "winner",
                    "gates": [
                        {
                            "gate_key": "sentinel",
                            "gate_name": "Market Filter",
                            "status": "PASS",
                            "actual_value": "score: 82; volume healthy",
                            "explanation": "AAPL passed Market Filter.",
                        }
                    ],
                },
                "directionBreakdown": {
                    "direction": "Bullish",
                    "bullConviction": 70,
                    "bearConviction": 30,
                    "netDirectionalEdge": 40,
                },
                "scoringBreakdown": {
                    "peerRiskAdjustedEdge": 1.24,
                    "sharpeRatio": 1.08,
                    "tStat": 2.31,
                },
            },
            "rejected": [],
        }

    def test_build_report_context_extracts_peer_metrics_and_percentiles(self) -> None:
        from reporting import build_report_context

        context = build_report_context(self.sample_scan_payload())
        self.assertEqual(context["ticker"], "AAPL")
        self.assertEqual(context["peer"]["peer_risk_adjusted_edge"], "1.240")
        self.assertEqual(context["peer"]["sharpe_ratio"], "1.080")
        self.assertEqual(context["peer"]["t_stat"], "2.310")
        self.assertEqual(context["percentiles"]["universe_rank"], "1")
        self.assertEqual(context["percentiles"]["universe_percentile"], "100%")
        self.assertEqual(context["gate_rows"][0]["score"], "82.0")

    def test_resolve_scan_session_id_uses_memory_run(self) -> None:
        from reporting import resolve_scan_session_id

        payload = self.sample_scan_payload()
        payload["memoryRunId"] = 42
        self.assertEqual(resolve_scan_session_id(payload), "RUN-42")


class PeerRiskAdjustedEdgePlanTests(unittest.TestCase):
    def test_feature_weights_sum_to_one(self) -> None:
        from peer_risk_adjusted_edge import FEATURE_WEIGHTS

        self.assertAlmostEqual(sum(FEATURE_WEIGHTS.values()), 1.0, places=6)

    def test_peer_conviction_cap_when_ei_active(self) -> None:
        from peer_risk_adjusted_edge import (
            PEER_CONVICTION_CAP,
            PEER_CONVICTION_CAP_WHEN_EI_ACTIVE,
            peer_conviction_cap,
        )

        self.assertEqual(peer_conviction_cap({"active": True}), PEER_CONVICTION_CAP_WHEN_EI_ACTIVE)
        self.assertEqual(peer_conviction_cap({"active": False}), PEER_CONVICTION_CAP)
        self.assertEqual(peer_conviction_cap(None), PEER_CONVICTION_CAP)


class PeerRiskAdjustedEdgeP0Tests(unittest.TestCase):
    def _candidate(
        self,
        ticker: str,
        score: float,
        *,
        sector: str = "Technology",
        **extra: float,
    ) -> "CandidateResult":
        from run_gates import GATES, CandidateResult

        data: dict = {
            "ticker": ticker,
            "scout_score": score,
            "sector": sector,
            "gates": {key: True for key, _, _ in GATES},
            "direction": "Bullish",
        }
        data.update(extra)
        return CandidateResult(ticker=ticker, data=data)

    def test_p0_bundle_scores_three_tickers(self) -> None:
        from peer_risk_adjusted_edge import MODE_SCORED, build_peer_bundle_for_run

        results = [
            self._candidate("AAPL", 90, change=1.5, wind=2.0, dcf_gap=5.0),
            self._candidate("MSFT", 70, change=-0.5, wind=-1.0, dcf_gap=-3.0),
            self._candidate("NVDA", 80, change=0.5, wind=1.0, dcf_gap=2.0),
        ]
        bundle = build_peer_bundle_for_run(results, run_timestamp="2026-05-19T12:00:00+00:00")
        self.assertEqual(bundle["AAPL"]["mode"], MODE_SCORED)
        self.assertIsNotNone(bundle["AAPL"]["peerRiskAdjustedEdge"])
        self.assertEqual(bundle["AAPL"]["percentiles"]["scoutScore"], 100.0)
        self.assertEqual(bundle["MSFT"]["percentiles"]["scoutScore"], 0.0)
        self.assertIsNone(bundle["AAPL"]["sharpeRatio"])
        self.assertIsNone(bundle["AAPL"]["tStat"])

    def test_p0_insufficient_peers_single_ticker(self) -> None:
        from peer_risk_adjusted_edge import MODE_INSUFFICIENT_PEERS, build_peer_bundle_for_run

        bundle = build_peer_bundle_for_run(
            [self._candidate("AAPL", 80)],
            run_timestamp="2026-05-19T12:00:00+00:00",
        )
        self.assertEqual(bundle["AAPL"]["mode"], MODE_INSUFFICIENT_PEERS)
        self.assertIsNone(bundle["AAPL"]["peerRiskAdjustedEdge"])

    def test_attach_peer_scoring_does_not_change_final_scores(self) -> None:
        from peer_risk_adjusted_edge import attach_peer_scoring, build_scoring_breakdown

        serialized = {
            "ticker": "AAPL",
            "score": 78,
            "scoutScoreBase": 78,
            "adjustedScoutScore": 81,
            "earningsConvictionAdjustment": 3,
            "passedAllGates": True,
            "gates": [{"key": "sentinel", "passed": True}],
        }
        breakdown = build_scoring_breakdown(
            "AAPL",
            {},
            {
                "AAPL": {
                    "mode": "scored",
                    "active": True,
                    "primaryGroup": "universe",
                    "primaryGroupKey": "scan_universe",
                    "peerCount": 3,
                    "universeSize": 3,
                    "percentiles": {"scoutScore": 100, "universeScore": 100},
                    "edgeComponents": {
                        "rawEdge": 1.0,
                        "riskAdjustment": 0.0,
                        "peerAdjustment": 1.0,
                        "sectorAdjustment": 0.0,
                        "volatilityPenalty": 0.0,
                        "liquidityPenalty": 0.0,
                        "finalEdge": 1.2,
                    },
                    "peerRiskAdjustedEdge": 1.2,
                    "sharpeRatio": None,
                    "tStat": None,
                    "returnsMode": "awaiting_returns",
                    "statusMessage": "ok",
                }
            },
        )
        updated = attach_peer_scoring(dict(serialized), breakdown)
        self.assertEqual(updated["score"], 78)
        self.assertEqual(updated["adjustedScoutScore"], 81)
        self.assertEqual(updated["earningsConvictionAdjustment"], 3)
        self.assertEqual(updated["peerConvictionAdjustment"], 0)
        self.assertIn("scoringBreakdown", updated)
        self.assertIsNone(updated["scoringBreakdown"]["sharpeRatio"])
        self.assertIsNone(updated["scoringBreakdown"]["tStat"])
        self.assertEqual(updated["scoringBreakdown"]["convictionAdjustment"], 0)

    def test_serialize_result_scores_match_ei_only_baseline(self) -> None:
        from unittest.mock import patch

        from dashboard import serialize_result
        from peer_risk_adjusted_edge import build_peer_bundle_for_run

        results = [
            self._candidate("AAPL", 88, rsi=55),
            self._candidate("MSFT", 72, rsi=48),
            self._candidate("NVDA", 80, rsi=60),
        ]
        inactive_ei = {
            "active": False,
            "conviction_adjustment": 0,
            "mode": "unavailable",
        }
        with patch(
            "dashboard.build_earnings_intelligence_for_result",
            return_value=inactive_ei,
        ):
            baseline = serialize_result(results[0], peer_bundle=None)
            bundle = build_peer_bundle_for_run(results, run_timestamp="2026-05-19T12:00:00+00:00")
            with_peer = serialize_result(results[0], peer_bundle=bundle)

        self.assertEqual(with_peer["score"], baseline["score"])
        self.assertEqual(with_peer["adjustedScoutScore"], baseline["adjustedScoutScore"])
        self.assertEqual(with_peer["scoutScoreBase"], baseline["scoutScoreBase"])
        self.assertEqual(with_peer["earningsConvictionAdjustment"], baseline["earningsConvictionAdjustment"])
        self.assertEqual(with_peer["peerConvictionAdjustment"], 0)
        self.assertTrue(with_peer["passedAllGates"])
        self.assertEqual(
            [gate["passed"] for gate in with_peer["gates"]],
            [gate["passed"] for gate in baseline["gates"]],
        )
        self.assertIsNotNone(with_peer.get("scoringBreakdown"))

    def test_pdf_report_context_reads_p0_breakdown(self) -> None:
        from reporting.scoring_breakdown import build_report_context

        payload = {
            "ok": True,
            "runTimestamp": "2026-05-19T12:00:00+00:00",
            "candidates": ["AAPL", "MSFT", "NVDA"],
            "results": [
                {
                    "ticker": "AAPL",
                    "score": 90,
                    "scoringBreakdown": {
                        "mode": "scored",
                        "active": True,
                        "peerRiskAdjustedEdge": 1.1,
                        "sharpeRatio": None,
                        "tStat": None,
                        "peerRiskAdjustedEdgeBreakdown": {
                            "rawEdge": 1.0,
                            "finalEdge": 1.1,
                        },
                        "peerContext": {
                            "primaryGroup": "universe",
                            "peerCount": 3,
                            "universePercentile": 100,
                        },
                    },
                },
                {"ticker": "MSFT", "score": 70},
                {"ticker": "NVDA", "score": 80},
            ],
            "finalPick": {"ticker": "AAPL", "score": 90},
        }
        context = build_report_context(payload, ticker="AAPL")
        self.assertEqual(context["peer"]["peer_risk_adjusted_edge"], "1.100")
        self.assertEqual(context["peer"]["sharpe_ratio"], "—")
        self.assertEqual(context["peer"]["t_stat"], "—")


class StableSignalLayersS1Tests(unittest.TestCase):
    def _candidate(self, ticker: str, score: float, **extra: Any) -> "CandidateResult":
        from run_gates import GATES, CandidateResult

        data: dict = {
            "ticker": ticker,
            "scout_score": score,
            "sector": "Technology",
            "trend": "UPTREND",
            "volume": 1_200_000,
            "wind": 1.5,
            "iv_elevated": False,
            "piotroski": 7,
            "z_score": 2.4,
            "breadth_score": 62,
            "rsi": 55,
            "change": 1.2,
            "dcf_gap": 8.0,
            "beta": 1.1,
            "gates": {key: True for key, _, _ in GATES},
            "direction": "Bullish",
        }
        data.update(extra)
        return CandidateResult(ticker=ticker, data=data)

    def test_build_stable_signal_exposes_seven_layers(self) -> None:
        from stable_signal_layers import LAYER_KEYS, build_stable_signal

        signal = build_stable_signal(self._candidate("AAPL", 82))
        self.assertEqual(signal["version"], "1")
        self.assertEqual(signal["rankingScoreField"], "scout_score")
        self.assertEqual(signal["rankingScore"], 82)
        self.assertEqual(tuple(signal["layers"].keys()), LAYER_KEYS)
        self.assertEqual(signal["layers"]["momentum"]["primary"]["field"], "trend")
        self.assertEqual(signal["layers"]["risk"]["primary"]["gate"], "fortress")
        self.assertEqual(signal["layers"]["liquidity"]["primary"]["field"], "volume")
        self.assertIn("rsi_multi_use", signal["redundancyFlags"])

    def test_attach_stable_signal_does_not_mutate_scores_or_gates(self) -> None:
        from stable_signal_layers import attach_stable_signal, build_stable_signal

        serialized = {
            "ticker": "AAPL",
            "score": 78,
            "adjustedScoutScore": 81,
            "earningsConvictionAdjustment": 3,
            "passedAllGates": True,
            "gates": [{"key": "sentinel", "passed": True}],
        }
        baseline = dict(serialized)
        updated = attach_stable_signal(dict(serialized), build_stable_signal(self._candidate("AAPL", 78)))
        self.assertEqual(updated["score"], baseline["score"])
        self.assertEqual(updated["adjustedScoutScore"], baseline["adjustedScoutScore"])
        self.assertEqual(updated["gates"], baseline["gates"])
        self.assertIn("stableSignal", updated)

    def test_serialize_result_attaches_stable_signal_without_ranking_change(self) -> None:
        from unittest.mock import patch

        from dashboard import serialize_result

        from run_gates import CandidateResult

        results = [
            self._candidate("AAPL", 88),
            self._candidate("MSFT", 72),
            self._candidate("NVDA", 80),
        ]
        inactive_ei = {"active": False, "conviction_adjustment": 0, "mode": "unavailable"}
        with patch(
            "dashboard.build_earnings_intelligence_for_result",
            return_value=inactive_ei,
        ):
            baseline = serialize_result(results[0], peer_bundle=None)
            # serialize_result now always attaches stableSignal; compare score fields only
            self.assertEqual(baseline["score"], 88)
            self.assertIn("stableSignal", baseline)
            signal = baseline["stableSignal"]
            self.assertEqual(signal["rankingScore"], 88)
            self.assertTrue(signal["layers"]["momentum"]["gates"][0]["passed"])


class ReportingPipelineTests(unittest.TestCase):
    def test_registry_lists_registered_report(self) -> None:
        import tempfile
        from pathlib import Path

        from reporting.config import ReportConfig
        from reporting.pipeline import ReportPipeline
        from reporting.registry import list_reports

        from reporting.registry_store import clear_registry_store_cache

        payload = ReportExportTests().sample_scan_payload()
        with tempfile.TemporaryDirectory() as tmp:
            clear_registry_store_cache()
            config = ReportConfig(exports_dir=Path(tmp))
            result = ReportPipeline(config).run(payload)
            self.assertIn("register", result["pipelineStages"])
            rows = list_reports(Path(tmp), limit=5)
            self.assertEqual(rows[0]["filename"], result["filename"])
            self.assertEqual(rows[0]["ticker"], "AAPL")

    def test_async_export_completes_via_worker(self) -> None:
        import tempfile
        import time
        from pathlib import Path

        from reporting.config import ReportConfig
        from reporting.jobs import ReportJobWorker
        from reporting.service import ReportService

        from reporting.registry_store import clear_registry_store_cache

        payload = ReportExportTests().sample_scan_payload()
        with tempfile.TemporaryDirectory() as tmp:
            clear_registry_store_cache()
            config = ReportConfig(exports_dir=Path(tmp))
            worker = ReportJobWorker(config)
            worker.start()
            try:
                queued = ReportService(config).export(payload, async_mode=True)
                self.assertTrue(queued.get("async"))
                job_id = str(queued["jobId"])
                completed = None
                for _ in range(40):
                    job = ReportService(config).get_job(job_id)
                    if job.get("status") == "completed":
                        completed = job
                        break
                    if job.get("status") == "failed":
                        self.fail(job.get("errorMessage") or "job failed")
                    time.sleep(0.2)
                self.assertIsNotNone(completed)
                self.assertTrue(completed.get("downloadUrl"))
            finally:
                worker.stop()
                clear_registry_store_cache()

    def test_idempotent_export_reuses_report(self) -> None:
        import tempfile
        from pathlib import Path

        from reporting.config import ReportConfig
        from reporting.service import ReportService

        from reporting.registry_store import clear_registry_store_cache

        payload = ReportExportTests().sample_scan_payload()
        with tempfile.TemporaryDirectory() as tmp:
            clear_registry_store_cache()
            config = ReportConfig(exports_dir=Path(tmp))
            service = ReportService(config)
            first = service.export(payload, async_mode=False)
            second = service.export(payload, async_mode=False)
            self.assertTrue(second.get("reused"))
            self.assertEqual(second.get("filename"), first.get("filename"))


if __name__ == "__main__":
    unittest.main()
