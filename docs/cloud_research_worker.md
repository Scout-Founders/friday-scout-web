# Scout Cloud Research Worker v1

**Status:** Implemented (GitHub Actions)  
**Goal:** Run scheduled Scout research jobs and generate research findings in GitHub Actions without keeping a local Mac awake or opening the dashboard UI.

---

## What the worker does

The Cloud Research Worker runs the read-only orchestration script:

```bash
python3 scout-gates-sandbox/scheduled_research_runner.py --cloud-worker
```

Each run:

1. Validates required GitHub Actions secrets
2. Restores or creates the dedicated cloud research SQLite database
3. Optionally imports a `scout-research-snapshot` artifact (scan signal history only)
4. Optionally imports a `scout-daily-reports` artifact (Scout v6 / Monday Coffee report copies)
5. Initializes the Scout sandbox SQLite memory database
6. Creates default research jobs if they are missing
7. Runs all enabled research jobs via `preview_backtest()` analytics
8. Generates research findings from recent completed runs and ingested daily reports
9. Prints a concise terminal summary (jobs run, completed, failed, findings generated, timestamp)

The workflow also:

- Restores a cached `scout-gates-sandbox/scout_research_cloud.db` between runs when available
- Optionally imports a `scout-research-snapshot` artifact into the cloud DB before research runs
- Optionally imports a `scout-daily-reports` artifact into `research_daily_reports` before research runs
- Uploads the updated cloud research database as a workflow artifact for inspection or manual recovery

Workflow file: [`.github/workflows/scout-research-worker.yml`](../.github/workflows/scout-research-worker.yml)

---

## Daily report artifact ingest (v1)

Cloud research **does not** authenticate to production Firestore from the hosted VPS or the research-worker job itself. Daily Scout v6 / Monday Coffee reports reach research through a **research-safe JSON artifact** published by the **Scout Daily Reports Publish** GitHub Actions workflow.

### Publisher architecture

```
Production Firestore scout_reports
        ↓
GitHub Actions: Scout Daily Reports Publish
  (read-only SA via secrets; export_scout_daily_reports.py)
        ↓
artifact: scout-daily-reports
  - scout_daily_reports.json
  - scout_daily_reports.manifest.json
        ↓
GitHub Actions: Scout Cloud Research Worker
  (workflow_run chain or manual use_daily_reports_artifact)
        ↓
ingest_scout_reports.py --from-bundle-dir
        ↓
research_daily_reports → research_sent_picks
```

- Production `friday-scout` email/SMTP is unchanged.
- Hosted VPS never receives Firestore credentials.
- Overlapping export windows are safe: ingest upserts by `report_id`.
- Default publisher lookback is **7 days** (`--lookback-days`) for reliability.
- Both `email_sent=true` and `email_sent=false` reports are exported; only `email_sent=true` enters `research_sent_picks`.

### Cloud import sequence

1. Restore `scout_research_cloud.db` cache
2. Import `scout-research-snapshot` when `use_snapshot_artifact=true`
3. Import `scout-daily-reports` when chained from the publisher (`workflow_run`) or when `use_daily_reports_artifact=true`
4. Run `scheduled_research_runner.py --cloud-worker --skip-report-ingest`
5. Generate daily report findings from already-imported `research_daily_reports`
6. Continue the existing research job / findings loop

Direct Firestore ingest remains available for **local development only** via `ingest_scout_reports.py` when credentials are configured. The publisher exporter is for CI only.

### Report bundle contract (`scout_daily_reports.json`)

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-20T12:00:00+00:00",
  "reports": [
    {
      "report_id": "rep-001",
      "report_type": "scout_v6",
      "report_version": "6.0",
      "generated_at": "2026-06-20T12:00:00+00:00",
      "market_date": "2026-06-20",
      "status_prefix": "FINAL",
      "email_subject": "Scout Daily Report",
      "raw_report_text": "...",
      "structured_report_json": {},
      "source_scan_run_id": "scan-123",
      "claude_model": "claude-...",
      "underlying_data_timestamp": "2026-06-20T11:30:00+00:00",
      "email_attempted": true,
      "email_sent": true,
      "email_sent_at": "2026-06-20T12:05:00+00:00",
      "email_error": null
    }
  ]
}
```

Required per report: `report_id`, `report_type`. All other fields are optional but map to `research_daily_reports` when present.

### Manifest contract (`scout_daily_reports.manifest.json`)

```json
{
  "schema_version": 1,
  "generated_at": "2026-06-20T12:00:00+00:00",
  "report_count": 1,
  "bundle_filename": "scout_daily_reports.json",
  "checksum_sha256": "<sha256 of scout_daily_reports.json>"
}
```

The cloud worker validates the manifest checksum and report count before import.

### Local file import

```bash
cd scout-gates-sandbox
python3 ingest_scout_reports.py \
  --from-file scout_daily_reports.json \
  --generate-findings
