# Scout scoring research register

**Status:** Documentation checkpoint, 2026-11-26. Open items from the Phase I–III scoring audits.
**Git checkpoint:** `266953de86a1fc9d059dce89bc15dd8e95456285`
**Related:** [Scoring methodology](./SCOUT_SCORING_METHODOLOGY.md) · [Signal interpretation guide](./SCOUT_SIGNAL_INTERPRETATION_GUIDE.md)

Identifiers are stable. Do not reuse an identifier for a different finding. New items take the next number.

Classifications used here:

| Class | Meaning |
|---|---|
| Confirmed defect | The inspected code does something other than the behavior its own text, flag, or comment states, or it drops a case the surrounding logic treats as real |
| Implementation divergence | Two inspected paths compute or label the same idea differently. This is a fact about the code. It is a defect only where a comment or a shared contract says they match |
| Methodological limitation | The formula behaves as written, and that behavior limits interpretation or research |
| Interpretation discrepancy | A user-facing or sandbox description does not match the boolean or the score |
| Research gap | The bank cannot answer the question yet |
| Unvalidated assumption | A number or sentence is used as if it were measured, and this bank has not measured it |

Severity is the effect on reading a score or on generalizing research. It is not a trading-loss estimate.

Source lines refer to the 28 September 2026 local snapshot `friday-scout_function-source-deploy-candidate/main.py` (SHA-256 `243ce011a4e8ad9fbade2622c0955d811c52db8045e2b23bd9d7517e599a4e51`). That file has not been verified as the revision deployed on 2026-11-26.

## SSR-001 — Single-scan and batch formulas differ

| Field | Record |
|---|---|
| Classification | Implementation divergence. The comment that the formulas are the same is a confirmed defect |
| Severity | High |
| Evidence | `run_full_scan` line 1328 says “same formula as single scanner.” Lines 1328–1343 omit Oracle, score gate-1 points instead of Atlas, and add +5 / −3 for catalyst level. `single_scan` lines 3218–3232 include Oracle and have no catalyst-level term. Worked split: identical component readings score 87 on the single path and 79 on the batch path |
| Affected path | Both production formulas |
| Research implications | A score from a manual scan and a score from the email are not interchangeable. Outcome stats cannot be pooled across the two paths |
| Proposed remediation or experiment | Give each formula a version string. Either make the arithmetic one function, or delete the “same formula” comment and store the path name with every score. Before any code change, replay a fixed input card through both formulas on paper and record both outputs |
| Dependencies | SSR-003, so a stored score can be tied to a formula version. A later source download, if approved, to confirm the live function matches this snapshot |
| Validation | For a published input card, the stored path and the recomputed integer match. The comment matches the code |
| Status | Open. Documented from the local snapshot. Live deployment not verified |

## SSR-002 — No outbound email evidence in the research bank

| Field | Record |
|---|---|
| Classification | Research gap |
| Severity | High |
| Evidence | All 227 research-eligible raw texts are `SCOUT SINGLE SCAN`. None are `SCOUT v6`. `research_sent_picks` has 0 rows. Eligible population hash `eaa0e0ec2c10ff7bd5ac99fa8d9ed5cdaed763579e0a7aa4bb24e84292763fc8`. The sent-pick ledger describes a separate ingest from Firestore that this bank has not populated |
| Affected path | Research interpretation of batch email picks |
| Research implications | Win rates, gate studies, and score distributions from the 227 rows describe manual scans only |
| Proposed remediation or experiment | Ingest already-sent reports into `research_sent_picks` under the existing ledger rules, without changing scoring or email. Keep those rows in a separate population from the 227 |
| Dependencies | Read-only export of historical `scout_reports`. SSR-001, because ingested email scores still use the batch formula |
| Validation | Each sent pick traces to `email_sent = true` and a raw SMTP body. The eligible single-scan hash remains unchanged unless a new approved population is declared |
| Status | Open. No ingest was run for this checkpoint |

## SSR-003 — Scoring provenance is missing

