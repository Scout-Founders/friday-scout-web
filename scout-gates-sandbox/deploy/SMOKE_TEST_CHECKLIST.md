# Sandbox Cloud v1 — Smoke Test Checklist

Run after VPS dashboard is reachable (SSH port-forward or Cloudflare Access).

## Prerequisites

- [ ] `deploy/verify_host.sh` passes (or only health warning if service stopped)
- [ ] `/etc/scout/scout-dashboard.env` populated (FMP key set)
- [ ] `scout-dashboard.service` active

## Tests (order)

| # | Test | Pass criteria |
|---|------|---------------|
| 1 | **Health** | `curl -s http://127.0.0.1:8765/api/health` → `ok: true`, no secrets in JSON |
| 2 | **Dashboard** | Browser loads `/`, universe presets appear |
| 3 | **One-ticker async scan** | Run Gates (1 ticker) → completes, results render |
| 4 | **Multi-ticker async scan** | 3–5 tickers → completes without timeout; note elapsed time |
| 5 | **Save scan** | Save Results → memory history shows new run |
| 6 | **Backtest** | Preview + run completes |
| 7 | **Research Queue** | List jobs; run one job |
| 8 | **Findings** | List; generate from recent runs |
| 9 | **Candidates** | List; generate from open findings |
| 10 | **Validations** | List; run pending validations |
| 11 | **Research Intelligence** | Dashboard API returns data |
| 12 | **PDF export** | Export scoring breakdown PDF; download works |
| 13 | **Scheduled research** | `systemctl start scout-scheduled-research.service` → journal shows success |
| 14 | **Restart persistence** | `systemctl restart scout-dashboard` → history + DB intact |

## Async scan notes

- UI uses `POST /api/scan-jobs` + polling (not long `POST /api/run`)
- One scan runs at a time; additional jobs queue

## Record

- Multi-ticker wall time (step 4)
- Any 524/timeout if testing through Cloudflare (Phase 2B)
