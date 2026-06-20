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


class GateIntelligenceMetricsTests(unittest.TestCase):
    def test_pass_conditioned_metrics_diverge_most_and_least(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import memory_store as ms

        def gate_intel_slices(rows: list[dict]) -> tuple[list[dict], list[dict]]:
            most = sorted(rows, key=lambda row: row["predictive_score"] or 0, reverse=True)[:7]
            least = sorted(rows, key=lambda row: row["predictive_score"] or 0)[:7]
            return most, least

        def insert_scan(
            conn: sqlite3.Connection,
            run_id: int,
            ticker: str,
            outcome: str,
            return_5d: float,
            gates: list[dict],
        ) -> None:
            snapshot = {"gates": gates}
            conn.execute(
                """
                INSERT INTO scan_results (
                    run_id, timestamp, ticker, scout_score, final_direction,
                    gates_json, gate_snapshot_json, stock_outcome_label, return_5d
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    "2026-05-19T12:00:00+00:00",
                    ticker,
                    80.0,
                    "Bullish",
                    "{}",
                    ms.json_dump(snapshot),
                    outcome,
                    return_5d,
                ),
            )

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "gate_intel_test.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                with ms.connect() as conn:
                    run_id = conn.execute(
                        """
                        INSERT INTO scan_runs (timestamp, universe_mode, pick_mode)
                        VALUES (?, ?, ?)
                        """,
                        ("2026-05-19T12:00:00+00:00", "test", "test"),
                    ).lastrowid
                    alpha = {"key": "alpha", "name": "Alpha Gate", "passed": True}
                    beta = {"key": "beta", "name": "Beta Gate", "passed": False}
                    insert_scan(conn, run_id, "AAA", "WIN", 10.0, [alpha, beta])
                    insert_scan(conn, run_id, "AAB", "WIN", 8.0, [alpha, beta])
                    beta_pass = {"key": "beta", "name": "Beta Gate", "passed": True}
                    alpha_fail = {"key": "alpha", "name": "Alpha Gate", "passed": False}
                    insert_scan(conn, run_id, "AAC", "LOSS", -6.0, [alpha_fail, beta_pass])
                    insert_scan(conn, run_id, "AAD", "LOSS", -4.0, [alpha_fail, beta_pass])
                    conn.commit()
                    metrics = ms.refresh_gate_intelligence_metrics(conn)

            by_key = {row["gate_key"]: row for row in metrics}
            self.assertGreater(
                by_key["alpha"]["predictive_score"],
                by_key["beta"]["predictive_score"],
            )
            self.assertEqual(by_key["alpha"]["win_count"], 2)
            self.assertEqual(by_key["beta"]["loss_count"], 2)
            most, least = gate_intel_slices(metrics)
            self.assertEqual(most[0]["gate_key"], "alpha")
            self.assertEqual(least[0]["gate_key"], "beta")
            self.assertNotEqual(most[0]["predictive_score"], least[0]["predictive_score"])


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

    def test_build_report_context_resolves_explicit_ticker_row(self) -> None:
        from reporting import build_report_context

        context = build_report_context(self.sample_scan_payload(), ticker="MSFT")
        self.assertEqual(context["ticker"], "MSFT")
        self.assertEqual(context["explanation_status"], "rejected")
        self.assertEqual(context["passed_all_gates"], "NO")
        self.assertEqual(context["explanation_summary"], "MSFT rejected.")

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


class StableSignalExplainabilityS2aTests(unittest.TestCase):
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
            "gates": {key: True for key, _, _ in GATES},
            "direction": "Bullish",
        }
        data.update(extra)
        return CandidateResult(ticker=ticker, data=data)

    def _serialized(self, candidate: "CandidateResult") -> dict:
        from stable_signal_layers import build_and_attach_stable_signal

        base = {
            "ticker": candidate.ticker,
            "score": candidate.score,
            "passedAllGates": candidate.passed_all_gates,
            "gates": [
                {"key": key, "code": code, "name": name, "passed": candidate.gates.get(key) is True}
                for key, code, name in __import__("run_gates", fromlist=["GATES"]).GATES
            ],
            "sector": candidate.data.get("sector"),
            "directionBreakdown": {
                "bullConviction": 70,
                "bearConviction": 40,
                "netDirectionalEdge": 30,
                "direction": "Bullish",
            },
            "earningsIntelligence": {"active": False, "mode": "unavailable"},
        }
        if not candidate.passed_all_gates:
            failed = candidate.first_failed_gate
            if failed:
                base["firstFailedGate"] = {
                    "index": failed[0],
                    "code": failed[1],
                    "name": failed[2],
                }
        return build_and_attach_stable_signal(candidate, base)

    def test_build_explainability_has_required_sections(self) -> None:
        from stable_signal_explainability import LAYER_KEYS, build_explainability

        candidate = self._candidate("MSFT", 89)
        serialized = self._serialized(candidate)
        explain = build_explainability(serialized, serialized["stableSignal"])
        self.assertEqual(explain["version"], "1")
        self.assertEqual(explain["tier"], "explainability")
        self.assertEqual(tuple(explain["layerStrengths"].keys()), LAYER_KEYS)
        self.assertIn("rankingExplanation", explain)
        self.assertIn("humanSummary", explain)
        self.assertIsNone(explain["placeholders"]["layerNumericScores"])

    def test_explainability_does_not_mutate_inputs(self) -> None:
        import copy

        from stable_signal_explainability import build_explainability

        candidate = self._candidate("AAPL", 80)
        serialized = self._serialized(candidate)
        stable_before = copy.deepcopy(serialized["stableSignal"])
        serial_before = copy.deepcopy(serialized)
        build_explainability(serialized, serialized["stableSignal"])
        self.assertEqual(serialized["score"], serial_before["score"])
        self.assertEqual(serialized["stableSignal"], stable_before)

    def test_ranking_explanation_final_pick_gate_runner(self) -> None:
        from stable_signal_explainability import ScanExplainContext, build_explainability

        from run_gates import GATES

        fail_gates = {key: True for key, _, _ in GATES}
        fail_gates["specter"] = False
        msft = self._candidate("MSFT", 89)
        nvda = self._candidate("NVDA", 100, gates=fail_gates)
        context = ScanExplainContext.from_universe(
            pick_mode="gate_runner",
            final_pick_ticker="MSFT",
            run_timestamp="2026-05-21T12:00:00+00:00",
            universe=[
                ("NVDA", 100.0, False),
                ("MSFT", 89.0, True),
            ],
        )
        msft_explain = build_explainability(
            self._serialized(msft),
            self._serialized(msft)["stableSignal"],
            context,
        )
        nvda_explain = build_explainability(
            self._serialized(nvda),
            self._serialized(nvda)["stableSignal"],
            context,
        )
        self.assertEqual(msft_explain["rankingExplanation"]["pickRole"], "final_pick")
        self.assertEqual(msft_explain["rankingExplanation"]["passingPoolRankByScore"], 1)
        self.assertIn("gate-runner pick", msft_explain["rankingExplanation"]["summary"])
        self.assertEqual(nvda_explain["rankingExplanation"]["universeRankByScore"], 1)
        self.assertFalse(nvda_explain["rankingExplanation"]["passedAllGates"])
        self.assertTrue(
            any(flag["code"] == "high_score_gate_fail" for flag in nvda_explain["warningFlags"])
        )

    def test_no_numeric_layer_score_fields(self) -> None:
        from stable_signal_explainability import build_explainability

        explain = build_explainability(
            self._serialized(self._candidate("AAPL", 75)),
            self._serialized(self._candidate("AAPL", 75))["stableSignal"],
        )
        for layer in explain["layerStrengths"].values():
            self.assertNotIn("layerScore", layer)
            self.assertIn(layer["band"], ("strong", "neutral", "weak", "unknown"))

    def test_feature_flag_default_off(self) -> None:
        import os

        from stable_signal_explainability import attach_explainability_if_enabled

        os.environ.pop("SCOUT_STABLE_SIGNAL_EXPLAINABILITY", None)
        stable = {"version": "1", "layers": {}}
        serialized = {"ticker": "AAPL", "score": 80}
        result = attach_explainability_if_enabled(stable, serialized)
        self.assertNotIn("explainability", result)

    def test_choose_final_pick_unaffected(self) -> None:
        from run_gates import choose_final_pick

        from stable_signal_explainability import ScanExplainContext, build_explainability

        from run_gates import GATES

        fail_gates = {key: True for key, _, _ in GATES}
        fail_gates["specter"] = False
        msft = self._candidate("MSFT", 89)
        nvda = self._candidate("NVDA", 100, gates=fail_gates)
        results = [nvda, msft]
        winner_before = choose_final_pick(results)
        context = ScanExplainContext.from_universe(
            pick_mode="gate_runner",
            final_pick_ticker="MSFT",
            run_timestamp="2026-05-21T12:00:00+00:00",
            universe=[("NVDA", 100.0, False), ("MSFT", 89.0, True)],
        )
        for result in results:
            serialized = self._serialized(result)
            build_explainability(serialized, serialized["stableSignal"], context)
        winner_after = choose_final_pick(results)
        self.assertEqual(winner_before.ticker, "MSFT")
        self.assertEqual(winner_after.ticker, "MSFT")
        self.assertEqual(winner_before.score, 89.0)

    def test_serialize_result_has_no_explainability_yet(self) -> None:
        from unittest.mock import patch

        from dashboard import serialize_result

        inactive_ei = {"active": False, "conviction_adjustment": 0, "mode": "unavailable"}
        with patch(
            "dashboard.build_earnings_intelligence_for_result",
            return_value=inactive_ei,
        ):
            payload = serialize_result(self._candidate("AAPL", 80), peer_bundle=None)
        self.assertIn("stableSignal", payload)
        self.assertNotIn("explainability", payload["stableSignal"])


class StableSignalExplainabilityS2bTests(unittest.TestCase):
    @staticmethod
    def _mock_results() -> list:
        from run_gates import GATES, CandidateResult

        def row(ticker: str, score: float, fail: str | None = None) -> CandidateResult:
            gates = {key: True for key, _, _ in GATES}
            if fail:
                gates[fail] = False
            return CandidateResult(
                ticker=ticker,
                data={
                    "ticker": ticker,
                    "scout_score": score,
                    "sector": "Technology",
                    "trend": "UPTREND",
                    "volume": 1_000_000,
                    "wind": 1.0,
                    "iv_elevated": False,
                    "piotroski": 7,
                    "z_score": 2.0,
                    "breadth_score": 60,
                    "gates": gates,
                    "direction": "Bullish",
                },
            )

        return [
            row("NVDA", 100, "specter"),
            row("MSFT", 89),
            row("AAPL", 75),
        ]

    @staticmethod
    def _strip_explainability(payload: dict) -> dict:
        import copy

        stripped = copy.deepcopy(payload)

        def clean(row: dict) -> None:
            stable = row.get("stableSignal")
            if isinstance(stable, dict):
                stable.pop("explainability", None)

        if isinstance(stripped.get("finalPick"), dict):
            clean(stripped["finalPick"])
        for key in ("rejected", "results"):
            for row in stripped.get(key) or []:
                if isinstance(row, dict):
                    clean(row)
        return stripped

    @staticmethod
    def _ranking_snapshot(payload: dict) -> dict:
        def snap(row: dict) -> dict:
            return {
                "ticker": row.get("ticker"),
                "score": row.get("score"),
                "passedAllGates": row.get("passedAllGates"),
                "adjustedScoutScore": row.get("adjustedScoutScore"),
                "peerConvictionAdjustment": row.get("peerConvictionAdjustment"),
                "gates": [g.get("passed") for g in row.get("gates") or []],
            }

        return {
            "finalPick": snap(payload["finalPick"]),
            "results": [snap(row) for row in payload.get("results") or []],
        }

    def _build_payload_with_flag(self, flag: str) -> dict:
        import os
        from unittest.mock import patch

        from dashboard import build_run_payload

        inactive_ei = {"active": False, "conviction_adjustment": 0, "mode": "unavailable"}
        results = self._mock_results()

        def fake_fetch(_api: str, ticker: str, _timeout: float):
            for item in results:
                if item.ticker == ticker:
                    return item
            raise RuntimeError(f"missing mock {ticker}")

        with patch.dict(os.environ, {"SCOUT_STABLE_SIGNAL_EXPLAINABILITY": flag}):
            with patch("dashboard.fetch_gate_result", side_effect=fake_fetch):
                with patch(
                    "dashboard.build_earnings_intelligence_for_result",
                    return_value=inactive_ei,
                ):
                    with patch("dashboard.choose_option_contract", return_value=None):
                        return build_run_payload(
                            {
                                "universeMode": "custom",
                                "tickers": "NVDA,MSFT,AAPL",
                                "pickMode": "gate_runner",
                                "timeout": 25,
                            }
                        )

    def test_flag_off_explainability_absent(self) -> None:
        payload = self._build_payload_with_flag("0")
        self.assertTrue(payload["ok"])
        for row in payload["results"]:
            self.assertNotIn("explainability", row.get("stableSignal") or {})

    def test_flag_on_explainability_attached(self) -> None:
        payload = self._build_payload_with_flag("1")
        self.assertTrue(payload["ok"])
        for row in payload["results"]:
            stable = row.get("stableSignal") or {}
            self.assertIn("explainability", stable)
            self.assertEqual(stable["explainability"]["version"], "1")
        self.assertEqual(payload["finalPick"]["stableSignal"]["explainability"]["rankingExplanation"]["pickRole"], "final_pick")

    def test_rankings_unchanged_flag_on_vs_off(self) -> None:
        off = self._build_payload_with_flag("0")
        on = self._build_payload_with_flag("1")
        self.assertEqual(self._ranking_snapshot(off), self._ranking_snapshot(on))
        self.assertEqual(off["finalPick"]["ticker"], on["finalPick"]["ticker"])
        self.assertEqual(off["finalPick"]["score"], on["finalPick"]["score"])

    def test_choose_final_pick_matches_payload_winner_both_flags(self) -> None:
        from run_gates import choose_final_pick

        results = self._mock_results()
        expected = choose_final_pick(results)
        for flag in ("0", "1"):
            payload = self._build_payload_with_flag(flag)
            self.assertEqual(payload["finalPick"]["ticker"], expected.ticker)
            self.assertEqual(payload["finalPick"]["score"], expected.score)

    def test_flag_on_preserves_s1_layers(self) -> None:
        off = self._strip_explainability(self._build_payload_with_flag("0"))
        on = self._strip_explainability(self._build_payload_with_flag("1"))
        self.assertEqual(
            off["finalPick"]["stableSignal"]["layers"],
            on["finalPick"]["stableSignal"]["layers"],
        )


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


class NeutralSegmentationTests(unittest.TestCase):
    def test_actionable_win_rate_excludes_neutral(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import memory_store as ms

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "neutral_seg.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                with ms.connect() as conn:
                    cursor = conn.execute(
                        """
                        INSERT INTO scan_runs (timestamp, universe_mode, pick_mode)
                        VALUES (?, ?, ?)
                        """,
                        ("2026-06-01T00:00:00+00:00", "custom", "score_only"),
                    )
                    run_id = int(cursor.lastrowid)
                    rows = [
                        ("AAA", "Bullish", "WIN"),
                        ("AAB", "Bullish", "LOSS"),
                        ("AAC", "Bearish", "WIN"),
                        ("AAD", "Neutral", "WIN"),
                        ("AAE", "Neutral", "WIN"),
                        ("AAF", "Neutral", "LOSS"),
                    ]
                    for ticker, direction, label in rows:
                        conn.execute(
                            """
                            INSERT INTO scan_results (
                                run_id, timestamp, ticker, scout_score,
                                final_direction, stock_outcome_label, gates_json
                            ) VALUES (?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                run_id,
                                "2026-06-01T00:00:00+00:00",
                                ticker,
                                70.0,
                                direction,
                                label,
                                "{}",
                            ),
                        )
                    conn.commit()
                analytics = ms.get_outcome_analytics()
        self.assertEqual(analytics["total_completed"], 6)
        self.assertEqual(analytics["mixed_win_rate"], 66.7)
        self.assertEqual(analytics["actionable_total"], 3)
        self.assertEqual(analytics["actionable_bullish_count"], 2)
        self.assertEqual(analytics["actionable_bearish_count"], 1)
        self.assertAlmostEqual(analytics["actionable_win_rate"], 66.7, places=1)
        self.assertEqual(analytics["neutral_count"], 3)
        self.assertEqual(analytics["neutral_percentage"], 50.0)
        self.assertEqual(analytics["win_rate"], analytics["mixed_win_rate"])
        self.assertIn("segmentation", analytics)
        self.assertEqual(analytics["segmentation"]["model"], "option_c")


class UniversePresetsTests(unittest.TestCase):
    def test_manifest_lists_phase1_presets(self) -> None:
        from universe_presets import list_preset_catalog

        catalog = list_preset_catalog()
        self.assertEqual(catalog["manifestVersion"], "2026.06.1")
        for preset_id in (
            "mega_cap_tech",
            "semiconductors",
            "financials",
            "healthcare",
            "retail",
            "energy",
            "etfs",
            "failure_learning",
        ):
            self.assertIn(preset_id, catalog["presets"])
            preset = catalog["presets"][preset_id]
            self.assertTrue(preset["tickers"])
            self.assertTrue(preset["cohortClass"])
            self.assertTrue(preset["scanPurpose"])

    def test_resolve_preset_etfs_is_research_cohort(self) -> None:
        from universe_presets import resolve_preset

        preset = resolve_preset("etfs")
        self.assertEqual(preset["cohortClass"], "research")
        self.assertEqual(preset["scanPurpose"], "regime_probe")
        self.assertEqual(len(preset["tickers"]), 10)

    def test_save_scan_persists_cohort_metadata(self) -> None:
        from memory_store import connect, init_db, save_scan_result

        init_db()
        payload = {
            "runTimestamp": "2026-06-02T12:00:00+00:00",
            "universeMode": "preset",
            "pickMode": "score_only",
            "timeout": 25,
            "apiUrl": "https://example.test/gates",
            "candidates": ["AAPL"],
            "universePresetId": "healthcare",
            "scanPurpose": "cohort_baseline",
            "cohortClass": "actionable",
            "universePresetVersion": "2026.06.1",
            "results": [
                {
                    "ticker": "AAPL",
                    "score": 70,
                    "direction": "Bullish",
                    "passedAllGates": True,
                    "gates": [
                        {
                            "key": "sentinel",
                            "code": "SENTINEL",
                            "name": "Market Filter",
                            "passed": True,
                        }
                    ],
                    "directionBreakdown": {
                        "direction": "Bullish",
                        "bullConviction": 60,
                        "bearConviction": 40,
                        "netDirectionalEdge": 20,
                    },
                    "raw": {"ticker": "AAPL", "direction": "Bullish"},
                }
            ],
        }
        run_id = save_scan_result(payload)
        with connect() as conn:
            run = conn.execute("SELECT * FROM scan_runs WHERE id = ?", (run_id,)).fetchone()
            row = conn.execute(
                "SELECT * FROM scan_results WHERE run_id = ?", (run_id,)
            ).fetchone()
        self.assertEqual(run["universe_preset_id"], "healthcare")
        self.assertEqual(run["scan_purpose"], "cohort_baseline")
        self.assertEqual(run["cohort_class"], "actionable")
        self.assertEqual(row["cohort_class"], "actionable")


class BacktestEngineTests(unittest.TestCase):
    def insert_completed_signal(
        self,
        conn,
        run_id: int,
        *,
        ticker: str,
        timestamp: str,
        direction: str,
        outcome: str,
        return_5d: float,
        return_20d: float,
        score: float,
        sector: str,
        gates: list[dict],
        cohort_class: str = "actionable",
        engine_version: str = "3.0.test",
        is_test: int = 0,
    ) -> int:
        import memory_store as ms

        snapshot = {"gates": gates}
        cursor = conn.execute(
            """
            INSERT INTO scan_results (
                run_id, timestamp, ticker, scout_score, final_direction,
                gates_json, gate_snapshot_json, stock_outcome_label,
                return_5d, return_20d, engine_version, cohort_class, is_test_record,
                feature_vector_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                timestamp,
                ticker,
                score,
                direction,
                ms.json_dump({gate["key"]: gate["passed"] for gate in gates}),
                ms.json_dump(snapshot),
                outcome,
                return_5d,
                return_20d,
                engine_version,
                cohort_class,
                is_test,
                ms.json_dump({"sector": sector}),
            ),
        )
        recommendation_id = int(cursor.lastrowid)
        conn.execute(
            """
            INSERT INTO feature_vectors (
                recommendation_id, scan_id, ticker, timestamp, engine_version, sector_name
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (recommendation_id, run_id, ticker, timestamp, engine_version, sector),
        )
        return recommendation_id

    def test_run_backtest_filters_and_persists_metrics(self) -> None:
        import sqlite3
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import backtest_engine as be
        import memory_store as ms

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "backtest_test.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                with ms.connect() as conn:
                    run_id = conn.execute(
                        """
                        INSERT INTO scan_runs (
                            timestamp, universe_mode, pick_mode, cohort_class, scan_purpose
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        ("2026-05-01T12:00:00+00:00", "preset", "gate_runner", "actionable", "cohort_baseline"),
                    ).lastrowid
                    self.insert_completed_signal(
                        conn,
                        run_id,
                        ticker="AAA",
                        timestamp="2026-05-10T12:00:00+00:00",
                        direction="Bullish",
                        outcome="WIN",
                        return_5d=4.0,
                        return_20d=8.0,
                        score=88.0,
                        sector="Technology",
                        gates=[
                            {"key": "sentinel", "passed": True},
                            {"key": "compass", "passed": True},
                        ],
                    )
                    self.insert_completed_signal(
                        conn,
                        run_id,
                        ticker="BBB",
                        timestamp="2026-05-12T12:00:00+00:00",
                        direction="Bearish",
                        outcome="LOSS",
                        return_5d=-3.0,
                        return_20d=-6.0,
                        score=72.0,
                        sector="Healthcare",
                        gates=[
                            {"key": "sentinel", "passed": True},
                            {"key": "pulse", "passed": True},
                        ],
                    )
                    self.insert_completed_signal(
                        conn,
                        run_id,
                        ticker="TST",
                        timestamp="2026-05-13T12:00:00+00:00",
                        direction="Bullish",
                        outcome="WIN",
                        return_5d=2.0,
                        return_20d=2.0,
                        score=90.0,
                        sector="Technology",
                        gates=[{"key": "sentinel", "passed": True}],
                        is_test=1,
                    )
                    conn.commit()

                result = be.run_backtest(
                    name="Filtered Test",
                    description="Actionable completed outcomes only",
                    filters=be.BacktestFilters(
                        start_date="2026-05-01",
                        end_date="2026-05-31",
                        cohort_class="actionable",
                        min_score=70.0,
                    ),
                )

                self.assertTrue(result["ok"])
                self.assertEqual(result["signalsSaved"], 2)
                self.assertEqual(result["metrics"]["sample_size"], 2)
                self.assertEqual(result["metrics"]["win_rate"], 50.0)
                self.assertAlmostEqual(result["metrics"]["avg_return"], 7.0, places=2)
                self.assertAlmostEqual(result["metrics"]["avg_stock_return"], 1.0, places=2)
                self.assertAlmostEqual(result["metrics"]["expectancy"], 7.0, places=2)
                self.assertEqual(len(result["analytics"]["sector_performance"]), 2)

                with ms.connect() as conn:
                    metrics_row = conn.execute(
                        "SELECT * FROM backtest_metrics WHERE run_id = ?",
                        (result["runId"],),
                    ).fetchone()
                    signal_count = conn.execute(
                        "SELECT COUNT(*) FROM backtest_signals WHERE run_id = ?",
                        (result["runId"],),
                    ).fetchone()[0]
                self.assertIsNotNone(metrics_row)
                self.assertEqual(int(signal_count), 2)
                self.assertEqual(metrics_row["best_trade"], 8.0)
                self.assertEqual(metrics_row["worst_trade"], 6.0)

    def test_preview_backtest_does_not_persist(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import backtest_engine as be
        import memory_store as ms

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "backtest_preview.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                preview = be.preview_backtest(be.BacktestFilters())
                self.assertTrue(preview["ok"])
                with ms.connect() as conn:
                    run_count = conn.execute("SELECT COUNT(*) FROM backtest_runs").fetchone()[0]
                self.assertEqual(int(run_count), 0)

    def test_gate_performance_groups_passed_gates(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "outcome_label": "WIN",
                    "direction": "Bullish",
                    "return_20d": 5.0,
                    "gate_snapshot_json": '{"gates":[{"key":"sentinel","passed":true}]}',
                }
            ),
            be.attach_return_fields(
                {
                    "outcome_label": "LOSS",
                    "direction": "Bearish",
                    "return_20d": -4.0,
                    "gate_snapshot_json": '{"gates":[{"key":"sentinel","passed":true}]}',
                }
            ),
        ]
        gate_rows = be.compute_gate_performance(signals)
        self.assertEqual(gate_rows[0]["label"], "SENTINEL")
        self.assertEqual(gate_rows[0]["sample_size"], 2)
        self.assertEqual(gate_rows[0]["win_rate"], 50.0)
        self.assertAlmostEqual(gate_rows[0]["avg_return"], 4.5, places=2)
        self.assertAlmostEqual(gate_rows[0]["avg_stock_return"], 0.5, places=2)

    def test_gate_contribution_audit_includes_all_horizon_gates(self) -> None:
        import backtest_engine as be
        from run_gates import GATES

        audit = be.compute_gate_contribution_audit([])
        self.assertEqual(len(audit["gates"]), len(GATES))
        self.assertEqual(audit["gates"][0]["signal_count"], 0)
        self.assertIsNone(audit["gates"][0]["expectancy"])

    def test_gate_contribution_audit_ranks_by_expectancy(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "outcome_label": "WIN",
                    "direction": "Bullish",
                    "return_20d": 12.0,
                    "gate_snapshot_json": '{"gates":[{"key":"compass","passed":true}]}',
                }
            ),
            be.attach_return_fields(
                {
                    "outcome_label": "LOSS",
                    "direction": "Bearish",
                    "return_20d": 10.0,
                    "gate_snapshot_json": '{"gates":[{"key":"pulse","passed":true}]}',
                }
            ),
            be.attach_return_fields(
                {
                    "outcome_label": "WIN",
                    "direction": "Bullish",
                    "return_20d": 4.0,
                    "gate_snapshot_json": '{"gates":[{"key":"compass","passed":true}]}',
                }
            ),
        ]
        audit = be.compute_gate_contribution_audit(signals)
        ranked = audit["gates"]
        compass = next(row for row in ranked if row["gate_code"] == "COMPASS")
        pulse = next(row for row in ranked if row["gate_code"] == "PULSE")

        self.assertEqual(compass["signal_count"], 2)
        self.assertAlmostEqual(compass["avg_signal_return"], 8.0, places=2)
        self.assertAlmostEqual(compass["avg_stock_return"], 8.0, places=2)
        self.assertAlmostEqual(compass["expectancy"], 8.0, places=2)
        self.assertEqual(pulse["signal_count"], 1)
        self.assertAlmostEqual(pulse["avg_signal_return"], -10.0, places=2)
        self.assertAlmostEqual(pulse["avg_stock_return"], 10.0, places=2)
        self.assertAlmostEqual(pulse["expectancy"], -10.0, places=2)
        self.assertGreater(compass["expectancy"], pulse["expectancy"])
        self.assertEqual(ranked[0]["gate_code"], "COMPASS")

        measurable_codes = [row["gate_code"] for row in ranked if row["expectancy"] is not None]
        self.assertEqual(measurable_codes[0], "COMPASS")
        self.assertEqual(measurable_codes[-1], "PULSE")

    def test_gate_contribution_audit_best_and_worst_highlights(self) -> None:
        import backtest_engine as be

        signals = []
        for index in range(6):
            gate_key = ("sentinel", "atlas", "oracle", "phantom", "catalyst", "specter")[index]
            signals.append(
                be.attach_return_fields(
                    {
                        "outcome_label": "WIN",
                        "direction": "Bullish",
                        "return_20d": float(index + 1),
                        "gate_snapshot_json": f'{{"gates":[{{"key":"{gate_key}","passed":true}}]}}',
                    }
                )
            )

        audit = be.compute_gate_contribution_audit(signals)
        self.assertEqual(len(audit["best_gates"]), 5)
        self.assertEqual(len(audit["worst_gates"]), 5)
        self.assertEqual(audit["best_gates"][0]["gate_code"], "SPECTER")
        self.assertAlmostEqual(audit["best_gates"][0]["expectancy"], 6.0, places=2)
        self.assertEqual(audit["worst_gates"][0]["gate_code"], "SENTINEL")
        self.assertAlmostEqual(audit["worst_gates"][0]["expectancy"], 1.0, places=2)

    def test_gate_intersection_matrix_includes_every_valid_pair(self) -> None:
        import backtest_engine as be
        from run_gates import GATES

        matrix = be.compute_gate_intersection_matrix([])
        expected_pairs = len(GATES) * (len(GATES) - 1) // 2
        self.assertEqual(matrix["pair_count"], expected_pairs)
        self.assertEqual(len(matrix["pairs"]), expected_pairs)
        self.assertEqual(len({row["gate_pair"] for row in matrix["pairs"]}), expected_pairs)
        self.assertEqual(matrix["pairs"][0]["signal_count"], 0)
        self.assertIsNone(matrix["pairs"][0]["expectancy"])

    def test_gate_intersection_matrix_ranks_by_expectancy(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "outcome_label": "WIN",
                    "direction": "Bullish",
                    "return_20d": 12.0,
                    "gate_snapshot_json": (
                        '{"gates":[{"key":"compass","passed":true},{"key":"pulse","passed":true}]}'
                    ),
                }
            ),
            be.attach_return_fields(
                {
                    "outcome_label": "LOSS",
                    "direction": "Bearish",
                    "return_20d": 10.0,
                    "gate_snapshot_json": (
                        '{"gates":[{"key":"compass","passed":true},{"key":"sentinel","passed":true}]}'
                    ),
                }
            ),
        ]
        matrix = be.compute_gate_intersection_matrix(signals)
        compass_pulse = next(row for row in matrix["pairs"] if row["gate_pair"] == "COMPASS+PULSE")
        compass_sentinel = next(row for row in matrix["pairs"] if row["gate_pair"] == "COMPASS+SENTINEL")

        self.assertEqual(compass_pulse["signal_count"], 1)
        self.assertAlmostEqual(compass_pulse["expectancy"], 12.0, places=2)
        self.assertEqual(compass_sentinel["signal_count"], 1)
        self.assertAlmostEqual(compass_sentinel["expectancy"], -10.0, places=2)
        self.assertGreater(compass_pulse["expectancy"], compass_sentinel["expectancy"])

        measurable = [row for row in matrix["pairs"] if row["expectancy"] is not None]
        self.assertEqual(measurable[0]["gate_pair"], "COMPASS+PULSE")
        self.assertEqual(measurable[-1]["gate_pair"], "COMPASS+SENTINEL")

    def test_gate_intersection_signal_and_stock_return_are_separate(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "outcome_label": "LOSS",
                    "direction": "Bearish",
                    "return_20d": 10.0,
                    "gate_snapshot_json": (
                        '{"gates":[{"key":"compass","passed":true},{"key":"pulse","passed":true}]}'
                    ),
                }
            ),
        ]
        matrix = be.compute_gate_intersection_matrix(signals)
        row = next(item for item in matrix["pairs"] if item["gate_pair"] == "COMPASS+PULSE")
        self.assertAlmostEqual(row["avg_stock_return"], 10.0, places=2)
        self.assertAlmostEqual(row["avg_signal_return"], -10.0, places=2)
        self.assertAlmostEqual(row["expectancy"], -10.0, places=2)

    def test_gate_intersection_top_and_bottom_pairs_capped_at_ten(self) -> None:
        import json

        import backtest_engine as be
        from run_gates import GATES

        signals = []
        for index, (gate_a, gate_b) in enumerate(be.iter_horizon_gate_pairs()[:12]):
            key_a = next(key for key, code, _ in GATES if code.upper() == gate_a)
            key_b = next(key for key, code, _ in GATES if code.upper() == gate_b)
            signals.append(
                be.attach_return_fields(
                    {
                        "outcome_label": "WIN",
                        "direction": "Bullish",
                        "return_20d": float(index + 1),
                        "gate_snapshot_json": json.dumps(
                            {
                                "gates": [
                                    {"key": key_a, "passed": True},
                                    {"key": key_b, "passed": True},
                                ]
                            }
                        ),
                    }
                )
            )

        matrix = be.compute_gate_intersection_matrix(signals)
        self.assertEqual(len(matrix["top_pairs"]), 10)
        self.assertEqual(len(matrix["bottom_pairs"]), 10)
        self.assertAlmostEqual(matrix["top_pairs"][0]["expectancy"], 12.0, places=2)
        self.assertAlmostEqual(matrix["bottom_pairs"][0]["expectancy"], 1.0, places=2)
        self.assertAlmostEqual(matrix["bottom_pairs"][-1]["expectancy"], 10.0, places=2)

    def test_bearish_failure_audit_filters_bearish_only(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "BULL",
                    "direction": "Bullish",
                    "sector": "Technology",
                    "outcome_label": "WIN",
                    "return_20d": 12.0,
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "BEAR",
                    "direction": "Bearish",
                    "sector": "Semiconductors",
                    "outcome_label": "LOSS",
                    "return_20d": 10.0,
                    "gate_snapshot_json": '{"gates":[{"key":"compass","passed":true}]}',
                    "recommendation_id": 2,
                }
            ),
        ]
        audit = be.compute_bearish_failure_audit(signals)
        summary = audit["summary"]
        self.assertEqual(summary["signal_count"], 1)
        self.assertEqual(summary["win_rate"], 0.0)
        self.assertAlmostEqual(summary["avg_signal_return"], -10.0, places=2)
        self.assertAlmostEqual(summary["avg_stock_return"], 10.0, places=2)
        self.assertAlmostEqual(summary["expectancy"], -10.0, places=2)

    def test_bearish_failure_audit_ranks_losing_trades(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "AMD",
                    "direction": "Bearish",
                    "sector": "Semiconductors",
                    "outcome_label": "LOSS",
                    "return_20d": 46.0,
                    "score": 88.0,
                    "gate_snapshot_json": '{"gates":[{"key":"sentinel","passed":true}]}',
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "INTC",
                    "direction": "Bearish",
                    "sector": "Semiconductors",
                    "outcome_label": "LOSS",
                    "return_20d": 20.0,
                    "score": 80.0,
                    "gate_snapshot_json": '{"gates":[{"key":"pulse","passed":true}]}',
                    "recommendation_id": 2,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "WINB",
                    "direction": "Bearish",
                    "sector": "Healthcare",
                    "outcome_label": "WIN",
                    "return_20d": -8.0,
                    "score": 75.0,
                    "recommendation_id": 3,
                }
            ),
        ]
        audit = be.compute_bearish_failure_audit(signals)
        losers = audit["top_losing_trades"]
        self.assertEqual(len(losers), 3)
        self.assertEqual(losers[0]["ticker"], "AMD")
        self.assertAlmostEqual(losers[0]["stock_return"], 46.0, places=2)
        self.assertAlmostEqual(losers[0]["signal_return"], -46.0, places=2)
        self.assertEqual(losers[0]["gate_combination"], "SENTINEL")
        self.assertEqual(losers[-1]["ticker"], "WINB")
        self.assertAlmostEqual(losers[-1]["signal_return"], 8.0, places=2)

    def test_bearish_failure_audit_common_loss_patterns(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "AMD",
                    "direction": "Bearish",
                    "sector": "Semiconductors",
                    "outcome_label": "LOSS",
                    "return_20d": 46.0,
                    "gate_snapshot_json": (
                        '{"gates":[{"key":"compass","passed":true},{"key":"pulse","passed":true}]}'
                    ),
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "NVDA",
                    "direction": "Bearish",
                    "sector": "Semiconductors",
                    "outcome_label": "LOSS",
                    "return_20d": 30.0,
                    "gate_snapshot_json": (
                        '{"gates":[{"key":"compass","passed":true},{"key":"pulse","passed":true}]}'
                    ),
                    "recommendation_id": 2,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "JPM",
                    "direction": "Bearish",
                    "sector": "Financials",
                    "outcome_label": "LOSS",
                    "return_20d": 12.0,
                    "gate_snapshot_json": '{"gates":[{"key":"sentinel","passed":true}]}',
                    "recommendation_id": 3,
                }
            ),
        ]
        audit = be.compute_bearish_failure_audit(signals)
        self.assertEqual(audit["common_loss_sectors"][0]["label"], "Semiconductors")
        self.assertEqual(audit["common_loss_sectors"][0]["count"], 2)
        self.assertEqual(audit["common_loss_gate_combinations"][0]["label"], "COMPASS+PULSE")
        self.assertEqual(audit["common_loss_gate_combinations"][0]["count"], 2)
        gate_counts = {row["label"]: row["count"] for row in audit["common_loss_gates"]}
        self.assertEqual(gate_counts["COMPASS"], 2)
        self.assertEqual(gate_counts["PULSE"], 2)
        self.assertEqual(gate_counts["SENTINEL"], 1)

    def test_trend_leadership_bullish_bearish_expectancy(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "NVDA",
                    "direction": "Bullish",
                    "sector": "AI_INFRASTRUCTURE",
                    "outcome_label": "WIN",
                    "return_20d": 12.0,
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "NVDA",
                    "direction": "Bearish",
                    "sector": "AI_INFRASTRUCTURE",
                    "outcome_label": "LOSS",
                    "return_20d": 10.0,
                    "recommendation_id": 2,
                }
            ),
        ]
        audit = be.compute_trend_leadership_audit(signals)
        leaders = next(group for group in audit["groups"] if group["id"] == "mega_cap_ai_leaders")
        self.assertEqual(leaders["signal_count"], 2)
        self.assertEqual(leaders["bullish"]["signal_count"], 1)
        self.assertAlmostEqual(leaders["bullish"]["expectancy"], 12.0, places=2)
        self.assertEqual(leaders["bearish"]["signal_count"], 1)
        self.assertAlmostEqual(leaders["bearish"]["expectancy"], -10.0, places=2)

        ai_infra = next(group for group in audit["groups"] if group["id"] == "ai_infrastructure")
        self.assertEqual(ai_infra["signal_count"], 2)
        self.assertAlmostEqual(ai_infra["bullish"]["expectancy"], 12.0, places=2)
        self.assertAlmostEqual(ai_infra["bearish"]["expectancy"], -10.0, places=2)

    def test_trend_leadership_classifies_semiconductors(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "AMD",
                    "direction": "Bullish",
                    "sector": "SEMICONDUCTORS",
                    "outcome_label": "WIN",
                    "return_20d": 8.0,
                    "universe_preset_id": "semiconductors",
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "JPM",
                    "direction": "Bullish",
                    "sector": "FINANCIALS",
                    "outcome_label": "WIN",
                    "return_20d": 5.0,
                    "recommendation_id": 2,
                }
            ),
        ]
        audit = be.compute_trend_leadership_audit(signals)
        semi = next(group for group in audit["groups"] if group["id"] == "semiconductors")
        ai_infra = next(group for group in audit["groups"] if group["id"] == "ai_infrastructure")
        leaders = next(group for group in audit["groups"] if group["id"] == "mega_cap_ai_leaders")
        self.assertEqual(semi["signal_count"], 1)
        self.assertAlmostEqual(semi["bullish"]["expectancy"], 8.0, places=2)
        self.assertEqual(ai_infra["signal_count"], 0)
        self.assertEqual(leaders["signal_count"], 0)

    def test_trend_leadership_top_winners_and_losers(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "NVDA",
                    "direction": "Bullish",
                    "sector": "AI_INFRASTRUCTURE",
                    "outcome_label": "WIN",
                    "return_20d": 20.0,
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "MSFT",
                    "direction": "Bullish",
                    "sector": "AI_INFRASTRUCTURE",
                    "outcome_label": "WIN",
                    "return_20d": 6.0,
                    "recommendation_id": 2,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "AAPL",
                    "direction": "Bearish",
                    "sector": "AI_INFRASTRUCTURE",
                    "outcome_label": "LOSS",
                    "return_20d": 15.0,
                    "recommendation_id": 3,
                }
            ),
        ]
        audit = be.compute_trend_leadership_audit(signals)
        leaders = next(group for group in audit["groups"] if group["id"] == "mega_cap_ai_leaders")
        self.assertEqual(leaders["top_winning_tickers"][0]["ticker"], "NVDA")
        self.assertAlmostEqual(leaders["top_winning_tickers"][0]["signal_return"], 20.0, places=2)
        self.assertAlmostEqual(leaders["top_winning_tickers"][0]["stock_return"], 20.0, places=2)
        self.assertEqual(leaders["top_losing_tickers"][0]["ticker"], "AAPL")
        self.assertAlmostEqual(leaders["top_losing_tickers"][0]["signal_return"], -15.0, places=2)
        self.assertAlmostEqual(leaders["top_losing_tickers"][0]["stock_return"], 15.0, places=2)

    def test_directional_return_flips_bearish(self) -> None:
        import backtest_engine as be

        self.assertEqual(be.directional_return("Bullish", 10.0), 10.0)
        self.assertEqual(be.directional_return("Bearish", 10.0), -10.0)
        self.assertIsNone(be.directional_return("Bearish", None))

    def test_bearish_loss_positive_stock_maps_to_negative_signal_return(self) -> None:
        import backtest_engine as be

        signal = be.attach_return_fields(
            {
                "direction": "Bearish",
                "outcome_label": "LOSS",
                "return_20d": 46.7222,
            }
        )
        self.assertAlmostEqual(signal["stock_return"], 46.7222, places=4)
        self.assertAlmostEqual(signal["signal_return"], -46.7222, places=4)

    def test_expectancy_equals_mean_signal_return(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields({"direction": "Bearish", "outcome_label": "LOSS", "return_20d": 46.0}),
            be.attach_return_fields({"direction": "Bearish", "outcome_label": "LOSS", "return_20d": 39.0}),
        ]
        metrics = be.compute_core_metrics(signals)
        self.assertAlmostEqual(metrics["avg_return"], -42.5, places=2)
        self.assertAlmostEqual(metrics["expectancy"], -42.5, places=2)
        self.assertAlmostEqual(metrics["avg_stock_return"], 42.5, places=2)

    def test_best_setups_rank_by_signal_return(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {"ticker": "AMD", "direction": "Bearish", "outcome_label": "LOSS", "return_20d": 46.0}
            ),
            be.attach_return_fields(
                {"ticker": "NOW", "direction": "Bullish", "outcome_label": "WIN", "return_20d": 24.0}
            ),
        ]
        best, worst = be.compute_setups(signals, limit=1)
        self.assertEqual(best[0]["ticker"], "NOW")
        self.assertEqual(worst[0]["ticker"], "AMD")
        self.assertAlmostEqual(best[0]["signal_return"], 24.0, places=2)
        self.assertAlmostEqual(worst[0]["signal_return"], -46.0, places=2)

    def test_direction_breakdown_includes_bullish_and_bearish(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields({"direction": "Bullish", "outcome_label": "WIN", "return_20d": 8.0}),
            be.attach_return_fields({"direction": "Bearish", "outcome_label": "LOSS", "return_20d": 10.0}),
        ]
        rows = be.compute_direction_breakdown(signals)
        self.assertEqual([row["direction"] for row in rows], ["Bullish", "Bearish", "Neutral"])
        bullish = rows[0]
        bearish = rows[1]
        self.assertEqual(bullish["signal_count"], 1)
        self.assertEqual(bullish["win_rate"], 100.0)
        self.assertAlmostEqual(bullish["avg_signal_return"], 8.0, places=2)
        self.assertAlmostEqual(bullish["avg_stock_return"], 8.0, places=2)
        self.assertAlmostEqual(bullish["expectancy"], 8.0, places=2)
        self.assertEqual(bearish["signal_count"], 1)
        self.assertEqual(bearish["win_rate"], 0.0)
        self.assertAlmostEqual(bearish["avg_signal_return"], -10.0, places=2)
        self.assertAlmostEqual(bearish["avg_stock_return"], 10.0, places=2)
        self.assertAlmostEqual(bearish["expectancy"], -10.0, places=2)

    def test_direction_breakdown_always_includes_neutral_row(self) -> None:
        import backtest_engine as be

        rows = be.compute_direction_breakdown([])
        self.assertEqual([row["direction"] for row in rows], ["Bullish", "Bearish", "Neutral"])
        neutral = rows[2]
        self.assertEqual(neutral["signal_count"], 0)
        self.assertIsNone(neutral["avg_signal_return"])
        self.assertIsNone(neutral["avg_stock_return"])
        self.assertIsNone(neutral["expectancy"])

    def test_direction_breakdown_includes_neutral_when_present(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields({"direction": "Neutral", "outcome_label": "FLAT", "return_20d": 0.5}),
        ]
        rows = be.compute_direction_breakdown(signals)
        self.assertEqual(len(rows), 3)
        neutral = rows[2]
        self.assertEqual(neutral["direction"], "Neutral")
        self.assertEqual(neutral["signal_count"], 1)
        self.assertAlmostEqual(neutral["avg_signal_return"], 0.5, places=2)
        self.assertAlmostEqual(neutral["avg_stock_return"], 0.5, places=2)
        self.assertAlmostEqual(neutral["expectancy"], 0.5, places=2)

    def test_sector_audit_selects_worst_sector_by_avg_signal_return(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "AAA",
                    "direction": "Bullish",
                    "sector": "TECHNOLOGY",
                    "outcome_label": "WIN",
                    "return_20d": 10.0,
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "BBB",
                    "direction": "Bearish",
                    "sector": "HEALTHCARE",
                    "outcome_label": "LOSS",
                    "return_20d": 5.0,
                    "recommendation_id": 2,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "CCC",
                    "direction": "Bearish",
                    "sector": "SEMICONDUCTORS",
                    "outcome_label": "LOSS",
                    "return_20d": 20.0,
                    "recommendation_id": 3,
                }
            ),
        ]
        audit = be.compute_sector_audit(signals)
        worst = audit["worst_sector"]
        self.assertEqual(worst["sector"], "SEMICONDUCTORS")
        self.assertAlmostEqual(worst["avg_signal_return"], -20.0, places=2)
        self.assertAlmostEqual(worst["avg_stock_return"], 20.0, places=2)

    def test_sector_audit_highlights_worst_sector_and_losers(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "AMD",
                    "direction": "Bearish",
                    "sector": "SEMICONDUCTORS",
                    "outcome_label": "LOSS",
                    "return_20d": 46.0,
                    "universe_preset_id": "mega_cap",
                    "cohort_class": "actionable",
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "AMD",
                    "direction": "Bearish",
                    "sector": "SEMICONDUCTORS",
                    "outcome_label": "LOSS",
                    "return_20d": 39.0,
                    "universe_preset_id": "mega_cap",
                    "cohort_class": "actionable",
                    "recommendation_id": 2,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "NOW",
                    "direction": "Bullish",
                    "sector": "TECHNOLOGY",
                    "outcome_label": "WIN",
                    "return_20d": 12.0,
                    "recommendation_id": 3,
                }
            ),
        ]
        audit = be.compute_sector_audit(signals)
        worst = audit["worst_sector"]
        self.assertEqual(worst["sector"], "SEMICONDUCTORS")
        self.assertEqual(worst["signal_count"], 2)
        self.assertAlmostEqual(worst["avg_signal_return"], -42.5, places=2)
        self.assertAlmostEqual(worst["avg_stock_return"], 42.5, places=2)
        self.assertEqual(len(worst["top_losing_tickers"]), 2)
        self.assertEqual(worst["top_losing_tickers"][0]["ticker"], "AMD")
        self.assertAlmostEqual(worst["top_losing_tickers"][0]["stock_return"], 46.0, places=2)
        self.assertAlmostEqual(worst["top_losing_tickers"][0]["signal_return"], -46.0, places=2)
        self.assertEqual(worst["top_losing_tickers"][0]["outcome_label"], "LOSS")
        self.assertEqual(worst["top_losing_tickers"][0]["preset_cohort"], "mega_cap / actionable")

    def test_sector_audit_top_losing_tickers_limited_to_five(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": f"T{i}",
                    "direction": "Bearish",
                    "sector": "SEMICONDUCTORS",
                    "outcome_label": "LOSS",
                    "return_20d": float(i),
                    "recommendation_id": i,
                }
            )
            for i in range(1, 7)
        ]
        audit = be.compute_sector_audit(signals)
        worst = audit["worst_sector"]
        self.assertEqual(worst["sector"], "SEMICONDUCTORS")
        self.assertEqual(len(worst["top_losing_tickers"]), 5)
        self.assertEqual(worst["top_losing_tickers"][0]["ticker"], "T6")
        self.assertAlmostEqual(worst["top_losing_tickers"][0]["signal_return"], -6.0, places=2)
        self.assertAlmostEqual(worst["top_losing_tickers"][0]["stock_return"], 6.0, places=2)
        self.assertEqual(worst["top_losing_tickers"][4]["ticker"], "T2")

    def test_trade_audit_sorts_by_signal_return_ascending(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "WINR",
                    "timestamp": "2026-05-10T12:00:00+00:00",
                    "direction": "Bullish",
                    "sector": "Technology",
                    "outcome_label": "WIN",
                    "return_20d": 12.0,
                    "score": 90.0,
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "LOSR",
                    "timestamp": "2026-05-11T12:00:00+00:00",
                    "direction": "Bearish",
                    "sector": "Semiconductors",
                    "outcome_label": "LOSS",
                    "return_20d": 20.0,
                    "score": 80.0,
                    "recommendation_id": 2,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "FLATR",
                    "timestamp": "2026-05-12T12:00:00+00:00",
                    "direction": "Bullish",
                    "sector": "Healthcare",
                    "outcome_label": "FLAT",
                    "return_20d": 0.0,
                    "score": 75.0,
                    "recommendation_id": 3,
                }
            ),
        ]
        rows = be.compute_trade_audit(signals)
        self.assertEqual([row["ticker"] for row in rows], ["LOSR", "FLATR", "WINR"])
        self.assertAlmostEqual(rows[0]["signal_return"], -20.0, places=2)
        self.assertAlmostEqual(rows[2]["signal_return"], 12.0, places=2)

    def test_trade_audit_rows_include_stock_and_signal_return(self) -> None:
        import backtest_engine as be

        signals = [
            be.attach_return_fields(
                {
                    "ticker": "AAA",
                    "direction": "Bullish",
                    "return_20d": 8.0,
                    "recommendation_id": 1,
                }
            ),
            be.attach_return_fields(
                {
                    "ticker": "BBB",
                    "direction": "Bearish",
                    "return_20d": 8.0,
                    "recommendation_id": 2,
                }
            ),
        ]
        rows = be.compute_trade_audit(signals)
        by_ticker = {row["ticker"]: row for row in rows}

        self.assertAlmostEqual(by_ticker["AAA"]["stock_return"], 8.0, places=2)
        self.assertAlmostEqual(by_ticker["AAA"]["signal_return"], 8.0, places=2)
        self.assertAlmostEqual(by_ticker["BBB"]["stock_return"], 8.0, places=2)
        self.assertAlmostEqual(by_ticker["BBB"]["signal_return"], -8.0, places=2)

    def test_trade_audit_row_includes_gate_combination_and_preset_cohort(self) -> None:
        import backtest_engine as be

        signal = be.attach_return_fields(
            {
                "ticker": "AMD",
                "timestamp": "2026-05-10T12:00:00+00:00",
                "direction": "Bearish",
                "sector": "Semiconductors",
                "outcome_label": "LOSS",
                "return_20d": 46.0,
                "score": 88.0,
                "universe_preset_id": "mega_cap",
                "cohort_class": "actionable",
                "gate_snapshot_json": '{"gates": [{"key": "sentinel", "passed": true}, {"key": "compass", "passed": true}, {"key": "pulse", "passed": false}]}',
                "recommendation_id": 99,
            }
        )
        row = be.trade_audit_row(signal)
        self.assertEqual(row["date"], "2026-05-10")
        self.assertEqual(row["preset_cohort"], "mega_cap / actionable")
        self.assertEqual(row["gate_combination"], "COMPASS+SENTINEL")
        self.assertAlmostEqual(row["stock_return"], 46.0, places=2)
        self.assertAlmostEqual(row["signal_return"], -46.0, places=2)

    def test_new_backtest_run_is_not_legacy(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import backtest_engine as be
        import memory_store as ms

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "backtest_legacy_new.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                with ms.connect() as conn:
                    run_id = conn.execute(
                        """
                        INSERT INTO scan_runs (
                            timestamp, universe_mode, pick_mode, cohort_class, scan_purpose
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        ("2026-05-01T12:00:00+00:00", "preset", "gate_runner", "actionable", "cohort_baseline"),
                    ).lastrowid
                    self.insert_completed_signal(
                        conn,
                        run_id,
                        ticker="AAA",
                        timestamp="2026-05-10T12:00:00+00:00",
                        direction="Bearish",
                        outcome="LOSS",
                        return_5d=-3.0,
                        return_20d=10.0,
                        score=88.0,
                        sector="Technology",
                        gates=[{"key": "sentinel", "passed": True}],
                    )
                    conn.commit()

                result = be.run_backtest(name="Modern Run", filters=be.BacktestFilters())
                loaded = be.get_backtest_run(result["runId"])
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertFalse(loaded["legacyMetricsWarning"])
                self.assertEqual(loaded["run"]["backtestVersion"], be.BACKTEST_VERSION)

                with ms.connect() as conn:
                    signal_row = conn.execute(
                        "SELECT signal_return FROM backtest_signals WHERE run_id = ?",
                        (result["runId"],),
                    ).fetchone()
                self.assertIsNotNone(signal_row)
                self.assertAlmostEqual(signal_row["signal_return"], -10.0, places=2)

    def test_legacy_backtest_run_without_version_flags_warning(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import backtest_engine as be
        import memory_store as ms

        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "backtest_legacy_old.db"
            with patch.object(ms, "DB_PATH", db_path), patch.object(ms, "_DB_INITIALIZED", False):
                ms.init_db()
                with ms.connect() as conn:
                    run_id = conn.execute(
                        """
                        INSERT INTO scan_runs (
                            timestamp, universe_mode, pick_mode, cohort_class, scan_purpose
                        ) VALUES (?, ?, ?, ?, ?)
                        """,
                        ("2026-05-01T12:00:00+00:00", "preset", "gate_runner", "actionable", "cohort_baseline"),
                    ).lastrowid
                    recommendation_id = self.insert_completed_signal(
                        conn,
                        run_id,
                        ticker="AMD",
                        timestamp="2026-05-10T12:00:00+00:00",
                        direction="Bearish",
                        outcome="LOSS",
                        return_5d=4.0,
                        return_20d=46.0,
                        score=88.0,
                        sector="Semiconductors",
                        gates=[{"key": "sentinel", "passed": True}],
                    )
                    legacy_run_id = conn.execute(
                        """
                        INSERT INTO backtest_runs (
                            created_at, name, description, engine_version, start_date, end_date,
                            filters_json, status
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'completed')
                        """,
                        (
                            "2026-05-01T12:00:00+00:00",
                            "Legacy Run",
                            "Pre-1.1",
                            None,
                            "2026-05-01",
                            "2026-05-31",
                            ms.json_dump({}),
                        ),
                    ).lastrowid
                    conn.execute(
                        """
                        INSERT INTO backtest_signals (
                            run_id, recommendation_id, ticker, timestamp, direction, score,
                            sector, outcome_label, return_5d, return_10d, return_20d
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            legacy_run_id,
                            recommendation_id,
                            "AMD",
                            "2026-05-10T12:00:00+00:00",
                            "Bearish",
                            88.0,
                            "Semiconductors",
                            "LOSS",
                            4.0,
                            None,
                            46.0,
                        ),
                    )
                    conn.execute(
                        """
                        INSERT INTO backtest_metrics (
                            run_id, win_rate, loss_rate, avg_return, median_return, max_drawdown,
                            best_trade, worst_trade, bullish_win_rate, bearish_win_rate, actionable_win_rate
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (legacy_run_id, 0.0, 100.0, 46.0, 46.0, 0.0, 46.0, 46.0, 0.0, 0.0, 0.0),
                    )
                    conn.commit()

                loaded = be.get_backtest_run(int(legacy_run_id))
                self.assertIsNotNone(loaded)
                assert loaded is not None
                self.assertTrue(loaded["legacyMetricsWarning"])
                self.assertEqual(loaded["legacyMetricsMessage"], be.LEGACY_METRICS_WARNING)


class ResearchJobRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import memory_store as ms

        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "research_jobs_test.db"
        self._patchers = [
            patch.object(ms, "DB_PATH", self._db_path),
            patch.object(ms, "_DB_INITIALIZED", False),
        ]
        for patcher in self._patchers:
            patcher.start()
        ms.init_db()

    def tearDown(self) -> None:
        for patcher in self._patchers:
            patcher.stop()
        self._tmpdir.cleanup()

    def test_create_default_research_jobs(self) -> None:
        import research_job_runner as rjr

        first = rjr.create_default_research_jobs()
        second = rjr.create_default_research_jobs()
        self.assertTrue(first["ok"])
        self.assertEqual(first["created"], len(rjr.DEFAULT_RESEARCH_JOB_SPECS))
        self.assertEqual(second["created"], 0)
        jobs = rjr.list_research_jobs()
        self.assertEqual(len(jobs), len(rjr.DEFAULT_RESEARCH_JOB_SPECS))
        names = {job["name"] for job in jobs}
        self.assertIn("Mega Cap Tech Scan", names)
        self.assertIn("AI Leadership Audit", names)

    def test_list_research_jobs(self) -> None:
        import research_job_runner as rjr

        rjr.create_default_research_jobs()
        jobs = rjr.list_research_jobs(include_disabled=False)
        self.assertEqual(len(jobs), len(rjr.DEFAULT_RESEARCH_JOB_SPECS))
        self.assertTrue(all(job["enabled"] for job in jobs))

    def test_run_research_job_executes_cohort_scan(self) -> None:
        import memory_store as ms
        import research_job_runner as rjr

        rjr.create_default_research_jobs()
        with ms.connect() as conn:
            run_id = conn.execute(
                """
                INSERT INTO scan_runs (
                    timestamp, universe_mode, pick_mode, cohort_class, scan_purpose
                ) VALUES (?, ?, ?, ?, ?)
                """,
                ("2026-05-01T12:00:00+00:00", "preset", "gate_runner", "actionable", "cohort_baseline"),
            ).lastrowid
            BacktestEngineTests().insert_completed_signal(
                conn,
                run_id,
                ticker="NVDA",
                timestamp="2026-05-10T12:00:00+00:00",
                direction="Bullish",
                outcome="WIN",
                return_5d=4.0,
                return_20d=8.0,
                score=88.0,
                sector="Technology",
                gates=[{"key": "sentinel", "passed": True}],
                cohort_class="actionable",
            )
            conn.commit()

        jobs = rjr.list_research_jobs()
        mega_job = next(job for job in jobs if job["name"] == "Mega Cap Tech Scan")
        result = rjr.run_research_job(int(mega_job["id"]))
        self.assertTrue(result["ok"])
        run = result["run"]
        self.assertEqual(run["status"], "completed")
        self.assertEqual(run["signalsCount"], 1)
        self.assertAlmostEqual(run["summary"]["expectancy"], 8.0, places=2)
        self.assertIsNotNone(run["completedAt"])
        refreshed = next(job for job in rjr.list_research_jobs() if job["id"] == mega_job["id"])
        self.assertIsNotNone(refreshed["lastRunAt"])

    def test_run_enabled_research_jobs(self) -> None:
        import memory_store as ms
        import research_job_runner as rjr

        rjr.create_default_research_jobs()
        with ms.connect() as conn:
            conn.execute("UPDATE research_jobs SET enabled = 0")
            conn.execute(
                "UPDATE research_jobs SET enabled = 1 WHERE name IN (?, ?)",
                ("Mega Cap Tech Scan", "Bearish Failure Audit"),
            )
            conn.commit()

        batch = rjr.run_enabled_research_jobs()
        self.assertEqual(batch["ran"], 2)
        self.assertEqual(batch["completed"], 2)
        self.assertEqual(batch["failed"], 0)
        self.assertEqual(len(batch["results"]), 2)

    def test_run_research_job_failure_handling(self) -> None:
        from unittest.mock import patch

        import research_job_runner as rjr

        rjr.create_default_research_jobs()
        jobs = rjr.list_research_jobs()
        audit_job = next(job for job in jobs if job["name"] == "Bearish Failure Audit")
        with patch("research_job_runner.preview_backtest", side_effect=RuntimeError("preview failed")):
            result = rjr.run_research_job(int(audit_job["id"]))
        self.assertFalse(result["ok"])
        self.assertEqual(result["run"]["status"], "failed")
        self.assertIn("preview failed", result["run"]["errorMessage"] or "")

    def test_list_research_job_runs_includes_job_name(self) -> None:
        import research_job_runner as rjr

        rjr.create_default_research_jobs()
        jobs = rjr.list_research_jobs()
        job_id = jobs[0]["id"]
        rjr.run_research_job(job_id)
        runs = rjr.list_research_job_runs(limit=10)
        self.assertGreaterEqual(len(runs), 1)
        self.assertEqual(runs[0]["jobId"], job_id)
        self.assertTrue(runs[0]["jobName"])


class ResearchFindingsEngineTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import memory_store as ms

        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "research_findings_test.db"
        self._patchers = [
            patch.object(ms, "DB_PATH", self._db_path),
            patch.object(ms, "_DB_INITIALIZED", False),
        ]
        for patcher in self._patchers:
            patcher.start()
        ms.init_db()
        import research_job_runner as rjr

        rjr.create_default_research_jobs()

    def tearDown(self) -> None:
        for patcher in self._patchers:
            patcher.stop()
        self._tmpdir.cleanup()

    def insert_completed_run(
        self,
        job_name: str,
        *,
        signals_count: int,
        summary: dict,
    ) -> int:
        import memory_store as ms
        import research_job_runner as rjr

        with ms.connect() as conn:
            job_row = conn.execute(
                "SELECT id FROM research_jobs WHERE name = ?",
                (job_name,),
            ).fetchone()
            self.assertIsNotNone(job_row)
            cursor = conn.execute(
                """
                INSERT INTO research_job_runs (
                    job_id, started_at, completed_at, status, signals_count, summary_json
                ) VALUES (?, ?, ?, 'completed', ?, ?)
                """,
                (
                    job_row["id"],
                    rjr.utc_now_iso(),
                    rjr.utc_now_iso(),
                    signals_count,
                    ms.json_dump(summary),
                ),
            )
            run_id = int(cursor.lastrowid)
            conn.commit()
            return run_id

    def test_create_and_list_research_finding(self) -> None:
        import research_findings_engine as rfe

        created = rfe.create_research_finding(
            finding_type="system_note",
            severity="info",
            title="Manual finding",
            description="Test finding",
            confidence="low",
        )
        self.assertTrue(created["created"])
        findings = rfe.list_research_findings()
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["title"], "Manual finding")
        self.assertEqual(findings[0]["status"], "open")

    def test_update_research_finding_status(self) -> None:
        import research_findings_engine as rfe

        created = rfe.create_research_finding(
            finding_type="anomaly",
            severity="watch",
            title="Status test",
            description="Status update test",
            confidence="medium",
        )
        finding_id = created["finding"]["id"]
        updated = rfe.update_research_finding_status(finding_id, "reviewed")
        self.assertTrue(updated["ok"])
        self.assertEqual(updated["finding"]["status"], "reviewed")

    def test_bearish_failure_finding_generation(self) -> None:
        import research_findings_engine as rfe

        run_id = self.insert_completed_run(
            "Bearish Failure Audit",
            signals_count=12,
            summary={
                "jobName": "Bearish Failure Audit",
                "signalsMatched": 12,
                "audit": {
                    "kind": "bearish_failure",
                    "summary": {
                        "signal_count": 12,
                        "expectancy": -5.0,
                        "win_rate": 8.0,
                        "avg_signal_return": -5.0,
                    },
                },
            },
        )
        result = rfe.generate_findings_from_job_run(run_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["generated"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["findingType"], "direction_failure")
        self.assertEqual(finding["title"], "Bearish signals underperforming")
        self.assertEqual(finding["confidence"], "medium")

    def test_gate_strength_finding_generation(self) -> None:
        import research_findings_engine as rfe

        run_id = self.insert_completed_run(
            "SPECTER Positive Audit",
            signals_count=15,
            summary={
                "jobName": "SPECTER Positive Audit",
                "signalsMatched": 15,
                "audit": {
                    "kind": "specter_positive",
                    "gateCode": "SPECTER",
                    "signalCount": 15,
                    "expectancy": 4.5,
                    "winRate": 62.0,
                    "avgSignalReturn": 4.5,
                },
            },
        )
        result = rfe.generate_findings_from_job_run(run_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["generated"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["findingType"], "gate_strength")
        self.assertEqual(finding["title"], "SPECTER showing positive expectancy")
        self.assertEqual(finding["relatedGates"], ["SPECTER"])

    def test_leadership_trend_finding_generation(self) -> None:
        import research_findings_engine as rfe

        run_id = self.insert_completed_run(
            "AI Leadership Audit",
            signals_count=20,
            summary={
                "jobName": "AI Leadership Audit",
                "signalsMatched": 20,
                "audit": {
                    "kind": "trend_leadership",
                    "groups": [
                        {
                            "id": "ai_infrastructure",
                            "label": "AI Infrastructure Stocks",
                            "signalCount": 20,
                            "bullishExpectancy": 6.0,
                            "bearishExpectancy": -4.0,
                        }
                    ],
                },
            },
        )
        result = rfe.generate_findings_from_job_run(run_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["generated"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["findingType"], "leadership_trend")
        self.assertEqual(finding["title"], "Leadership cohort favors bullish exposure over bearish calls")

    def test_empty_run_finding_generation(self) -> None:
        import research_findings_engine as rfe

        run_id = self.insert_completed_run(
            "Mega Cap Tech Scan",
            signals_count=0,
            summary={"jobName": "Mega Cap Tech Scan", "signalsMatched": 0},
        )
        result = rfe.generate_findings_from_job_run(run_id)
        self.assertTrue(result["ok"])
        self.assertEqual(result["generated"], 1)
        finding = result["findings"][0]
        self.assertEqual(finding["findingType"], "system_note")
        self.assertEqual(finding["title"], "Research job completed with no matched signals")

    def test_duplicate_prevention(self) -> None:
        import research_findings_engine as rfe

        run_id = self.insert_completed_run(
            "Mega Cap Tech Scan",
            signals_count=0,
            summary={"jobName": "Mega Cap Tech Scan", "signalsMatched": 0},
        )
        first = rfe.generate_findings_from_job_run(run_id)
        second = rfe.generate_findings_from_job_run(run_id)
        self.assertEqual(first["generated"], 1)
        self.assertEqual(second["generated"], 0)
        self.assertEqual(second["skippedDuplicates"], 1)
        findings = rfe.list_research_findings(finding_type="system_note")
        self.assertEqual(len(findings), 1)


class ScheduledResearchRunnerTests(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile
        from pathlib import Path
        from unittest.mock import patch

        import memory_store as ms

        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = Path(self._tmpdir.name) / "scheduled_research_test.db"
        self._patchers = [
            patch.object(ms, "DB_PATH", self._db_path),
            patch.object(ms, "_DB_INITIALIZED", False),
        ]
        for patcher in self._patchers:
            patcher.start()
        ms.init_db()

    def tearDown(self) -> None:
        for patcher in self._patchers:
            patcher.stop()
        self._tmpdir.cleanup()

    def test_format_scheduled_research_summary(self) -> None:
        import scheduled_research_runner as srr

        text = srr.format_scheduled_research_summary(
            {
                "ok": False,
                "timestamp": "2026-06-20T12:00:00+00:00",
                "jobsRun": 3,
                "completed": 2,
                "failed": 1,
                "findingsGenerated": 4,
                "findingsSkipped": 1,
                "findingsAvailable": True,
                "errors": ["Job failed (Bearish Failure Audit): preview failed"],
            }
        )
        self.assertIn("jobs run: 3", text)
        self.assertIn("completed: 2", text)
        self.assertIn("failed: 1", text)
        self.assertIn("findings generated: 4", text)
        self.assertIn("status: failed", text)

    def test_generate_findings_if_available(self) -> None:
        from unittest.mock import patch

        import scheduled_research_runner as srr

        with patch(
            "scheduled_research_runner._load_findings_generator",
            return_value=lambda limit=20: {
                "ok": True,
                "generated": 2,
                "skippedDuplicates": 1,
            },
        ):
            result = srr.generate_findings_if_available(limit=5)
        self.assertTrue(result["available"])
        self.assertEqual(result["generated"], 2)
        self.assertEqual(result["skippedDuplicates"], 1)

    def test_generate_findings_when_engine_missing(self) -> None:
        from unittest.mock import patch

        import scheduled_research_runner as srr

        with patch("scheduled_research_runner._load_findings_generator", return_value=None):
            result = srr.generate_findings_if_available()
        self.assertFalse(result["available"])
        self.assertEqual(result["generated"], 0)

    def test_run_scheduled_research_success(self) -> None:
        from unittest.mock import patch

        import scheduled_research_runner as srr

        with patch(
            "scheduled_research_runner.create_default_research_jobs",
            return_value={"ok": True, "created": 7},
        ), patch(
            "scheduled_research_runner.run_enabled_research_jobs",
            return_value={"ok": True, "ran": 2, "completed": 2, "failed": 0, "results": []},
        ), patch(
            "scheduled_research_runner.generate_findings_if_available",
            return_value={
                "ok": True,
                "available": True,
                "generated": 3,
                "skippedDuplicates": 1,
            },
        ):
            summary = srr.run_scheduled_research()

        self.assertTrue(summary["ok"])
        self.assertEqual(summary["defaultsCreated"], 7)
        self.assertEqual(summary["jobsRun"], 2)
        self.assertEqual(summary["completed"], 2)
        self.assertEqual(summary["failed"], 0)
        self.assertEqual(summary["findingsGenerated"], 3)
        self.assertEqual(summary["findingsSkipped"], 1)
        self.assertTrue(summary["findingsAvailable"])

    def test_run_scheduled_research_records_job_failures(self) -> None:
        from unittest.mock import patch

        import scheduled_research_runner as srr

        with patch(
            "scheduled_research_runner.create_default_research_jobs",
            return_value={"ok": True, "created": 0},
        ), patch(
            "scheduled_research_runner.run_enabled_research_jobs",
            return_value={
                "ok": False,
                "ran": 1,
                "completed": 0,
                "failed": 1,
                "results": [
                    {
                        "ok": False,
                        "job": {"name": "Bearish Failure Audit"},
                        "message": "preview failed",
                    }
                ],
            },
        ), patch(
            "scheduled_research_runner.generate_findings_if_available",
            return_value={"ok": True, "available": True, "generated": 0, "skippedDuplicates": 0},
        ):
            summary = srr.run_scheduled_research()

        self.assertFalse(summary["ok"])
        self.assertEqual(summary["failed"], 1)
        self.assertTrue(any("Bearish Failure Audit" in error for error in summary["errors"]))

    def test_main_exit_code(self) -> None:
        from unittest.mock import patch

        import scheduled_research_runner as srr

        with patch(
            "scheduled_research_runner.run_scheduled_research",
            return_value={
                "ok": True,
                "timestamp": "2026-06-20T12:00:00+00:00",
                "jobsRun": 1,
                "completed": 1,
                "failed": 0,
                "findingsGenerated": 0,
                "errors": [],
            },
        ):
            self.assertEqual(srr.main(["--skip-findings"]), 0)

        with patch(
            "scheduled_research_runner.run_scheduled_research",
            return_value={
                "ok": False,
                "timestamp": "2026-06-20T12:00:00+00:00",
                "jobsRun": 1,
                "completed": 0,
                "failed": 1,
                "findingsGenerated": 0,
                "errors": ["Job failed (Audit): boom"],
            },
        ):
            self.assertEqual(srr.main(["--skip-findings"]), 1)


class CloudResearchWorkerTests(unittest.TestCase):
    def test_validate_cloud_worker_environment_success(self) -> None:
        from unittest.mock import patch

        import cloud_research_worker as crw

        with patch.dict(
            "os.environ",
            {
                "SCOUT_RESEARCH_WORKER_SECRET": "worker-token",
                "SCOUT_CLOUD_RESEARCH_ENABLED": "true",
            },
            clear=True,
        ):
            result = crw.validate_cloud_worker_environment()
        self.assertTrue(result["ok"])
        self.assertEqual(result["errors"], [])

    def test_validate_cloud_worker_environment_missing_secret(self) -> None:
        from unittest.mock import patch

        import cloud_research_worker as crw

        with patch.dict(
            "os.environ",
            {"SCOUT_CLOUD_RESEARCH_ENABLED": "true"},
            clear=True,
        ):
            result = crw.validate_cloud_worker_environment()
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("SCOUT_RESEARCH_WORKER_SECRET" in error for error in result["errors"])
        )

    def test_validate_cloud_worker_environment_disabled(self) -> None:
        from unittest.mock import patch

        import cloud_research_worker as crw

        with patch.dict(
            "os.environ",
            {
                "SCOUT_RESEARCH_WORKER_SECRET": "worker-token",
                "SCOUT_CLOUD_RESEARCH_ENABLED": "false",
            },
            clear=True,
        ):
            result = crw.validate_cloud_worker_environment()
        self.assertFalse(result["ok"])
        self.assertTrue(
            any("SCOUT_CLOUD_RESEARCH_ENABLED" in error for error in result["errors"])
        )

    def test_scheduled_runner_cloud_worker_requires_secrets(self) -> None:
        from unittest.mock import patch

        import scheduled_research_runner as srr

        with patch.dict("os.environ", {}, clear=True):
            exit_code = srr.main(["--cloud-worker", "--skip-findings"])
        self.assertEqual(exit_code, 2)

    def test_scheduled_runner_cloud_worker_runs_when_configured(self) -> None:
        from unittest.mock import patch

        import scheduled_research_runner as srr

        with patch.dict(
            "os.environ",
            {
                "SCOUT_RESEARCH_WORKER_SECRET": "worker-token",
                "SCOUT_CLOUD_RESEARCH_ENABLED": "true",
            },
            clear=True,
        ), patch(
            "scheduled_research_runner.run_scheduled_research",
            return_value={
                "ok": True,
                "timestamp": "2026-06-20T12:00:00+00:00",
                "jobsRun": 1,
                "completed": 1,
                "failed": 0,
                "findingsGenerated": 0,
                "errors": [],
            },
        ):
            exit_code = srr.main(["--cloud-worker", "--skip-findings"])
        self.assertEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