| Field | Record |
|---|---|
| Classification | Research gap |
| Severity | High |
| Evidence | `engine_version.py` lines 15–21 fingerprint five sandbox files and do not fingerprint the Cloud Function. Stored rows have no formula version, no pre-clamp total, and no contribution vector. `scout_score` is the clamped integer. Component lines exist only inside `raw_output` prose |
| Affected path | Every stored scan |
| Research implications | A later formula change cannot be separated from an older row. Ceiling scores of 100 cannot be reopened without reparsing prose. Phase II could rebuild six rows only because the prose still contained `Score:` lines |
| Proposed remediation or experiment | On a future capture, store path (`single_scan` or `run_full_scan`), scorer hash, unclamped total, each capped contribution, earnings count, and the direction string passed into Phantom and Specter. Do not backfill by rewriting old rows |
| Dependencies | A capture change, which is outside this documentation checkpoint. SSR-001 for the path field |
| Validation | A new row can be recomputed from the stored vector without reading prose. Old rows remain byte-stable |
| Status | Open. Not implemented |

## SSR-004 — Earnings day zero skips the penalty and the shield

| Field | Record |
|---|---|
| Classification | Confirmed defect |
| Severity | High |
| Evidence | Both formulas use `if earnings_days and earnings_days <= 7` then `elif earnings_days and earnings_days <= 14` (`main.py` 1339–1340 and 3230–3231). A count of 0 is falsy, so both branches are skipped. Aegis is `not (earnings_days and earnings_days <= 14)` (line 3287), so 0 passes. NVDA scan 153 stores `earnings_days` 0, Aegis true, score 100, and the text “EARNINGS IN 0 DAYS … EXTREME RISK.” Counts 1–7 and 8–14 are mutually exclusive by the if/elif; that exclusivity is the written structure and is not itself the defect |
| Affected path | Single scan and batch |
| Research implications | Same-day earnings can look safer than earnings in 10 days. Rows with a stored 0 must not be grouped with “no earnings date” |
| Proposed remediation or experiment | Define the intended treatment of 0 before any edit: same as 1–7, or a third explicit state. Add a regression card with counts `None`, 0, 1, 7, 8, 14, and 15. Do not change the live function as part of this item’s documentation |
| Dependencies | None for the paper regression. A code change depends on an approved scoring revision |
| Validation | Count 0 and count 1 take the same documented penalty and the same Aegis result, or an explicit exception is stored |
| Status | Open. Confirmed on the local snapshot and on NVDA scan 153 |

## SSR-005 — Bearish single scans use call-side Phantom and Specter

| Field | Record |
|---|---|
| Classification | Confirmed defect |
| Severity | High |
| Evidence | `single_scan` calls `scan_gate2(ticker, "CALL", price)` at line 2785 and `scan_gate4(ticker, "CALL", ...)` at line 2820. Direction is chosen later, at lines 3003–3012. The put branches in `scan_gate2` (consensus, target gap, downgrades, insider sales, negative revisions) and in `scan_gate4` (a −20% one-month move) never run for a single scan. Batch calls both functions with CALL or PUT (lines 1079 and 1128). NVDA scan 153 is Bearish and was threat-scanned as a call: insider selling failed Specter; a one-month rally would have been the call-side “priced in” flag |
| Affected path | `single_scan` only. Batch already passes the screen direction |
| Research implications | Bearish and Neutral single-scan scores are not direction-aware in smart money or threats. They cannot be compared with batch PUT scores as if the same rules ran |
| Proposed remediation or experiment | Decide whether direction must be known before those two calls. A paper test can score one bearish card with the CALL argument and with the PUT argument and record both Phantom and Specter results. Any code change waits for an approved scoring revision |
| Dependencies | Direction rule in SSR methodology section 5. Reordering would change historical comparability, so SSR-003 should land with it |
| Validation | A bearish single scan’s stored direction argument is PUT, or the record states that the call-side rules were used on purpose |
| Status | Open |

## SSR-006 — Score saturation at 100

