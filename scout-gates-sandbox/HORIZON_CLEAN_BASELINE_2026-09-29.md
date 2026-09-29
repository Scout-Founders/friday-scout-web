# Horizon clean baseline — 2026-09-29

Authoritative baseline after the first clean derived publications. C1 Pattern, C2 Gate Alpha, and C3 Gate Intelligence are complete. This checkpoint is read-only with respect to `scout_memory.db`. No publication, migration, scan, outcome refresh, or feature refresh was run while it was written.

Git checkpoint: branch `feature/daily-picks`, commit `b1ce37b3239f1432559f22190aaa0e92a120893a`. That commit is the source checkpoint. This document is a later untracked record of the published bank.

## Bank identity

Phase A archive: `data-archives/scout_memory_pre_horizon_migration_2026-09-28.db`

Archive SHA-256: `dbfb8fb4394e4af7419ac8544d004d431c5d1cb526ef3a7b5d18a806cd8de436`

Current `scout_memory.db` SHA-256: `fafc3682d898d0b197a079d6d8a191f51f1d7c02d5918315d92730c49c0d0d34`

`PRAGMA integrity_check`: ok

`raw_result_json` digest: `e14857d1029dc0759b76b7a34d681f0a6f7ca053a1e6a889d44826698d2ed358`

| Table | Rows |
|---|---:|
| `scan_results` | 274 |
| `observation_provenance` | 274 |
| `regime_snapshots` | 243 |

The live hash differs from the Phase A archive and from the pre-rebuild checkpoint `2fde0888f818a953520f7dc77bfc01754cfdbc4989e84e6a8f10a07f42bdcddc` because C1, C2, and C3 published derived generations. The archive file was not modified.

## Canonical evidence population

`assert_research_population` passes.

| Origin | Rows |
|---|---:|
| production | 227 |
| test | 1 |
| synthetic | 9 |
| fixture | 37 |

Eligible rows: 227. Excluded rows: 47. Parent-linked observations: 10.

Every eligible row has `origin = production` and `parent_observation_uid` NULL. No test, synthetic, fixture, or production child is eligible.

Classifier identity: `b0-2026-09-28`

Eligible population hash: `eaa0e0ec2c10ff7bd5ac99fa8d9ed5cdaed763579e0a7aa4bb24e84292763fc8`

The hash is the SHA-256 of eligible `observation_uid` values ordered by `scan_result_id`, one uid per line. `generated_at` is not part of the identity.

Approved provenance versions are `b0-2026-09-28` and `capture-v1`. Every stored observation in this bank is still `b0-2026-09-28`. An unapproved classifier version fails closed. When an eligible `capture-v1` observation later joins this population, the stamp identity becomes the lexical composite `b0-2026-09-28+capture-v1` and the eligible hash changes. No production capture has exercised `capture-v1` in the live database yet.

## Provenance architecture

Eligibility is `observation_provenance.research_eligible = 1`. Readers use that flag. They do not re-derive eligibility from `is_test_record`, API URL, pick mode, ticker, or id ranges.

A new production observation is stored with its provenance in one transaction: `origin = production`, `research_eligible = 1`, parent NULL, `classifier_version = capture-v1`, `observation_uid = sr:{id}`. Explicit test and synthetic writers record ineligible provenance and a parent when they clone a stored observation. `save_scan_result_once` does not invent provenance for an existing observation that lacks it.

## Clean derived generations

`derived_builds` contains exactly three rows. All three are `COMPLETED`. There is no `STARTED`, `FAILED`, or `ROLLED_BACK` manifest. Each manifest records classifier `b0-2026-09-28`, eligible count 227, and the eligible hash above. Each live table count equals that manifest's `artifact_row_count`.

### Pattern — C1

- build_id: `pattern-pattern-1-20260929T130814Z-bd3a61ed`
- artifact_type: `pattern_intelligence`
- builder_version: `pattern-1`
- started_at: `2026-09-29T13:08:14Z`
- built_at: `2026-09-29T13:08:14.759028+00:00`
- live rows: 36, all on this build id, no duplicate pattern ids
- source: 215 eligible completed WIN/LOSS/FLAT observations

### Gate Alpha — C2

- build_id: `gate-alpha-gate-alpha-1-20260929T131618Z-fb09a847`
- artifact_type: `gate_alpha_metrics`
- builder_version: `gate-alpha-1`
- started_at: `2026-09-29T13:16:18Z`
- built_at: `2026-09-29T13:16:18.396646+00:00`
- live rows: 350, all on this build id, no duplicate segment keys
- source: 205 eligible completed attributed observations and 2,870 attributions

### Gate Intelligence — C3

- build_id: `gate-intelligence-gate-intelligence-1-20260929T131948Z-3f2573be`
- artifact_type: `gate_intelligence_metrics`
- builder_version: `gate-intelligence-1`
- started_at: `2026-09-29T13:19:48Z`
- built_at: `2026-09-29T13:19:48.439027+00:00`
- live rows: 14, all on this build id, no duplicate gate keys
- source: 117 eligible completed Bullish/Bearish observations
- clean `total_occurrences`: 117 on every gate

A read-only recalculation during this checkpoint reproduced those three source sizes and the same 36, 350, and 14 identities.

## Legacy backups

