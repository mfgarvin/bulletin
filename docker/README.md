# Bulletin worker in Docker (Unraid)

Containerized version of `local_worker.sh`. It runs `main.py` on a cron schedule
and writes extraction results to Notion.

Why it exists: a few parish websites block GitHub Actions' datacenter IPs but load
fine from a residential connection, so those parishes have to be processed from
home. This **does not** regenerate `export.json` — the weekly GitHub Actions job
rebuilds `export.json` / `parish_data.json` from Notion. The worker just keeps
those Notion rows fresh in between.

Differences from `local_worker.sh`: secrets come from container env vars instead
of a `.env` file, and there is no venv (dependencies live in the image's system
Python). The run lock, per-run logs, "skip if the previous run is still going",
and the `git` sync are the same.

## The image holds no application code

`/app` is **cloned from GitHub when the container starts and re-synced with
`origin/$BRANCH` immediately before every run** (`docker/sync-code.sh`, called
from both `entrypoint.sh` and `run-worker.sh`). So:

- **Shipping a fix = pushing to `main`.** The next scheduled run picks it up.
  No rebuild, no container restart.
- **Rebuild only** when the base image, the three worker scripts, or the
  dependency baseline should change.
- Dependencies are installed at build time from `requirements.txt` and
  reinstalled at run time only when the pulled `requirements.txt` no longer
  matches the hash recorded in `/var/lib/bulletin-worker/requirements.sha256`.
  The marker lives in the container, not in `/app`, so a container recreated
  against an already-updated `/app` still notices it needs the install.

Every run logs the commit it is about to execute:

```
[sync] code: 17ced5900 -> 9f44ac504
[sync]       Document bulletin freshness as its own failure class
worker start — ss-c st-basil-the-g olp-cle @ 9f44ac5
```

If the update fails (GitHub unreachable, bad `BRANCH`), the run **continues on
the last checked-out commit** and says so loudly —

```
[sync] WARNING: update FAILED — running previously checked-out 9f44ac504
continuing with STALE code (see the warning above)
```

— because skipping the week is worse than running last week's scraper. Grep the
logs for `STALE code` if a fix you pushed doesn't seem to have taken effect. If
there is no usable checkout at all, the run exits 2 rather than pretending.

`/app` is an ordinary container directory by default, so a recreate gets a fresh
clone — a few seconds, and unambiguous. Mount a host path there if you would
rather it persist.

## Configuration

Everything is a Docker env var, so it's all editable from the Unraid container
settings page.

| Variable | Default | What it does |
|---|---|---|
| `OPENAI_API_KEY` | — | **Required.** |
| `NOTION_API_KEY` | — | **Required.** |
| `PARISH_DB_ID` | — | **Required.** Notion parish database ID. |
| `CRON_SCHEDULE` | `0 9 * * 6` | Five-field cron, in `TZ`. Default = Saturday 09:00. |
| `TZ` | `America/New_York` | Timezone the schedule is read in. |
| `REPO_URL` | `https://github.com/mfgarvin/bulletin.git` | Cloned into `/app`; re-synced before every run. |
| `BRANCH` | `main` | Branch to track — what GitHub Actions runs. |
| `AUTO_UPDATE` | `true` | `false` pins `/app` to its current commit. Debugging only. |
| `GIT_TOKEN` | (unset) | Only needed if the repo is ever made private. Used at fetch time; never written to `.git/config`. |
| `MODE` | `ids` | `ids` = process exactly `PARISHES`; `all` = every enabled stale parish. |
| `PARISHES` | — | IDs for `MODE=ids`, space- or comma-separated (e.g. `ss-c st-basil-the-g olp-cle`). |
| `STALE_DAYS` | `6` | For `MODE=all`: age before a parish is reprocessed. |
| `RUN_ON_START` | `false` | `true` runs once at container start, then follows the schedule. |
| `DRY_RUN` | `false` | `true` extracts but writes nothing to Notion. |
| `VERBOSE` | `false` | Debug logging. |
| `METHOD` | (unset) | `direct_pdf` or `marker_ocr`; unset uses main.py's default. |
| `LOG_RETENTION_DAYS` | `30` | Prune per-run logs older than this. `0` disables. |

Volume: `/logs` → per-run `run-YYYYMMDD-HHMMSS.log` files. Job output also goes to
the container log, so the Unraid log viewer shows it live.

The container refuses to start if a required secret is missing, if `MODE=ids`
with an empty `PARISHES`, if `CRON_SCHEDULE` isn't 5 fields, or if the initial
clone fails — better a loud failure at start than a job that silently never
fires, or one that fires on Saturday against a `REPO_URL` typo.

## Install on Unraid

