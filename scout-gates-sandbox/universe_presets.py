#!/usr/bin/env python3
"""Versioned universe preset manifests for Horizon-1 statistical diversification."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

PRESETS_DIR = Path(__file__).resolve().parent / "universe_presets"
MANIFEST_PATH = PRESETS_DIR / "manifest.json"

CUSTOM_PRESET: dict[str, Any] = {
    "id": "custom",
    "label": "Custom",
    "version": None,
    "cohortClass": "research",
    "scanPurpose": "manual_research",
    "sectorHint": None,
    "tags": ["RESEARCH"],
    "tickers": [],
    "note": "Custom universe uses the tickers you enter below.",
}


@lru_cache(maxsize=1)
def load_manifest() -> dict[str, Any]:
    if not MANIFEST_PATH.is_file():
        raise FileNotFoundError(f"Universe manifest not found: {MANIFEST_PATH}")
    with MANIFEST_PATH.open(encoding="utf-8") as handle:
        data = json.load(handle)
    if not isinstance(data, dict):
        raise ValueError("Universe manifest must be a JSON object.")
    presets = data.get("presets")
    if not isinstance(presets, dict):
        raise ValueError("Universe manifest must include a presets object.")
    return data


def list_preset_catalog() -> dict[str, Any]:
    """Return manifest metadata and UI-safe preset entries."""
    manifest = load_manifest()
    presets: dict[str, Any] = {}
    for preset_id, preset in manifest["presets"].items():
        if not isinstance(preset, dict):
            continue
        tickers = [str(ticker).upper() for ticker in preset.get("tickers") or []]
        presets[preset_id] = {
            "id": preset_id,
            "label": preset.get("label") or preset_id,
            "version": preset.get("version"),
            "cohortClass": preset.get("cohortClass"),
            "scanPurpose": preset.get("scanPurpose"),
            "sectorHint": preset.get("sectorHint"),
            "tags": list(preset.get("tags") or []),
            "tickers": tickers,
            "tickerCount": len(tickers),
            "note": preset.get("note") or "",
        }
    presets["custom"] = dict(CUSTOM_PRESET)
    presets["custom"]["tickerCount"] = 0
    from run_gates import DEFAULT_CANDIDATES

    return {
        "manifestVersion": manifest.get("manifestVersion"),
        "maxTickers": manifest.get("maxTickers", 12),
        "recommendedTimeoutSec": manifest.get("recommendedTimeoutSec", 25),
        "fallbackTickers": list(DEFAULT_CANDIDATES),
        "presets": presets,
    }


def resolve_preset(preset_id: Optional[str]) -> dict[str, Any]:
    """Resolve a preset id to metadata and tickers. Unknown ids fall back to custom."""
    normalized = str(preset_id or "custom").strip()
    if not normalized or normalized == "custom":
        return dict(CUSTOM_PRESET)
    manifest = load_manifest()
    preset = manifest["presets"].get(normalized)
    if not isinstance(preset, dict):
        return dict(CUSTOM_PRESET)
    tickers = [str(ticker).upper() for ticker in preset.get("tickers") or []]
    return {
        "id": normalized,
        "label": preset.get("label") or normalized,
        "version": preset.get("version") or manifest.get("manifestVersion"),
        "cohortClass": preset.get("cohortClass") or "research",
        "scanPurpose": preset.get("scanPurpose") or "manual_research",
        "sectorHint": preset.get("sectorHint"),
        "tags": list(preset.get("tags") or []),
        "tickers": tickers,
        "note": preset.get("note") or "",
        "manifestVersion": manifest.get("manifestVersion"),
        "maxTickers": manifest.get("maxTickers", 12),
    }


def format_ticker_list(tickers: list[str]) -> str:
    return ", ".join(tickers)


def cohort_metadata_from_request(request: dict[str, Any]) -> dict[str, Any]:
    """Build persisted cohort fields from a dashboard run/save request."""
    preset_id = str(request.get("universePresetId") or "custom").strip() or "custom"
    preset = resolve_preset(preset_id)
    scan_purpose = request.get("scanPurpose") or preset.get("scanPurpose")
    cohort_class = request.get("cohortClass") or preset.get("cohortClass")
    return {
        "universePresetId": preset_id,
        "universePresetLabel": request.get("universePresetLabel") or preset.get("label"),
        "universePresetVersion": request.get("universePresetVersion")
        or preset.get("version")
        or preset.get("manifestVersion"),
        "scanPurpose": str(scan_purpose or "manual_research"),
        "cohortClass": str(cohort_class or "research"),
        "sectorHint": preset.get("sectorHint"),
        "manifestVersion": preset.get("manifestVersion"),
    }


def resolve_universe_from_request(request: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """
    Resolve ticker list and cohort metadata without changing gate/score behavior.

    Uses explicit tickers when provided; otherwise the built-in fallback list
    or manifest tickers for preset runs.
    """
    universe_mode = str(request.get("universeMode") or "custom")
    raw_tickers = str(request.get("tickers") or "")
    from run_gates import DEFAULT_CANDIDATES, parse_ticker_list

    cohort = cohort_metadata_from_request(request)
    preset_id = cohort["universePresetId"]

    parsed = parse_ticker_list(raw_tickers)
    if parsed:
        return parsed, cohort

    if universe_mode == "fallback":
        return list(DEFAULT_CANDIDATES), cohort

    preset = resolve_preset(preset_id)
    if preset_id != "custom" and preset.get("tickers"):
        return list(preset["tickers"]), cohort

    return [], cohort
