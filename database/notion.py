"""Notion database implementation."""

import asyncio
import json
import logging
import os
from datetime import date, timedelta
from typing import Optional

from notion_client import APIResponseError, AsyncClient

from schemas import BulletinExtraction, ParishRecord
from utils.retry import retry_async

from .base import DatabaseClient

logger = logging.getLogger(__name__)

# Global rate limiter for Notion API (3 requests/second limit)
_notion_semaphore = asyncio.Semaphore(2)  # Allow 2 concurrent requests
_notion_delay = 0.4  # 400ms between requests for safety margin

# Field update controls - set to False to preserve existing values
UPDATE_NAME = False
UPDATE_ADDRESS = False
UPDATE_CITY = False
UPDATE_ZIPCODE = False
UPDATE_PHONE = False
UPDATE_WEBSITE = False
# Adoration schedules rarely change and are noisy to extract (LLM occasionally
# hallucinates is_perpetual=true). Lock by default; set True for a one-off
# refresh run if you've manually updated Adoration in Notion and want it pushed.
UPDATE_ADORATION = False

# Notion caps a single rich_text block at 2000 characters, but a property can
# hold an array of them. Splitting across blocks is what lets a long JSON
# schedule round-trip intact; truncating to one block corrupted it (a sliced
# JSON string is unparseable, and readers then saw an empty schedule).
NOTION_BLOCK_LIMIT = 2000
NOTION_MAX_BLOCKS = 100

# Issue statuses set by hand that the pipeline must never overwrite.
# "Manual" marks a parish whose data is maintained by hand (no bulletin to
# scrape); "Unsupported" marks one the scraper can't read. Both survive a run
# that would otherwise stamp "No Issues"/"Warning"/"Error" over them — the
# status is a human's classification of the parish, not a run outcome.
PROTECTED_STATUSES = frozenset({"Manual", "Unsupported"})

# An extraction that comes back with a *fraction* of the stored schedule is
# usually a bulletin that didn't print one this week, not a parish that
# cancelled most of its Masses. St. Justin Martyr (`37345`) went from 9 stored
# Masses to 4 on an edition that was a newsletter with no schedule block: every
# weekday Mass vanished, and because `save_extraction` writes any non-empty
# list, a correct schedule would have been replaced by a quarter of one.
#
# This is the empty-extraction retraction guard generalised to the partial
# case, and it behaves the same way: the field is NOT written and the run says
# what it declined to overwrite. It is not trying to decide which side is
# right — a large drop is sometimes a genuine correction (`1584` shedding seven
# phantom "Federal Holiday" Masses was one). It is making the loss impossible
# to take silently, and putting both lists in front of a human who can settle
# it in one look and apply it with `utils.notion_fixes`.
#
# Measured over the 50-parish sample in `studies/verification/`: 2 rows trip it
# (4%), which is a readable rate for a guard that stops real data loss.
PARTIAL_RETRACTION_RATIO = 0.5
# Below this many stored entries a "half the schedule" ratio is meaningless -
# one slot flapping on a 2-Mass row would hold the write every week.
PARTIAL_RETRACTION_MIN_STORED = 3


