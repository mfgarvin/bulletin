#!/usr/bin/env bash
#
# Container entrypoint: validate config, install the crontab, hand off to cron.
#
# cron wipes the environment of the jobs it runs, so the container's env vars
# (API keys, PARISHES, MODE, ...) are snapshotted to a file here and sourced
# back by run-worker.sh at run time.

set -euo pipefail

APP_DIR="${APP_DIR:-/app}"
ENV_SNAPSHOT="$APP_DIR/.docker-env"
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

# ---- snapshot the environment for the cron job ----------------------------
# printf %q quotes values so newlines/spaces in secrets survive the round trip.
umask 077
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
echo "  mode     : $MODE"
[ "$MODE" = "ids" ] \
    && echo "  parishes : $PARISHES" \
    || echo "  stale    : >${STALE_DAYS:-7} days"
echo "  logs     : ${LOG_DIR:-/logs}"

# ---- optional immediate run ----------------------------------------------
if [ "${RUN_ON_START:-false}" = "true" ]; then
    echo "$(date -Is) RUN_ON_START=true — running once now"
    /usr/local/bin/run-worker.sh || echo "$(date -Is) startup run failed (continuing to schedule)"
fi

exec cron -f -L 2
