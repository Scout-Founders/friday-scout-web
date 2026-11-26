# Scout scoring methodology

**Status:** Documentation checkpoint, 2026-11-26. Describes the scoring implementation inspected in Phases I–III.
**Git checkpoint:** `266953de86a1fc9d059dce89bc15dd8e95456285`
**Related:** [Signal interpretation guide](./SCOUT_SIGNAL_INTERPRETATION_GUIDE.md) · [Research register](./SCOUT_SCORING_RESEARCH_REGISTER.md) · [Stable Signal State v1](./stable-signal-state-v1.md)

This document is the authoritative reference for the scoring implementation that was inspected. It is not a statement of product intent, and it is not a description of a verified live deployment.

## Source status

The formulas below were read from this local file:

`/Users/chaseouhrabka/Downloads/friday-scout_function-source-deploy-candidate/main.py`

SHA-256: `243ce011a4e8ad9fbade2622c0955d811c52db8045e2b23bd9d7517e599a4e51`
Size: 175,770 bytes

That file is byte-identical to the archive labeled `friday-scout-LIVE-post-archive-2026-09-28.zip`. Phase II compared the scoring functions with the 1 July 2026 download and with two other local copies. The scoring function bodies matched. The file hashes differ because those other copies contain local email-archive edits outside the scorer:

| Local copy | `main.py` SHA-256 | Role |
|---|---|---|
| `friday-scout_function-source-deploy-candidate/main.py` | `243ce011a4e8ad9fbade2622c0955d811c52db8045e2b23bd9d7517e599a4e51` | September 28 snapshot used for the line references below |
| `friday-scout_function-source/main.py` | `a123688e13a03f2d16d7a11ab9fc8b4f8e7f8126f2b93234c09c53353a616303` | Local edit of the July download. Scorer unchanged |
| `friday-scout_function-source (1)/main.py` | `710c2bdaf4eb8ae9002794e06f60a40d56af2b7911a64a1abbcda173f0e5f511` | Further local archive edits. Scorer unchanged |

Phase II recorded these function-body SHA-256 prefixes, identical across those copies:

| Function | Prefix | Span |
|---|---|---|
| `single_scan` | `e99f139892c7` | 685 lines |
| `run_full_scan` | `a0234c9dc9a0` | 359 lines |
| `scan_gate2` | `591f490ddb1c` | |
| `scan_gate3` | `7e40e7b924fd` | |
| `scan_gate4` | `714d07e2818a` | |
| `claude_analyze` | `7810b1901c88` | |
| `friday_scout_cloud` | `d6af9c23c3a1` | |

The July download’s `main.py` prefix recorded in Phase II is `96ba063cb8c57498` (172,321 bytes). It differs from the September snapshot by the `save_scout_report` email archive, about +82/−2 lines. The score arithmetic is the same.

**Deployed source was not verified.** On 2026-11-26, `gcloud` was not installed on the audit machine, so `gcloud functions describe friday-scout --project=scout-493918 --region=us-central1` could not be executed. Update time, generation, runtime, entry point, and service identity were not retrieved. The function was not invoked, and production source was not downloaded. Line numbers in this document refer to the September 28 local snapshot only.

Horizon-1 does not compute `scout_score`. It stores the value returned by the Cloud Function and computes Net Score afterward.

## 1. Single-scan formula

`single_scan` (`main.py` lines 3218–3232) builds one integer and then clamps it to 0–100.

```text
scout_score = 50
            + min(atlas_score * 5, 15)
            + min(oracle_score * 4, 12)
            + min(phantom_score * 3, 9)
            + min(catalyst_score * 3, 9)
            + (5 if specter_clean else 0)
            + specter_penalty
            + wind * 3
            + (5 if (uptrend and BULLISH) or (downtrend and BEARISH) else 0)
            + (3 if not iv_elevated else 0)
            + earnings_adjustment
scout_score = max(0, min(100, scout_score))
```

`min(value, cap)` limits the upside only. A negative component is added in full.

`earnings_adjustment` is an if/elif, so the two branches cannot both apply:

