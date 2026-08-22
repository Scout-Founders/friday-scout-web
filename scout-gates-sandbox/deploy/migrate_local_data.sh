#!/usr/bin/env bash
# Scout Gates Sandbox — migrate scout_memory.db + reports to hosted paths.
#
# Modes:
#   prepare  — run on Mac (or source host) to build a migration bundle
#   install  — run on VPS to install bundle into /data/scout
#
# Examples:
#   ./migrate_local_data.sh prepare --repo-root /path/to/friday-scout-web --output ./scout-migration-bundle
#   ./migrate_local_data.sh install --bundle ./scout-migration-bundle --dry-run
set -euo pipefail

MODE=""
REPO_ROOT=""
OUTPUT=""
BUNDLE=""
DRY_RUN=0
DEST_DB="${SCOUT_RESEARCH_DB_PATH:-/data/scout/scout_memory.db}"
DEST_REPORTS="${SCOUT_REPORTS_DIR:-/data/scout/reports}"
SCOUT_USER="${SCOUT_USER:-scout}"

log() { printf '[scout-migrate] %s\n' "$*"; }
fail() { printf '[scout-migrate] ERROR: %s\n' "$*" >&2; exit 1; }

dashboard_active() {
  if pgrep -f "[p]ython.*dashboard\\.py" >/dev/null 2>&1; then
    return 0
  fi
  if command -v lsof >/dev/null 2>&1 && lsof -iTCP:8765 -sTCP:LISTEN -t >/dev/null 2>&1; then
    return 0
  fi
  return 1
}

sha256_file() {
  if command -v shasum >/dev/null 2>&1; then
    shasum -a 256 "$1" | awk '{print $1}'
  else
    sha256sum "$1" | awk '{print $1}'
  fi
}

usage() {
  cat <<'EOF'
Usage:
  migrate_local_data.sh prepare --repo-root PATH --output BUNDLE_DIR
  migrate_local_data.sh install --bundle BUNDLE_DIR [--dry-run]

Environment overrides (install):
  SCOUT_RESEARCH_DB_PATH  default /data/scout/scout_memory.db
  SCOUT_REPORTS_DIR       default /data/scout/reports
  SCOUT_USER              default scout
EOF
}

parse_args() {
  if [[ $# -lt 1 ]]; then
    usage
    exit 1
  fi
  MODE="$1"
  shift
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --repo-root) REPO_ROOT="$2"; shift 2 ;;
      --output) OUTPUT="$2"; shift 2 ;;
      --bundle) BUNDLE="$2"; shift 2 ;;
      --dry-run) DRY_RUN=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) fail "Unknown argument: $1" ;;
    esac
  done
}

prepare_bundle() {
  [[ -n "${REPO_ROOT}" ]] || fail "prepare requires --repo-root"
  [[ -n "${OUTPUT}" ]] || fail "prepare requires --output"
  REPO_ROOT="$(cd "${REPO_ROOT}" && pwd)"
  OUTPUT="$(mkdir -p "${OUTPUT}" && cd "${OUTPUT}" && pwd)"

  local src_db="${REPO_ROOT}/scout-gates-sandbox/scout_memory.db"
  local src_reports="${REPO_ROOT}/exports/reports"

  if dashboard_active; then
    fail "dashboard.py appears to be running — stop it before migration prepare"
  fi

  if [[ ! -f "${src_db}" ]]; then
    fail "Source database not found: ${src_db}"
  fi

  log "Creating SQLite backup (does not modify source)"
  sqlite3 "${src_db}" ".backup '${OUTPUT}/scout_memory.db'"
  local checksum
  checksum="$(sha256_file "${OUTPUT}/scout_memory.db")"
  printf '%s\n' "${checksum}" > "${OUTPUT}/scout_memory.db.sha256"

  if [[ -d "${src_reports}" ]]; then
    log "Copying reports directory"
    rsync -a --delete "${src_reports}/" "${OUTPUT}/reports/"
  else
    log "No reports directory at ${src_reports}; creating empty reports/"
    mkdir -p "${OUTPUT}/reports"
  fi

  cat > "${OUTPUT}/manifest.json" <<EOF
{
  "schemaVersion": 1,
  "preparedAt": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "sourceRepoRoot": "${REPO_ROOT}",
  "databaseFile": "scout_memory.db",
  "reportsDir": "reports",
  "checksumSha256": "${checksum}"
}
EOF
  log "Bundle ready: ${OUTPUT}"
  log "Transfer to VPS (example): rsync -avz ${OUTPUT}/ scout@vps:/tmp/scout-migration-bundle/"
}