```

Or import an artifact directory:

```bash
python3 ingest_scout_reports.py \
  --from-bundle-dir ./daily-reports-bundle \
  --generate-findings
```

### What publishes `scout-daily-reports`

The in-repo publisher workflow [`.github/workflows/scout-daily-reports-publish.yml`](../.github/workflows/scout-daily-reports-publish.yml) runs:

```bash
python3 scout-gates-sandbox/export_scout_daily_reports.py \
  --output-dir scout-gates-sandbox/daily-reports-bundle \
  --lookback-days 7 \
  --limit 100
```

It uploads GitHub Actions artifact **`scout-daily-reports`** containing:

- `scout_daily_reports.json`
- `scout_daily_reports.manifest.json`

On success, **Scout Cloud Research Worker** is triggered via `workflow_run` and downloads that artifact using the publisher run ID (no duplicated ingest logic in YAML).

Manual handoff still works:

- `use_daily_reports_artifact=true`
- `daily_reports_artifact_run_id=<publisher run id>`

### Required publisher secrets

| Secret | Required | Purpose |
|--------|----------|---------|
| `SCOUT_FIRESTORE_PROJECT_ID` | Yes (publisher) | GCP project id hosting Firestore `scout_reports` (e.g. `scout-493918`) |
| `SCOUT_FIRESTORE_SERVICE_ACCOUNT_JSON` | Yes (publisher) | Read-only service-account JSON for Firestore `scout_reports` reads |

Recommended IAM for the service account:

- Firestore / Datastore **read-only** access sufficient to list/query `scout_reports`
- **No** write roles
- **Not** installed on the hosted VPS

Workload Identity Federation is preferred long-term if the repo later adds it; v1 uses the SA JSON secret names already recognized by `ingest_scout_reports._load_firestore_client()`.

Do **not** put these secrets on the hosted sandbox / VPS dashboard environment.

---

## What it does not do

The Cloud Research Worker is **research orchestration only**. It does **not**:

- Place trades or submit orders
- Change scoring logic
- Change gate weights
- Change Stable Signal behavior
- Change recommendation logic
- Automatically promote findings into live rules
- Start the local dashboard HTTP server
- Call live scan endpoints that mutate production trading state

Research jobs use read-only backtest preview analytics over stored sandbox memory.

---

## Required GitHub Secrets

Configure these in **GitHub → Settings → Secrets and variables → Actions → Repository secrets**:

| Secret | Required | Purpose |
|--------|----------|---------|
| `SCOUT_RESEARCH_WORKER_SECRET` | Yes (worker) | Shared worker authorization token. Must be non-empty. Prevents accidental runs on forks or misconfigured repos. |
| `SCOUT_CLOUD_RESEARCH_ENABLED` | Yes (worker) | Must be set to `true` (or `1` / `yes`) to allow cloud runs. Use this as an explicit kill switch by setting it to `false` or removing it. |
| `SCOUT_FIRESTORE_PROJECT_ID` | Yes (publisher) | GCP project for read-only `scout_reports` export |
| `SCOUT_FIRESTORE_SERVICE_ACCOUNT_JSON` | Yes (publisher) | Read-only Firestore service-account JSON (publisher workflow only) |

The runner fails fast with clear stderr messages if either worker secret is missing or disabled.

**Do not hardcode secrets in the workflow file.** The workflows read them from GitHub Actions secrets only.

**Do not place Firestore credentials on the hosted VPS.** Hosted mode continues to block direct Firestore ingest.

---

## How to run manually from GitHub Actions

### A. Daily reports publisher smoke test

1. Ensure publisher secrets are configured (`SCOUT_FIRESTORE_PROJECT_ID`, `SCOUT_FIRESTORE_SERVICE_ACCOUNT_JSON`)
2. Open **Actions → Scout Daily Reports Publish**
3. Click **Run workflow** (optional: set `lookback_days`, `limit`, or explicit `since`)
4. Confirm the job uploads artifact **`scout-daily-reports`**
5. Confirm **Scout Cloud Research Worker** starts via `workflow_run` and imports the bundle
6. Inspect worker logs for daily-report import counts and research summary

### B. Research worker only

1. Open the repository on GitHub
2. Go to **Actions**
3. Select **Scout Cloud Research Worker**
4. Click **Run workflow**
5. Optional inputs:
   - **findings_limit** — number of recent completed runs to scan (default `20`)
   - **skip_findings** — run research jobs only
   - **use_snapshot_artifact** — import `scout-research-snapshot` before running research (default `false`)
   - **snapshot_artifact_run_id** — workflow run ID that uploaded the snapshot (leave empty to use the current run)
   - **use_daily_reports_artifact** — import `scout-daily-reports` before research (default `false`; automatic when chained from the publisher)
   - **daily_reports_artifact_run_id** — publisher run ID containing `scout-daily-reports`
6. Click **Run workflow**

Review the job log for the summary block:

```text
Scout scheduled research run
timestamp: ...
jobs run: ...
completed: ...
failed: ...
findings generated: ...
status: ok
```

Download the `scout-research-cloud-db-<run_id>` artifact if you need the updated SQLite file.

---

## Research snapshot sync (v1)

The cloud worker analyzes historical signals from `scout_research_cloud.db`. To import local sandbox signal history without touching `scout_memory.db`, use the snapshot export/import flow.

### 1. Create a local snapshot (read-only export)

From the repository root:

```bash
cd scout-gates-sandbox
python3 export_research_snapshot.py
```

This reads `scout_memory.db` in **read-only mode** and writes:

- `research_snapshot.db`
- `research_snapshot.manifest.json`

The source database is not modified.

Optional copy for artifact naming used by GitHub Actions:

```bash
cp research_snapshot.manifest.json research_snapshot_manifest.json
```

### 2. Publish the snapshot artifact

Copy the exported files into `scout-gates-sandbox/` in your workspace, then run the publish workflow:

1. Go to **Actions → Scout Research Snapshot Publish**
2. Click **Run workflow**

This uploads artifact **`scout-research-snapshot`** containing:

- `research_snapshot.db`
- `research_snapshot_manifest.json` (copied automatically from `research_snapshot.manifest.json` when needed)

Note the workflow **run ID** from the publish job. You will use it when importing into the cloud worker.

### 3. Run the cloud worker with snapshot import enabled

1. Go to **Actions → Scout Cloud Research Worker**
2. Click **Run workflow**
3. Set:
   - **use_snapshot_artifact** = `true`
   - **snapshot_artifact_run_id** = the publish workflow run ID
4. Run the workflow

The worker will:

1. Restore `scout_research_cloud.db` cache
2. Download artifact `scout-research-snapshot`
3. Run:

```bash
python3 scout-gates-sandbox/import_research_snapshot.py \
  --snapshot scout-gates-sandbox/snapshot-bundle/research_snapshot.db \
  --manifest <resolved manifest path> \
  --target scout-gates-sandbox/scout_research_cloud.db