```text
if earnings_days and earnings_days <= 7:
    adjustment = -10
elif earnings_days and earnings_days <= 14:
    adjustment = -5
else:
    adjustment = 0
```

A count of 0 is falsy in Python, so it takes the else branch. `None` also takes the else branch. Counts 1–7 take −10 only. Counts 8–14 take −5 only.

### Single-scan contributions

| Component | Input | Points before the cap | Contribution |
|---|---|---|---|
| Base | Constant | 50 | 50 |
| Atlas | Piotroski, Altman Z, RSI, DCF gap | See schedule below. Observed reconstructed range about −4 to +7 | `min(score × 5, 15)`. A negative score is uncapped by this `min` |
| Oracle | Estimate record and revenue growth | −1 to +3. EPS growth is printed and is not added | `min(score × 4, 12)` |
| Phantom | `scan_gate2(ticker, "CALL", price)` | Call-side schedule below | `min(score × 3, 9)` |
| Catalyst | `scan_gate3` plus an upgrade boost taken from Phantom’s text | Insider buys, headline keywords, and the boost | `min(score × 3, 9)` |
| Specter clean | `scan_gate4(ticker, "CALL", ...)` | +5 when `clean` is true | +5 or 0 |
| Specter penalty | News categories, once each | 0 to −56 | Added in full |
| Wind | Static `SECTOR_WIND` lookup, lines 81–113 and 262–265 | −3 to +3, unknown sector is 0 | `wind × 3`, so −9 to +9 |
| Alignment | Trend and the direction assigned later in the same function | +5 or 0 | +5 only when the trend agrees with BULLISH or BEARISH |
| Volatility | Absolute daily change, and earnings within 14 days | +3 or 0 | +3 when IV is not marked elevated |
| Earnings | Truthy day count | 0, −5, or −10 | Mutually exclusive if/elif |

Atlas point schedule (`main.py` 2691–2740):

| Reading | Points |
|---|---|
| Piotroski ≥ 7 / ≥ 6 / ≥ 4 / below 4 | +3 / +2 / +1 / −1 |
| Piotroski missing | 0 |
| Z-score > 3 / 1.8–3 / below 1.8 | +1 / 0 / −1 |
| RSI < 30 / 30–80 / > 80 | +1 / 0 / −1. RSI 70–80 and 30–40 are described in the text and score 0 |
| DCF gap > 30% / > 10% / below −30% | +2 / +1 / −1 |
| DCF gap `(DCF − price) / price × 100` | Same ratio used for direction |

Oracle (`main.py` 2751–2773): +1 if any analyst-estimate row is returned, then revenue growth > 15% adds +2, growth above 0 adds +1, and growth below −10% adds −1.

Phantom is always invoked with direction `"CALL"` (`main.py` 2785), before direction is chosen. The call-side schedule is in `scan_gate2` (`main.py` 728–817): consensus, target gap, recent upgrades, insider buys versus sells, and EPS-revision direction. The put-side branches in that function are not used by `single_scan`.

Catalyst (`scan_gate3`, lines 825–888) scores 30-day insider purchases and seven-day headline keywords. After that returns, `single_scan` (lines 2796–2810) adds +2 when Phantom’s text reports at least five recent upgrades, or +1 when it reports at least two.

Specter news penalties, one category each (`main.py` 921–970):

| Category | Penalty |
|---|---|
| LEGAL | −10 |
| ACCOUNTING | −8 |
| LEADERSHIP | −5 |
| SHORT_SELLER | −5 |
| EXISTENTIAL | −15 |
| FDA | −5 |
| DILUTION | −3 |
| DOWNGRADE | −5 |

The category sum is −56. Two other flags set `clean` to false and add no points: a one-month move above +20% on a CALL argument, and insider sales above $10 million on a CALL argument (`main.py` 902 and 911–912). Losing `clean` removes the +5 bonus.

### Arithmetic range before the clamp

Adding each single-scan term at its own extreme gives about +117 on the high side and about −73 on the low side:

```text
high: 50 + 15 + 12 + 9 + 9 + 5 + 0 + 9 + 5 + 3 = 117
low:  50 − 20 − 4 − 18 − 6 + 0 − 56 − 9 + 0 + 0 − 10 = −73
```

