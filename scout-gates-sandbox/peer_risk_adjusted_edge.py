#!/usr/bin/env python3
"""Peer Risk-Adjusted Edge (PRAE) — plan stub.

Architecture and scoring formulas: docs/peer-risk-adjusted-edge.md

This module defines constants and interfaces only. Scoring logic is NOT
implemented yet. Do not import from production gate paths until P0 ships.
"""

from __future__ import annotations

from typing import Any, Optional

# --- Peer cohort ---
MIN_PEER_COUNT = 3
PRIMARY_GROUP_SECTOR = "sector"
PRIMARY_GROUP_UNIVERSE = "universe"
PRIMARY_GROUP_MARKET_CAP = "market_cap"

# --- Influence caps (mirror earnings_intelligence pattern) ---
PEER_CONVICTION_CAP = 5
PEER_CONVICTION_CAP_WHEN_EI_ACTIVE = 3
PEER_REFERENCE_WEIGHT = 0.25
MAX_EDGE_MAGNITUDE = 2.0
EDGE_TO_POINTS_MULTIPLIER = 2.0

# --- Feature composite weights (must sum to 1.0) ---
FEATURE_WEIGHTS: dict[str, float] = {
    "scout_score": 0.40,
    "rsi": 0.15,
    "change": 0.15,
    "wind": 0.15,
    "dcf_gap": 0.15,
}
Z_SCORE_WINSOR = 2.5

# --- Returns-based metrics (P3) ---
MIN_RETURN_SAMPLES = 20

# --- Modes (align with docs §11) ---
MODE_UNAVAILABLE = "unavailable"
MODE_INSUFFICIENT_PEERS = "insufficient_peers"
MODE_MISSING_SECTOR = "missing_sector"
MODE_PARTIAL_FEATURES = "partial_features"
MODE_AWAITING_RETURNS = "awaiting_returns"
MODE_SCORED = "scored"


class PeerScoringNotImplementedError(NotImplementedError):
    """Raised until PRAE implementation lands (see docs/peer-risk-adjusted-edge.md)."""


def build_peer_bundle_for_run(
    results: list[Any],
    *,
    run_timestamp: str,
) -> dict[str, dict[str, Any]]:
    """Build per-ticker peer context for an entire scan run.

    Planned: peer groups, percentiles, z-scores, and edge breakdown inputs.
    """
    raise PeerScoringNotImplementedError(
        "PRAE run-level bundle not implemented. See docs/peer-risk-adjusted-edge.md §17."
    )


def build_scoring_breakdown(
    ticker: str,
    result_data: dict[str, Any],
    peer_bundle: dict[str, Any],
    *,
    earnings_intelligence: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Compute scoringBreakdown for one ticker."""
    raise PeerScoringNotImplementedError(
        "PRAE scoring breakdown not implemented. See docs/peer-risk-adjusted-edge.md §17.4."
    )


def attach_peer_scoring(
    serialized: dict[str, Any],
    breakdown: dict[str, Any],
) -> dict[str, Any]:
    """Attach scoringBreakdown and optional peerConvictionAdjustment to result payload."""
    raise PeerScoringNotImplementedError(
        "PRAE attach not implemented. See docs/peer-risk-adjusted-edge.md §17.8."
    )


def peer_conviction_cap(earnings_intelligence: Optional[dict[str, Any]]) -> int:
    """Return active peer adjustment cap based on EI state."""
    if isinstance(earnings_intelligence, dict) and earnings_intelligence.get("active"):
        return PEER_CONVICTION_CAP_WHEN_EI_ACTIVE
    return PEER_CONVICTION_CAP
