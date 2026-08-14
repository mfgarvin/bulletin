#!/usr/bin/env bash
#
# One worker run: sync the code from GitHub, then process the configured
# parishes and write results to Notion.
#
# Invoked by cron (with an empty environment) or directly for a manual run:
#   docker exec bulletin-worker run-worker.sh
#
# This does NOT regenerate export.json. main.py writes straight to Notion; the
# weekly GitHub Actions job rebuilds export.json / parish_data.json from Notion.

set -euo pipefail

# APP_DIR is overridable so these scripts can be exercised outside the image.
# BIN_DIR lets the scripts find each other both in the image (/usr/local/bin)
# and when exercised straight from a checkout.
BIN_DIR="${BIN_DIR:-$(cd "$(dirname "$0")" && pwd)}"
APP_DIR="${APP_DIR:-/app}"
ENV_SNAPSHOT="${ENV_SNAPSHOT:-/var/lib/bulletin-worker/env}"
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

# ---- update the code ------------------------------------------------------
# Inside the lock and before the arg list, so every run executes whatever is on
# origin/$BRANCH right now. A scraper fix goes live on the next run without the
# image being rebuilt. Failure here is a warning, not a stop: processing the
# parishes with last week's code beats skipping the week — but the log says so
# explicitly, because a run that looks successful while using stale code is
# exactly how this project has been bitten before.
set +e
"$BIN_DIR/sync-code.sh" 2>&1 | tee -a "$RUN_LOG"
sync_rc=${PIPESTATUS[0]}
set -e
if [ "$sync_rc" -eq 2 ]; then
    log "FATAL: no application code available — nothing to run"
    exit 2
elif [ "$sync_rc" -ne 0 ]; then
    log "continuing with STALE code (see the warning above)"
fi

# ---- build the main.py argument list --------------------------------------
args=()
if [ "${MODE:-ids}" = "all" ]; then
    args+=(--all --stale-days "${STALE_DAYS:-6}")
    target="all stale parishes (>${STALE_DAYS:-6} days)"
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

commit="$(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
if [ "${DRY_RUN:-false}" = "true" ]; then
    log "worker start — $target @ $commit (DRY RUN: nothing will be saved to Notion)"
else
    log "worker start — $target @ $commit"
fi

# ---- run ------------------------------------------------------------------
# tee so output lands in both the per-run log and the container log.
set +e
python "$APP_DIR/main.py" "${args[@]}" 2>&1 | tee -a "$RUN_LOG"
rc=${PIPESTATUS[0]}
set -e

if [ "$rc" -eq 0 ] && [ "${DRY_RUN:-false}" = "true" ]; then
    log "done OK — dry run, nothing written to Notion"
elif [ "$rc" -eq 0 ]; then
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