Those are independent extremes, not a proven joint market case. The stored value is then clamped with `max(0, min(100, scout_score))`. Several different uncapped totals therefore display as 100. The research rows store the clamped integer only.

## 2. Batch formula

`run_full_scan` (`main.py` 1328–1343) comments that it uses the same formula as the single scanner. The arithmetic is different. A score is computed only for a name that has already survived the G2 and G4 drop rules.

```text
scout_score = 50
            + min(gate1_score * 5, 15)
            + min(catalyst_score * 3, 9)
            + min(phantom_score * 3, 9)
            + (5 if specter_clean else 0)
            + specter_penalty
            + wind * 3
            + (5 if (uptrend and CALL) or (downtrend and PUT) else 0)
            + (3 if not iv_elevated else 0)
            + earnings_adjustment
            + (5 if catalyst_level == "STRONG" else -3 if catalyst_level == "NONE" else 0)
scout_score = max(0, min(100, scout_score))
```

The earnings if/elif is the same code as the single scan.

Differences that change the number:

- There is no Oracle term (`fwd_score × 4`).
- The fundamental input is the gate-1 screen score, not the Atlas score. Gate-1 also counts drawdown or run-up, wind, forward P/E, cash, margins, and FCF or R&D.
- A name is returned from gate 1 only when that score is at least 4 (`main.py` 502 and the put equivalent). Then `4 × 5 = 20`, and `min(20, 15)` is 15. Every published batch score therefore receives +15 from fundamentals. Fundamental points above 4 do not change `scout_score`. They still change `total_score`.
- Catalyst level adds a second catalyst term: +5 for STRONG (`g3 ≥ 2`), 0 for PRESENT (`g3 ≥ 1`), and −3 for NONE.
- Phantom and Specter are called with `"CALL"` or `"PUT"`, matching the screen (`main.py` 1079 and 1128).
- A Specter flag drops the name before this block (`main.py` 1131–1137). On the published path, `clean` is true and the penalty is 0. The penalty line does not change a published score.
- Alignment uses the labels CALL and PUT.

`total_score` is a separate sum: gate-1 points + Phantom points + Catalyst points, then trend and sector-ETF adjustments (`main.py` 1142 and 1171–1236). Strike selection and the no-survivor fallback use `total_score`. They do not use `scout_score`.

### Batch universe before the formula

Calls (`scan_gate1_calls`, lines 372–405): price ≥ $10, volume ≥ 100,000, drawdown from the 52-week high ≤ −15%. The ten largest drawdowns are enriched. Wind ≤ −3 returns `None` (line 428). An early score below 3 is dropped (line 454). The final return requires score ≥ 4.

Puts (`scan_gate1_puts`, lines 522–553): price ≥ $10, volume ≥ 100,000, run-up from the 52-week low ≥ 40%, and within 15% of the 52-week high. The ten largest run-ups are enriched. Wind ≥ 2 returns `None` (line 632).

Put DCF gap (`main.py` 572) is `(price − DCF) / DCF × 100`. Call and single-scan DCF gap is `(DCF − price) / price × 100`. A positive put gap means the price is above DCF. A positive call gap means DCF is above the price.

The sequential filter then keeps at most seven names per side (line 1073). G2 drops a name when Phantom ≤ −2, and drops a CALL when Phantom < 1. Puts do not need a positive Phantom score (lines 1080–1095). Catalyst does not drop a name (line 1098). G4 drops any name with `clean` false.

## 3. Side-by-side comparison

