# Horizon checkpoint — 2026-09-28

Checkpoint for sandbox work B0 through B2.4A. This records the verified bank and the source that is ready for review. The first clean derived rebuild has not been planned or executed in this checkpoint.

## Bank

Phase A archive: `data-archives/scout_memory_pre_horizon_migration_2026-09-28.db`

Archive SHA-256: `dbfb8fb4394e4af7419ac8544d004d431c5d1cb526ef3a7b5d18a806cd8de436`

Current `scout_memory.db` SHA-256: `2fde0888f818a953520f7dc77bfc01754cfdbc4989e84e6a8f10a07f42bdcddc`

`PRAGMA integrity_check`: ok

`raw_result_json` digest: `e14857d1029dc0759b76b7a34d681f0a6f7ca053a1e6a889d44826698d2ed358`

## Provenance

`scan_results` and `observation_provenance` are both 274. Every stored observation still carries classifier version `b0-2026-09-28`.

Origins:

- production: 227
- test: 1
- synthetic: 9
- fixture: 37

Canonical research population:

- classifier identity: `b0-2026-09-28`
- eligible count: 227
- eligible hash: `eaa0e0ec2c10ff7bd5ac99fa8d9ed5cdaed763579e0a7aa4bb24e84292763fc8`

Approved provenance versions are `b0-2026-09-28` and `capture-v1`. Historical rows keep the first. Observations created after B2.4A receive the second. An unapproved version fails closed. When both versions are present in the eligible population, the stamp identity is the lexical composite `b0-2026-09-28+capture-v1`. The population hash remains the SHA-256 of eligible `observation_uid` values ordered by `scan_result_id`, one uid per line.

## Legacy derived artifacts

The clean rebuild has not occurred.

- `derived_builds`: 0
- `pattern_intelligence`: 36, every `build_id` NULL, backup 0
- `gate_alpha_metrics`: 350, every `build_id` NULL, backup 0
- `gate_intelligence_metrics`: 14, every `build_id` NULL, backup 0
- `regime_snapshots`: 243, and regime snapshots are not a derived-build artifact

## Derived publication

Pattern, Gate Alpha, and Gate Intelligence each have an explicit publisher. A build records a committed `STARTED` manifest, calculates outside the write lock, then publishes inside `BEGIN IMMEDIATE`. Inside that transaction it recaptures the population identity and aborts if the classifier identity, eligible count, or eligible hash changed. Failure leaves the previous live generation and the previous backup in place and marks the manifest `FAILED`. Success copies the previous live rows to the one-generation backup, replaces the live rows with candidates carrying `build_id`, and completes the manifest in the same commit.

Outcome refresh does not publish Gate Alpha or Gate Intelligence. Production scan capture does not publish Pattern, Gate Alpha, or Gate Intelligence.

## Provenance at capture

A new production observation is stored with its provenance in one transaction: `origin=production`, `research_eligible=1`, `parent_observation_uid` NULL, `classifier_version=capture-v1`, `observation_uid` `sr:{id}`. New explicit test and synthetic observations are written ineligible, with a parent when the writer clones a stored observation. `save_scan_result_once` does not invent provenance for an existing observation that lacks it.

## Known legacy fixture failures

These temporary-database tests still fail closed because their fixtures do not create `observation_provenance`:

- `GateIntelligenceMetricsTests.test_pass_conditioned_metrics_diverge_most_and_least`
- `BacktestEngineTests.test_new_backtest_run_is_not_legacy`
- `BacktestEngineTests.test_preview_backtest_does_not_persist`
- `BacktestEngineTests.test_run_backtest_filters_and_persists_metrics`
- `NeutralSegmentationTests.test_actionable_win_rate_excludes_neutral`

## Next step

Plan and execute the first clean derived rebuild only after checkpoint review.
