# Scout v6 / Monday Coffee Report Pipeline Audit

**Repository:** Friday Scout (this codebase)  
**Audit date:** 2026-08-22  
**Scope:** Read-only investigation — no code changes

---

## Executive Summary

**Is this the production report backend?** **NO**

This repository is a **research/sandbox consumer** of reports that are already generated elsewhere. It **reads** Firestore `scout_reports`, mirrors them into local SQLite, and derives research findings. It does **not** run the full production pipeline:

```
scheduled trigger → gather market/Scout data → call Claude → generate Scout v6 / Monday Coffee
→ persist to Firestore scout_reports → send email
```

---

## 1. Is this the production report backend?

**NO**

The ingest module states the scope explicitly:

**File:** `scout-gates-sandbox/ingest_scout_reports.py`

```python
"""Research-only mirror of Scout Firestore daily reports (Scout v6 / Monday Coffee).

One-way ingest:
  Firestore scout_reports (read-only)
  → research_daily_reports (SQLite research DB)
  → research_findings (optional adapter)

Never writes to Firestore, SMTP, live scans, scoring, gates, or trades.
"""
```

---

## 2. Exact file/function generating Scout v6

**None in this codebase.**

Scout v6 is only recognized as a **report type alias** for already-produced reports:

**File:** `scout-gates-sandbox/ingest_scout_reports.py`  
**Function:** `normalize_report_type()`

Aliases mapped to `daily_scan`:
- `scout_v6`
- `scout-v6`
- `v6`
- `scout_v6_daily`
- `daily_scan`
- `daily`

The closest related logic is **findings extraction** from ingested Scout v6 structured JSON (not report generation):

| Function | File |
|----------|------|
| `finding_payloads_from_daily_report()` | `scout-gates-sandbox/ingest_scout_reports.py` |
| `generate_findings_from_daily_reports()` | `scout-gates-sandbox/ingest_scout_reports.py` |

There is no Claude call, scan orchestration, or report text generation for Scout v6 in this repo.

---

## 3. Exact file/function generating Monday Coffee

**None in this codebase.**

Monday Coffee is also only normalized and ingested:

**File:** `scout-gates-sandbox/ingest_scout_reports.py`  
**Function:** `normalize_report_type()`

Aliases mapped to `monday_coffee`:
- `monday_coffee`
- `monday-coffee`
- `monday coffee`
- `coffee`

Monday Coffee–specific logic exists only inside `finding_payloads_from_daily_report()` (approx. lines 679–739) to turn already-ingested structured JSON into research findings — **not** to generate the report itself.

---

## 4. Exact function writing `scout_reports`

**None — no Firestore writes to `scout_reports` exist in this repo.**

Only **read** access exists:

| Function | File | Action |
|----------|------|--------|
| `fetch_scout_report_documents()` | `scout-gates-sandbox/ingest_scout_reports.py` | Reads from Firestore collection `scout_reports` |
| `_load_firestore_client()` | `scout-gates-sandbox/ingest_scout_reports.py` | Creates read-only Firestore client |

What this repo **does** write is a local SQLite mirror:

| Function | Destination |
|----------|-------------|
| `ingest_scout_report_document()` | SQLite table `research_daily_reports` |
| `ingest_scout_reports()` | Batch wrapper for the above |

---

## 5. Exact function sending the email

**None.**

No `send_alert`, SMTP, `smtplib`, or mail-sending code exists anywhere in the repo.

Email-related fields are only **read** from Firestore documents in `normalize_firestore_document()`:
- `email_subject` / `emailSubject`
- `email_attempted` / `emailAttempted`
- `email_sent` / `emailSent`
- `email_sent_at` / `emailSentAt`
- `email_error` / `emailError`

---

## 6. Exact scheduler/trigger in this repo

| Trigger | File | What it actually does |
|---------|------|----------------------|
| GitHub Actions cron `0 14 * * 1-5` (weekdays 14:00 UTC) | `.github/workflows/scout-research-worker.yml` | Runs cloud research worker |
| `run_scheduled_research()` | `scout-gates-sandbox/scheduled_research_runner.py` | Runs research jobs; **optionally ingests** Firestore reports |

This scheduler is for **research orchestration**, not report generation.

The sandbox explicitly states it does not touch the production scheduler:

**File:** `scout-gates-sandbox/memory_store.py`

```python
{"name": "Scheduler", "status": "WARNING", "detail": "Production scheduler not touched by sandbox."}
```

