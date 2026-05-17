#!/usr/bin/env python3
"""Sandbox CLI runner for Scout's deployed gate engine.

This file intentionally stays outside the production app path. The repository
does not include the Cloud Function source, so the safest way to reuse the
existing gate logic is to call the same read-only single-ticker endpoint used by
the frontend scanner.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Tuple


DEFAULT_API_URL = "https://us-central1-scout-493918.cloudfunctions.net/friday-scout"
DEFAULT_CANDIDATES = [
    "AAPL",
    "MSFT",
    "NVDA",
    "AMZN",
    "GOOGL",
    "META",
    "TSLA",
    "AVGO",
    "NFLX",
    "NOW",
    "AMD",
    "CRM",
    "COST",
    "LLY",
    "JPM",
]

GATES = [
    ("sentinel", "SENTINEL", "Market Filter"),
    ("atlas", "ATLAS", "Core Strength"),
    ("oracle", "ORACLE", "Forward Vision"),
    ("phantom", "PHANTOM", "Smart Money"),
    ("catalyst", "CATALYST", "Event Trigger"),
    ("specter", "SPECTER", "Threat Scan"),
    ("meridian", "MERIDIAN", "Sector Wind"),
    ("aegis", "AEGIS", "Earnings Shield"),
    ("compass", "COMPASS", "Trend Lock"),
    ("pulse", "PULSE", "Volatility Read"),
    ("signal", "SIGNAL", "Intel Feed"),
    ("current", "CURRENT", "Flow Analysis"),
    ("archer", "ARCHER", "Strategy Select"),
    ("fortress", "FORTRESS", "Risk Gate"),
]


@dataclass(frozen=True)
class CandidateResult:
    ticker: str
    data: dict[str, Any]

    @property
    def score(self) -> float:
        value = self.data.get("scout_score")
        return float(value) if isinstance(value, (int, float)) else 0.0

    @property
    def gates(self) -> dict[str, Any]:
        gates = self.data.get("gates")
        return gates if isinstance(gates, dict) else {}

    @property
    def first_failed_gate(self) -> Optional[Tuple[int, str, str]]:
        for index, (key, code, name) in enumerate(GATES, start=1):
            if self.gates.get(key) is False:
                return index, code, name
        return None

    @property
    def passed_all_gates(self) -> bool:
        return all(self.gates.get(key) is True for key, _, _ in GATES)


def repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def load_env_file(path: Path) -> None:
    if not path.exists():
        return

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value


def load_env() -> None:
    root = repo_root()
    load_env_file(root / ".env")
    load_env_file(root / ".env.local")
    load_env_file(root / "scout-gates-sandbox" / ".env")


def parse_ticker_list(raw: str) -> list[str]:
    seen: set[str] = set()
    tickers: list[str] = []
    for item in raw.replace("\n", ",").split(","):
        ticker = item.strip().upper()
        if ticker and ticker not in seen:
            seen.add(ticker)
            tickers.append(ticker)
    return tickers


def load_candidates(args: argparse.Namespace) -> list[str]:
    if args.tickers:
        return parse_ticker_list(args.tickers)

    for env_key in ("SCOUT_CANDIDATES", "SCOUT_CANDIDATE_TICKERS", "SCOUT_TICKERS"):
        env_value = os.environ.get(env_key)
        if env_value:
            return parse_ticker_list(env_value)

    candidates_file = Path(args.candidates_file)
    if candidates_file.exists():
        return parse_ticker_list(candidates_file.read_text(encoding="utf-8"))

    return DEFAULT_CANDIDATES


def gate_api_url() -> str:
    return (
        os.environ.get("SCOUT_GATE_API_URL")
        or os.environ.get("SCOUT_API_URL")
        or DEFAULT_API_URL
    ).rstrip("/")


def fetch_gate_result(api_url: str, ticker: str, timeout: float) -> CandidateResult:
    query = urllib.parse.urlencode(
        {"mode": "single", "ticker": ticker, "format": "json"}
    )
    request = urllib.request.Request(
        f"{api_url}?{query}",
        headers={"Accept": "application/json", "User-Agent": "scout-gates-sandbox/1.0"},
    )

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"{ticker}: HTTP {exc.code} from gate API: {body[:300]}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"{ticker}: could not reach gate API: {exc.reason}") from exc

    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{ticker}: gate API returned non-JSON response") from exc

    if not isinstance(payload, dict):
        raise RuntimeError(f"{ticker}: gate API returned unexpected payload")

    return CandidateResult(ticker=ticker, data=payload)


def choose_final_pick(results: list[CandidateResult]) -> CandidateResult:
    passing = [result for result in results if result.passed_all_gates]
    pool = passing or results
    return max(pool, key=lambda result: result.score)


def format_money(value: Any) -> str:
    return f"${value:.2f}" if isinstance(value, (int, float)) else "n/a"


def print_summary(results: list[CandidateResult], winner: CandidateResult) -> None:
    print("\nFINAL PICK")
    print("-" * 60)
    print(f"Ticker:      {winner.data.get('ticker', winner.ticker)}")
    print(f"Scout score: {winner.score:.0f}")
    print(f"Price:       {format_money(winner.data.get('price'))}")
    print(f"Direction:   {winner.data.get('direction', 'n/a')}")
    print(f"Trend:       {winner.data.get('trend', 'n/a')}")
    print(f"Sector:      {winner.data.get('sector', 'n/a')}")
    if not winner.passed_all_gates:
        failed = winner.first_failed_gate
        if failed:
            _, code, name = failed
            print(f"Note:        No candidate passed every gate; winner failed {code} ({name}).")

    print("\nWINNING TICKER GATES")
    print("-" * 60)
    for index, (key, code, name) in enumerate(GATES, start=1):
        status = "PASS" if winner.gates.get(key) is True else "FAIL"
        print(f"{index:>2}. {code:<9} {status:<4}  {name}")

    print("\nREJECTED CANDIDATES")
    print("-" * 60)
    rejected = [result for result in results if result.ticker != winner.ticker]
    if not rejected:
        print("None")
        return

    for result in sorted(rejected, key=lambda item: item.score, reverse=True):
        failed = result.first_failed_gate
        if failed:
            index, code, name = failed
            reason = f"failed Gate {index} {code} ({name})"
        else:
            reason = "passed gates but scored below final pick"
        print(f"{result.ticker:<6} score={result.score:>3.0f}  {reason}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run Scout gates on demand using the deployed gate engine."
    )
    parser.add_argument(
        "--tickers",
        help="Comma-separated ticker universe. Defaults to env/candidates file/fallback list.",
    )
    parser.add_argument(
        "--candidates-file",
        default=str(repo_root() / "scout-gates-sandbox" / "candidates.txt"),
        help="Optional newline or comma separated candidate file.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=25.0,
        help="Seconds to wait for each ticker scan.",
    )
    return parser


def main() -> int:
    load_env()
    args = build_parser().parse_args()
    candidates = load_candidates(args)
    if not candidates:
        print("No candidate tickers found.", file=sys.stderr)
        return 2

    api_url = gate_api_url()
    print(f"Scout gate API: {api_url}")
    print(f"Candidate universe ({len(candidates)}): {', '.join(candidates)}")

    results: list[CandidateResult] = []
    failures: list[str] = []

    for ticker in candidates:
        print(f"Running gates for {ticker}...", flush=True)
        try:
            results.append(fetch_gate_result(api_url, ticker, args.timeout))
        except RuntimeError as exc:
            failures.append(str(exc))

    if not results:
        print("\nNo ticker scans completed successfully.", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1

    winner = choose_final_pick(results)
    print_summary(results, winner)

    if failures:
        print("\nSCAN ERRORS")
        print("-" * 60)
        for failure in failures:
            print(f"- {failure}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