| Backup table | Rows | `build_id` |
|---|---:|---|
| `pattern_intelligence_backup` | 36 | all NULL |
| `gate_alpha_metrics_backup` | 350 | all NULL |
| `gate_intelligence_metrics_backup` | 14 | all NULL |

These backups are the only in-database copies of the immediately preceding legacy generations. A second publication of an artifact would replace that artifact's backup with the generation that is live now. This checkpoint did not publish again.

The Pattern backup still holds the pre-clean metrics for the four patterns C1 replaced: `d9401b8a62453dd2`, `aa2404e0b9f74140`, `271ac032efcbe7e8`, and `4acfeac269a7a96b`. The other 32 pattern metric rows still match the clean generation. The Gate Alpha backup still holds the pre-exclusion samples for the 70 segments that lost test observation 31, including unclassified sample 94 / wins 68 and the global slices at sample 206 / wins 123. The other 280 Gate Alpha metric rows still match the clean generation. The Gate Intelligence backup still has `total_occurrences = 225` on all 14 gates, including specter win rate 54.12 and meridian win rate 52.33.

## Semantic corrections

Pattern kept all 36 identities. Four patterns changed because the builder now reads only eligible completed outcomes. Their 20-day averages are null because the eligible population has no numeric `return_20d`.

Gate Alpha kept all 350 identities. Seventy segments each lost one winning sample when test observation 31 left the source. Twenty-eight unclassified segments moved from sample 94 / wins 68 / win rate 72.34 to sample 93 / wins 67 / win rate 72.04. Losses on those segments stayed 26. The other 42 changed segments are the global and unknown-regime slices of the same observation.

Gate Intelligence kept all 14 identities and changed every metric row. Confidence labels stayed `Medium`. Specter win rate moved from 54.12 to 75.00. Meridian win rate moved from 52.33 to 46.59. Clean 20-day averages are null. Specter's legacy 20-day average was already null.

The occurrence change is the existing formula applied to the canonical selector:

- 117 eligible completed Bullish/Bearish observations are counted
- 98 eligible completed Neutral observations stay in the bank and stay out of Gate Intelligence
- 10 completed observations are research-ineligible
- 117 + 98 + 10 = 225, the legacy occurrence base

The current formula was not changed in this checkpoint.

## Architectural invariants

Current source matches the publication design. No deviation was found in this audit.

- Research Memory aggregates select with `RESEARCH_ELIGIBLE_SQL`.
- Live backtest selection asserts the research population and uses `research_eligible_predicate("sr")`.
- Historical replay reads stored `backtest_signals` for a run and does not reapply the eligibility predicate.
- Pattern, Gate Alpha, and Gate Intelligence candidate builders each require canonical eligibility.
- Production capture writes production provenance in the same transaction as the scan row, before the feature-store commit.
- Test and synthetic capture write explicit ineligible provenance with a parent.
- An unapproved classifier version fails closed.
- Saving a scan does not call the Pattern, Gate Alpha, or Gate Intelligence publishers.
- Outcome refresh reports Gate Alpha and Gate Intelligence updates as 0 and does not call those publishers.
- Each artifact publishes only through its explicit rebuild path.
- Each publisher recaptures the population stamp inside `BEGIN IMMEDIATE` and aborts when the classifier identity, eligible count, or eligible hash differs.
- A failure rolls the publication transaction back and marks the manifest `FAILED`, leaving the previous live rows in place.
- A success stamps every new live row with `build_id` and completes the manifest in that same commit.

## Known limitations

These are recorded and were not changed in C4.

A. Each backup holds one prior generation. The next publication of that artifact overwrites it.

B. `mark_rolled_back` moves a completed manifest to `ROLLED_BACK`. It does not copy backup rows back into the live table.

C. Five legacy tests still fail closed because their temporary fixtures do not create `observation_provenance`:

- `GateIntelligenceMetricsTests.test_pass_conditioned_metrics_diverge_most_and_least`
- `BacktestEngineTests.test_new_backtest_run_is_not_legacy`
- `BacktestEngineTests.test_preview_backtest_does_not_persist`
- `BacktestEngineTests.test_run_backtest_filters_and_persists_metrics`
- `NeutralSegmentationTests.test_actionable_win_rate_excludes_neutral`

D. `regime_snapshots` are capture-time rows. They are not a manifest-backed derived generation. The table still has 243 rows.

E. Historical rows use `b0-2026-09-28`. Future production observations use `capture-v1`. The population contract allows both versions to coexist.

F. No new production capture has yet written `capture-v1` into the live database.

## Next phase

The next phase is the Horizon and sandbox structural cleanup and information architecture. It is not another intelligence rebuild.

The evidence foundation is now trustworthy enough to organize the product around it:

1. Treat Horizon-1 as the primary quant, research, and learning backend.
2. Separate operational controls from research intelligence.
3. Reduce dashboard and tab overload while keeping the existing capability.
4. Give the system clear domains: System / Health, Live Scanner, Research Memory, Backtests, Horizon Intelligence, Experiments / Sandbox, and Data / Provenance.
5. Put a concise summary at the top of each domain, with drill-down views underneath.
6. Preserve the data bank and the analytical depth behind those summaries.
7. Keep historical evidence intact. Cleanup of the interface must not delete or rewrite the bank.

Do not start that architecture from this checkpoint document. It is the baseline the next phase should read.