| Item | `single_scan` | `run_full_scan` |
|---|---|---|
| When a score exists | Every completed ticker scan | Only after G2 and G4 pass |
| Start | 50 | 50 |
| Fundamentals | Atlas score, cap +15 | Gate-1 score, cap +15, and +15 for every published name |
| Oracle | Cap +12 | Absent |
| Phantom | Cap +9, always CALL | Cap +9, CALL or PUT |
| Catalyst points | Cap +9 | Cap +9, plus +5 / 0 / −3 by level |
| Specter | +5 if clean, plus a penalty down to −56 | Published names are clean, so +5 and penalty 0 |
| Wind | `wind × 3` | Same term, after the screen’s hard wind block |
| Alignment | +5 for BULLISH/BEARISH agreement | +5 for CALL/PUT agreement |
| Quiet volatility | +3 | +3 |
| Earnings | If/elif −10 / −5 / 0 | Same if/elif. Lookup window is 14 days, and a negative count is still truthy |
| Direction labels | BULLISH, BEARISH, NEUTRAL | CALL or PUT, from the screen the name entered |
| Verdict text | STRONG / MODERATE / WEAK / AVOID at 80 / 60 / 40 | STRONG BUY / MODERATE / WATCH / AVOID at the same cuts |
| Ranking | One name. Horizon sorts later | Stable sort by `scout_score` descending |
| Claude | Not called | Writes the email after the score exists |
| Options | Narrative strategy in the text | `recommend_strikes` uses `total_score` |

The same component readings can produce different grades. Atlas 2, Oracle 2, Phantom 1, Catalyst 1 and PRESENT, clean, wind 0, uptrend, bullish, quiet IV, no earnings:

```text
single: 50 + 10 + 8 + 3 + 3 + 5 + 0 + 0 + 5 + 3 = 87  → STRONG
batch:  50 + 10 + 3 + 3 + 5 + 0 + 0 + 5 + 3 + 0 = 79  → MODERATE
```

The 8-point gap is the Oracle term. A real batch gate-1 score would differ again, because it counts drawdown, wind, and forward multiples that Atlas does not. A published batch name with gate-1 at least 4 replaces the +10 with +15.

A name can also be scored on one path and absent from the other. Drawdown −10%, run-up 20%, RSI 50, and a flat DCF gap is NEUTRAL on the single scan and still receives a score. It enters neither the call screen nor the put screen. A name that passes both screens can be scored twice, once as CALL and once as PUT.

## 4. Fourteen gates

The fourteen names live in the single-scan JSON (`main.py` 3278–3293) and in Horizon’s `GATES` list (`scout-gates-sandbox/run_gates.py` lines 43–58). Horizon treats a pass as the boolean `True`. A missing or false value fails that gate.

| # | Key | Name | Role in the single-scan JSON | Pass condition |
|---|---|---|---|---|
| 1 | `sentinel` | Market Filter | Price and volume screen | Price ≥ $5 and volume ≥ 50,000 |
| 2 | `atlas` | Core Strength | Fundamental quality | Atlas score ≥ 2. The prose says MARGINAL from 0 to 1 and FAIL below 0. The JSON is true only at ≥ 2 |
| 3 | `oracle` | Forward Vision | Estimates and revenue growth | Oracle score ≥ 1 |
| 4 | `phantom` | Smart Money | Analysts, targets, insiders, revisions | Phantom score ≥ 0 |
| 5 | `catalyst` | Event Trigger | Insider buys, headlines, upgrade boost | Catalyst score ≥ 1 |
| 6 | `specter` | Threat Scan | News categories, one-month move, large insider sales | `clean` is true |
| 7 | `meridian` | Sector Wind | Static sector table | Wind ≥ 0. Unknown is 0 and passes |
| 8 | `aegis` | Earnings Shield | Near-term earnings | Not (a truthy `earnings_days` and `earnings_days ≤ 14`) |
| 9 | `compass` | Trend Lock | 20-day SMA state | `trend_status != "UNKNOWN"`. An identified downtrend passes |
| 10 | `pulse` | Volatility Read | Daily move and earnings proximity | IV is not elevated. Elevated means absolute move > 5%, or a truthy earnings count ≤ 14 |
| 11 | `signal` | Intel Feed | Headlines are printed | Hard-coded `True` |
| 12 | `current` | Flow Analysis | Sector ETF move is printed | Hard-coded `True` |
| 13 | `archer` | Strategy Select | Strategy prose is written | Hard-coded `True` |
| 14 | `fortress` | Risk Gate | Restates the verdict floor | `scout_score ≥ 40` |

