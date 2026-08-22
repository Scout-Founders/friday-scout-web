#!/usr/bin/env bash
# Scout Gates Sandbox — host readiness verification (no secrets printed).
set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/scout/scout-dashboard.env}"
HEALTH_URL="${HEALTH_URL:-http://127.0.0.1:8765/api/health}"
SERVICE_NAME="${SERVICE_NAME:-scout-dashboard.service}"
FAILURES=0

log() { printf '[scout-verify] %s\n' "$*"; }
warn() { printf '[scout-verify] WARN: %s\n' "$*" >&2; }
fail_check() {
  printf '[scout-verify] FAIL: %s\n' "$*" >&2
  FAILURES=$((FAILURES + 1))
}

check_python() {
  if command -v python3.12 >/dev/null 2>&1; then
    log "OK python3.12: $(python3.12 --version 2>/dev/null || true)"
  elif command -v python3 >/dev/null 2>&1; then
    log "OK python3: $(python3 --version 2>/dev/null || true)"
  else
    fail_check "Python 3 not found"
  fi
}

check_chromium() {
  local found=""
  for candidate in chromium-browser chromium google-chrome-stable google-chrome; do
    if command -v "${candidate}" >/dev/null 2>&1; then
      found="${candidate}"
      break
    fi
  done
  if [[ -n "${found}" ]]; then
    log "OK Chromium/Chrome executable: ${found}"
  else
    fail_check "Chromium/Chrome not found in PATH"
  fi
}

load_env_safely() {
  DB_PATH="/data/scout/scout_memory.db"
  REPORTS_DIR="/data/scout/reports"
  if [[ -f "${ENV_FILE}" ]]; then
    log "OK environment file exists: ${ENV_FILE}"
    while IFS= read -r line || [[ -n "${line}" ]]; do
      line="${line%%#*}"
      line="$(echo "${line}" | xargs)"
      [[ -z "${line}" ]] && continue
      case "${line}" in
        SCOUT_RESEARCH_DB_PATH=*) DB_PATH="${line#SCOUT_RESEARCH_DB_PATH=}" ;;
        SCOUT_REPORTS_DIR=*) REPORTS_DIR="${line#SCOUT_REPORTS_DIR=}" ;;
      esac
    done < "${ENV_FILE}"
  else
    warn "Environment file missing: ${ENV_FILE}"
  fi
}

check_writable_paths() {
  local db_parent
  db_parent="$(dirname "${DB_PATH}")"
  if [[ -d "${db_parent}" ]] && [[ -w "${db_parent}" ]]; then
    log "OK database parent writable: ${db_parent}"
  else
    fail_check "database parent not writable: ${db_parent}"
  fi
  if [[ -f "${DB_PATH}" ]]; then
    if [[ -w "${DB_PATH}" ]]; then
      log "OK database file writable: ${DB_PATH}"
    else
      fail_check "database file not writable: ${DB_PATH}"
    fi
  else
    warn "Database file does not exist yet: ${DB_PATH}"
  fi
  if [[ -d "${REPORTS_DIR}" ]] && [[ -w "${REPORTS_DIR}" ]]; then
    log "OK reports directory writable: ${REPORTS_DIR}"
  else
    fail_check "reports directory not writable: ${REPORTS_DIR}"
  fi
}

check_sqlite_integrity() {
  if [[ -f "${DB_PATH}" ]] && command -v sqlite3 >/dev/null 2>&1; then
    local result
    result="$(sqlite3 "${DB_PATH}" 'PRAGMA integrity_check;' | head -n 1)"
    if [[ "${result}" == "ok" ]]; then
      log "OK SQLite integrity_check"
    else
      fail_check "SQLite integrity_check: ${result}"
    fi
  fi
}

check_systemd() {
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
      log "OK systemd service active: ${SERVICE_NAME}"
    else
      warn "systemd service not active: ${SERVICE_NAME}"
    fi
  else
    warn "systemctl not available"
  fi
}

check_health_endpoint() {
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 5 "${HEALTH_URL}" >/dev/null 2>&1; then
      log "OK health endpoint reachable: ${HEALTH_URL}"
    else
      warn "health endpoint not reachable (service may be stopped): ${HEALTH_URL}"
    fi
  else
    warn "curl not available — skipping health HTTP check"
  fi
}

main() {
  check_python
  check_chromium
  load_env_safely
  check_writable_paths
  check_sqlite_integrity
  check_systemd
  check_health_endpoint

  if [[ "${FAILURES}" -gt 0 ]]; then
    fail_check "${FAILURES} check(s) failed"
    exit 1
  fi
  log "All critical checks passed"
}

main "$@"