| Field | Record |
|---|---|
| Classification | Methodological limitation |
| Severity | Medium |
| Evidence | Both formulas end with `max(0, min(100, scout_score))`. `min(component, cap)` caps upside only. Independent extremes of the single-scan terms sum to about +117 and about −73 before the clamp. Stored reconstructions: BSX scan 210 is 101 displayed as 100; NVDA scan 153 is 103 displayed as 100; TSM scan 167 is 105 displayed as 100. Fifteen of 227 eligible rows are stored at 100 |
| Affected path | Both formulas, and every consumer of the clamped integer |
| Research implications | Scores of 100 are tied for rank even when the uncapped totals differ. Averages and correlations that include 100 understate spread |
| Proposed remediation or experiment | Store the unclamped total (SSR-003). Research can then rank ceiling names by the raw total without changing the displayed 0–100 grade |
| Dependencies | SSR-003 |
| Validation | For BSX, NVDA, and TSM, a stored raw total matches the reconstruction, while the displayed score remains 100 |
| Status | Open. The clamp is behaving as written |

## SSR-007 — Net Score reinforces direction and then saturates

| Field | Record |
|---|---|
| Classification | Methodological limitation for the +25 and ×0.65 anchor. Confirmed defect for the summary sentence |
| Severity | Medium |
| Evidence | `directionality.py` lines 516–529 add 25 to the labeled side and multiply the other side by 0.65, then clamp each side to 0–100. The comment at lines 518–520 describes this as an anchor, so the reinforcement is intentional. Lines 542–556 say the labeled side “exceeded” the other. NVDA scan 153 is Bearish with bull 84, bear 83, and Net +1, which falsifies that sentence. TSM scan 167 is Bearish with bull 54, bear 100, Net −46, where the bear side did land higher after the anchor and the ceiling |
| Affected path | Horizon display only. Production `scout_score` is unchanged |
| Research implications | Net Score is not an independent vote on direction. Values at ±100 are saturated. The summary text cannot be used as the reason the direction was chosen |
| Proposed remediation or experiment | Rewrite the summary so it names the direction rule and then reports the two piles. Keep the anchor only if a research note still wants a direction-conditioned reading, and store both the raw piles and the anchored piles |
| Dependencies | None for a copy change in the summary. Storing both piles is a capture change |
| Validation | NVDA scan 153’s summary no longer claims that bear conviction exceeded bull conviction. Recomputing Net from the stored piles matches 84, 83, and +1 |
| Status | Open |

## SSR-008 — Signal, Current, and Archer always pass

| Field | Record |
|---|---|
| Classification | Confirmed implementation gap |
| Severity | Medium |
| Evidence | `single_scan` JSON sets `signal`, `current`, and `archer` to `True` (`main.py` 3289–3291). Headlines, ETF flow, and strategy prose are still written earlier and do not affect the flags. `explainability.py` describes those gates as news available for review, supportive ETF flow, and a viable strategy |
| Affected path | Single-scan JSON and Horizon’s all-fourteen test |
| Research implications | “Passed 14” can be true when those three descriptions were not met. Gate-combination studies that include SIGNAL, CURRENT, or ARCHER have no variation from this path |
| Proposed remediation or experiment | Either implement the conditions the explanations describe, or label the three flags as unused and stop counting them in “14 of 14.” Do this only under an approved gate revision, because it changes `passed_all_gates` and therefore Horizon’s preferred pool |
| Dependencies | Historical comparability. A definition change needs SSR-003’s gate-version field |
| Validation | A fixture with no headlines, hostile ETF flow, and no viable strategy fails the flags that claim to measure those conditions, or the flags are removed from the pass count |
| Status | Open |

## SSR-009 — Fortress repeats the score floor

| Field | Record |
|---|---|
| Classification | Methodological limitation |
| Severity | Medium |
| Evidence | `fortress` is `scout_score >= 40` (`main.py` 3292). The AVOID band is score < 40 (lines 3246–3249). TLT scan 77 scores 55 with Atlas, Oracle, and Catalyst failed, and Fortress true. IWM scan 53 scores 30 and Fortress false. The sandbox text calls Fortress a risk gate and talks about position size |
| Affected path | Single-scan JSON and Horizon pass count |
| Research implications | Fortress adds no information beyond the score band. A study of “Fortress passed” is a study of score ≥ 40 |
| Proposed remediation or experiment | In research extracts, derive Fortress from the score and do not treat it as a separate factor. Any new risk rule should be a new field, not a silent change to this boolean |
| Dependencies | SSR-008 if the pass count is redefined |
| Validation | On the 227 eligible rows, Fortress matches `scout_score >= 40` with no exceptions |
| Status | Open. The redundancy is confirmed on the two named scans and on the source line. A full 227-row check has not been re-run for this document |

