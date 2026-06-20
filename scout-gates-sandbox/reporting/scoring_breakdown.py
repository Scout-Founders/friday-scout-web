#!/usr/bin/env python3
"""Scoring breakdown report context and HTML templates."""

from __future__ import annotations

import html
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from memory_store import build_rank_maps, gate_score_from_text


METRIC_KEY_GROUPS = {
    "peer_risk_adjusted_edge": (
        "peerRiskAdjustedEdge",
        "peer_risk_adjusted_edge",
        "riskAdjustedEdge",
        "risk_adjusted_edge",
    ),
    "sharpe_ratio": ("sharpeRatio", "sharpe_ratio", "sharpe"),
    "t_stat": ("tStat", "t_stat", "tstat", "t_statistic"),
}

PEER_EDGE_COMPONENT_KEYS = (
    ("raw_edge", ("rawEdge", "raw_edge")),
    ("risk_adjustment", ("riskAdjustment", "risk_adjustment")),
    ("peer_adjustment", ("peerAdjustment", "peer_adjustment")),
    ("sector_adjustment", ("sectorAdjustment", "sector_adjustment")),
    ("volatility_penalty", ("volatilityPenalty", "volatility_penalty")),
    ("liquidity_penalty", ("liquidityPenalty", "liquidity_penalty")),
    ("final_edge", ("finalEdge", "final_edge", "peerRiskAdjustedEdge", "peer_risk_adjusted_edge")),
)


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip()).strip("-").lower()
    return cleaned or "scan"


def resolve_scan_session_id(payload: dict[str, Any]) -> str:
    memory_run_id = payload.get("memoryRunId")
    if memory_run_id not in (None, ""):
        return f"RUN-{memory_run_id}"
    run_timestamp = str(payload.get("runTimestamp") or "")
    if run_timestamp:
        return f"PREVIEW-{slugify(run_timestamp)}"
    return f"PREVIEW-{uuid.uuid4().hex[:12]}"


def display_timestamp(payload: dict[str, Any]) -> str:
    raw = payload.get("runTimestamp")
    if not raw:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        parsed = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return str(raw)


def first_present_mapping(sources: list[dict[str, Any]], keys: tuple[str, ...]) -> Any:
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def metric_sources(result: dict[str, Any]) -> list[dict[str, Any]]:
    nested = [
        result.get("scoringBreakdown"),
        result.get("peerContext"),
        result.get("peerMetrics"),
        result.get("riskMetrics"),
        result.get("analytics"),
    ]
    raw = result.get("raw") if isinstance(result.get("raw"), dict) else {}
    return [result, raw, *[item for item in nested if isinstance(item, dict)]]


def format_metric(value: Any, *, digits: int = 3, suffix: str = "") -> str:
    if value in (None, ""):
        return "—"
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, (int, float)):
        if suffix == "%":
            return f"{value:.{max(digits - 2, 0)}f}%"
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def extract_peer_metrics(result: dict[str, Any]) -> dict[str, Any]:
    sources = metric_sources(result)
    edge = first_present_mapping(sources, METRIC_KEY_GROUPS["peer_risk_adjusted_edge"])
    sharpe = first_present_mapping(sources, METRIC_KEY_GROUPS["sharpe_ratio"])
    t_stat = first_present_mapping(sources, METRIC_KEY_GROUPS["t_stat"])

    breakdown_source = first_present_mapping(
        sources,
        ("peerRiskAdjustedEdgeBreakdown", "peer_risk_adjusted_edge_breakdown", "edgeBreakdown", "edge_breakdown"),
    )
    if not isinstance(breakdown_source, dict):
        breakdown_source = {}

    components: list[dict[str, str]] = []
    for label, keys in PEER_EDGE_COMPONENT_KEYS:
        value = first_present_mapping([breakdown_source, *sources], keys)
        components.append({"label": label.replace("_", " ").title(), "value": format_metric(value)})

    return {
        "peer_risk_adjusted_edge": format_metric(edge),
        "sharpe_ratio": format_metric(sharpe),
        "t_stat": format_metric(t_stat),
        "edge_components": components,
        "metrics_pending": edge in (None, "") and sharpe in (None, "") and t_stat in (None, ""),
    }