class NotionClient(DatabaseClient):
    """Notion database client implementation."""

    def __init__(self, client: AsyncClient, database_id: str):
        self._client = client
        self._database_id = database_id

    @classmethod
    def from_environment(cls) -> "NotionClient":
        """Create client from environment variables."""
        api_key = os.environ["NOTION_API_KEY"]
        database_id = os.environ["PARISH_DB_ID"]
        client = AsyncClient(auth=api_key)
        return cls(client, database_id)

    async def _rate_limited_call(self, coro):
        """Execute a Notion API call with rate limiting."""
        async with _notion_semaphore:
            result = await coro
            await asyncio.sleep(_notion_delay)
            return result

    @retry_async(
        max_attempts=3,
        base_delay=2.0,
        retryable_exceptions=(APIResponseError, asyncio.TimeoutError, ConnectionError),
    )
    async def _query_database(self, **kwargs):
        """Query database with rate limiting and retry."""
        return await self._rate_limited_call(
            self._client.databases.query(database_id=self._database_id, **kwargs)
        )

    @retry_async(
        max_attempts=3,
        base_delay=2.0,
        retryable_exceptions=(APIResponseError, asyncio.TimeoutError, ConnectionError),
    )
    async def _update_page(self, page_id: str, properties: dict):
        """Update page with rate limiting and retry."""
        return await self._rate_limited_call(
            self._client.pages.update(page_id=page_id, properties=properties)
        )

    async def get_parishes_to_process(self, stale_days: int = 6) -> list[ParishRecord]:
        """Get enabled parishes with data older than stale_days.

        `stale_days` defaults to 6 rather than 7 so the weekly Saturday job
        doesn't skip its own previous run: at 7, a row stamped last Saturday
        sits exactly on the cutoff, fails the strict `<`, and refreshes only
        every other week.
        """
        all_parishes = await self._get_all_parishes()
        cutoff = date.today() - timedelta(days=stale_days)
        return [
            p for p in all_parishes if p.enabled and (p.last_run is None or p.last_run < cutoff)
        ]

    async def get_parish(self, parish_id: str) -> Optional[ParishRecord]:
        """Get a single parish by ID."""
        response = await self._query_database(
            filter={"property": "ParishID", "rich_text": {"equals": parish_id}},
        )
        if not response["results"]:
            return None
        return self._row_to_parish_record(response["results"][0])

    async def get_bulletin_group(self, group_id: str) -> list[ParishRecord]:
        """Get all parishes that share a bulletin (same Bulletin Group ID)."""
        response = await self._query_database(
            filter={"property": "Bulletin Group ID", "rich_text": {"equals": group_id}},
        )
        return [self._row_to_parish_record(row) for row in response["results"]]

    async def get_stored_schedules(
        self, parish_id: str
    ) -> Optional[tuple[Optional[list], Optional[list]]]:
        """(Mass Times, Confessions) as stored, for change verification.

        Returns None when the row can't be found. Either element is None when
        its stored JSON is corrupt — that is the v2.5.1 alarm's territory, not
        a diffable schedule.
        """
        row = await self._get_parish_row(parish_id)
        if not row:
            return None

        def parse(prop: str) -> Optional[list]:
            raw = self._get_property(row, prop)
            if not raw:
                return []
            try:
                value = json.loads(raw)
                return value if isinstance(value, list) else None
            except json.JSONDecodeError:
                return None

        return parse("Mass Times"), parse("Confessions")

    async def save_extraction(
        self,
        parish_id: str,
        extraction: BulletinExtraction,
        bulletin_url: str,
        log: list[str],
        site_index: int = 0,
        skip_name_update: bool = False,
    ) -> list[str]:
        """Save extraction results to Notion.

        Returns a list of warnings about fields the extraction came back empty
        on while Notion still holds a value — see `retractions` below.

        Args:
            parish_id: The parish/site ID to update
            extraction: Full bulletin extraction
            bulletin_url: URL of the processed bulletin
            log: Extraction log messages
            site_index: Which site to save (default 0 = first/primary site)
            skip_name_update: If True, don't overwrite the Name field (for multi-site)
        """
        row = await self._get_parish_row(parish_id)
        if not row:
            raise ValueError(f"Parish not found: {parish_id}")
        page_id = row["id"]

        # Get the specific site's data (or empty if no sites)
        site = extraction.sites[site_index] if extraction.sites else None

        # A field the extractor came back empty on is never written (see the
        # guards below), so a wrong value can outlive every correct run that
        # followed it. Blanking it automatically is not safe — one bad scan
        # would wipe a good schedule — but staying silent is how a stale value
        # survives for months, so the run says what it declined to overwrite.
        # Adoration is excluded: UPDATE_ADORATION is a deliberate lock, so it
        # is never written and would warn on every parish, every week.
        retractions: list[str] = []
        for label, prop, found in (
            ("mass times", "Mass Times", site.mass_times if site else []),
            ("confession times", "Confessions", site.confession_times if site else []),
        ):
            if not found and self._has_stored_entries(row, prop):
                retractions.append(
                    f"No {label} extracted for '{parish_id}', but Notion still "
                    f"holds a previous value, which was kept rather than "
                    f"overwritten. Check it against the bulletin; if the parish "
                    f"really dropped them, clear the field via utils.notion_fixes."
                )

        # Serialize site-specific data
        if site:
            mass_json = json.dumps([m.model_dump(mode="json") for m in site.mass_times])
            conf_json = json.dumps(
                [c.model_dump(mode="json") for c in site.confession_times]
            )
            adore_json = json.dumps(site.adoration.model_dump(mode="json"))
        else:
            mass_json = "[]"
            conf_json = "[]"
            adore_json = "{}"

        # Serialize parish-wide data
        events_json = json.dumps([e.model_dump(mode="json") for e in extraction.events])

        properties: dict = {
            "GPT Timestamp": self._text_property(date.today().isoformat()),
            "GPT Logs": self._text_property("\n".join(log)),
            "Link to latest bulletin": {"url": bulletin_url},
        }

        # A large drop is held back rather than written; see
        # PARTIAL_RETRACTION_RATIO. Only recurring entries are compared.
        if site and site.mass_times:
            stored = self._stored_schedule(row, "Mass Times")
            warning = self._partial_retraction(
                row, "Mass Times", "Mass times",
                self._recurring_keys(stored or [], "mass_date", "day", "time"),
                self._recurring_keys(site.mass_times, "mass_date", "day", "time"),
            ) if stored else None
            if warning:
                retractions.append(warning)
            else:
                properties["Mass Times"] = self._text_property(mass_json)

        if site and site.confession_times:
            stored = self._stored_schedule(row, "Confessions")
            warning = self._partial_retraction(
                row, "Confessions", "confession slots",
                self._recurring_keys(stored or [], None, "day", "start_time"),
                self._recurring_keys(site.confession_times, None, "day", "start_time"),
            ) if stored else None
            if warning:
                retractions.append(warning)
            else:
                properties["Confessions"] = self._text_property(conf_json)
        if UPDATE_ADORATION and site and (site.adoration.times or site.adoration.is_perpetual):
            properties["Adoration"] = self._text_property(adore_json)
        if extraction.events:
            properties["Events"] = self._text_property(events_json)
        if extraction.events_summary:
            properties["Events Summary"] = self._text_property(
                extraction.events_summary
            )

        # Update parish contact info (parish-level)
        info = extraction.parish_info
        if UPDATE_NAME and info.name and not skip_name_update:
            properties["Name"] = {"title": [{"text": {"content": info.name}}]}
        if UPDATE_PHONE and info.phone:
            properties["Phone Number"] = self._text_property(info.phone)
        if UPDATE_WEBSITE and info.website:
            properties["Website"] = self._text_property(info.website)

        # Update site-specific address info
        if site:
            if UPDATE_ADDRESS and site.address:
                properties["Street Address"] = self._text_property(site.address)
            if UPDATE_CITY and site.city:
                properties["City"] = self._text_property(site.city)
            if UPDATE_ZIPCODE and site.zipcode:
                properties["Zip Code"] = self._text_property(site.zipcode)

        # Clear any previous issues on successful save, unless a human has
        # classified this parish (Manual/Unsupported) — that outranks a run.
        current_status = self._current_status(row)
        if current_status in PROTECTED_STATUSES:
            logger.info(
                f"Preserving '{current_status}' status for parish: {parish_id}"
            )
        else:
            properties["Issues"] = {"status": {"name": "No Issues"}}
            properties["Issue Log"] = self._text_property("")

        await self._update_page(page_id=page_id, properties=properties)
        logger.info(f"Saved extraction to Notion for parish: {parish_id}")
        return retractions

    async def save_issue(
        self,
        parish_id: str,
        error: str | None = None,
        warnings: list[str] | None = None,
    ) -> None:
        """Save error/warning status to Notion.

        Args:
            parish_id: The parish ID to update
            error: Error message if processing failed
            warnings: List of warning messages
        """
        row = await self._get_parish_row(parish_id)
        if not row:
            logger.warning(f"Cannot save issue - parish not found: {parish_id}")
            return
        page_id = row["id"]

        # A hand-set classification outranks whatever this run concluded. The
        # Issue Log is still written, so the detail isn't lost.
        current_status = self._current_status(row)
        protected = current_status in PROTECTED_STATUSES

        # Build issue log
        log_parts = []
        if error:
            log_parts.append(f"ERROR: {error}")
        if warnings:
            for w in warnings:
                log_parts.append(f"WARNING: {w}")

        issue_log = "\n".join(log_parts)

        # Determine status: Error if there's an error, Warning if only warnings
        if error:
            status = "Error"
        elif warnings:
            status = "Warning"
        else:
            status = "No Issues"

        properties: dict = {"Issue Log": self._text_property(issue_log)}
        if not protected:
            properties["Issues"] = {"status": {"name": status}}
        # Only refresh the timestamp on clean runs, so parishes with
        # errors/warnings stay stale and are retried on the next run.
        if status == "No Issues":
            properties["GPT Timestamp"] = self._text_property(date.today().isoformat())

        await self._update_page(page_id=page_id, properties=properties)
        if protected:
            logger.info(
                f"Logged issues for parish {parish_id}, kept '{current_status}' status"
            )
        else:
            logger.info(f"Saved issue status ({status}) for parish: {parish_id}")

    # Private helpers

    async def _get_all_parishes(self, cursor: Optional[str] = None) -> list[ParishRecord]:
        """Fetch all parishes with pagination."""
        if cursor:
            response = await self._query_database(start_cursor=cursor)
        else:
            response = await self._query_database()

        results = [self._row_to_parish_record(row) for row in response["results"]]

        if response.get("has_more"):
            results.extend(await self._get_all_parishes(cursor=response["next_cursor"]))

        return results

    async def _get_parish_row(self, parish_id: str) -> Optional[dict]:
        """Get the full Notion row for a parish."""
        response = await self._query_database(
            filter={"property": "ParishID", "rich_text": {"equals": parish_id}},
        )
        if response["results"]:
            return response["results"][0]
        return None

    @staticmethod
    def _current_status(row: dict) -> str:
        """Read the current `Issues` status name, or '' if unset."""
        prop = row["properties"].get("Issues")
        if not prop or prop.get("type") != "status":
            return ""
        status = prop["status"]
        return status["name"] if status else ""

    def _row_to_parish_record(self, row: dict) -> ParishRecord:
        """Convert a Notion row to a ParishRecord."""
        last_run_str = self._get_property(row, "GPT Timestamp")
        last_run = None
        if last_run_str and last_run_str.strip():
            try:
                last_run = date.fromisoformat(last_run_str)
            except ValueError:
                pass

        # Bulletin Group ID links parishes sharing a bulletin
        group_id = self._get_property(row, "Bulletin Group ID")
        bulletin_group_id = group_id if group_id else None

        # Bulletin Page URL for self-hosted bulletins
        bulletin_url = self._get_property(row, "Bulletin Page URL") or None

        return ParishRecord(
            parish_id=self._get_property(row, "ParishID"),
            name=self._get_property(row, "Name"),
            enabled=self._get_property(row, "Enable"),
            publisher=self._get_property(row, "Bulletin Publisher"),
            last_run=last_run,
            bulletin_url=bulletin_url,
            bulletin_group_id=bulletin_group_id,
        )

    @staticmethod
    def _recurring_keys(entries, mass_date_key: str | None, day_key: str,
                        time_key: str) -> set[tuple]:
        """(day, time) keys for comparing two versions of one schedule.

        Accepts either stored JSON dicts or the extraction's Pydantic models.
        Dated Masses are excluded: a one-off legitimately disappears once its
        date passes, and counting that as a retraction would fire every week.
        """
        keys: set[tuple] = set()
        for e in entries:
            get = e.get if isinstance(e, dict) else lambda k, _e=e: getattr(_e, k, None)
            if mass_date_key and get(mass_date_key) is not None:
                continue
            day, time = get(day_key), get(time_key)
            day = getattr(day, "value", day)
            if day is not None and time is not None:
                keys.add((day, time))
        return keys

    def _stored_schedule(self, row: dict, prop: str) -> list | None:
        """Stored JSON list for `prop`, or None when absent/corrupt."""
        raw = self._get_property(row, prop)
        if not raw:
            return None
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            # Corrupt stored JSON is the v2.5.1 alarm's business, and it is not
            # a schedule we can compare against. Never block a write on it -
            # overwriting corruption with a good extraction is the repair path.
            return None
        return value if isinstance(value, list) else None

    def _partial_retraction(self, row: dict, prop: str, label: str,
                            stored_keys: set, new_keys: set) -> str | None:
        """Warning text when the new schedule would gut the stored one."""
        if len(stored_keys) < PARTIAL_RETRACTION_MIN_STORED:
            return None
        removed = stored_keys - new_keys
        if not removed or len(removed) / len(stored_keys) <= PARTIAL_RETRACTION_RATIO:
            return None
        shown = ", ".join(f"{d} {t:04d}" for d, t in sorted(removed, key=str))
        return (
            f"Extraction dropped {len(removed)} of {len(stored_keys)} stored "
            f"{label} ({shown}) - more than half the schedule, which is usually "
            f"a bulletin that printed no schedule this week rather than a real "
            f"change. The field was NOT overwritten. Check it against the "
            f"bulletin; if the change is real, apply it via utils.notion_fixes."
        )

    def _has_stored_entries(self, row: dict, prop: str) -> bool:
        """True if `prop` currently holds a non-empty JSON collection."""
        raw = self._get_property(row, prop)
        if not raw:
            return False
        try:
            return bool(json.loads(raw))
        except json.JSONDecodeError:
            # Corrupt stored JSON is its own alarm (v2.5.1). Treat it as
            # present so this warning doesn't quietly stand in for that one.
            return True

    def _get_property(self, row: dict, name: str):
        """Extract a property value from a Notion row."""
        prop = row["properties"].get(name)
        if not prop:
            return ""

        prop_type = prop["type"]

        if prop_type in ["rich_text", "title"]:
            # Join every block: long values are written across several.
            return "".join(item["plain_text"] for item in prop[prop_type])
        elif prop_type == "checkbox":
            return prop["checkbox"]
        elif prop_type == "url":
            return prop["url"] or ""
        elif prop_type == "select":
            select = prop["select"]
            return select["name"] if select else ""

        return ""

    @staticmethod
    def _text_property(text: str) -> dict:
        """Create a Notion rich_text property value, split across blocks.

        Notion rejects any single block over 2000 characters, so long values
        are chunked. Readers concatenate every block, so the original string
        round-trips exactly — which matters most for the JSON schedule fields,
        where losing the tail makes the whole value unparseable.

        Only a value too long for even the maximum number of blocks is cut,
        and that is logged rather than passing silently.
        """
        if not text:
            return {"rich_text": []}

        chunks = [
            text[i : i + NOTION_BLOCK_LIMIT]
            for i in range(0, len(text), NOTION_BLOCK_LIMIT)
        ]
        if len(chunks) > NOTION_MAX_BLOCKS:
            logger.error(
                f"Value of {len(text)} chars exceeds Notion's maximum "
                f"({NOTION_MAX_BLOCKS * NOTION_BLOCK_LIMIT}); dropping the tail"
            )
            chunks = chunks[:NOTION_MAX_BLOCKS]

        return {"rich_text": [{"text": {"content": c}} for c in chunks]}
