"""Alert when the weekly pipeline silently didn't run.

Actions cron is best-effort: on 2026-08-29 the scheduled Bulletin processor
never fired and *no run record was created at all*, so the failure was
invisible in the run list — and it happened again the following week. Every
downstream trigger (`workflow_run` on the export and issue-check workflows)
chains off the processor, so when the processor is skipped the whole Saturday
goes quiet in a way that looks identical to a healthy week.

This is a second, independent cron that asks two questions after the processor
should have finished:

1. **Did a schedule-triggered processor run start today?** (Saturdays only —
   a manual dispatch on another day would make this check meaningless.)
   Queried from the Actions API, so it detects the dropped-cron case exactly.
2. **Were the enabled parishes actually re-stamped?** With `stale_days=6` and
   a weekly cadence, a successful Saturday run refreshes `GPT Timestamp` on
   essentially every enabled, non-protected row the same day. A large share of
   rows not stamped within the last few days means the run was skipped, died
   early, or — the v2.5.7 trap — an out-of-band run moved every row onto a
   cadence the scheduled job then skips. All three are worth a ping.

Posts to NOTIFY_WEBHOOK_URL (same Discord/Slack webhook as check_issues) only
when something is wrong; a quiet week posts nothing. This watcher is itself a
cron and can itself be dropped, but two independent schedules dropping the
same Saturday is a much smaller coincidence than one.

Env: NOTION_API_KEY, PARISH_DB_ID, NOTIFY_WEBHOOK_URL; optionally
GITHUB_REPOSITORY + GITHUB_TOKEN for the schedule check (skipped without them).
"""

import asyncio
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone

import httpx
from notion_client import AsyncClient

from utils.check_issues import _plain_text, _status, post_webhook

logger = logging.getLogger(__name__)

PROCESSOR_WORKFLOW = "gh-actions.yml"

# A successful Saturday run stamps rows with today's date; the local worker
# stamps its three the same morning. Anything older than this many days on
# watcher day was not touched by this week's run.
FRESH_WITHIN_DAYS = 3

# Alert when more than this fraction of countable rows went unstamped. Well
# above the handful of legitimately skipped rows (blocked downloads mid-repair,
# a parish added Friday night), well below the ~everything of a missed run.
STALE_FRACTION_ALERT = 0.25

# Hand-set classifications that no run ever stamps.
NEVER_STAMPED_STATUSES = {"Manual", "Unsupported"}


async def scheduled_run_started_today() -> bool | None:
    """True/False from the Actions API; None when the check can't be made."""
    repo = os.environ.get("GITHUB_REPOSITORY")
    token = os.environ.get("GITHUB_TOKEN")
    if not (repo and token):
        logger.info("no GITHUB_REPOSITORY/GITHUB_TOKEN - skipping schedule check")
        return None

    today = datetime.now(timezone.utc).date().isoformat()
    url = (
        f"https://api.github.com/repos/{repo}/actions/workflows/"
        f"{PROCESSOR_WORKFLOW}/runs"
    )
    try:
        async with httpx.AsyncClient(timeout=30.0) as http:
            r = await http.get(
                url,
                params={"event": "schedule", "created": f">={today}"},
                headers={
                    "Authorization": f"Bearer {token}",
                    "Accept": "application/vnd.github+json",
                },
            )
            r.raise_for_status()
            return r.json().get("total_count", 0) > 0
    except Exception as e:  # the watcher must not die on an API hiccup
        logger.warning("schedule check failed: %s", e)
        return None


async def count_unstamped(client: AsyncClient, database_id: str) -> tuple[int, int]:
    """(rows not stamped within FRESH_WITHIN_DAYS, rows countable)."""
    cutoff = date.today() - timedelta(days=FRESH_WITHIN_DAYS)
    unstamped = 0
    countable = 0
    cursor = None

    while True:
        kwargs = {
            "database_id": database_id,
            "filter": {"property": "Enable", "checkbox": {"equals": True}},
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = await client.databases.query(**kwargs)

        for row in response["results"]:
            if _status(row, "Issues") in NEVER_STAMPED_STATUSES:
                continue
            countable += 1
            stamp = _plain_text(row, "GPT Timestamp")
            try:
                if date.fromisoformat(stamp.strip()) >= cutoff:
                    continue
            except ValueError:
                pass  # empty or unparseable stamp counts as unstamped
            unstamped += 1

        if not response.get("has_more"):
            break
        cursor = response["next_cursor"]

    return unstamped, countable


async def main_async() -> int:
    api_key = os.environ.get("NOTION_API_KEY")
    database_id = os.environ.get("PARISH_DB_ID")
    webhook_url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not (api_key and database_id and webhook_url):
        logger.error("missing required env: NOTION_API_KEY, PARISH_DB_ID, NOTIFY_WEBHOOK_URL")
        return 2

    alerts: list[str] = []

    if datetime.now(timezone.utc).weekday() == 5:  # Saturday, the cron's day
        started = await scheduled_run_started_today()
        if started is False:
            alerts.append(
                "No schedule-triggered Bulletin processor run started today - "
                "the Actions cron was dropped again. Dispatch the workflow "
                "manually (`gh workflow run gh-actions.yml`)."
            )
        elif started:
            logger.info("scheduled processor run found for today")

    unstamped, countable = await count_unstamped(AsyncClient(auth=api_key), database_id)
    logger.info("%d of %d countable parishes not stamped within %d days",
                unstamped, countable, FRESH_WITHIN_DAYS)
    if countable and unstamped / countable > STALE_FRACTION_ALERT:
        alerts.append(
            f"{unstamped} of {countable} enabled parishes have a GPT Timestamp "
            f"older than {FRESH_WITHIN_DAYS} days. The weekly run was skipped, "
            "failed early, or an out-of-band run moved rows off the Saturday "
            "cadence (the stale_days trap). Check `gh run list`; a manual "
            "dispatch with stale_days=0 forces every parish."
        )

    if not alerts:
        logger.info("freshness OK - nothing to report")
        return 0

    message = "**Pipeline freshness alert**\n\n" + "\n\n".join(f"• {a}" for a in alerts)
    await post_webhook(webhook_url, message)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