install_bundle() {
  [[ -n "${BUNDLE}" ]] || fail "install requires --bundle"
  BUNDLE="$(cd "${BUNDLE}" && pwd)"

  local bundle_db="${BUNDLE}/scout_memory.db"
  local bundle_reports="${BUNDLE}/reports"
  [[ -f "${bundle_db}" ]] || fail "Bundle database missing: ${bundle_db}"

  if dashboard_active; then
    fail "dashboard.py appears to be running — stop scout-dashboard.service before install"
  fi

  local can_chown=0
  if id "${SCOUT_USER}" >/dev/null 2>&1; then
    can_chown=1
  else
    log "User ${SCOUT_USER} not found — skipping chown (acceptable for local test installs)"
  fi

  if [[ -f "${BUNDLE}/scout_memory.db.sha256" ]]; then
    local expected actual
    expected="$(cat "${BUNDLE}/scout_memory.db.sha256")"
    actual="$(sha256_file "${bundle_db}")"
    if [[ "${expected}" != "${actual}" ]]; then
      fail "Bundle checksum mismatch"
    fi
    log "Checksum verified"
  fi

  log "SQLite integrity_check on bundle"
  sqlite3 "${bundle_db}" "PRAGMA integrity_check;" | while read -r line; do
    [[ "${line}" == "ok" ]] || fail "integrity_check failed: ${line}"
  done

  if [[ "${DRY_RUN}" -eq 1 ]]; then
    log "DRY RUN — would install:"
    log "  DB: ${bundle_db} -> ${DEST_DB}"
    log "  Reports: ${bundle_reports} -> ${DEST_REPORTS}"
    return 0
  fi

  if [[ "${can_chown}" -eq 1 ]]; then
    local scout_group
    scout_group="$(id -gn "${SCOUT_USER}")"
    install -d -o "${SCOUT_USER}" -g "${scout_group}" -m 750 "$(dirname "${DEST_DB}")"
    install -d -o "${SCOUT_USER}" -g "${scout_group}" -m 750 "${DEST_REPORTS}"
  else
    install -d -m 750 "$(dirname "${DEST_DB}")"
    install -d -m 750 "${DEST_REPORTS}"
  fi

  log "Installing database to ${DEST_DB}"
  sqlite3 "${bundle_db}" ".backup '${DEST_DB}'"
  if [[ "${can_chown}" -eq 1 ]]; then
    local scout_group
    scout_group="$(id -gn "${SCOUT_USER}")"
    chown "${SCOUT_USER}:${scout_group}" "${DEST_DB}"
    chmod 660 "${DEST_DB}"
  fi

  log "Installing reports to ${DEST_REPORTS}"
  if [[ -d "${bundle_reports}" ]]; then
    rsync -a "${bundle_reports}/" "${DEST_REPORTS}/"
  fi
  if [[ "${can_chown}" -eq 1 ]]; then
    local scout_group
    scout_group="$(id -gn "${SCOUT_USER}")"
    chown -R "${SCOUT_USER}:${scout_group}" "${DEST_REPORTS}"
  fi

  log "Destination integrity_check"
  sqlite3 "${DEST_DB}" "PRAGMA integrity_check;" | while read -r line; do
    [[ "${line}" == "ok" ]] || fail "destination integrity_check failed: ${line}"
  done

  log "Install complete. Source bundle was not deleted."
}

main() {
  parse_args "$@"
  case "${MODE}" in
    prepare) prepare_bundle ;;
    install) install_bundle ;;
    *) fail "Unknown mode: ${MODE} (use prepare or install)" ;;
  esac
}

main "$@"
