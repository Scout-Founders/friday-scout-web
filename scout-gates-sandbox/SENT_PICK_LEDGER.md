# Scout Sent-Pick Ledger v1

Research-only historical ledger of stock picks that Scout **actually emailed**.

## Data flow

```
Production Firestore `scout_reports`
        ↓  GitHub Actions publisher (read-only export)
scout-daily-reports artifact
        ↓  sandbox ingest (artifact bundle)
research_daily_reports
        ↓  (parse raw_report_text when email_sent = true)
research_sent_picks
```

## Table roles

| Table | Meaning |
|-------|---------|
| `research_daily_reports` | Complete report/archive history (sent and unsent), including `structured_report_json.picks` as **scan candidates** |
| `research_sent_picks` | Only emailed picks parsed from `raw_report_text` when `email_sent = true` |

Unsent / failed sends remain in `research_daily_reports` for audit. They do **not** appear in `research_sent_picks`.

## Critical distinction

| Source | Meaning |
|--------|---------|
| `raw_report_text` | Authoritative SMTP body. Claude TOP / SECONDARY / WATCH classifications. |
| `structured_report_json.picks` | Deterministic gate output (`passed[:6]`). **Scan candidates only.** |

These are **not** interchangeable.

## Authoritative sent definition

A pick is a **sent pick** only when its parent report has:

```text
email_sent == true
```

and the deterministic email parser extracts a high-confidence TOP / SECONDARY / WATCH entry from `raw_report_text`.

Parse modes:

- `editorial` — normal Claude email body
- `raw_scan_fallback` — Claude empty; raw scan dump emailed (FINAL RANKINGS preserved on report, not fabricated as editorial picks)
- `unrecognized` — no safe pick extraction

## Identity

`sent_pick_id = report_id|email_classification|TICKER`  
(with `|N` ordinal suffix if the same classification+ticker appears more than once)

Re-ingest upserts by `sent_pick_id` and removes stale rows for that `report_id`.

## API

```text
GET /api/research-sent-picks?ticker=&direction=&reportType=&startDate=&endDate=&reportId=&emailClassification=&limit=
GET /api/research-sent-picks/recent?limit=
GET /api/research-daily-reports/detail?reportId=
```

When `reportId` is supplied, the sent-picks response also includes `scanCandidates` from `structured_report_json.picks`.

## Research Intelligence

- Summary metrics: total sent picks, sent reports represented, latest date
- Finding type: `sent_pick_observation` (separate from `daily_report_observation`)
- Dashboard section: **Sent Picks** (emailed picks table)

## Out of scope (v1)

- Production friday-scout / SMTP / scoring / gates changes
- Firestore auth or hosted-mode policy changes
- Performance attribution claims
- Treating `passed[:6]` as official emailed picks
