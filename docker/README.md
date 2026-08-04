# Bulletin worker in Docker (Unraid)

Containerized version of `local_worker.sh`. It runs `main.py` on a cron schedule
and writes extraction results to Notion.

Why it exists: a few parish websites block GitHub Actions' datacenter IPs but load
fine from a residential connection, so those parishes have to be processed from
home. This **does not** regenerate `export.json` — the weekly GitHub Actions job
rebuilds `export.json` / `parish_data.json` from Notion. The worker just keeps
those Notion rows fresh in between.

Differences from `local_worker.sh`: no `git pull` / venv management (the code is
baked into the image — rebuild to update), and secrets come from container env
vars instead of a `.env` file. The run lock, per-run logs, and "skip if the
previous run is still going" behavior are unchanged.

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
| `MODE` | `ids` | `ids` = process exactly `PARISHES`; `all` = every enabled stale parish. |
| `PARISHES` | — | IDs for `MODE=ids`, space- or comma-separated (e.g. `ss-c st-basil-the-g`). |
| `STALE_DAYS` | `6` | For `MODE=all`: age before a parish is reprocessed. |
| `RUN_ON_START` | `false` | `true` runs once at container start, then follows the schedule. |
| `DRY_RUN` | `false` | `true` extracts but writes nothing to Notion. |
| `VERBOSE` | `false` | Debug logging. |
| `METHOD` | (unset) | `direct_pdf` or `marker_ocr`; unset uses main.py's default. |
| `LOG_RETENTION_DAYS` | `30` | Prune per-run logs older than this. `0` disables. |

Volume: `/logs` → per-run `run-YYYYMMDD-HHMMSS.log` files. Job output also goes to
the container log, so the Unraid log viewer shows it live.

The container refuses to start if a required secret is missing, or if
`MODE=ids` with an empty `PARISHES`, or if `CRON_SCHEDULE` isn't 5 fields —
better a loud failure at start than a job that silently never fires.

## Install on Unraid

Unraid has no build UI, so build the image once over SSH, then point a container
at it.

```bash
# 1. On the Unraid box, get the code somewhere persistent
mkdir -p /mnt/user/appdata/bulletin-worker
cd /mnt/user/appdata/bulletin-worker
git clone https://github.com/mfgarvin/bulletin.git repo
cd repo

# 2. Build
docker build -t bulletin-worker .
```

Then either:

**A. Use the template (nicer UI).** Copy `docker/unraid-template.xml` to
`/boot/config/plugins/dockerMan/templates-user/` on the box, then
**Docker → ADD CONTAINER → bulletin-worker** in the template dropdown. Fill in
the three keys, adjust the schedule/parishes, Apply.

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
  -e PARISHES="ss-c st-basil-the-g" \
  -v /mnt/user/appdata/bulletin-worker/logs:/logs \
  bulletin-worker
```

To update after a code change: `git pull && docker build -t bulletin-worker .`,
then restart the container from the Unraid UI.

## Operating it

```bash
docker logs -f bulletin-worker          # scheduler + job output
docker exec bulletin-worker run-worker.sh   # force a run right now
ls /mnt/user/appdata/bulletin-worker/logs   # per-run logs
```

First-time sanity check: set `RUN_ON_START=true` and `DRY_RUN=true`, start it,
watch the log, then turn both back off.