## SSR-010 — Compass description and Compass flag disagree

| Field | Record |
|---|---|
| Classification | Interpretation discrepancy |
| Severity | Medium |
| Evidence | The JSON passes Compass when `trend_status != "UNKNOWN"` (`main.py` 3288). A downtrend is printed with a warning (lines 2909–2911) and still passes. `explainability.py` lines 53–57 require “Price confirms trend, typically above the referenced moving average.” TLT scan 77 is a downtrend with Compass true. IWM scan 53 is UNKNOWN with Compass false. The +5 alignment term is a different rule and requires the trend to match BULLISH or BEARISH |
| Affected path | Single-scan flag, sandbox explanation, and any gate study of COMPASS |
| Research implications | A Compass pass does not mean the trend supports the direction. TLT is Bullish, in a downtrend, Compass true, and alignment bonus 0 |
| Proposed remediation or experiment | Change the explanation to “a 20-day trend was identified,” or change the flag to require agreement with direction. Those are different products. Pick one before editing. Batch Gate 5C already adjusts `total_score` for disagreement and does not fail the name |
| Dependencies | SSR-003 if the flag’s meaning changes, because old Compass passes would mean something else |
| Validation | The published definition and the boolean agree on an uptrend, a downtrend, and a missing average |
| Status | Open |

## SSR-011 — Batch Specter penalty does not affect a published score

| Field | Record |
|---|---|
| Classification | Confirmed defect in the published-score path |
| Severity | Medium |
| Evidence | Lines 1333–1334 add +5 when clean and then add `penalty`. Line 1131 drops the name when `clean` is false, which is every path that sets a news, insider, or one-month penalty. A published batch score therefore has penalty 0. The single scan applies the penalty and still returns a score. IWM scan 53 shows the single-scan side: −20 and a stored score of 30 |
| Affected path | `run_full_scan` published scores |
| Research implications | Email scores are conditional on a clean Specter result. They do not show how large a threat penalty would have been. Comparing them with single scans that survived a penalty mixes two populations |
| Proposed remediation or experiment | Keep the drop if the email should only contain clean names, and stop adding a penalty that cannot occur. If research needs the penalty, store it on the dropped candidate without publishing that name |
| Dependencies | SSR-002 if dropped names are later studied. SSR-005 for the call-versus-put flag rules |
| Validation | Every archived email pick has Specter clean and penalty 0, or the archive explicitly stores a nonzero penalty |
| Status | Open |

## SSR-012 — Call and put DCF gaps use different ratios

| Field | Record |
|---|---|
| Classification | Implementation divergence |
| Severity | Medium |
| Evidence | Single scan and call gate 1 use `(DCF − price) / price × 100` (`main.py` 2732 and 422). Put gate 1 uses `(price − DCF) / DCF × 100` (line 572). The field name is `dcf_gap` on both. Positive means undervalued on a call and overvalued on a put. `recommend_strikes` treats `dcf_gap > 30` as the extreme case for both directions (lines 1001 and 1013) |
| Affected path | Batch put scoring, put strike selection, and any shared DCF display |
| Research implications | A 30% gap on a call card and a 30% gap on a put card are not the same distance. Strike logic that wants “more than 30% of mispricing” is using two definitions |
| Proposed remediation or experiment | Store the raw DCF, the price, and the ratio identity. Decide whether puts should use the price denominator with the sign flipped, which would make 30% mean the same distance. Until that decision, label put gaps as overvaluation ratios in research extracts |
| Dependencies | None for labeling. A formula change is an approved scoring revision and would affect historical put cards |
| Validation | One price and one DCF produce the documented call ratio and the documented put ratio, and the strike rule cites which ratio it reads |
| Status | Open. Classified as a divergence because the put sign is a deliberate “overvalued is positive” construction. The shared threshold is the part that needs an explicit decision |

