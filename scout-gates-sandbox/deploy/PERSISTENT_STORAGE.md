# Persistent Storage — Scout Gates Sandbox

## Overview

All durable sandbox state lives in two configurable locations:

1. **SQLite database** — scan memory, backtests, research jobs/findings, daily reports
2. **Reports directory** — PDF exports, `.report-registry.db`, manifest

## Environment variables

| Variable | Default (local dev) | Hosted example |
|----------|---------------------|----------------|
| `SCOUT_RESEARCH_DB_PATH` | `scout-gates-sandbox/scout_memory.db` | `/data/scout/scout_memory.db` |
| `SCOUT_REPORTS_DIR` | `exports/reports/` (repo root) | `/data/scout/reports/` |

Set both in `/etc/scout/scout-dashboard.env` on the VPS. Create directories before
first start:

```bash
mkdir -p /data/scout/reports
chown -R scout:scout /data/scout
```

## Report registry

`SCOUT_REPORTS_DIR/.report-registry.db` is created automatically beneath the reports
directory. Do not relocate it separately.

## SQLite

- WAL journal mode and `busy_timeout` are applied centrally for multi-thread safety.
- **Single writer process** — run one `dashboard.py` instance per database file.
- Scheduled research (`scheduled_research_runner.py`) should not overlap with heavy
  dashboard writes; systemd timer runs as oneshot off-peak.

## Migration from Mac

Copy existing files to the volume:

```bash
scp scout_memory.db vps:/data/scout/scout_memory.db
scp -r exports/reports/ vps:/data/scout/reports/
```

Then set `SCOUT_RESEARCH_DB_PATH` and `SCOUT_REPORTS_DIR` accordingly.

## Not consolidated in Phase 1

`scout_research_cloud.db` (GitHub Actions worker) remains separate until a later phase.
