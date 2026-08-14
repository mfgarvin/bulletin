#!/usr/bin/env bash
#
# Container entrypoint: validate config, fetch the code, install the crontab,
# hand off to cron.
#
# cron wipes the environment of the jobs it runs, so the container's env vars
# (API keys, PARISHES, MODE, ...) are snapshotted to a file here and sourced
# back by run-worker.sh at run time.

set -euo pipefail

BIN_DIR="${BIN_DIR:-$(cd "$(dirname "$0")" && pwd)}"
APP_DIR="${APP_DIR:-/app}"
# Outside $APP_DIR: that directory is a git checkout that gets reset --hard on
# every run, and a stray tracked-looking file in it is asking for trouble.
ENV_SNAPSHOT="${ENV_SNAPSHOT:-/var/lib/bulletin-worker/env}"
CRON_FILE="${CRON_FILE:-/etc/cron.d/bulletin-worker}"

# ---- timezone -------------------------------------------------------------
# CRON_SCHEDULE is interpreted in this zone. Unraid passes TZ by default.
# Non-fatal: if /etc isn't writable (non-root, read-only rootfs) the TZ env var
# still applies to the job itself — only cron's own clock falls back to UTC.
if [ -n "${TZ:-}" ] && [ -f "/usr/share/zoneinfo/$TZ" ]; then
    if ! { ln -snf "/usr/share/zoneinfo/$TZ" /etc/localtime && echo "$TZ" > /etc/timezone; } 2>/dev/null; then
        echo "WARNING: could not set /etc/localtime to $TZ; cron will schedule in UTC" >&2
    fi
fi

# ---- required secrets -----------------------------------------------------
missing=()
for var in OPENAI_API_KEY NOTION_API_KEY PARISH_DB_ID; do
    [ -n "${!var:-}" ] || missing+=("$var")
done
if [ ${#missing[@]} -gt 0 ]; then
    echo "FATAL: missing required environment variable(s): ${missing[*]}" >&2
    echo "       set them in the Docker container settings and restart." >&2
    exit 1
fi

# ---- config with defaults -------------------------------------------------
# Saturday 09:00 local, comfortably before the 14:00 UTC GitHub Actions export.
CRON_SCHEDULE="${CRON_SCHEDULE:-0 9 * * 6}"
MODE="${MODE:-ids}"
PARISHES="${PARISHES:-}"

if [ "$MODE" = "ids" ] && [ -z "${PARISHES// /}" ]; then
    echo "FATAL: MODE=ids but PARISHES is empty." >&2
    echo "       set PARISHES (e.g. 'ss-c st-basil-the-g') or use MODE=all." >&2
    exit 1
fi

# A crontab line is 5 whitespace-separated fields. Catch typos now rather than
# having cron silently ignore the file.
if [ "$(echo "$CRON_SCHEDULE" | wc -w)" -ne 5 ]; then
    echo "FATAL: CRON_SCHEDULE must have 5 fields, got: '$CRON_SCHEDULE'" >&2
    exit 1
fi

# ---- get the code ---------------------------------------------------------
# The image ships no application code, so this is what makes the container
# runnable at all. Doing it here rather than only in run-worker.sh means a bad
# REPO_URL/BRANCH fails now, in plain sight, instead of on Saturday morning.
# Exit 2 = nothing usable on disk; exit 1 = stale but runnable, which is the
# scheduler's problem to report, not a reason to refuse to start.
"$BIN_DIR/sync-code.sh" || rc=$?
if [ "${rc:-0}" -eq 2 ]; then
    echo "FATAL: could not obtain the application code from $REPO_URL" >&2
    exit 1
fi

# ---- snapshot the environment for the cron job ----------------------------
# printf %q quotes values so newlines/spaces in secrets survive the round trip.
umask 077
mkdir -p "$(dirname "$ENV_SNAPSHOT")"
: > "$ENV_SNAPSHOT"
while IFS='=' read -r -d '' name value; do
    case "$name" in
        HOME|PATH|PWD|SHLVL|OLDPWD|_|HOSTNAME|TERM) continue ;;
    esac
    printf '%s=%q\n' "$name" "$value" >> "$ENV_SNAPSHOT"
done < <(env -0)
umask 022

# ---- install the crontab --------------------------------------------------
# /proc/1/fd/1 is cron's own stdout, i.e. the container log, so job output
# shows up in `docker logs` / the Unraid log viewer.
{
    echo "SHELL=/bin/bash"
    echo "PATH=/usr/local/bin:/usr/local/sbin:/usr/sbin:/usr/bin:/sbin:/bin"
    echo "$CRON_SCHEDULE root /usr/local/bin/run-worker.sh >> /proc/1/fd/1 2>&1"
} > "$CRON_FILE"
chmod 0644 "$CRON_FILE"

mkdir -p "${LOG_DIR:-/logs}"

echo "$(date -Is) bulletin worker starting"
echo "  schedule : $CRON_SCHEDULE  (TZ=${TZ:-UTC})"
echo "  code     : ${REPO_URL:-https://github.com/mfgarvin/bulletin.git} @ ${BRANCH:-main} — $(git -C "$APP_DIR" rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "  update   : ${AUTO_UPDATE:-true} (before each run)"
echo "  mode     : $MODE"
[ "$MODE" = "ids" ] \
    && echo "  parishes : $PARISHES" \
    || echo "  stale    : >${STALE_DAYS:-6} days"
echo "  logs     : ${LOG_DIR:-/logs}"

# ---- optional immediate run ----------------------------------------------
if [ "${RUN_ON_START:-false}" = "true" ]; then
    echo "$(date -Is) RUN_ON_START=true — running once now"
    "$BIN_DIR/run-worker.sh" || echo "$(date -Is) startup run failed (continuing to schedule)"
fi

exec cron -f -L 2
