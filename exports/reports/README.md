# Scout Report Exports

Scalable PDF export storage and registry for the Scout gate sandbox.

## Storage layout

| Path | Purpose |
|------|---------|
| `*.pdf` | Generated scoring-breakdown reports |
| `*.html` | Optional HTML artifacts (same basename as PDF) |
| `.report-registry.db` | SQLite registry (reports + jobs) |
| `.report-manifest.json.bak` | Legacy JSON manifest after migration |

## Pipeline stages

1. **validate** — scan payload is exportable
2. **build_context** — scores, gates, percentiles, explanations
3. **render_html** — terminal-style template
4. **render_pdf** — headless Chrome
5. **register** — SQLite registry + idempotency key

## Scalability features

- **SQLite registry** — indexed queries, pagination, 5k report retention
- **Job queue** — `queued` → `running` → `completed` / `failed`
- **Background worker** — single Chrome worker (avoids render contention)
- **Idempotency** — `{reportType}:{scanSessionId}:{TICKER}` dedupes exports
- **Batch export** — `tickers: ["AAPL","MSFT"]` enqueues multiple jobs
- **Async by default in UI** — non-blocking HTTP; poll job status

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/reports/export-pdf` | Export one ticker (`async`, `tickers[]`) |
| `GET` | `/api/reports/jobs/{jobId}` | Poll job status / result |
| `GET` | `/api/reports/jobs?status=queued&batchId=` | List jobs (paginated) |
| `GET` | `/api/reports/list?limit=&offset=&ticker=` | List completed reports |
| `GET` | `/api/reports/status` | Worker + queue + registry health |
| `GET` | `/api/reports/download/{filename}` | Download PDF |

### Export body example

```json
{
  "scan": { "ok": true, "results": [], "finalPick": {} },
  "ticker": "AAPL",
  "async": true,
  "tickers": ["AAPL", "MSFT"]
}
```

## CLI

```bash
cd scout-gates-sandbox
python3 -m reporting.cli status
python3 -m reporting.cli list --limit 20 --offset 0
python3 -m reporting.cli jobs --status queued
python3 -m reporting.cli export ./scan.json --ticker AAPL --async
```

## Environment

| Variable | Default | Description |
|----------|---------|-------------|
| `SCOUT_REPORTS_DIR` | `exports/reports/` | Output directory |
| `SCOUT_REPORTS_ASYNC` | `false` | Default async for API when `async` omitted |

## Requirements

- Google Chrome or Chromium (headless PDF rendering)
