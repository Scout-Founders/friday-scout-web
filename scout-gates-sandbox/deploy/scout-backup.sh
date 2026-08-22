#!/usr/bin/env bash
# Scout Gates Sandbox — SQLite-safe backup of database + reports archive.
# Suitable for cron or systemd timer. No external credentials required.
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/scout/scout-dashboard.env}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups/scout}"
RETENTION_DAYS="${RETENTION_DAYS:-14}"
SCOUT_USER="${SCOUT_USER:-scout}"

log() { printf '[scout-backup] %s\n' "$*"; }
fail() { printf '[scout-backup] ERROR: %s\n' "$*" >&2; exit 1; }

load_env_paths() {
  DB_PATH="${SCOUT_RESEARCH_DB_PATH:-/data/scout/scout_memory.db}"
  REPORTS_DIR="${SCOUT_REPORTS_DIR:-/data/scout/reports}"
  if [[ -f "${ENV_FILE}" ]]; then
    while IFS= read -r line || [[ -n "${line}" ]]; do
      case "${line}" in
        SCOUT_RESEARCH_DB_PATH=*) DB_PATH="${line#SCOUT_RESEARCH_DB_PATH=}" ;;
        SCOUT_REPORTS_DIR=*) REPORTS_DIR="${line#SCOUT_REPORTS_DIR=}" ;;
      esac
    done < "${ENV_FILE}"
  fi
}

main() {
  load_env_paths
  STAMP="$(date -u +%Y%m%d-%H%M%S)"
  if id "${SCOUT_USER}" >/dev/null 2>&1; then
    local scout_group
    scout_group="$(id -gn "${SCOUT_USER}")"
    install -d -o "${SCOUT_USER}" -g "${scout_group}" -m 750 "${BACKUP_ROOT}"
  else
    install -d -m 750 "${BACKUP_ROOT}"
  fi

  if [[ ! -f "${DB_PATH}" ]]; then
    fail "Database not found: ${DB_PATH}"
  fi

  local db_backup="${BACKUP_ROOT}/scout_memory-${STAMP}.db"
  log "Backing up database to ${db_backup}"
  sqlite3 "${DB_PATH}" ".backup '${db_backup}'"

  local reports_archive="${BACKUP_ROOT}/reports-${STAMP}.tar.gz"
  if [[ -d "${REPORTS_DIR}" ]]; then
    log "Archiving reports to ${reports_archive}"
    tar -czf "${reports_archive}" -C "$(dirname "${REPORTS_DIR}")" "$(basename "${REPORTS_DIR}")"
  else
    log "Reports directory missing — skipping archive (${REPORTS_DIR})"
  fi

  log "Pruning backups older than ${RETENTION_DAYS} days"
  find "${BACKUP_ROOT}" -name 'scout_memory-*.db' -mtime "+${RETENTION_DAYS}" -delete || true
  find "${BACKUP_ROOT}" -name 'reports-*.tar.gz' -mtime "+${RETENTION_DAYS}" -delete || true

  log "Backup complete"
}

main "$@"