## SSR-013 — Tie breaks prefer earlier calls

| Field | Record |
|---|---|
| Classification | Methodological limitation |
| Severity | Low |
| Evidence | Batch sort is `sorted(call_results + put_results, key=scout_score, reverse=True)` (`main.py` 1369). Python’s sort is stable. The key ignores ticker and `total_score`. Calls are concatenated first, and calls were already ordered by gate-1 score. Horizon’s `max` in `choose_final_pick` (`run_gates.py` 178–181) keeps the first equal score |
| Affected path | Batch TOP PICK and Horizon’s list winner |
| Research implications | Two names with the same displayed score do not have an explicit rank rule. A call outranks a put at the same score. Ceiling scores make this more common (SSR-006) |
| Proposed remediation or experiment | Publish the tie order as the rule, or add an explicit second key such as unclamped total, then gate-1 score, then ticker. Changing the key changes historical winners, so record the rule version |
| Dependencies | SSR-006 if the second key is the unclamped total |
| Validation | A two-name fixture with equal scores ranks in the documented order |
| Status | Open. The current order is deterministic. It is undocumented in the product text |

## SSR-014 — Kelly size uses fixed track-record constants

| Field | Record |
|---|---|
| Classification | Unvalidated assumption |
| Severity | Medium |
| Evidence | `main.py` 1306–1322 sets win rate 0.667 and win/loss ratio 1.61, with the comment “Current track record: 4W-2L” and “Avg win 114.6% / Avg loss 71.0%.” Half-Kelly is then capped at 5%, 3%, or 1.5% from `total`, the gate-point sum. The 227-row bank was not the source of those constants. This block does not change `scout_score` |
| Affected path | Batch position-size sentence only |
| Research implications | The percentage in the email is not an estimate from Horizon-1 outcomes. Quoting it as the system’s measured edge overstates the evidence |
| Proposed remediation or experiment | Label the sentence as a fixed illustration, or replace the constants with a measured sample that names its population, date range, and sample size. Do not point it at the 227 single-scan rows and call that the email track record (SSR-002) |
| Dependencies | SSR-002 before any email-outcome measurement |
| Validation | The printed percentage either matches a cited sample or is labeled as a constant |
| Status | Open |

## SSR-015 — Recommendation words differ by surface

| Field | Record |
|---|---|
| Classification | Implementation divergence |
| Severity | Medium |
| Evidence | Cuts are 80 / 60 / 40 everywhere. Words are not. Single scan: STRONG, MODERATE, WEAK, AVOID (lines 3246–3249). Batch detail: STRONG BUY, MODERATE, WATCH, AVOID (line 1345). Batch ranking line: STRONG, MODERATE, WATCH, with no AVOID branch (line 1378), so a score under 40 prints WATCH in the ranking. Website ring in `src/pages/index.js` lines 57–59: STRONG BUY, MODERATE, WATCH, AVOID |
| Affected path | Single-scan text, batch email text, website |
| Research implications | “WEAK” and “WATCH” are not a shared category. A ranking export can call an AVOID name WATCH |
| Proposed remediation or experiment | One label table, used by the scan, the ranking line, and the website. Until then, research should store the numeric band and ignore the word |
| Dependencies | None |
| Validation | Scores 100, 79, 60, 40, and 39 produce the same word on each surface, and that word is the one in the label table |
| Status | Open |

## SSR-016 — “ALL 14 GATES CLEARED” is unconditional for batch survivors

| Field | Record |
|---|---|
| Classification | Confirmed defect |
| Severity | High |
| Evidence | Line 1348 prints the sentence for every name that reaches the score, which is every G2 and G4 survivor. Catalyst does not drop (line 1098). Earnings, trend, volatility, and the sector ETF adjust text or `total_score` and do not remove the name. Gate 5E always prints that the news scan is pending (lines 1204–1206). The fourteen single-scan flags are a different list (SSR-008, SSR-009, SSR-010) |
| Affected path | Batch email and any reader of that sentence |
| Research implications | An email claim of fourteen cleared gates cannot be coded as `passed_all_gates` from the single-scan contract |
| Proposed remediation or experiment | Print the checks that actually passed, by name. Reserve “14 gates” for the single-scan boolean list |
| Dependencies | SSR-008 and SSR-010, so the fourteen names are not reused for a shorter checklist |
| Validation | A survivor with no catalyst, an earnings date inside 14 days, and elevated IV does not receive the fourteen-gate sentence |
| Status | Open |