def final_score_for_result(result: dict[str, Any]) -> dict[str, Any]:
    base = result.get("score")
    adjusted = result.get("adjustedScoutScore")
    if adjusted in (None, ""):
        adjusted = base
    return {
        "base": format_metric(base, digits=0),
        "adjusted": format_metric(adjusted, digits=0),
        "display": format_metric(adjusted if adjusted not in (None, "") else base, digits=0),
    }


def extract_gate_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    explanation = result.get("explanation") if isinstance(result.get("explanation"), dict) else {}
    explanation_gates = {
        str(gate.get("gate_key") or ""): gate
        for gate in explanation.get("gates", [])
        if isinstance(gate, dict)
    }
    rows: list[dict[str, Any]] = []
    for gate in result.get("gates", []):
        if not isinstance(gate, dict):
            continue
        gate_key = str(gate.get("key") or gate.get("code") or "")
        detail = explanation_gates.get(gate_key, {})
        actual_value = detail.get("actual_value")
        parsed_score = gate_score_from_text(actual_value)
        rows.append(
            {
                "index": gate.get("index"),
                "code": gate.get("code") or gate_key.upper(),
                "name": gate.get("name") or detail.get("gate_name") or gate_key,
                "passed": gate.get("passed") is True,
                "score": format_metric(parsed_score, digits=1) if parsed_score is not None else "—",
                "status": "PASS" if gate.get("passed") is True else "FAIL",
                "explanation": str(detail.get("explanation") or "").strip(),
                "actual_value": str(actual_value or "").strip(),
            }
        )
    return rows


def percentile_from_rank(rank: Optional[int], size: int) -> str:
    if rank is None or size <= 0:
        return "—"
    return format_metric(round(100 * (size - rank + 1) / size, 1), digits=1, suffix="%")


def build_percentile_metrics(payload: dict[str, Any], ticker: str) -> dict[str, Any]:
    results = [row for row in payload.get("results", []) if isinstance(row, dict)]
    universe_ranks, sector_ranks = build_rank_maps(results)
    universe_size = len(results)
    target = next((row for row in results if str(row.get("ticker") or "").upper() == ticker.upper()), None)
    sector = None
    if target:
        raw = target.get("raw") if isinstance(target.get("raw"), dict) else {}
        sector = first_present_mapping([target, raw], ("sector",))
    sector_key = str(sector or "")
    sector_results = [
        row
        for row in results
        if str(first_present_mapping([row, row.get("raw") if isinstance(row.get("raw"), dict) else {}], ("sector",)) or "")
        == sector_key
    ] if sector_key else []
    sector_size = len(sector_results)
    universe_rank = universe_ranks.get(ticker)
    sector_rank = sector_ranks.get(ticker)
    return {
        "universe_rank": format_metric(universe_rank, digits=0),
        "universe_size": format_metric(universe_size, digits=0),
        "universe_percentile": percentile_from_rank(universe_rank, universe_size),
        "sector_rank": format_metric(sector_rank, digits=0),
        "sector_size": format_metric(sector_size, digits=0),
        "sector_percentile": percentile_from_rank(sector_rank, sector_size),
    }


def find_result(payload: dict[str, Any], ticker: Optional[str]) -> dict[str, Any]:
    results = [row for row in payload.get("results", []) if isinstance(row, dict)]
    if ticker:
        for result in results:
            if str(result.get("ticker") or "").upper() == ticker.upper():
                return result
    final_pick = payload.get("finalPick")
    if isinstance(final_pick, dict):
        return final_pick
    if results:
        return results[0]
    raise ValueError("No scan results available to export.")


