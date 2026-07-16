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

    async def get_parishes_to_process(self, stale_days: int = 7) -> list[ParishRecord]:
        """Get enabled parishes with data older than stale_days."""
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

    async def save_extraction(
        self,
        parish_id: str,
        extraction: BulletinExtraction,
        bulletin_url: str,
        log: list[str],
        site_index: int = 0,
        skip_name_update: bool = False,
    ) -> None:
        """Save extraction results to Notion.

        Args:
            parish_id: The parish/site ID to update
            extraction: Full bulletin extraction
            bulletin_url: URL of the processed bulletin
            log: Extraction log messages
            site_index: Which site to save (default 0 = first/primary site)
            skip_name_update: If True, don't overwrite the Name field (for multi-site)
        """
        page_id = await self._get_parish_page_id(parish_id)
        if not page_id:
            raise ValueError(f"Parish not found: {parish_id}")

        # Notion rich_text has a 2000 character limit per block
        def truncate(s: str, limit: int = 2000) -> str:
            return s if len(s) <= limit else s[: limit - 3] + "..."

        # Get the specific site's data (or empty if no sites)
        site = extraction.sites[site_index] if extraction.sites else None

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

        if site and site.mass_times:
            properties["Mass Times"] = self._text_property(truncate(mass_json))
        if site and site.confession_times:
            properties["Confessions"] = self._text_property(truncate(conf_json))
        if UPDATE_ADORATION and site and (site.adoration.times or site.adoration.is_perpetual):
            properties["Adoration"] = self._text_property(truncate(adore_json))
        if extraction.events:
            properties["Events"] = self._text_property(truncate(events_json))
        if extraction.events_summary:
            properties["Events Summary"] = self._text_property(
                truncate(extraction.events_summary)
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

        # Clear any previous issues on successful save
        properties["Issues"] = {"status": {"name": "No Issues"}}
        properties["Issue Log"] = self._text_property("")

        await self._update_page(page_id=page_id, properties=properties)
        logger.info(f"Saved extraction to Notion for parish: {parish_id}")

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
        page_id = await self._get_parish_page_id(parish_id)
        if not page_id:
            logger.warning(f"Cannot save issue - parish not found: {parish_id}")
            return

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

        properties = {
            "Issues": {"status": {"name": status}},
            "Issue Log": self._text_property(issue_log),
        }
        # Only refresh the timestamp on clean runs, so parishes with
        # errors/warnings stay stale and are retried on the next run.
        if status == "No Issues":
            properties["GPT Timestamp"] = self._text_property(date.today().isoformat())

        await self._update_page(page_id=page_id, properties=properties)
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

    async def _get_parish_page_id(self, parish_id: str) -> Optional[str]:
        """Get the Notion page ID for a parish."""
        response = await self._query_database(
            filter={"property": "ParishID", "rich_text": {"equals": parish_id}},
        )
        if response["results"]:
            return response["results"][0]["id"]
        return None

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

    def _get_property(self, row: dict, name: str):
        """Extract a property value from a Notion row."""
        prop = row["properties"].get(name)
        if not prop:
            return ""

        prop_type = prop["type"]

        if prop_type in ["rich_text", "title"]:
            items = prop[prop_type]
            return items[0]["plain_text"] if items else ""
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
        """Create a Notion rich_text property value."""
        return {"rich_text": [{"text": {"content": text}}]}