---

## 7. Where the final report object exists after email

| Location | Role |
|----------|------|
| **Firestore `scout_reports`** | Production source of truth (written by external backend; read here) |
| **SQLite `research_daily_reports`** | Local research mirror after ingest via `ingest_scout_report_document()` |
| **`research_findings`** | Derived observations from structured JSON — not the report itself |

After ingest, reports are available via:
- `get_latest_ingested_report()`
- `list_ingested_daily_reports()`

Both in `scout-gates-sandbox/ingest_scout_reports.py`.

---

## 8. Actual fields stored in `scout_reports`

As understood from `normalize_firestore_document()` in `scout-gates-sandbox/ingest_scout_reports.py` (supports snake_case and camelCase):

| Field | Aliases |
|-------|---------|
| `report_id` | `reportId`, Firestore doc `id` |
| `report_type` | `reportType` |
| `report_version` | `reportVersion` |
| `market_date` | `marketDate` |
| `generated_at` | `generatedAt` |
| `status_prefix` | `statusPrefix` |
| `email_subject` | `emailSubject` |
| `raw_report_text` | `rawReportText` |
| `structured_report_json` | `structuredReportJson`, `structured_report`, `structuredReport` |
| `source_scan_run_id` | `sourceScanRunId` |
| `claude_model` | `claudeModel` |
| `underlying_data_timestamp` | `underlyingDataTimestamp` |
| `email_attempted` | `emailAttempted` |
| `email_sent` | `emailSent` |
| `email_sent_at` | `emailSentAt` |
| `email_error` | `emailError` |

### Structured JSON examples (from test fixtures)

**Scout v6 (`daily_scan`):**
- `regime`
- `leadingSectors`
- `laggingSectors`
- `directionalCaution`
- `tickers`

**Monday Coffee:**
- `macroRisks`
- `earningsConcentration`
- `activeTradeConcerns`

---

## 9. Where the real backend likely lives

References in this repo point to external systems:

| Reference | Location | Implication |
|-----------|----------|-------------|
| GCP project `scout-493918` | `src/firebase.js` | Firebase/Firestore host |
| Cloud Function `friday-scout` | `scout-gates-sandbox/run_gates.py`, `src/pages/index.js` | `https://us-central1-scout-493918.cloudfunctions.net/friday-scout` — gate scoring API only; **source not in this repo** |
| `"Production scheduler not touched by sandbox"` | `scout-gates-sandbox/memory_store.py` | Real report scheduler is external |
| `"Horizon-Flow"` | Various docs under `docs/` | Referenced as a **separate** external workflow system — not implemented here |

### Search terms with zero matches in this codebase

These terms were searched and **not found** anywhere:
- `GOOD MORNING RANGERS`
- `MACRO BRIEF`
- `claude_analyze`
- `run_scan`
- `send_alert`
- SMTP / email sending implementation

---

## What this repo actually does with reports

```
[External production backend]
  scheduled trigger
    → gather market/Scout data
    → call Claude
    → generate Scout v6 / Monday Coffee
    → write Firestore scout_reports
    → send email
        │
        ▼ (read-only)
[this repo: ingest_scout_reports.py]
  → SQLite research_daily_reports
  → research_findings (optional)
```

### Related but separate: PDF reporting

The `scout-gates-sandbox/reporting/` package generates **per-ticker scoring-breakdown PDFs** from scan JSON. This is **unrelated** to Scout v6 / Monday Coffee email reports.

---

## Key entry points in this repo (consumer side only)

| Entry point | File | Purpose |
|-------------|------|---------|
| `ingest_scout_reports()` | `scout-gates-sandbox/ingest_scout_reports.py` | Batch ingest from Firestore |
| `POST /api/research-daily-reports/ingest` | `scout-gates-sandbox/dashboard.py` | HTTP trigger for ingest |
| `run_scheduled_research()` | `scout-gates-sandbox/scheduled_research_runner.py` | Scheduled research + optional ingest |
| Scout Cloud Research Worker | `.github/workflows/scout-research-worker.yml` | GitHub Actions scheduled runner |

---

## Conclusion

This codebase is **not** the production Scout v6 / Monday Coffee report backend. It is a downstream research layer that consumes already-generated reports from Firestore `scout_reports`. The actual report generation, Claude analysis, Firestore writes, and email delivery live in an external system (likely tied to GCP project `scout-493918` and/or a separate "Horizon-Flow" orchestration layer not present in this repository).