`explainability.py` describes Compass as “Price confirms trend, typically above the referenced moving average.” The boolean does not require that. A downtrend passes Compass. The same file describes Signal as news available for review, Current as supportive ETF flow, Archer as a viable strategy, and Fortress as acceptable position-size guidance. The JSON assignments for Signal, Current, and Archer do not read those conditions. Fortress is the same cut as leaving the AVOID band.

The batch path prints `ALL 14 GATES CLEARED` for every name that reaches the score block (`main.py` 1348). That path’s checks are G0, G1, G2, G3, G4, then 5B earnings, 5C trend, 5D volatility, 5E news, 5F sector ETF, strategy, and sizing. G3 does not block. Earnings, trend, wind, and IV adjust text or `total_score` and do not remove the name. Gate 5E prints “News scan pending” and has no failure condition (`main.py` 1204–1206). Signal, Current, and Archer are not evaluated there. The printed claim is unconditional for a survivor of G2 and G4.

## 5. Direction

Single-scan direction is assigned after Phantom and Specter have already run (`main.py` 3003–3012):

```text
call_case = drawdown ≤ −20
            or (dcf_gap and dcf_gap > 20)
            or (rsi and rsi < 40)
put_case  = runup ≥ 60
            or (dcf_gap and dcf_gap < −30)
            or (rsi and rsi > 75)

BULLISH  when call_case and not put_case
BEARISH  when put_case and not call_case
NEUTRAL  when both are true, or neither is true
```

Drawdown and run-up are percentages from the 52-week high and low. DCF gap uses `(DCF − price) / price`.

Batch direction is the screen. A call candidate is labeled CALL. A put candidate is labeled PUT. The BULLISH / BEARISH / NEUTRAL rule is not applied. NEUTRAL is unreachable in the batch score.

## 6. Score classifications

Cuts are 80, 60, and 40 on every surface. The words differ.

| Score | Single-scan text (`main.py` 3246–3249) | Batch detail (`main.py` 1345) | Batch ranking line (`main.py` 1378) | Website ring (`src/pages/index.js` 57–59) |
|---|---|---|---|---|
| ≥ 80 | STRONG — HIGH CONVICTION | STRONG BUY | STRONG | STRONG BUY |
| ≥ 60 | MODERATE — PROCEED WITH CAUTION | MODERATE | MODERATE | MODERATE |
| ≥ 40 | WEAK — WATCH LIST ONLY | WATCH | WATCH | WATCH |
| < 40 | AVOID | AVOID | WATCH | AVOID |

The batch ranking line has no AVOID branch. A score of 30 is AVOID in the detail block and WATCH on the ranking line.

Single-scan position text uses the same cuts: up to 3% at ≥ 80, up to 2% at ≥ 60, max 1% or paper at ≥ 40, and avoid or paper below 40 (lines 3234–3241). Batch Gate 7 sizes from `total`, the gate-point sum, using a fixed Kelly sketch: win rate 0.667, win/loss ratio 1.61, half-Kelly, then caps of 5%, 3%, and 1.5% at `total` ≥ 15, ≥ 10, and below 10 (`main.py` 1306–1322). Those constants are written in the source. They are not computed from the Horizon-1 bank.

## 7. Ranking, ties, and selection

**Single scan.** One ticker. There is no multi-name rank inside `single_scan`.

**Horizon.** `choose_final_pick` (`run_gates.py` 178–181) keeps the names whose fourteen JSON booleans are all true. If that pool is empty, it uses every returned name. It then takes `max` by `scout_score`. Python’s `max` keeps the first object when scores are equal. A failed gate removes a name from the preferred pool. It does not zero the score, and it does not block selection when nobody passed all fourteen.

**Batch.** `passed_list = sorted(call_results + put_results, key=scout_score, reverse=True)` (`main.py` 1369). The sort is stable and the key is only the score. Equal scores keep earlier list order: passing calls first, in descending gate-1 order, then passing puts. There is no ticker tie-break. A put loses a tie to any surviving call. `TOP PICK` is `passed_list[0]`.

