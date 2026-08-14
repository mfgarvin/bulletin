#!/usr/bin/env bash
#
# Bring $APP_DIR up to date with origin/$BRANCH, then reinstall dependencies if
# requirements.txt changed. Called twice: once by entrypoint.sh at container
# start (so a bad REPO_URL fails loudly then, not silently on Saturday) and
# again by run-worker.sh immediately before every scheduled run.
#
# The image contains no application code — only the dependencies. This is the
# step that puts code on disk, which is why the container picks up commits
# without being rebuilt.
#
# Exit codes are meaningful; the two callers treat them differently:
#   0  code present and synced with origin
#   1  code present but the update failed (network, bad ref) — usable but stale
#   2  no usable code in $APP_DIR — nothing can run

set -uo pipefail   # deliberately not -e: this script reports failure via its
                   # exit code rather than dying halfway through.

APP_DIR="${APP_DIR:-/app}"
REPO_URL="${REPO_URL:-https://github.com/mfgarvin/bulletin.git}"
BRANCH="${BRANCH:-main}"
# Marker lives in the container filesystem, NOT in $APP_DIR: it records what
# this container's site-packages actually has installed. Keeping it alongside
# the code would go stale the moment the container is recreated against a
# persistent /app mount, and we would skip a needed install.
DEPS_MARKER="${DEPS_MARKER:-/var/lib/bulletin-worker/requirements.sha256}"

log() { echo "$(date -Is) [sync] $*"; }

# A token is only needed if the repo is ever made private. Injected at use time
# so it is never written into .git/config, where it would outlive the process.
git_auth_url() {
    if [ -n "${GIT_TOKEN:-}" ]; then
        echo "${REPO_URL/https:\/\//https://x-access-token:${GIT_TOKEN}@}"
    else
        echo "$REPO_URL"
    fi
}

# Unraid mounts host paths with arbitrary ownership; without this git refuses to
# operate on a tree it considers someone else's.
git config --global --add safe.directory "$APP_DIR" 2>/dev/null || true

# ---- 1. clone if there is nothing there -----------------------------------
if [ ! -d "$APP_DIR/.git" ]; then
    if [ -n "$(ls -A "$APP_DIR" 2>/dev/null)" ]; then
        log "ERROR: $APP_DIR is non-empty but is not a git checkout; refusing to clobber it"
        exit 2
    fi
    log "cloning $REPO_URL ($BRANCH) into $APP_DIR"
    mkdir -p "$APP_DIR"
    if ! git clone --quiet --branch "$BRANCH" --single-branch "$(git_auth_url)" "$APP_DIR"; then
        log "ERROR: clone failed"
        exit 2
    fi
    git -C "$APP_DIR" remote set-url origin "$REPO_URL"   # drop any token
    log "cloned at $(git -C "$APP_DIR" rev-parse --short HEAD)"
    install_deps=yes
    just_cloned=yes
else
    install_deps=no
    just_cloned=no
fi

BEFORE="$(git -C "$APP_DIR" rev-parse HEAD 2>/dev/null)" || { log "ERROR: $APP_DIR/.git is unreadable"; exit 2; }

# ---- 2. fast-forward to origin --------------------------------------------
if [ "$just_cloned" = "yes" ]; then
    : # a clone is current by construction; fetching again would only add noise
elif [ "${AUTO_UPDATE:-true}" != "true" ]; then
    log "AUTO_UPDATE=false — staying on $(git -C "$APP_DIR" rev-parse --short HEAD)"
else
    # A worker never has local commits, so reset --hard is the correct sync: it
    # discards drift and matches the remote exactly. Untracked files (logs) stay.
    if git -C "$APP_DIR" fetch --quiet "$(git_auth_url)" "$BRANCH" 2>/dev/null \
        && git -C "$APP_DIR" reset --hard --quiet FETCH_HEAD; then
        AFTER="$(git -C "$APP_DIR" rev-parse HEAD)"
        if [ "$BEFORE" = "$AFTER" ]; then
            log "code: already current at ${AFTER:0:9}"
        else
            log "code: ${BEFORE:0:9} -> ${AFTER:0:9}"
            log "      $(git -C "$APP_DIR" log -1 --format='%s' 2>/dev/null)"
            install_deps=yes
        fi
    else
        # Loud, because running stale code that reports success is this
        # project's recurring failure mode. The caller decides whether to
        # proceed; it is still better to process parishes with last week's
        # scraper than to skip the week entirely.
        log "WARNING: update FAILED — running previously checked-out ${BEFORE:0:9}"
        UPDATE_FAILED=yes
    fi
fi

# ---- 3. dependencies -------------------------------------------------------
# Compared by content hash rather than "did requirements.txt appear in the
# diff", so a container recreated against an already-updated /app still
# notices that its own site-packages predates the current requirements.
REQ="$APP_DIR/requirements.txt"
if [ -f "$REQ" ]; then
    want="$(sha256sum "$REQ" | cut -d' ' -f1)"
    have="$(cat "$DEPS_MARKER" 2>/dev/null || echo none)"
    if [ "$want" != "$have" ]; then
        log "requirements.txt changed (or first run) — installing"
        if pip install --no-cache-dir --quiet -r "$REQ"; then
            mkdir -p "$(dirname "$DEPS_MARKER")"
            echo "$want" > "$DEPS_MARKER"
            log "dependencies installed"
        else
            log "WARNING: pip install failed — continuing with the installed set"
            UPDATE_FAILED=yes
        fi
    elif [ "$install_deps" = "yes" ]; then
        log "dependencies unchanged"
    fi
fi

[ -n "${UPDATE_FAILED:-}" ] && exit 1
exit 0