```

4. Run `scheduled_research_runner.py --cloud-worker`
5. Upload updated `scout_research_cloud.db`

If snapshot import fails, the workflow stops **before** scheduled research runs.

### 4. Smoke test without snapshot import

For empty-db smoke tests or when no snapshot artifact is available:

1. Run **Scout Cloud Research Worker**
2. Leave **use_snapshot_artifact** = `false`

The worker skips snapshot import and runs against the cached/empty cloud research DB only.

Scheduled weekday runs also skip snapshot import unless you later enable it explicitly in workflow inputs (scheduled runs default to `false`).

### Local import parity

```bash
cd scout-gates-sandbox
python3 export_research_snapshot.py
python3 import_research_snapshot.py \
  --snapshot research_snapshot.db \
  --target scout_research_cloud.db
export SCOUT_RESEARCH_WORKER_SECRET='your-token'
export SCOUT_CLOUD_RESEARCH_ENABLED=true
export SCOUT_RESEARCH_DB_PATH="$(pwd)/scout_research_cloud.db"
python3 scheduled_research_runner.py --cloud-worker
```

---

## How to disable the schedule

Choose one:

1. **Kill switch (recommended):** Set repository secret `SCOUT_CLOUD_RESEARCH_ENABLED` to `false`. Scheduled and manual runs will fail fast until re-enabled.
2. **Disable cron only:** Comment out or remove the `schedule:` block in `.github/workflows/scout-research-worker.yml`.
3. **Disable workflow entirely:** Delete or rename `.github/workflows/scout-research-worker.yml`.

Manual `workflow_dispatch` remains available unless you remove the workflow file or disable Actions for the repository.

Current schedule: **weekdays at 14:00 UTC** (`0 14 * * 1-5`), roughly 6:00 AM US Pacific during standard time.

---

## Safety constraints

- Read-only research intelligence layer
- No trade placement
- No gate/scoring/Stable Signal/recommendation mutations
- Secrets loaded from GitHub Actions only
- Missing or disabled secrets cause an immediate non-zero exit (`2`) before research jobs run
- Workflow uses read-only `contents` permission
- Concurrency group prevents overlapping worker runs for the same repository ref

---

## Local parity

Run the same cloud-validated path locally (with secrets exported in your shell):

```bash
export SCOUT_RESEARCH_WORKER_SECRET='your-token'
export SCOUT_CLOUD_RESEARCH_ENABLED=true
cd scout-gates-sandbox
python3 scheduled_research_runner.py --cloud-worker
```

For local scheduled runs without cloud secret checks, omit `--cloud-worker`:

```bash
python3 scheduled_research_runner.py
```

See also `scheduled_research_runner.py` header comments for macOS `launchd` and `cron` examples.

---

## Database note

The cloud worker uses a **dedicated research database** at `scout-gates-sandbox/scout_research_cloud.db`, configured through `SCOUT_RESEARCH_DB_PATH`. It does **not** write to the local dashboard database `scout_memory.db`.

Research jobs analyze historical signals by reading `scan_results` / `scan_runs` inside that dedicated database file. On first cloud run the database may be empty until you seed a sandbox signal snapshot into the cloud research DB artifact/cache chain. Empty runs still complete safely and may produce system-note findings.

---

## Isolation audit

### What the cloud worker writes

During normal operation, data writes are limited to:

| Table | Purpose |
|-------|---------|
| `research_jobs` | Create default jobs if missing; update `last_run_at` |
| `research_job_runs` | Insert/update run records and summaries |
| `research_findings` | Insert generated findings |

`preview_backtest()` used by research jobs is **read-only** and does **not** persist `backtest_runs`, `backtest_signals`, or `backtest_metrics`.

### What the cloud worker reads (preview analytics)

Research preview analytics may read completed signal history from:

| Table | Access |
|-------|--------|
| `scan_results` | Read-only |
| `scan_runs` | Read-only |

If those tables are empty in the cloud research DB, jobs still run and may emit empty-run findings.

### What the cloud worker does not modify

The worker does **not** mutate:

- Live scan outputs (`scan_runs`, `scan_results` rows)
- Customer-facing recommendations / Research Memory saves
- Outcome audit or institutional audit tables
- Gate scoring configuration tables (`gate_intelligence_metrics`, `gate_alpha_metrics`, `gate_attributions`, `regime_snapshots`)
- Stable Signal stored fields on recommendations (`explanation_json`, gate snapshots on saved rows)
- Production/customer memory outside the dedicated cloud research DB file
- Email/PDF report registry tables (stored separately under `exports/reports/registry.db`)

Report generation uses a separate SQLite registry in `exports/reports/` and is not invoked by the cloud worker.

### Shared DB risk and mitigation

**Prior risk:** The workflow originally cached `scout_memory.db`, the same file used by the local dashboard. Even though research code only inserted into research tables, `init_db()` could still apply schema migrations/indexes against the shared file.

**Mitigation (v1):**

- Cloud runs set `SCOUT_RESEARCH_DB_PATH=scout-gates-sandbox/scout_research_cloud.db`
- `--cloud-worker` configures that dedicated path before any database initialization
- GitHub Actions cache/artifacts use the cloud research DB only

Local dashboard usage continues to default to `scout_memory.db` when `SCOUT_RESEARCH_DB_PATH` is unset.

Optional override:

```bash
export SCOUT_RESEARCH_DB_PATH=/path/to/custom_research.db
python3 scheduled_research_runner.py --cloud-worker
```