If `passed_list` is empty, the text says no stock passed all 14 gates and then prints the top three of `all_cands` sorted by `total_score`, not by `scout_score` (`main.py` 1382–1386). Dropped names may have no `scout_score`.

## 8. Options selection

Three different selectors exist.

1. **Single-scan prose.** Archer writes a strategy sentence from catalyst, trend, earnings, and direction (`main.py` 3016 onward). That sentence is not the batch strike function.
2. **Batch `recommend_strikes`** (`main.py` 998–1028). The input is `total_score`, price, direction, and DCF gap. It is not `scout_score`. Calls and puts use percentage offsets from the spot price, rounded to a $5 strike. EXTREME requires `total_score ≥ 12` and DCF gap > 30. HIGH requires `total_score ≥ 10`. Otherwise the label is MODERATE.
3. **Horizon `option_picker.choose_option_contract`.** `dashboard.py` calls it only when `passed_all_gates` is true (lines 268–274). It does not change `scout_score` or the winner.

## 9. Claude

`claude_analyze` (`main.py` 1395) runs from `run_scan` after `run_full_scan` has already produced `scout_score` (lines 3428–3438). The model string stored with an archived report is `claude-sonnet-4-6`. The scan text passed to Claude is truncated at 8,000 characters. The email body is the analysis truncated at 15,000 characters, or the scan text if there is no analysis (lines 3449–3450).

Claude writes the outbound narrative. It does not assign `scout_score`. The structured archive stores `scout_score` from the scan object (`main.py` 3467–3470). The number in the prose and the stored number can diverge. `single_scan` does not call Claude.

## 10. Net Score

Net Score is `netDirectionalEdge` from `scout-gates-sandbox/directionality.py` lines 516–529. It is computed after the Cloud Function returns. It does not change `scout_score`, gate booleans, batch ranking, or the email pick.

```text
bull_raw = sum of bullish evidence weights
bear_raw = sum of bearish evidence weights
if direction == "Bullish":
    bull_raw += 25
    bear_raw *= 0.65
elif direction == "Bearish":
    bear_raw += 25
    bull_raw *= 0.65
bull = clamp(bull_raw)   # 0–100, rounded
bear = clamp(bear_raw)
net  = bull − bear
```

Direction is an input. The +25 and the 0.65 factor reinforce the direction the Cloud Function already returned. The comment at lines 518–520 describes that as an anchor. Each side is then saturated at 0 and 100, so Net lies between −100 and +100.

The summary sentence (lines 542–556) says the winning side “exceeded” the other. That sentence describes the result of this reinforcement, not the rule that chose BULLISH, BEARISH, or NEUTRAL. NVDA scan 153 is the stored counterexample: direction Bearish, bull 84, bear 83, Net +1, while the summary form would say bear conviction exceeded bull conviction.

`earnings_intelligence.py` can display an adjusted score capped at ±8. The saved grade remains the Cloud Function score. `peer_risk_adjusted_edge.py` uses a conviction adjustment of 0. `stable_signal_layers.py` does not change the score.

## 11. Worked examples from stored scans

These rows are research-eligible single scans. The arithmetic was reconstructed from the stored `raw_output` component lines and matches the stored `scout_score`. Production code was not re-executed. Pre-clamp totals are not stored; they are the reconstruction.

### BSX, scan 210, 2026-05-21 — clamp from 101 to 100

Stored: price $56.56, direction BULLISH, wind +1, UPTREND, IV quiet, no earnings date, all fourteen JSON gates true, score 100.

Atlas 6 (Piotroski 7, Z 3.9, RSI 45, DCF gap +47%), Oracle 2, Phantom 6, Catalyst 1, clean.

```text
50 + min(30, 15) + min(8, 12) + min(18, 9) + min(3, 9) + 5 + 0 + 3 + 5 + 3
= 50 + 15 + 8 + 9 + 3 + 5 + 3 + 5 + 3
= 101 → 100
```

Direction is BULLISH because drawdown −48% makes `call_case`, and RSI 45 with a positive DCF gap does not make `put_case`.

### PLTR, scan 32, 2026-05-17 — 94, Neutral, Specter failed

