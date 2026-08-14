#!/usr/bin/env bash
#
# Local bulletin worker
# ---------------------
# Processes the handful of parishes whose websites block GitHub Actions'
# datacenter IPs (currently St. Stephen / St. Basil) but load fine from a
# normal residential connection. Run this from cron on such a machine.
#
# It does NOT regenerate export.json. main.py writes extraction results
# straight to Notion; the weekly GitHub Actions job rebuilds export.json /
# parish_data.json from Notion, so this worker only needs to keep those rows
# fresh in Notion between now and the next export.
#
# One-time setup on the worker machine:
#   git clone https://github.com/mfgarvin/bulletin.git "$REPO_DIR"
#   cd "$REPO_DIR"
#   python3.12 -m venv venv                 # match CI's Python 3.12
#   venv/bin/pip install -r requirements.txt
#   cp /path/to/.env "$REPO_DIR/.env"       # OPENAI_API_KEY, NOTION_API_KEY, PARISH_DB_ID
#
# Keep THIS script OUTSIDE the cloned repo so a git pull can't rewrite it
# while it is running. Then add a cron entry, e.g. run Saturday 09:00 local,
# comfortably before the 2 PM UTC GitHub Actions export:
#
#   0 9 * * 6  /home/you/bin/local_worker.sh
#
# (stdout/stderr already go to a per-run log; redirect in cron too if you
#  want a single rolling file: ... >> /home/you/worker.log 2>&1)

set -euo pipefail

# ---- config ---------------------------------------------------------------
REPO_DIR="${REPO_DIR:-$HOME/bulletin-v2}"     # where the repo is cloned
BRANCH="${BRANCH:-main}"                       # what GitHub Actions runs on
PARISHES=(ss-c st-basil-the-g olp-cle)         # parish IDs to process locally
LOG_DIR="${LOG_DIR:-$REPO_DIR/worker-logs}"    # per-run logs live here
# ---------------------------------------------------------------------------

mkdir -p "$LOG_DIR"
RUN_LOG="$LOG_DIR/run-$(date +%Y%m%d-%H%M%S).log"
log() { echo "$(date -Is) $*" | tee -a "$RUN_LOG"; }

# Serialize runs: if a slow previous run is still going, bail instead of stacking.
exec 9>"$LOG_DIR/.lock"
if ! flock -n 9; then
    echo "$(date -Is) previous run still holds the lock; exiting" >&2
    exit 0
fi

cd "$REPO_DIR"
log "worker start — parishes: ${PARISHES[*]}"

# 1. Mirror the latest code (a worker never has local commits; match remote exactly).
BEFORE="$(git rev-parse HEAD)"
git fetch --quiet origin "$BRANCH"
git reset --hard --quiet "origin/$BRANCH"     # discards any local drift, keeps .env & logs (untracked)
AFTER="$(git rev-parse HEAD)"
log "code: ${BEFORE:0:9} -> ${AFTER:0:9}"

# 2. Reinstall deps only when requirements.txt actually changed.
if [ "$BEFORE" != "$AFTER" ] && ! git diff --quiet "$BEFORE" "$AFTER" -- requirements.txt; then
    log "requirements.txt changed — reinstalling"
    venv/bin/pip install --quiet -r requirements.txt
fi

# 3. Load secrets (main.py reads them from the environment; it does not load .env itself).
if [ ! -f .env ]; then
    log "ERROR: no .env in $REPO_DIR (need OPENAI_API_KEY, NOTION_API_KEY, PARISH_DB_ID)"
    exit 1
fi
set -a; . ./.env; set +a

# 4. Run the processor — this is what uploads results to Notion.
log "processing ${#PARISHES[@]} parish(es)..."
if venv/bin/python main.py "${PARISHES[@]}" >>"$RUN_LOG" 2>&1; then
    log "done OK — results written to Notion"
else
    rc=$?
    log "processor exited $rc — see $RUN_LOG"
    exit "$rc"
fi
