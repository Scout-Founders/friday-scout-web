# Horizon-1 — Stable Signal State v1

**Status:** Architecture audit; **S1 implemented** (`stable_signal_layers.py` + `serialize_result`)  
**Goal:** Improve clarity, explainability, consistency, and modularity **without** changing the 14-gate architecture or adding gates  
**Constraint:** Preserve Cloud Function `scout_score`, gate pass/fail semantics, and `choose_final_pick` behavior  

**Related:** [Infrastructure Hardening](./infrastructure-hardening-plan.md) · [Peer Risk-Adjusted Edge](./peer-risk-adjusted-edge.md) · [Remote Access](./remote-access-plan.md)

---

## Table of Contents

1. [Scoring authority model](#1-scoring-authority-model)
2. [Institutional layer definitions](#2-institutional-layer-definitions)
3. [Full input inventory by layer](#3-full-input-inventory-by-layer)
4. [14-gate map to layers (unchanged gates)](#4-14-gate-map-to-layers-unchanged-gates)
5. [Sandbox overlay map](#5-sandbox-overlay-map)
6. [Redundancy and overlap matrix](#6-redundancy-and-overlap-matrix)
7. [Stable Signal State v1 — primary vs shadow](#7-stable-signal-state-v1--primary-vs-shadow)
8. [Shadow logging contract (planned)](#8-shadow-logging-contract-planned)
9. [Modularity boundaries](#9-modularity-boundaries)
10. [Phased rollout (no gate changes)](#10-phased-rollout-no-gate-changes)
11. [What not to do in v1](#11-what-not-to-do-in-v1)

---

## 1. Scoring authority model

Horizon-1 has **three tiers** of signal influence. Stable Signal State v1 reorganizes **tier 2 and 3** for explainability; **tier 1 is frozen**.

| Tier | Source | Affects gate pass/fail? | Affects winner / rank? | v1 action |
|------|--------|-------------------------|-------------------------|-----------|
| **1 — Authoritative** | Cloud Function (`friday-scout`) | **Yes** | **Yes** — `scout_score`, 14× `gates.*`, `direction` | **No change** |
| **2 — Display / conviction** | Sandbox overlays | No | **No** (by design today) | Clarify layers; demote duplicates to shadow |
| **3 — Retrospective** | SQLite memory, outcomes, patterns | No | No | Keep analytics; tag inputs by layer |

### Tier 1 (frozen)

- **`scout_score`** — blended conviction; sole numeric input to `max(score)` winner selection among gate passers (`run_gates.choose_final_pick`).
- **`gates.sentinel` … `gates.fortress`** — strict AND for `passed_all_gates`; order defines `first_failed_gate` only.
- **`direction`** — trade bias from CF; not recomputed in sandbox.

### Tier 2 (sandbox — reorganize, do not amplify)

| Module | Output | Current rank impact |
|--------|--------|---------------------|
| `earnings_intelligence.py` | `adjustedScoutScore`, `earningsConvictionAdjustment` (±8 cap) | **None** on pick (DB saves base `score`) |
| `directionality.py` | `bullConviction`, `bearConviction`, `netDirectionalEdge` | **None** |
| `peer_risk_adjusted_edge.py` (P0) | `scoringBreakdown`, `peerConvictionAdjustment=0` | **None** |
| `explainability.py` | Per-gate PASS/FAIL narrative | **None** |
| `option_picker.py` | `optionPick` | **None** on score |

### Tier 3 (post-save only)

- `memory_store` — ranks, regime snapshots, gate attributions, gate alpha, patterns  
- `performance_tracker` — forward returns, outcome labels  
- `feature_store` / `pattern_engine` — institutional vectors and cohort stats  

None of tier 3 may feed back into live `/api/run` ranking in v1.

---

## 2. Institutional layer definitions

Each layer has **one primary signal** for Stable Signal State v1. All other layer-related inputs become **shadow** (logged, explained, not used for ranking).

| Layer | Institutional meaning in Horizon-1 | Primary signal (v1) | Gate anchor (unchanged) |
|-------|-----------------------------------|---------------------|-------------------------|
| **1. Momentum** | Price trend and directional persistence | **`trend` + COMPASS pass** (trend lock) | `compass` |
| **2. Risk** | Position sizing, beta, earnings/event risk, threats | **`scout_score` at FORTRESS + FORTRESS pass** | `fortress` |
| **3. Liquidity** | Tradability, volume, slippage risk | **`volume` + SENTINEL pass** | `sentinel` |
| **4. Volatility** | IV regime, daily shock, setup stability | **`iv_elevated` + PULSE pass** | `pulse` |
| **5. Fundamental Quality** | Earnings power, balance sheet, valuation, smart money | **`piotroski` + `z_score` + ATLAS pass** | `atlas` (+ `oracle`, `phantom` as secondary gate context) |
| **6. Regime** | Sector/macro wind, catalysts, intel context | **`wind` + MERIDIAN pass** | `meridian` |
| **7. Breadth** | Market-wide participation and index tone | **`breadth_score` (or `spy_trend` + `qqq_trend` fallback)** | `current` (flow) as gate context; no dedicated breadth gate |

**Note:** The Cloud Function already blends many inputs into `scout_score`. Sandbox layers **do not replace** that blend; they **label and explain** it consistently.

---

## 3. Full input inventory by layer

Legend: **P** = primary (v1), **S** = shadow (log only), **G** = gate pass/fail only (tier 1), **A** = analytics only (tier 3).

### 3.1 Momentum

| Input / study | Typical source | Used today in | v1 |
|---------------|----------------|---------------|-----|
| `trend` | CF | COMPASS gate, regime, explainability | **P** |
| `price` vs MA (gate 9 text) | CF `raw_output` | COMPASS, directionality | **S** (parsed lines) |
| `rsi` | CF | ATLAS, directionality, PRAE, feature_store | **S** — overlaps quality/momentum |
| `change` (daily %) | CF | SENTINEL/PULSE context, directionality, PRAE, EI fallback | **S** — overlaps volatility |
| `direction` | CF | ARCHER, directionality anchor (+25 / ×0.65) | **G** + direction overlay (not layer primary) |
| `compass` pass | CF | Gate runner | **G** |
| Revenue/EPS growth % (gate 3) | CF `raw_output` | ORACLE, directionality | **S** — fundamental growth, not price momentum |
| Strategy lines (gate 13) | CF `raw_output` | ARCHER, directionality | **S** |
| `distance_to_*ema`, `macd_state`, `gap_percent` | CF (optional) | feature_store | **A** |
| `netDirectionalEdge` | Sandbox | regime `classify_market_trend` | **S** — do not use as breadth primary |

### 3.2 Risk

| Input / study | Typical source | Used today in | v1 |
|---------------|----------------|---------------|-----|
| `scout_score` (risk gate context) | CF | FORTRESS, explainability | **P** (with fortress pass) |
| `fortress` pass | CF | Gate runner | **G** |
| `beta` | CF (optional) | PRAE `riskAdjustment`, feature_store, risk flags | **S** |
| `earnings_days` / proximity | CF | AEGIS, EI, regime, risk flags | **S** — event risk |
| `specter` pass + threat lines | CF | Gate runner, directionality | **G** + **S** text |
| `short_interest` | CF (optional) | feature_store, risk flags | **A** |
| `aegis` pass | CF | Gate runner | **G** |
| Position size / risk text (gate 14) | CF `raw_output` | FORTRESS explainability | **S** |
| EI `earnings_score` / guidance tiers | Sandbox + FMP | `adjustedScoutScore` | **S** for layer (EI is earnings-quality + event, not position risk) |

### 3.3 Liquidity

| Input / study | Typical source | Used today in | v1 |
|---------------|----------------|---------------|-----|
| `volume` | CF | SENTINEL, explainability | **P** |
| `sentinel` pass | CF | Gate runner | **G** |
| `relative_volume` / `volume_ratio` | CF (optional) | PRAE penalty, regime liquidity, feature_store, patterns | **S** |
| `options_volume_score` | CF (optional) | feature_store, regime liquidity fallback | **A** |
| Option chain `volume`, `openInterest` | FMP | option_picker scoring | **A** (post-pass pick only) |
| `price` (tradability) | CF | SENTINEL | **S** (supports primary) |

### 3.4 Volatility

| Input / study | Typical source | Used today in | v1 |
|---------------|----------------|---------------|-----|
| `iv_elevated` | CF | PULSE, explainability | **P** |
| `pulse` pass | CF | Gate runner | **G** |
| `iv_percentile` / `iv_rank` | CF (optional) | PRAE, regime, feature_store, patterns | **S** |
| `change` (daily %) | CF | PULSE field, directionality, PRAE | **S** |
| `atr` | CF (optional) | feature_store, patterns | **A** |
| Pulse gate text (“elevated”, “moved”) | CF `raw_output` | directionality | **S** |
| `vix` / `vix_level` | CF (optional) | regime volatility classifier | **S** — macro vol, not name vol |

### 3.5 Fundamental Quality

| Input / study | Typical source | Used today in | v1 |
|---------------|----------------|---------------|-----|
| `piotroski` | CF | ATLAS, explainability | **P** |
| `z_score` (Altman-style) | CF | ATLAS | **P** |
| `atlas` pass | CF | Gate runner | **G** |
| `dcf_value`, `dcf_gap` | CF | ATLAS, directionality, PRAE | **S** — valuation modifier |
| `rsi` | CF | ATLAS field | **S** — tactical, not quality primary |
| Oracle growth lines | CF `raw_output` | ORACLE, directionality | **S** |
| Phantom consensus / insider / upgrades | CF `raw_output` | PHANTOM, directionality | **S** |
| `revenue_growth`, `earnings_growth` | CF (optional) | feature_store | **A** |
| `market_cap`, `market_cap_bucket` | CF (optional) | feature_store | **A** |
| EI: `eps_surprise_pct`, `revenue_surprise_pct`, `guidance`, actuals | CF + FMP | EI score, direction dampening | **S** — earnings quality overlay |
| `altman`, `debt_profile` | CF (optional) | feature_store | **A** |

### 3.6 Regime

| Input / study | Typical source | Used today in | v1 |
|---------------|----------------|---------------|-----|
| `wind` | CF | MERIDIAN, CURRENT, directionality, PRAE | **P** |
| `sector` | CF | MERIDIAN, CURRENT, cohorts, ranks | **S** (cohort key, not regime level) |
| `meridian` pass | CF | Gate runner | **G** |
| `catalyst` pass + gate 5 text | CF | directionality (EI-damped) | **G** + **S** |
| `signal` pass + gate 11 intel text | CF | directionality (EI-damped) | **G** + **S** |
| `risk_regime`, `macro_bias` | CF (optional) | regime snapshot, macro classifier | **S** |
| `spy_trend`, `qqq_trend` | CF (optional) | feature_store, market_trend classifier | **S** — feeds breadth when breadth missing |

### 3.7 Breadth

| Input / study | Typical source | Used today in | v1 |
|---------------|----------------|---------------|-----|
| `breadth_score` | CF (optional) | feature_store, `classify_macro_bias`, `classify_market_trend` | **P** |
| `spy_trend` + `qqq_trend` | CF (optional) | feature_store, classifiers | **P fallback** if no breadth_score |
| `current` pass (ETF/sector flow) | CF | Gate runner | **G** |
| `wind` | CF | CURRENT gate | **S** — sector flow, not market breadth |
| `netDirectionalEdge` | Sandbox | Incorrectly mixed into `classify_market_trend` | **S** — **remove from breadth primary path** in v1 |
| Gate pass rate across scan universe | Derived at save | rank maps | **A** |

---

## 4. 14-gate map to layers (unchanged gates)

Gates are **not** merged, reordered, or split. This table is **documentation only** — which institutional layer each gate primarily expresses.

| Gate | Code | Layer (primary) | Layer (secondary) | Key fields |
|------|------|-----------------|-------------------|------------|
| 1 | SENTINEL | **Liquidity** | Momentum (`change` context) | `price`, `volume` |
| 2 | ATLAS | **Fundamental Quality** | Momentum (`rsi`) | `piotroski`, `z_score`, `rsi`, `dcf_*` |
| 3 | ORACLE | **Fundamental Quality** | — | `raw_output` gate 3 |
| 4 | PHANTOM | **Fundamental Quality** | — | `raw_output` gate 4 |
| 5 | CATALYST | **Regime** | — | `raw_output` gate 5 |
| 6 | SPECTER | **Risk** | — | `raw_output` gate 6 |
| 7 | MERIDIAN | **Regime** | — | `sector`, `wind` |
| 8 | AEGIS | **Risk** | Regime (event) | `earnings_days` |
| 9 | COMPASS | **Momentum** | — | `trend`, `price` |
| 10 | PULSE | **Volatility** | Momentum (`change`) | `iv_elevated`, `change` |
| 11 | SIGNAL | **Regime** | — | `raw_output` gate 11 |
| 12 | CURRENT | **Breadth** | Regime (flow) | `sector`, `wind` |
| 13 | ARCHER | **Momentum** | — | `direction`, `strategies` |
| 14 | FORTRESS | **Risk** | — | `scout_score` |

**Layer coverage:** All seven layers are represented by at least one gate. Fundamental Quality spans three gates (2–4) by design — v1 does **not** collapse them; shadow scoring dedupes **numeric** overlays only.

---

## 5. Sandbox overlay map

| Overlay | Inputs touched | Layers | v1 classification |
|---------|----------------|--------|---------------------|
| **Directionality** | `rsi`, `dcf_gap`, `change`, `wind`, gates 3–6, 9–11, 13 text, `direction` | All except Liquidity | **Shadow bundle** `directionalBreakdown` — keep for UI; tag each signal with `layer` + `shadow: true` |
| **Earnings Intelligence** | surprises, guidance, reaction, days | Fundamental Quality + Regime | **Shadow** for ranking; keep `adjustedScoutScore` as labeled **“earnings overlay (non-ranking)”** |
| **PRAE (P0)** | `scout_score`, `rsi`, `change`, `wind`, `dcf_gap`, `beta`, `iv_*`, `relative_volume` | Momentum, Volatility, Liquidity, Risk | **Shadow** `scoringBreakdown` — already non-ranking |
| **Explainability** | all gate fields | Per-gate | **Tier 1 narrative** — not scoring |
| **Rank maps (save)** | `scout_score`, `sector` | Cross-layer | **Analytics** — universe/sector percentile of **base score only** |
| **Regime snapshot** | classifiers on IV, VIX, volume, breadth, earnings days | Regime, Volatility, Liquidity, Breadth | **Analytics** — fix breadth vs `netDirectionalEdge` coupling (§6) |
| **Gate attributions** | parsed `score:` from gate text | Per-gate | **Shadow** — retrospective %; can disagree with CF blend |
| **Gate alpha / patterns** | attributions + outcomes + feature vectors | All | **Analytics only** |

---

## 6. Redundancy and overlap matrix

### 6.1 High-severity overlaps (same economic signal, multiple ranking-adjacent paths)

| Signal | Locations | Problem | v1 resolution |
|--------|-----------|---------|---------------|
| **`rsi`** | ATLAS gate, directionality (large weights), PRAE (15%), feature_store | Triple-count in UI/peer; confuses “momentum” vs “quality” | **Shadow** everywhere except ATLAS gate pass narrative; layer tag = Fundamental Quality (tactical) |
| **`change` (daily %)** | directionality, PRAE, PULSE, EI reaction fallback | Mixes volatility shock and momentum | **Shadow**; primary volatility = `iv_elevated`; primary momentum = `trend` |
| **`wind` / `sector`** | MERIDIAN, CURRENT, directionality, PRAE, regime | Sector flow counted as regime + breadth + peer | **Primary** = `wind` for Regime only; **shadow** in PRAE/direction; CURRENT = gate context only |
| **`dcf_gap`** | directionality, PRAE, ATLAS | Valuation in momentum-ish overlays | **Shadow**; primary quality = piotroski + z_score |
| **Earnings narrative** | AEGIS gate, EI, catalyst dampening, regime proximity | Intentional anti-double-count in direction; still noisy in UI | EI **shadow** for layers; gates unchanged |
| **`scout_score` vs `adjustedScoutScore` vs peer edge vs direction net** | CF vs EI vs PRAE vs directionality | Operators unsure which “wins” | Single label: **“Ranking score” = `score` (scout_score)**; others prefixed “Overlay” / “Shadow” |
| **Percentile rank** | PRAE `peerContext` vs save-time `build_rank_maps` | Two algorithms, same scan | **Shadow** both; one canonical analytics field later (P2) |

### 6.2 Medium-severity overlaps

| Signal | Locations | v1 resolution |
|--------|-----------|---------------|
| **`iv_percentile` vs `iv_elevated` vs VIX** | PRAE, PULSE, regime classifiers | Primary vol = `iv_elevated`; IV percentile + VIX **shadow** |
| **`volume` vs `relative_volume`** | SENTINEL vs PRAE/regime/patterns | Primary liq = `volume`; relative **shadow** |
| **`beta`** | PRAE, feature_store, risk flags | **Shadow**; risk primary = FORTRESS + pass |
| **Gate text `score: N`** | explainability → attributions → gate_alpha | **Shadow** parsed score; never override `scout_score` |
| **`breadth_score` vs `netDirectionalEdge`** | `classify_market_trend` | Stop using net edge as breadth proxy; breadth **primary** = `breadth_score` or index trends |

### 6.3 Low-severity / acceptable

| Signal | Notes |
|--------|-------|
| Gate pass booleans vs underlying fields | Expected — pass is tier 1, fields explain why |
| `direction` in ARCHER + directionality | ARCHER gate unchanged; directionality anchor stays display-only |
| FMP earnings merge | Supplements missing CF fields; shadow under Fundamental Quality |

---

## 7. Stable Signal State v1 — primary vs shadow

### 7.1 Per-layer summary

| Layer | Primary (ranking context label only) | Shadow (log, explain, no rank effect) |
|-------|--------------------------------------|----------------------------------------|
| **Momentum** | `trend`, COMPASS pass | `rsi`, `change`, gate 3/13 growth & strategy text, EMA/MACD/gap fields |
| **Risk** | FORTRESS pass + `scout_score` at risk gate | `beta`, `earnings_days`, specter text, short interest, EI adjustment |
| **Liquidity** | `volume`, SENTINEL pass | `relative_volume`, option liquidity metrics |
| **Volatility** | `iv_elevated`, PULSE pass | `iv_percentile`, `change`, `atr`, VIX, pulse text |
| **Fundamental Quality** | `piotroski`, `z_score`, ATLAS pass | `dcf_gap`, oracle/phantom text, EI components, revenue/earnings growth fields |
| **Regime** | `wind`, MERIDIAN pass | `sector` label, catalyst/signal text, `risk_regime`, spy/qqq trend |
| **Breadth** | `breadth_score` or (`spy_trend` + `qqq_trend`) | `wind` on CURRENT, `netDirectionalEdge` |

### 7.2 Composite “Scout Signal Stack” (display contract)

For UI, PDF, and saved JSON — **one ordered stack**:

```
1. Ranking score     → score (scout_score)          [TIER 1 — ONLY RANK INPUT]
2. Gate verdict      → passedAllGates + gates[]    [TIER 1]
3. Layer primaries   → stableSignal.layers{7}      [TIER 2 — labels only in v1]
4. Shadow overlays   → stableSignal.shadow{...}    [TIER 2 — logged]
5. Direction read    → directionBreakdown          [TIER 2 — shadow bundle]
6. Earnings overlay  → adjustedScoutScore          [TIER 2 — labeled non-ranking]
7. Peer overlay      → scoringBreakdown            [TIER 2 — already non-ranking P0]
```

### 7.3 Gate ↔ layer quick view (operator card)

```
Momentum ........ COMPASS, ARCHER
Risk ............ FORTRESS, SPECTER, AEGIS
Liquidity ....... SENTINEL
Volatility ...... PULSE
Fundamental ..... ATLAS, ORACLE, PHANTOM
Regime .......... MERIDIAN, CATALYST, SIGNAL
Breadth ......... CURRENT (+ breadth_score field)
```

---

## 8. Shadow logging contract (planned)

**No change to gate math or `choose_final_pick`.** Add a optional block on serialize/save:

```json
{
  "stableSignal": {
    "version": "1",
    "rankingScoreField": "scout_score",
    "layers": {
      "momentum": { "primary": { "field": "trend", "value": "UPTREND", "gate": "compass" }, "shadow": ["rsi", "change"] },
      "risk": { "primary": { "field": "scout_score", "gate": "fortress", "passed": true }, "shadow": ["beta", "earnings_days"] },
      "liquidity": { "primary": { "field": "volume", "gate": "sentinel" }, "shadow": ["relative_volume"] },
      "volatility": { "primary": { "field": "iv_elevated", "gate": "pulse" }, "shadow": ["iv_percentile", "change"] },
      "fundamentalQuality": { "primary": { "fields": ["piotroski", "z_score"], "gate": "atlas" }, "shadow": ["dcf_gap", "earningsIntelligence"] },
      "regime": { "primary": { "field": "wind", "gate": "meridian" }, "shadow": ["catalyst_text", "signal_text"] },
      "breadth": { "primary": { "field": "breadth_score", "fallback": ["spy_trend", "qqq_trend"] }, "shadow": ["netDirectionalEdge"] }
    },
    "redundancyFlags": ["rsi_multi_use", "change_multi_use"]
  }
}
```

**Rules:**

- `layers.*.primary` must be populated from tier-1 API fields when present; never from recomputed blends.
- `shadow` lists field names only; values copied in `shadowValues` if needed for research exports.
- Directionality signals gain `layer` + `"tier": "shadow"` metadata (no weight changes in v1).
- PRAE remains entirely shadow; do not promote `peerRiskAdjustedEdge` to primary without explicit P2 flag.

---

## 9. Modularity boundaries

| Module | Responsibility after v1 |
|--------|-------------------------|
| `run_gates.py` | Fetch CF payload; winner = `scout_score` + gates — **unchanged** |
| `stable_signal_layers.py` | Map payload → primary/shadow per layer; attached on `serialize_result` |
| `directionality.py` | Bull/bear transparency; consume layer tags — **no new formulas** |
| `earnings_intelligence.py` | Earnings overlay; tagged Fundamental Quality shadow |
| `peer_risk_adjusted_edge.py` | Peer shadow breakdown only until P2 flag |
| `explainability.py` | Gate rules unchanged; add layer badge per gate |
| `memory_store.py` | Persist `stableSignal` JSON; regime classifiers use breadth primary only |
| `feature_store.py` | Continue full vectors; `layer_weights_json` remains **analytics-only** |

---

## 10. Phased rollout (no gate changes)

| Phase | Deliverable | Alters ranking? |
|-------|-------------|-----------------|
| **S0 (this doc)** | Audit + primary/shadow table | No |
| **S1** | `stable_signal_layers.py` + attach on `serialize_result` | No — **done** |
| **S2** | `stableSignal.explainability` (see [stable-signal-s2-explainability-plan.md](./stable-signal-s2-explainability-plan.md)) | No |
| **S3** | Dashboard/PDF labels: “Ranking score” vs overlays; layer badges on gate cards | No |
| **S3** | Directionality signal metadata (`layer`, `shadow`) | No |
| **S4** | Regime classifier fix: decouple `netDirectionalEdge` from breadth | No |
| **S5** | Research Memory filters by `stableSignal.layers.*` | No |
| **P2+** | Optional capped layer adjustments (env-flagged) | Only with explicit `SCOUT_LAYER_ADJUST=1` |

---

## 11. What not to do in v1

| Do not | Reason |
|--------|--------|
| Add or remove gates | User constraint |
| Reblend `scout_score` in sandbox | CF is authoritative |
| Let `adjustedScoutScore` or PRAE change `choose_final_pick` | Breaks Horizon-1 baseline |
| Promote shadow signals without audit trail | Loses explainability |
| Use `netDirectionalEdge` as breadth primary | Conflates ticker direction with market breadth |
| Merge ATLAS/ORACLE/PHANTOM into one gate | Reduces CF parity |
| Apply `feature_store` layer weights (60/25/15) on `/api/run` | Weights are retrospective ML prep only |
| Double-count EI + catalyst + pulse in direction without dampening | Already partially handled — keep EI as shadow |

---

## Appendix A — Cloud Function fields (expected on payload)

Fields the sandbox consumes (may be missing per ticker). **Not exhaustive of CF internals.**

`scout_score`, `gates`, `ticker`, `price`, `direction`, `trend`, `sector`, `rsi`, `dcf_gap`, `dcf_value`, `change`, `wind`, `volume`, `piotroski`, `z_score`, `earnings_days`, `iv_elevated`, `strategies`, `raw_output`, `beta`, `iv_percentile`, `relative_volume`, `market_cap`, `short_interest`, `spy_trend`, `qqq_trend`, `vix`, `breadth_score`, `risk_regime`, earnings actuals/surprises, `gate_engine_version`.

---

## Appendix B — Document history

| Date | Change |
|------|--------|
| 2026-05-21 | Initial Stable Signal State v1 audit |
| 2026-05-21 | S1: `stable_signal_layers.py` + `stableSignal` on serialize |
| 2026-05-21 | S2 explainability plan (doc only) |

---

*Gate pass/fail and `scout_score` remain owned by the deployed Cloud Function. This plan only standardizes how Horizon-1 explains and logs the same inputs.*