Stored: price $133.99, wind +3, DOWNTREND, DCF gap −92%, drawdown −35%, score 94. Specter is false because of insider selling of about $410 million. That flag adds no penalty points. It removes the +5 clean bonus.

```text
50 + 15 + 8 + 6 + 3 + 0 + 0 + 9 + 0 + 3 = 94
```

Drawdown ≤ −20 makes `call_case`. DCF gap < −30 makes `put_case`. Both true, so direction is NEUTRAL. NEUTRAL receives no alignment bonus even though the trend is a downtrend. The verdict text is STRONG.

### NVDA, scan 153, 2026-05-19 — 100 with a failed gate and earnings day 0

Stored: price $223.54, direction BEARISH, run-up 73.1%, wind +3, UPTREND, RSI 62, earnings_days 0, score 100. Specter failed on about $204 million of insider selling, with no point penalty. The text says earnings in 0 days is extreme risk. Aegis is true, and the earnings branch adds 0, because 0 is falsy.

```text
50 + 15 + 8 + min(12, 9) + 9 + 0 + 0 + 9 + 0 + 3 = 103 → 100
```

Alignment is 0 because the trend is an uptrend and the direction is BEARISH. Stored Net Score is bull 84, bear 83, Net +1.

### TSM, scan 167, 2026-05-19 — 100, Bearish, Specter failed

Stored: price $392.61, DOWNTREND, wind +3, DCF gap −61%, earnings in 57 days, score 100. Insider selling of about $79 million fails Specter with no point penalty.

```text
50 + 15 + 8 + 9 + 6 + 0 + 0 + 9 + 5 + 3 = 105 → 100
```

The +5 is downtrend plus BEARISH. Earnings at 57 days is outside both penalty branches. Stored Net Score is bull 54, bear 100, Net −46.

### TLT, scan 77, 2026-05-18 — 55, Bullish, three gates failed

Stored: unclassified sector, wind 0, DOWNTREND, RSI 31, no Piotroski or Z, score 55. Atlas, Oracle, and Catalyst are false. Fortress is true because 55 ≥ 40. The verdict text is WEAK.

```text
50 + 0 + 0 + 0 + min(−3, 9) + 5 + 0 + 0 + 0 + 3 = 55
```

RSI below 40 makes `call_case` even though the Atlas RSI band gave that reading 0 points. The downtrend does not add the alignment bonus, because direction is BULLISH.

### IWM, scan 53, 2026-05-18 — 30 after a −20 news penalty

Stored: unclassified, wind 0, trend UNKNOWN, score 30, verdict AVOID. Compass fails because the trend is UNKNOWN. Fortress fails because 30 < 40. The text records two penalties: existential risk −15 and short-seller −5. The short-seller keyword matched a headline containing “hindenburg omen.”

```text
50 + 0 + 0 + 0 − 3 + 0 − 20 + 0 + 0 + 3 = 30
```

Run-up 39% is below 60, drawdown is about −3%, and RSI is about 53, so both direction cases are false and the label is NEUTRAL.

## 12. What the research bank records

`scout_memory.db` SHA-256 at this checkpoint: `fafc3682d898d0b197a079d6d8a191f51f1d7c02d5918315d92730c49c0d0d34`.

The 227 research-eligible rows are production observations with parent NULL and classifier `b0-2026-09-28`. Eligible population hash: `eaa0e0ec2c10ff7bd5ac99fa8d9ed5cdaed763579e0a7aa4bb24e84292763fc8`. Their raw texts are `SCOUT SINGLE SCAN` responses. None are `SCOUT v6` batch reports. `research_sent_picks` has 0 rows. Dates in that eligible set run from 2026-05-17 through 2026-06-02.

`engine_version` fingerprints five sandbox files (`engine_version.py` lines 15–21): `run_gates.py`, `explainability.py`, `directionality.py`, `earnings_intelligence.py`, and `option_picker.py`. It does not fingerprint `main.py`. Stored rows have no formula version, no cloud-source hash, no pre-clamp total, and no structured contribution vector. Component `Score:` lines inside `raw_output` are the only way to rebuild a single-scan total.