Prerequisites: SSH to the box, and outbound HTTPS from the container to
**github.com** (the code), **api.openai.com**, **api.notion.com**, and the
parish sites. Nothing else is needed on the host — `git` runs *inside* the
container, so the host does not need it at run time.

### 1. Build the image

Unraid has no build UI, so build once over SSH. The build context only supplies
`requirements.txt` and `docker/*.sh`; the application code is not in the image.

Stock Unraid 6.x does **not** ship `git` (it comes from NerdTools), so the
tarball route is the one that always works:

```bash
mkdir -p /mnt/user/appdata/bulletin-worker/src
cd /mnt/user/appdata/bulletin-worker/src
curl -L https://github.com/mfgarvin/bulletin/archive/refs/heads/main.tar.gz | tar xz
cd bulletin-main
docker build -t bulletin-worker .
```

If you do have `git` on the box, `git clone https://github.com/mfgarvin/bulletin.git`
and build from the checkout instead — same result, easier to rebuild later.

### 2. Create the container

**A. Use the template (nicer UI).** Copy `docker/unraid-template.xml` from the
directory you just built in to `/boot/config/plugins/dockerMan/templates-user/`,
then **Docker → ADD CONTAINER → bulletin-worker** in the template dropdown.
Fill in the three keys, check the schedule and parish list, Apply.

Unraid tries to pull `bulletin-worker:latest` on Apply and there is no registry
copy — the locally built image is used, but if the UI treats the failed pull as
fatal, use option B and then "Add container" against the running container to
get it into the UI.

**B. Plain docker run**, then "Add container" against the existing one:

```bash
docker run -d \
  --name bulletin-worker \
  --restart unless-stopped \
  -e OPENAI_API_KEY=sk-... \
  -e NOTION_API_KEY=secret_... \
  -e PARISH_DB_ID=... \
  -e TZ=America/New_York \
  -e CRON_SCHEDULE="0 9 * * 6" \
  -e MODE=ids \
  -e PARISHES="ss-c st-basil-the-g olp-cle" \
  -v /mnt/user/appdata/bulletin-worker/logs:/logs \
  bulletin-worker
```

### 3. Verify it

`docker logs bulletin-worker` right after start should show the clone, then the
banner. The `code:` line is the point of the whole design — it names the commit
this container will actually run:

```
2026-08-14T09:00:01-04:00 [sync] cloning https://github.com/mfgarvin/bulletin.git (main) into /app
2026-08-14T09:00:03-04:00 [sync] cloned at 9f44ac5
2026-08-14T09:00:03-04:00 [sync] dependencies unchanged
2026-08-14T09:00:03-04:00 bulletin worker starting
  schedule : 0 9 * * 6  (TZ=America/New_York)
  code     : https://github.com/mfgarvin/bulletin.git @ main — 9f44ac5
  update   : true (before each run)
  mode     : ids
  parishes : ss-c st-basil-the-g olp-cle
  logs     : /logs
```

If it exits instead, the last line says which check failed (missing secret,
empty `PARISHES`, malformed `CRON_SCHEDULE`, unreachable repo).

Then force one real run and watch it work:

```bash
docker exec bulletin-worker run-worker.sh
```

Expect a `[sync] code: already current` line, `worker start — … @ <commit>`, per-parish
progress, and `done OK`. Confirm in Notion that those three rows have today's
`GPT Timestamp`. The three defaults are the parishes that 403 from GitHub
Actions' datacenter IPs but load fine from a residential connection — which is
the entire reason this container exists, so a run that fails on all three means
the box is not getting the residential path you expect.

`DRY_RUN=true` is available for a no-write rehearsal, but note it still
downloads bulletins and calls OpenAI — it costs the same as a real run, it just
skips the Notion write.

## Operating it

```bash
docker logs -f bulletin-worker              # scheduler + job output
docker exec bulletin-worker run-worker.sh   # force a run right now
ls /mnt/user/appdata/bulletin-worker/logs   # per-run logs
grep -l 'STALE code' /mnt/user/appdata/bulletin-worker/logs/*.log   # runs that missed an update
```

Cron only fires while the container is up; `--restart unless-stopped` (or the
template's equivalent) is what makes it survive a reboot. A run that is still
going when the next one is due is skipped, not stacked.

**Updating.** Code changes need nothing here — push to `main` and the next run
has them. Rebuild the image only for the base image, the worker scripts
(`docker/*.sh`), or the dependency baseline: re-download or `git pull`, then
`docker build -t bulletin-worker .` and restart the container from the Unraid
UI. Editing `docker/unraid-template.xml` in the repo does not touch the copy in
`/boot/...`; re-copy it if you want the new defaults in the UI.
