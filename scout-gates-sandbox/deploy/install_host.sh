#!/usr/bin/env bash
# Scout Gates Sandbox — Ubuntu 24.04 host preparation (Phase 2A).
# Idempotent where practical. Does NOT clone the repo, write secrets, or configure Cloudflare.
set -euo pipefail

SCOUT_USER="${SCOUT_USER:-scout}"
APP_ROOT="${APP_ROOT:-/opt/friday-scout-web}"
DATA_ROOT="${DATA_ROOT:-/data/scout}"
ENV_DIR="${ENV_DIR:-/etc/scout}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/scout}"
LOG_ROOT="${LOG_ROOT:-/var/log/scout}"

log() {
  printf '[scout-install] %s\n' "$*"
}

fail() {
  printf '[scout-install] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  if [[ "${EUID:-$(id -u)}" -ne 0 ]]; then
    fail "Run as root (sudo $0)"
  fi
}

ensure_apt_packages() {
  log "Installing system packages..."
  apt-get update -qq
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    git \
    python3.12 \
    python3.12-venv \
    python3-pip \
    sqlite3 \
    curl \
    ca-certificates \
    rsync \
    ufw \
    fail2ban
  # Chromium package name varies; try common candidates.
  if ! command -v chromium-browser >/dev/null 2>&1 && ! command -v chromium >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y chromium-browser || \
      DEBIAN_FRONTEND=noninteractive apt-get install -y chromium || \
      fail "Could not install Chromium — install google-chrome-stable or chromium manually for PDF exports"
  fi
}

ensure_scout_user() {
  if id "${SCOUT_USER}" >/dev/null 2>&1; then
    log "User ${SCOUT_USER} already exists"
  else
    log "Creating system user ${SCOUT_USER}"
    useradd --system --home "/home/${SCOUT_USER}" --create-home --shell /usr/sbin/nologin "${SCOUT_USER}"
  fi
}

ensure_directories() {
  log "Creating directories"
  install -d -m 755 "${APP_ROOT}"
  if id "${SCOUT_USER}" >/dev/null 2>&1; then
    local scout_group
    scout_group="$(id -gn "${SCOUT_USER}")"
    install -d -o "${SCOUT_USER}" -g "${scout_group}" -m 750 "${DATA_ROOT}"
    install -d -o "${SCOUT_USER}" -g "${scout_group}" -m 750 "${DATA_ROOT}/reports"
    install -d -o "${SCOUT_USER}" -g "${scout_group}" -m 750 "${BACKUP_ROOT}"
    install -d -o "${SCOUT_USER}" -g "${scout_group}" -m 750 "${LOG_ROOT}"
    install -d -o root -g "${scout_group}" -m 750 "${ENV_DIR}"
  else
    install -d -m 750 "${DATA_ROOT}" "${DATA_ROOT}/reports" "${BACKUP_ROOT}" "${LOG_ROOT}"
    install -d -m 750 "${ENV_DIR}"
  fi
}

verify_python() {
  if ! command -v python3.12 >/dev/null 2>&1; then
    fail "python3.12 not found after package install"
  fi
  log "Python: $(python3.12 --version)"
}

verify_chromium() {
  local chrome=""
  for candidate in chromium-browser chromium google-chrome-stable google-chrome; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      chrome="${candidate}"
      break
    fi
  done
  if [[ -z "${chrome}" ]]; then
    fail "No Chromium/Chrome executable found — PDF exports will not work"
  fi
  log "Chromium/Chrome: ${chrome}"
}

configure_firewall_baseline() {
  if command -v ufw >/dev/null 2>&1; then
    log "Configuring UFW (SSH only — dashboard stays on 127.0.0.1)"
    ufw default deny incoming || true
    ufw default allow outgoing || true
    ufw allow OpenSSH || true
    ufw --force enable || true
  fi
}

main() {
  require_root
  if [[ ! -f /etc/os-release ]]; then
    fail "/etc/os-release not found — this script targets Ubuntu 24.04"
  fi
  # shellcheck disable=SC1091
  source /etc/os-release
  if [[ "${ID:-}" != "ubuntu" ]]; then
    log "WARNING: Expected Ubuntu; continuing on ${ID:-unknown}"
  fi

  ensure_apt_packages
  ensure_scout_user
  ensure_directories
  verify_python
  verify_chromium
  configure_firewall_baseline

  log "Host preparation complete."
  log "Next: clone ${APP_ROOT}, create venv, copy env file, migrate data, install systemd units."
  log "See deploy/README.md for the full runbook."
}

main "$@"
