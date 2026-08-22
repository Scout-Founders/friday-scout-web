# Scout Gates Sandbox — VPS Deployment Package (Phase 2A)

Repository templates and scripts for deploying `scout-gates-sandbox` on Ubuntu 24.04.
**No secrets, DNS, Cloudflare, or VPS provisioning are performed from this repo.**

## Contents

| File | Purpose |
|------|---------|
| `install_host.sh` | Prepare fresh Ubuntu host (packages, user, directories) |
| `migrate_local_data.sh` | Prepare/install Mac → VPS data migration bundle |
| `scout-backup.sh` | SQLite-safe backup + reports archive |
| `verify_host.sh` | Readiness checks (no secrets printed) |
| `requirements-hosted.txt` | Hosted Python deps (stdlib-only for v1) |
| `scout-dashboard.env.example` | Environment variable names + recommended hosted values |
| `scout-dashboard.service` | systemd unit for dashboard |
| `scout-scheduled-research.service` | Oneshot scheduled research |
| `scout-scheduled-research.timer` | Weekday off-peak timer |
| `SMOKE_TEST_CHECKLIST.md` | Post-deploy verification |
| `LINUX_DEPENDENCIES.md` | Package reference |
| `PERSISTENT_STORAGE.md` | Volume layout |

## Async scan jobs (Phase 1.5)

- `POST /api/scan-jobs` — queue scan (same body as `/api/run`)
- `GET /api/scan-jobs/{jobId}` — poll progress/result
- One scan at a time; additional jobs queue
- `POST /api/run` remains for backward compatibility

## Persistent layout

| Path | Purpose |
|------|---------|
| `/opt/friday-scout-web/` | Git clone + `.venv` |
| `/data/scout/scout_memory.db` | `SCOUT_RESEARCH_DB_PATH` |
| `/data/scout/reports/` | `SCOUT_REPORTS_DIR` |
| `/etc/scout/scout-dashboard.env` | Secrets + config |
| `/var/backups/scout/` | Local backup staging |

---

## Deployment runbook (exact sequence)

### 1. Provision Ubuntu 24.04 VPS

Hetzner CX22 (2 vCPU, 4 GB RAM, 40 GB) or equivalent. SSH key access only.

### 2. Run host install (on VPS as root)

```bash
git clone https://github.com/Scout-Founders/friday-scout-web.git /opt/friday-scout-web
sudo bash /opt/friday-scout-web/scout-gates-sandbox/deploy/install_host.sh
```

### 3. Clone repo (if not already)

```bash
git clone https://github.com/Scout-Founders/friday-scout-web.git /opt/friday-scout-web
chown -R root:scout /opt/friday-scout-web
chmod -R g+rX /opt/friday-scout-web
```

### 4. Checkout deployment branch

```bash
cd /opt/friday-scout-web
git fetch origin
git checkout backtesting-foundation-v1
```

### 5. Create Python venv (stdlib-only for v1)

```bash
cd /opt/friday-scout-web
python3.12 -m venv .venv
chown -R root:scout .venv
```

No `pip install` required for hosted v1 — see `requirements-hosted.txt`.

### 6. Environment file

```bash
sudo install -o root -g scout -m 640 \
  /opt/friday-scout-web/scout-gates-sandbox/deploy/scout-dashboard.env.example \
  /etc/scout/scout-dashboard.env
sudo nano /etc/scout/scout-dashboard.env
```

Set `FMP_API_KEY` on the host. Verify paths match `/data/scout/`.

### 7. Migrate data from Mac

**On Mac** (stop local dashboard first):

```bash
cd /path/to/friday-scout-web/scout-gates-sandbox/deploy
./migrate_local_data.sh prepare \
  --repo-root /path/to/friday-scout-web \
  --output ~/scout-migration-bundle
rsync -avz ~/scout-migration-bundle/ scout@VPS:/tmp/scout-migration-bundle/
```

**On VPS**:

```bash
sudo bash /opt/friday-scout-web/scout-gates-sandbox/deploy/migrate_local_data.sh install \
  --bundle /tmp/scout-migration-bundle --dry-run
sudo bash /opt/friday-scout-web/scout-gates-sandbox/deploy/migrate_local_data.sh install \
  --bundle /tmp/scout-migration-bundle
```

### 8. Install systemd units

```bash
sudo cp /opt/friday-scout-web/scout-gates-sandbox/deploy/scout-dashboard.service /etc/systemd/system/
sudo cp /opt/friday-scout-web/scout-gates-sandbox/deploy/scout-scheduled-research.service /etc/systemd/system/
sudo cp /opt/friday-scout-web/scout-gates-sandbox/deploy/scout-scheduled-research.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable scout-dashboard.service
sudo systemctl enable scout-scheduled-research.timer
```

### 9. Start dashboard

```bash
sudo systemctl start scout-dashboard.service
sudo systemctl status scout-dashboard.service
journalctl -u scout-dashboard -n 50 --no-pager
```

### 10. Verify host

```bash
sudo bash /opt/friday-scout-web/scout-gates-sandbox/deploy/verify_host.sh
```

### 11. SSH port-forward test (from laptop)

```bash
ssh -L 8765:127.0.0.1:8765 scout@VPS_HOST
```

Open http://127.0.0.1:8765 locally.

### 12. Smoke tests

Follow [SMOKE_TEST_CHECKLIST.md](./SMOKE_TEST_CHECKLIST.md).

### 13. Cloudflare (Phase 2B only)

Do not configure Tunnel/Access until steps 1–12 pass.

---

## Backup

```bash
sudo RETENTION_DAYS=14 bash /opt/friday-scout-web/scout-gates-sandbox/deploy/scout-backup.sh
```

## Scheduled research

```bash
sudo systemctl start scout-scheduled-research.timer
sudo systemctl list-timers scout-scheduled-research.timer
```

## Dashboard command (reference)

```bash
/opt/friday-scout-web/.venv/bin/python3 dashboard.py \
  --host 127.0.0.1 \
  --port 8765 \
  --hosted \
  --no-open
```

Environment from `/etc/scout/scout-dashboard.env`.