## SSR-017 — Score bands have no validated outcome relationship

| Field | Record |
|---|---|
| Classification | Unvalidated assumption |
| Severity | High |
| Evidence | The eligible bank has 227 single-scan rows from 2026-05-17 to 2026-06-02, classifier `b0-2026-09-28`. Stored scores include the reconstructed ceiling cases and the low cases in the interpretation guide. No audit in this series estimated a return, hit rate, or expectancy by score band, gate combination, or direction. The batch formula, the email population, and the live deployment were not part of that evidence (SSR-001, SSR-002, and the unverified deployment) |
| Affected path | Any claim that a higher score, a STRONG label, or a 14-gate pass predicts a better result |
| Research implications | Score and gate research can describe how the grade is built. It cannot yet support a performance claim |
| Proposed remediation or experiment | Pre-register a study on one population at a time. First population: the 227 single scans, with bands on the clamped score, a declared horizon, and the existing outcome fields. Second population, only after SSR-002: emailed picks, scored with the batch formula. Do not pool them. Report sample size and refuse a conclusion below a stated minimum |
| Dependencies | SSR-001, SSR-002, SSR-003, SSR-006. Outcome fields already on `scan_results` stay read-only until a study plan is approved |
| Validation | The study names its population hash, formula version, horizon, and the rule that separates single scans from email picks. Results are descriptive until the pre-registered test is run |
| Status | Open. No outcome test was run for this checkpoint |

## SSR-018 — Specter keywords can match an incidental phrase

| Field | Record |
|---|---|
| Classification | Methodological limitation |
| Severity | Medium |
| Evidence | Short-seller keywords include the substring “hindenburg” (`main.py` 943–946). IWM scan 53 records the flag “Short seller report” on the headline “the hindenburg omen flashes a warning sign,” together with an existential-risk flag. The penalties are −5 and −15. The stored score is 30, which matches `50 − 3 − 20 + 3` |
| Affected path | `scan_gate4`, both formulas. On the batch path a false flag drops the name (SSR-011). On the single path it reduces the score |
| Research implications | A Specter failure is a keyword hit, not a confirmed event. IWM’s −20 should not be read as two verified corporate events without reading the headlines |
| Proposed remediation or experiment | Require a phrase boundary or a more specific phrase list, and store the matched keyword with the headline. Replay IWM’s two headlines through the current list and the revised list |
| Dependencies | SSR-011 for how a batch drop would change. A keyword change is a scoring change and needs approval |
| Validation | The Hindenburg Omen headline does not take the short-seller penalty. A headline that names Hindenburg Research still does |
| Status | Open. Added because the IWM reconstruction identified the match. It was not in the original audit checklist |

## Deployment item, not a scoring defect

Live metadata for `friday-scout` in project `scout-493918`, region `us-central1`, was not retrieved. On 2026-11-26 the audit machine had no `gcloud` binary. The function was not invoked and its source was not downloaded. Until a describe command returns update time, generation, runtime, entry point, and service identity, this register describes the September 28 local snapshot and the stored scans. It does not describe a verified deployed revision.

## Suggested order of work

1. Retrieve deployment metadata. Decide separately whether a source download is warranted.
2. Keep the 227-row studies inside the single-scan population (SSR-002, SSR-017).
3. Define earnings-day 0 and the Compass sentence before any scoring edit (SSR-004, SSR-010).
4. Add provenance on new captures only (SSR-003).
5. Treat formula parity, call-side bearish scans, the fourteen-gate sentence, and label words as product decisions (SSR-001, SSR-005, SSR-015, SSR-016).
6. Leave Net Score, Fortress, ties, Kelly constants, and the DCF ratio as documented behavior until a study needs them changed (SSR-007, SSR-009, SSR-012, SSR-013, SSR-014).
