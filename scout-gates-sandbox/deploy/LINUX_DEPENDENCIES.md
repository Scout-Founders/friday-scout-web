# Linux Dependencies — Scout Gates Sandbox

## Required

| Package | Purpose |
|---------|---------|
| `python3` (3.11+) | Dashboard, research engines, scheduled runner |
| `git` | Engine version metadata (optional but recommended) |

Python dependencies are stdlib-only for the sandbox core. No `pip install` required
for dashboard operation unless you add optional packages (e.g. `google-cloud-firestore`
for local Firestore ingest — **not used on hosted v1**).

## PDF export (optional)

Install one of:

| Package | Notes |
|---------|-------|
| `chromium` or `chromium-browser` | Debian/Ubuntu |
| `google-chrome-stable` | Google Chrome `.deb` |
| Snap `chromium` | `/snap/bin/chromium` supported |

The PDF renderer discovers binaries at common Linux paths and via `PATH`
(`google-chrome`, `google-chrome-stable`, `chromium`, `chromium-browser`).

## Hosted tunnel (Phase 2 — not configured in Phase 1)

| Package | Purpose |
|---------|---------|
| `cloudflared` | Cloudflare Tunnel to localhost:8765 |

## systemd

Templates assume:

- Application root: `/opt/friday-scout-web/scout-gates-sandbox`
- Environment file: `/etc/scout/scout-dashboard.env`
- Service user: `scout`

Adjust paths in unit files if your layout differs.