def build_report_context(payload: dict[str, Any], ticker: Optional[str] = None) -> dict[str, Any]:
    if not payload.get("ok"):
        raise ValueError(payload.get("message") or "Scan payload is not exportable.")
    result = find_result(payload, ticker)
    ticker_value = str(result.get("ticker") or ticker or "UNKNOWN").upper()
    direction = result.get("directionBreakdown") if isinstance(result.get("directionBreakdown"), dict) else {}
    explanation = result.get("explanation") if isinstance(result.get("explanation"), dict) else {}
    peer = extract_peer_metrics(result)
    return {
        "brand": "SCOUT GATE SANDBOX",
        "report_title": "Scoring Breakdown Report",
        "ticker": ticker_value,
        "scan_session_id": resolve_scan_session_id(payload),
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "run_timestamp": display_timestamp(payload),
        "universe_mode": str(payload.get("universeMode") or "custom"),
        "pick_mode": str(payload.get("pickMode") or "gate_runner"),
        "candidates": ", ".join(str(item) for item in payload.get("candidates") or []),
        "final_score": final_score_for_result(result),
        "direction": str(direction.get("direction") or result.get("direction") or "n/a"),
        "sector": str(result.get("sector") or "n/a"),
        "price": format_metric(result.get("price"), digits=2),
        "passed_all_gates": "YES" if result.get("passedAllGates") else "NO",
        "bull_conviction": format_metric(direction.get("bullConviction"), digits=0),
        "bear_conviction": format_metric(direction.get("bearConviction"), digits=0),
        "net_directional_edge": format_metric(direction.get("netDirectionalEdge"), digits=0),
        "peer": peer,
        "percentiles": build_percentile_metrics(payload, ticker_value),
        "gate_rows": extract_gate_rows(result),
        "explanation_summary": str(explanation.get("summary") or "").strip() or "No summary returned.",
        "explanation_status": str(explanation.get("status") or "n/a"),
        "score_interpretation": str(direction.get("scoreInterpretation") or "").strip(),
        "metrics_pending_note": (
            "Peer Risk-Adjusted Edge, Sharpe ratio, and t-stat will populate when the peer scoring layer is enabled. "
            "Percentile rankings below are computed from this scan universe only."
            if peer["metrics_pending"]
            else "Percentile rankings are computed from this scan universe only."
        ),
    }


