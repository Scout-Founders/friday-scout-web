# Stable Signal S2 — Explainability Layer (Plan Only)

**Status:** S2a implemented (`stable_signal_explainability.py` + tests); S2b attachment not wired  
**Prerequisite:** S1 implemented (`stable_signal_layers.py`, `stableSignal` on `serialize_result`)  
**Goal:** Make Horizon-1 outputs more understandable via **additive metadata** under `stableSignal.explainability`  
**Parent:** [stable-signal-state-v1.md](./stable-signal-state-v1.md)

---

## Table of Contents

1. [Scope and non-goals](#1-scope-and-non-goals)
2. [Placement in the signal stack](#2-placement-in-the-signal-stack)
3. [Recommended JSON shape](#3-recommended-json-shape)
4. [Field specifications](#4-field-specifications)
5. [Safe derivation vs placeholders](#5-safe-derivation-vs-placeholders)
6. [Risk areas (accidental scoring/ranking mutation)](#6-risk-areas-accidental-scoringranking-mutation)
7. [S2 implementation plan (small steps)](#7-s2-implementation-plan-small-steps)
8. [Testing strategy](#8-testing-strategy)
9. [Reversibility and versioning](#9-reversibility-and-versioning)
10. [What not to do in S2](#10-what-not-to-do-in-s2)

---

## 1. Scope and non-goals

### S2 does

- Add `stableSignal.explainability` to each serialized ticker in `/api/run` (and save payloads that copy serialize output).
- Summarize **existing** S1 layers, gates, overlays, and scan context in operator-friendly language.
- Use **qualitative** strength labels (strong / neutral / weak / unknown) — not new numeric scores.
- Copy or reference S1 `redundancyFlags`; add **warningFlags** for interpretive cautions only.

### S2 does not

| Constraint | S2 compliance |
|------------|----------------|
| No scoring changes | Explainability is read-only over serialized payload |
| No ranking changes | `rankingExplanation` describes pick logic; never recomputes winner |
| No new indicators | No new API fields or studies |
| No gate restructuring | 14 gates unchanged; gate text not re-parsed for scoring |
| No filtering changes | No SQL / history filter impact |
| No memory influence | Optional persist of JSON blob only; no analytics reads feeding `/api/run` |
| No Horizon-Flow | No orchestration hooks (reserved keys only) |
| Metadata only | `tier: "explainability"` on every derived field |

---

## 2. Placement in the signal stack

S1 stack (unchanged order of authority):

```
1. score (scout_score)           ← ONLY ranking input
2. passedAllGates + gates[]
3. stableSignal.layers           ← S1
4. directionBreakdown / EI / PRAE
5. stableSignal.explainability   ← S2 (NEW, lowest authority)
```

**Module boundary (planned):** `stable_signal_explainability.py`  
- Input: `CandidateResult` **or** post-serialize dict + optional `ScanExplainContext`  
- Output: `explainability` dict only  
- Called from `build_stable_signal()` **or** `attach_stable_signal()` after layers are built — **never** from `choose_final_pick`, `build_run_payload` winner path, or `memory_store` scoring helpers.

---

## 3. Recommended JSON shape

Attach under existing `stableSignal` (version stays `"1"`; explainability has its own sub-version).

```json
{
  "stableSignal": {
    "version": "1",
    "rankingScoreField": "scout_score",
    "rankingScore": 89,
    "rankingTier": "authoritative",
    "overlayTier": "shadow",
    "layers": { "...": "S1 unchanged" },
    "redundancyFlags": ["rsi_multi_use", "change_multi_use"],
    "gateLayerMap": { "...": "S1 unchanged" },
    "explainability": {
      "version": "1",
      "tier": "explainability",
      "generatedAt": "2026-05-21T18:00:00+00:00",
      "rankingExplanation": {
        "rankingScoreField": "scout_score",
        "rankingScore": 89,
        "passedAllGates": true,
        "pickRole": "final_pick",
        "universeRankByScore": 4,
        "universeSize": 15,
        "passingPoolRankByScore": 1,
        "passingPoolSize": 1,
        "summary": "MSFT is the gate-runner pick: highest scout_score (89) among 1 ticker(s) that passed all 14 gates."
      },
      "strongestLayer": "fundamentalQuality",
      "weakestLayer": "breadth",
      "layerStrengths": {
        "momentum": { "band": "strong", "gatePassRate": 1.0, "primaryPresent": true, "notes": [] },
        "risk": { "band": "strong", "gatePassRate": 1.0, "primaryPresent": true, "notes": [] },
        "liquidity": { "band": "neutral", "gatePassRate": 1.0, "primaryPresent": true, "notes": [] },
        "volatility": { "band": "weak", "gatePassRate": 0.0, "primaryPresent": true, "notes": ["pulse gate failed"] },
        "fundamentalQuality": { "band": "strong", "gatePassRate": 1.0, "primaryPresent": true, "notes": [] },
        "regime": { "band": "neutral", "gatePassRate": 1.0, "primaryPresent": true, "notes": [] },
        "breadth": { "band": "unknown", "gatePassRate": 1.0, "primaryPresent": false, "notes": ["breadth_score unavailable; using spy/qqq fallback absent"] }
      },
      "confidenceDrivers": [
        { "layer": "fundamentalQuality", "source": "primary", "label": "Piotroski 7, Z-score 2.4", "gate": "atlas", "passed": true }
      ],
      "penaltyDrivers": [
        { "layer": "volatility", "source": "gate", "label": "PULSE gate failed", "gate": "pulse", "passed": false }
      ],
      "warningFlags": [
        { "code": "redundancy_rsi_multi_use", "severity": "info", "message": "RSI appears in multiple overlay paths (shadow only)." }
      ],
      "redundancyFlags": ["rsi_multi_use", "change_multi_use"],
      "regimeContext": {
        "sector": "Technology",
        "wind": 1.5,
        "primaryGate": "meridian",
        "meridianPassed": true,
        "riskRegime": null,
        "summary": "Sector wind is positive; MERIDIAN passed."
      },
      "breadthContext": {
        "breadthScore": null,
        "spyTrend": null,
        "qqqTrend": null,
        "currentGatePassed": true,
        "dataQuality": "partial",
        "summary": "Market breadth fields were not returned; CURRENT gate still passed."
      },
      "humanSummary": "MSFT scores 89 on scout_score and passed all 14 gates, making it the scan winner under gate-runner mode. Fundamental quality and risk layers look strongest on available data; breadth data was incomplete.",
      "overlays": {
        "directionBreakdown": { "available": true, "netDirectionalEdge": 12, "tier": "shadow" },
        "earningsIntelligence": { "available": true, "active": true, "mode": "active", "tier": "shadow" },
        "peerRiskAdjustedEdge": { "available": true, "active": false, "mode": "partial_features", "tier": "shadow" }
      },
      "placeholders": {
        "horizonFlow": null,
        "layerNumericScores": null,
        "outcomeCalibratedConfidence": null
      }
    }
  }
}
```

### Minimal required keys (S2 MVP)

| Key | Required | Purpose |
|-----|----------|---------|
| `explainability.version` | Yes | Sub-schema versioning |
| `explainability.tier` | Yes | Always `"explainability"` |
| `rankingExplanation` | Yes | Tie to scout_score + pick rules |
| `layerStrengths` | Yes | Seven layers, qualitative bands |
| `strongestLayer` / `weakestLayer` | Yes | Derived from `layerStrengths` |
| `humanSummary` | Yes | One paragraph operator read |
| `redundancyFlags` | Yes | Copy from S1 (single source of truth) |
| `confidenceDrivers` / `penaltyDrivers` | Yes | May be empty arrays |
| `warningFlags` | Yes | May be empty arrays |
| `regimeContext` / `breadthContext` | Yes | May be sparse |
| `overlays` | Recommended | Pointer summary only |
| `placeholders` | Recommended | Explicit deferred fields |

---

## 4. Field specifications

### 4.1 `rankingExplanation`

**Purpose:** Answer “why is this ticker the pick (or not)?” without recomputing.

| Subfield | Type | Semantics |
|----------|------|-----------|
| `rankingScoreField` | string | Always `"scout_score"` |
| `rankingScore` | number | Copy `stableSignal.rankingScore` |
| `passedAllGates` | boolean | Copy serialized `passedAllGates` |
| `pickRole` | enum | `"final_pick"` \| `"rejected"` \| `"also_ran"` \| `"not_in_winner_slot"` |
| `universeRankByScore` | int \| null | 1 = highest scout_score in scan (dense rank) |
| `universeSize` | int | Tickers in successful scan results |
| `passingPoolRankByScore` | int \| null | Rank among tickers with all gates true |
| `passingPoolSize` | int | Count with all gates true |
| `summary` | string | Templated English sentence |

**Pick role assignment (metadata only):**

- Set at serialize time when `ScanExplainContext` includes `finalPickTicker` and list of result scores.
- `build_run_payload` already knows winner vs rejected — pass context into explainability builder.

**Safe template examples:**

- Final pick, gate_runner:  
  `"{ticker} is the gate-runner pick: highest scout_score ({score}) among {passingPoolSize} ticker(s) that passed all 14 gates."`
- High score, failed gates:  
  `"{ticker} has scout_score {score} (rank {universeRankByScore}/{universeSize}) but did not pass all gates; first failure: {gateName}."`
- Score-only mode (if ever labeled in context):  
  `"{ticker} leads by scout_score ({score}) in score-only mode (gate filter not applied to pick)."`

---

### 4.2 `layerStrengths`

**Purpose:** Per-layer interpretive health — **not** a new composite score.

| Subfield per layer | Type | Derivation rule |
|--------------------|------|-----------------|
| `band` | enum | `"strong"` \| `"neutral"` \| `"weak"` \| `"unknown"` |
| `gatePassRate` | float 0–1 | Fraction of `stableSignal.layers[layer].gates` with `passed === true` |
| `primaryPresent` | boolean | Any primary `values` entry non-null |
| `notes` | string[] | Human reasons (failed gate name, missing primary field) |

**Band rules (S2 — deterministic, non-numeric):**

```
if not primaryPresent → unknown
elif gatePassRate < 1.0 for layer → weak   # any layer gate failed
elif layer has failed primary anchor gate → weak
else → strong

neutral → optional: primaryPresent && gatePassRate === 1.0 but shadowValues show redundancy or warning
```

Do **not** compute band from `rsi`, `change`, or direction weights.

**`strongestLayer` / `weakestLayer`:**

- `strongestLayer`: layer with `band === "strong"` and highest `gatePassRate`; tie-break by fixed layer priority order (fundamentalQuality → momentum → risk → …).
- `weakestLayer`: layer with `band === "weak"` or `"unknown"`; prefer `weak` over `unknown`.
- If all strong: `weakestLayer` = layer with most `notes` or fewest primary values.

---

### 4.3 `confidenceDrivers`

**Purpose:** Positive explainability bullets (why operator might trust the read).

**Safe sources (read-only):**

| Source | Example label |
|--------|----------------|
| S1 `layers.*.primary.values` | `"Piotroski 7, Z-score 2.4"` |
| Gate pass on layer anchor | `"MERIDIAN passed with wind +1.5"` |
| `passedAllGates === true` | `"All 14 gates passed"` |
| `explanation.gates[].status === PASS` | First 3 PASS gate names (cap list length) |
| `directionBreakdown.bullConviction` vs bear | `"Bull conviction 72 vs bear 41"` — **label as directional overlay, not score** |

**Exclude from confidenceDrivers:**

- `adjustedScoutScore` as a driver (unless explicitly tagged `overlay, non-ranking`).
- `peerRiskAdjustedEdge` as confidence until P2+ policy defined.
- Parsed gate `score: N` text as numeric authority.

Each entry:

```json
{
  "layer": "fundamentalQuality",
  "source": "primary|gate|overlay|scan",
  "label": "human readable",
  "gate": "atlas",
  "passed": true,
  "tier": "explainability"
}
```

---

### 4.4 `penaltyDrivers`

**Purpose:** Negative explainability bullets (why caution is warranted).

**Safe sources:**

| Source | Example |
|--------|---------|
| `firstFailedGate` | `"Failed SPECTER (Threat Scan)"` |
| Any `gates[].passed === false` | Per-gate row |
| S1 layer `gates[].passed === false` | Layer-specific |
| `directionBreakdown` opposing signals | Top bear signal one-liner |
| `earningsIntelligence.mode` | `"awaiting_provider"`, `"pre_earnings"` |
| PRAE `scoringBreakdown.mode` | `"insufficient_peers"`, `"partial_features"` |
| Shadow-only conflicts | `"Daily change elevated while PULSE failed"` — template only, no new math |

---

### 4.5 `warningFlags`

**Purpose:** Machine-readable cautions (UI badges, PDF footnotes).

| Code (examples) | Severity | Trigger (existing data only) |
|-----------------|----------|------------------------------|
| `redundancy_*` | info | Copy from S1 `redundancyFlags` |
| `missing_breadth_primary` | info | breadth primary absent, fallback empty |
| `ei_inactive` | info | `earningsIntelligence.active === false` |
| `ei_pre_earnings` | warn | EI mode pre-earnings |
| `peer_partial` | info | PRAE `mode !== scored` |
| `high_score_gate_fail` | info | `universeRankByScore === 1` && `!passedAllGates` |
| `adjusted_score_display` | info | `adjustedScoutScore !== score` — remind non-ranking |

---

### 4.6 `redundancyFlags`

- **Copy** S1 root `stableSignal.redundancyFlags` into `explainability.redundancyFlags` for convenience.
- Do not re-detect differently in S2 (single detector stays in S1).

---

### 4.7 `regimeContext`

**Purpose:** Sector/macro read for Regime layer only.

| Field | Source |
|-------|--------|
| `sector` | `serialized.sector` or `layers.regime.primary.values.sector` |
| `wind` | `layers.regime.primary.values.wind` |
| `primaryGate` | `"meridian"` |
| `meridianPassed` | S1 layer gate entry |
| `riskRegime` | `layers.regime.shadowValues.risk_regime` |
| `spyTrend` / `qqqTrend` | shadowValues (regime shadow, not breadth primary) |
| `summary` | One sentence template |

---

### 4.8 `breadthContext`

**Purpose:** Market-wide participation read — separate from Regime.

| Field | Source |
|-------|--------|
| `breadthScore` | `layers.breadth.primary.values.breadth_score` |
| `spyTrend` / `qqqTrend` | primary fallback fields or shadow |
| `currentGatePassed` | `layers.breadth.gates` CURRENT entry |
| `dataQuality` | `"complete"` \| `"partial"` \| `"missing"` |
| `summary` | Template from availability |

**Do not** use `directionBreakdown.netDirectionalEdge` as breadth (S1 doc §6 fix alignment).

---

### 4.9 `humanSummary`

**Purpose:** Single paragraph for mobile/PDF/dashboard header.

**Composition order (template):**

1. Ticker + `rankingExplanation.summary` (one clause).
2. Strongest/weakest layer clause.
3. Optional: first penalty if not final pick.
4. Optional: one warning if `severity === warn`.

Max ~320 characters for mobile; full text allowed in PDF.

---

### 4.10 `overlays` (summary block)

Lightweight availability flags — **no duplication** of full `directionBreakdown` / EI / PRAE bodies.

| Overlay | Fields |
|---------|--------|
| `directionBreakdown` | `available`, `netDirectionalEdge`, `direction`, `tier: shadow` |
| `earningsIntelligence` | `available`, `active`, `mode`, `convictionAdjustment`, `tier: shadow` |
| `peerRiskAdjustedEdge` | `available`, `active`, `mode`, `peerRiskAdjustedEdge`, `tier: shadow` |

---

## 5. Safe derivation vs placeholders

### 5.1 Derivable in S2 from existing outputs only

| Field | Inputs |
|-------|--------|
| `rankingExplanation` | `score`, `passedAllGates`, `firstFailedGate`, `ScanExplainContext` (ranks, pickRole, pickMode) |
| `layerStrengths` | `stableSignal.layers.*` (gates, primary, shadowValues) |
| `strongestLayer` / `weakestLayer` | `layerStrengths` bands |
| `confidenceDrivers` | S1 primaries, gate passes, `explanation`, `directionBreakdown` (labeled overlay) |
| `penaltyDrivers` | failed gates, `firstFailedGate`, EI/PRAE modes |
| `warningFlags` | S1 redundancy + overlay modes + missing primaries |
| `redundancyFlags` | S1 copy |
| `regimeContext` | regime layer + sector field |
| `breadthContext` | breadth layer |
| `humanSummary` | templates over above |
| `overlays.*` | keys present on serialized dict |

### 5.2 Placeholders until later phases

| Field / capability | Phase | Reason |
|--------------------|-------|--------|
| `placeholders.layerNumericScores` | P2+ | Numeric layer scores could be mistaken for ranking |
| `placeholders.outcomeCalibratedConfidence` | P3+ | Needs outcome history / returns |
| `placeholders.horizonFlow` | HF-1 | Orchestration not wired |
| Peer edge in `confidenceDrivers` | P2 | Policy for non-ranking peer influence |
| Gate attribution `%` in drivers | S4+ | Post-save analytics only |
| Regime snapshot DB classifiers | S4+ | Do not read SQLite during `/api/run` |
| `sharpeRatio` / `tStat` narrative | P3 | Awaiting returns |
| Cross-scan percentile in rankingExplanation | S2.5 | Needs explicit scan bundle; optional context pass |
| Natural-language LLM summary | Never in S2 | Deterministic templates only |

---

## 6. Risk areas (accidental scoring/ranking mutation)

| Risk | How it happens | Mitigation |
|------|----------------|------------|
| **Replacing `score` with layer blend** | `layerStrengths` encoded as 0–100 numbers used in UI sort | Use `band` enum only; no numeric `layerScore` in S2 |
| **Winner from `strongestLayer`** | Dashboard sorts by explainability field | Never read explainability in `pick_winner` / `choose_final_pick` |
| **EI/PRAE in `rankingExplanation`** | Template says “adjusted score wins” | Templates hard-code `scout_score`; CI assertion |
| **Re-parsing gate text for new score** | confidenceDrivers call `extract_score` | Do not import attribution parsers in S2 |
| **Memory feedback loop** | save reads explainability to update ranks | Persist blob only; `build_rank_maps` still uses `score` |
| **Filter side effects** | history API filters on `strongestLayer` | No SQL/index on explainability in S2 |
| **Gate pass recompute** | layerStrengths calls CF or changes `gates[]` | Read serialized gates only; deep-copy inputs |
| **Scan context mutation** | `ScanExplainContext` modifies results list | Context is read-only snapshot at end of run |
| **Option picker influence** | confidence uses option greeks | Exclude option_pick from drivers unless `available` flag |
| **Test drift** | tests assert on `humanSummary` wording | Assert structure + keys; snapshot templates separately |

**Golden rule:** `build_stable_signal_explainability()` must accept only **immutable snapshots** (dict copy) and return a new dict subtree. No writes to `result.data`, `payload["score"]`, or gate booleans.

---

## 7. S2 implementation plan (small steps)

### S2a — Schema module (no dashboard wire) — **done**

- Add `stable_signal_explainability.py` with:
  - `ExplainabilityContext` dataclass (`final_pick_ticker`, `pick_mode`, `universe_scores`, `run_timestamp`)
  - `build_explainability(serialized: dict, stable_signal: dict, context: ExplainabilityContext | None) -> dict`
  - Pure functions per section (`build_ranking_explanation`, `build_layer_strengths`, …)
- Unit tests with fixture JSON from validation run (MSFT pick / NVDA high-score fail).

**Exit:** Tests pass; no change to `serialize_result` output yet.

---

### S2b — Wire into S1 attach path

- In `stable_signal_layers.py` (or `build_and_attach_stable_signal`):
  - After `layers` built, call `build_explainability` if `context` provided.
  - Default `context=None` → minimal explainability (`rankingExplanation` with `pickRole: "unknown"`, empty drivers).
- In `build_run_payload`:
  - After all results known, build `ExplainabilityContext` once.
  - Pass context into each `serialize_result` (new optional param) **or** post-process serialized rows in loop.

Prefer **post-process in `build_run_payload`** to avoid changing `serialize_result` signature twice:

```text
for row in all_serialized:
    row["stableSignal"]["explainability"] = build_explainability(row, row["stableSignal"], context)
```

**Exit:** `/api/run` includes `explainability`; scores/gates identical to S1-only run (diff test).

---

### S2c — Ranking explanation accuracy

- Implement dense ranks from `ExplainabilityContext.universe_scores`.
- Set `pickRole` correctly for `finalPick`, `rejected`, and `results` arrays.
- Template tests for gate_runner vs score_only.

**Exit:** Validation script asserts MSFT-style pick text matches `choose_final_pick`.

---

### S2d — Drivers and warnings

- Implement `confidenceDrivers`, `penaltyDrivers`, `warningFlags` with caps (max 8 entries each).
- Map S1 `redundancyFlags` → `warningFlags` entries.

**Exit:** No empty explainability on full fallback scan; runtime increase &lt; 50ms/ticker.

---

### S2e — UI/PDF read-only surfacing (optional)

- `dashboard.html`: collapsible “Explain” panel reading `stableSignal.explainability.humanSummary`.
- `reporting/scoring_breakdown.py`: optional footnote block (no scoring table changes).

**Exit:** Visual confirmation; still no ranking change.

---

### S2f — Documentation

- Update `stable-signal-state-v1.md` §10 phase table (S2 done).
- Add JSON schema appendix to this doc.

---

## 8. Testing strategy

| Test | Asserts |
|------|---------|
| `test_explainability_does_not_mutate_serialized` | Same `score`, `gates`, `passedAllGates` before/after attach |
| `test_ranking_explanation_final_pick` | `pickRole`, `passingPoolRankByScore` for known fixture |
| `test_high_score_failed_gates` | NVDA-like: rank 1, `passedAllGates false`, penalty driver present |
| `test_layer_strengths_seven_keys` | All layers present; bands in allowed enum |
| `test_no_numeric_layer_score_fields` | JSON schema forbids `layerScore`, `composite` |
| `test_choose_final_pick_unchanged` | With/without explainability build on same CandidateResults |
| Integration | Full fallback scan: explainability on every row; winner unchanged vs S1-only baseline |

---

## 9. Reversibility and versioning

| Mechanism | Detail |
|-----------|--------|
| **Feature flag** | `SCOUT_STABLE_SIGNAL_EXPLAINABILITY=0` skips `build_explainability` |
| **Sub-version** | `explainability.version = "1"` independent of `stableSignal.version` |
| **Removal** | Delete post-process loop in `build_run_payload`; S1 blob unchanged |
| **Save compatibility** | `raw_result_json` grows; old rows without explainability remain valid |

---

## 10. What not to do in S2

- Do not add `explainability.score` or `explainability.rank`.
- Do not sort tickers by `strongestLayer` or `layerStrengths.band`.
- Do not call `build_directional_breakdown` again inside explainability (use serialized copy).
- Do not fetch FMP or gate API inside explainability builder.
- Do not write explainability fields into `memory_store` filter WHERE clauses.
- Do not merge layers into a single “institutional score.”
- Do not change `STABLE_SIGNAL_VERSION` to `"2"` until a breaking schema change is intentional.

---

## Appendix A — `ScanExplainContext` (planned)

```python
@dataclass(frozen=True)
class ScanExplainContext:
    pick_mode: str                    # gate_runner | score_only
    final_pick_ticker: str
    universe: tuple[tuple[str, float, bool], ...]  # ticker, score, passed_all
    run_timestamp: str
```

Built once at end of `build_run_payload` from `results: list[CandidateResult]` before serialize.

---

## Appendix B — Example (validation run shape)

From fallback scan (gate_runner): NVDA `score=100` failed gates; MSFT `score=89` all passed → final pick.

| Ticker | universeRankByScore | passedAllGates | pickRole |
|--------|---------------------|----------------|----------|
| MSFT | 4 | true | final_pick |
| NVDA | 1 | false | also_ran |
| AMD | 2 | false | also_ran |

`rankingExplanation.summary` for NVDA should mention high score but gate failure; MSFT should state gate-runner pick among passers.

---

## Appendix C — Document history

| Date | Change |
|------|--------|
| 2026-05-21 | Initial S2 explainability plan (plan only) |

---

*S2 is explainability metadata only. Ranking remains `scout_score` + `choose_final_pick` + 14 gates, exactly as in S1.*
