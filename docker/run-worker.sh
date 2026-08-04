#!/usr/bin/env bash
#
# One worker run: process the configured parishes and write results to Notion.
#
# Invoked by cron (with an empty environment) or directly for a manual run:
#   docker exec bulletin-worker run-worker.sh
#
# This does NOT regenerate export.json. main.py writes straight to Notion; the
# weekly GitHub Actions job rebuilds export.json / parish_data.json from Notion.

set -euo pipefail

# APP_DIR is overridable so these scripts can be exercised outside the image.
APP_DIR="${APP_DIR:-/app}"
ENV_SNAPSHOT="$APP_DIR/.docker-env"
if [ -f "$ENV_SNAPSHOT" ]; then
    set -a; . "$ENV_SNAPSHOT"; set +a
fi

LOG_DIR="${LOG_DIR:-/logs}"
mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"

log() { echo "$(date -Is) $*" | tee -a "$RUN_LOG"; }

# Serialize runs: if a slow previous run is still going, skip rather than stack.
exec 9>"$LOG_DIR/.lock"
if ! flock -n 9; then
    echo "$(date -Is) previous run still holds the lock; skipping this run"
    exit 0
fi

# ---- build the main.py argument list --------------------------------------
args=()
if [ "${MODE:-ids}" = "all" ]; then
    args+=(--all --stale-days "${STALE_DAYS:-7}")
    target="all stale parishes (>${STALE_DAYS:-7} days)"
else
    # Accept either commas or spaces between IDs: "ss-c,st-basil" or "ss-c st-basil"
    read -ra ids <<< "${PARISHES//,/ }"
    if [ ${#ids[@]} -eq 0 ]; then
        log "ERROR: MODE=ids but PARISHES is empty"
        exit 1
    fi
    args+=("${ids[@]}")
    target="${ids[*]}"
fi

# Note: plain `[ ... ] && args+=(...)` would abort the script under `set -e`
# whenever the test is false, so each flag gets a real if.
if [ "${DRY_RUN:-false}" = "true" ]; then args+=(--dry-run); fi
if [ "${VERBOSE:-false}" = "true" ]; then args+=(--verbose); fi
if [ -n "${METHOD:-}" ]; then args+=(--method "$METHOD"); fi

if [ "${DRY_RUN:-false}" = "true" ]; then
    log "worker start — $target (DRY RUN: nothing will be saved to Notion)"
else
    log "worker start — $target"
fi

# ---- run ------------------------------------------------------------------
# tee so output lands in both the per-run log and the container log.
set +e
python "$APP_DIR/main.py" "${args[@]}" 2>&1 | tee -a "$RUN_LOG"
rc=${PIPESTATUS[0]}
set -e

if [ "$rc" -eq 0 ]; then
    log "done OK — results written to Notion"
else
    log "processor exited $rc — see $RUN_LOG"
fi

# ---- prune old per-run logs ----------------------------------------------
retention="${LOG_RETENTION_DAYS:-30}"
if [ "$retention" -gt 0 ] 2>/dev/null; then
    find "$LOG_DIR" -maxdepth 1 -name 'run-*.log' -mtime "+$retention" -delete 2>/dev/null || true
fi

exit "$rc"