def render_report_html(context: dict[str, Any]) -> str:
    def esc(value: Any) -> str:
        return html.escape(str(value if value is not None else ""))

    gate_rows = "".join(
        f"""
        <tr>
          <td class="num">{esc(row.get('index'))}</td>
          <td class="mono">{esc(row.get('code'))}</td>
          <td>{esc(row.get('name'))}</td>
          <td class="status {('pass' if row.get('passed') else 'fail')}">{esc(row.get('status'))}</td>
          <td class="num mono">{esc(row.get('score'))}</td>
          <td class="detail">{esc(row.get('actual_value') or row.get('explanation') or '—')}</td>
        </tr>
        """
        for row in context["gate_rows"]
    )
    edge_rows = "".join(
        f"<tr><td>{esc(item['label'])}</td><td class='num mono'>{esc(item['value'])}</td></tr>"
        for item in context["peer"]["edge_components"]
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{esc(context['ticker'])} — {esc(context['report_title'])}</title>
  <style>
    @page {{ size: letter; margin: 0.55in 0.65in; }}
    :root {{
      --bg: #f4f6f8;
      --ink: #0b1118;
      --muted: #5f6f82;
      --line: #c9d3df;
      --panel: #ffffff;
      --header: #0a111a;
      --accent: #1f6feb;
      --pass: #0f7a45;
      --fail: #b42318;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: "Helvetica Neue", Helvetica, Arial, sans-serif;
      color: var(--ink);
      background: var(--bg);
      font-size: 9.5pt;
      line-height: 1.4;
    }}
    .sheet {{
      background: var(--panel);
      border: 1px solid var(--line);
    }}
    .topbar {{
      background: linear-gradient(180deg, #101a27 0%, var(--header) 100%);
      color: #eef3f8;
      padding: 18px 22px 16px;
      border-bottom: 3px solid var(--accent);
    }}
    .brand {{
      font-size: 8pt;
      letter-spacing: 0.22em;
      text-transform: uppercase;
      color: #8ea4bd;
      margin-bottom: 6px;
    }}
    h1 {{
      margin: 0;
      font-size: 20pt;
      font-weight: 700;
      letter-spacing: -0.02em;
    }}
    .ticker {{
      font-family: "SF Mono", Menlo, Consolas, monospace;
      color: #9ec5ff;
      font-size: 13pt;
      margin-top: 4px;
    }}
    .meta-grid {{
      display: grid;
      grid-template-columns: repeat(4, 1fr);
      gap: 8px;
      padding: 14px 22px 0;
      color: #d7e2ee;
      font-size: 8.5pt;
    }}
    .meta-grid div span {{
      display: block;
      color: #8ea4bd;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      font-size: 7pt;
      margin-bottom: 2px;
    }}
    .content {{ padding: 16px 22px 22px; }}
    .kpi-row {{
      display: grid;
      grid-template-columns: repeat(5, 1fr);
      gap: 10px;
      margin-bottom: 16px;
    }}
    .kpi {{
      border: 1px solid var(--line);
      background: #f8fafc;
      padding: 10px 12px;
    }}
    .kpi .label {{
      color: var(--muted);
      font-size: 7.5pt;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      margin-bottom: 4px;
    }}
    .kpi .value {{
      font-family: "SF Mono", Menlo, Consolas, monospace;
      font-size: 14pt;
      font-weight: 700;
    }}
    h2 {{
      margin: 18px 0 8px;
      font-size: 10.5pt;
      text-transform: uppercase;
      letter-spacing: 0.12em;
      color: #223044;
      border-bottom: 1px solid var(--line);
      padding-bottom: 4px;
      page-break-after: avoid;
    }}
    .note {{
      color: var(--muted);
      font-size: 8.5pt;
      margin: 0 0 10px;
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
      margin-bottom: 8px;
      page-break-inside: avoid;
    }}
    th, td {{
      border: 1px solid var(--line);
      padding: 6px 8px;
      text-align: left;
      vertical-align: top;
    }}
    th {{
      background: #e9eef3;
      font-size: 8pt;
      text-transform: uppercase;
      letter-spacing: 0.06em;
      color: #314559;
    }}
    .mono {{ font-family: "SF Mono", Menlo, Consolas, monospace; }}
    .num {{ text-align: right; white-space: nowrap; }}
    .status.pass {{ color: var(--pass); font-weight: 700; }}
    .status.fail {{ color: var(--fail); font-weight: 700; }}
    .detail {{ font-size: 8.5pt; color: #2f3f52; }}
    .two-col {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: 12px;
    }}
    .explanation-box {{
      border: 1px solid var(--line);
      background: #f8fafc;
      padding: 12px 14px;
      min-height: 72px;
    }}
    .footer {{
      margin-top: 14px;
      padding-top: 10px;
      border-top: 1px solid var(--line);
      color: var(--muted);
      font-size: 8pt;
      display: flex;
      justify-content: space-between;
      gap: 12px;
    }}
    .page-break {{ page-break-before: always; }}
  </style>
</head>
<body>
  <div class="sheet">
    <header class="topbar">
      <div class="brand">{esc(context['brand'])}</div>
      <h1>{esc(context['report_title'])}</h1>
      <div class="ticker">{esc(context['ticker'])}</div>
      <div class="meta-grid">
        <div><span>Scan Session</span>{esc(context['scan_session_id'])}</div>
        <div><span>Run Timestamp</span>{esc(context['run_timestamp'])}</div>
        <div><span>Generated</span>{esc(context['generated_at'])}</div>
        <div><span>Run Mode</span>{esc(context['pick_mode'])}</div>
      </div>
    </header>

    <div class="content">
      <div class="kpi-row">
        <div class="kpi"><div class="label">Final Score</div><div class="value">{esc(context['final_score']['display'])}</div></div>
        <div class="kpi"><div class="label">Base Score</div><div class="value">{esc(context['final_score']['base'])}</div></div>
        <div class="kpi"><div class="label">Peer Risk-Adj Edge</div><div class="value">{esc(context['peer']['peer_risk_adjusted_edge'])}</div></div>
        <div class="kpi"><div class="label">Sharpe Ratio</div><div class="value">{esc(context['peer']['sharpe_ratio'])}</div></div>
        <div class="kpi"><div class="label">t-stat</div><div class="value">{esc(context['peer']['t_stat'])}</div></div>
      </div>

      <p class="note">{esc(context['metrics_pending_note'])}</p>

      <div class="two-col">
        <section>
          <h2>Percentile Rankings</h2>
          <table>
            <thead><tr><th>Scope</th><th>Rank</th><th>Universe</th><th>Percentile</th></tr></thead>
            <tbody>
              <tr>
                <td>Universe</td>
                <td class="num mono">{esc(context['percentiles']['universe_rank'])}</td>
                <td class="num mono">{esc(context['percentiles']['universe_size'])}</td>
                <td class="num mono">{esc(context['percentiles']['universe_percentile'])}</td>
              </tr>
              <tr>
                <td>Sector</td>
                <td class="num mono">{esc(context['percentiles']['sector_rank'])}</td>
                <td class="num mono">{esc(context['percentiles']['sector_size'])}</td>
                <td class="num mono">{esc(context['percentiles']['sector_percentile'])}</td>
              </tr>
            </tbody>
          </table>
        </section>
        <section>
          <h2>Position Context</h2>
          <table>
            <tbody>
              <tr><td>Direction</td><td class="mono">{esc(context['direction'])}</td></tr>
              <tr><td>Sector</td><td>{esc(context['sector'])}</td></tr>
              <tr><td>Price</td><td class="num mono">{esc(context['price'])}</td></tr>
              <tr><td>All Gates Passed</td><td class="mono">{esc(context['passed_all_gates'])}</td></tr>
              <tr><td>Bull / Bear / Net</td><td class="mono">{esc(context['bull_conviction'])} / {esc(context['bear_conviction'])} / {esc(context['net_directional_edge'])}</td></tr>
            </tbody>
          </table>
        </section>
      </div>

      <h2>Peer Risk-Adjusted Edge Breakdown</h2>
      <table>
        <thead><tr><th>Component</th><th>Value</th></tr></thead>
        <tbody>{edge_rows}</tbody>
      </table>

      <h2 class="page-break">Gate Scores</h2>
      <table>
        <thead>
          <tr><th>#</th><th>Code</th><th>Gate</th><th>Status</th><th>Score</th><th>Detail / Explanation</th></tr>
        </thead>
        <tbody>{gate_rows or '<tr><td colspan="6">No gate data returned.</td></tr>'}</tbody>
      </table>

      <h2>Explanation</h2>
      <div class="explanation-box">
        <div><strong>Status:</strong> {esc(context['explanation_status'])}</div>
        <p>{esc(context['explanation_summary'])}</p>
        {f"<p><strong>Direction read:</strong> {esc(context['score_interpretation'])}</p>" if context.get('score_interpretation') else ""}
      </div>

      <div class="footer">
        <span>Session {esc(context['scan_session_id'])} · Universe: {esc(context['candidates'])}</span>
        <span>Confidential — Scout Horizon-1 internal</span>
      </div>
    </div>
  </div>
</body>
</html>"""


def build_pdf_filename(context: dict[str, Any]) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    return f"scout-scoring-{slugify(context['ticker'])}-{slugify(context['scan_session_id'])}-{stamp}.pdf"


def export_report_json(scan_payload: dict[str, Any], ticker: Optional[str] = None) -> str:
    return json.dumps(build_report_context(scan_payload, ticker=ticker), indent=2)
