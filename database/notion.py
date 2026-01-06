"""Notion database implementation."""

import json
import os
from datetime import date, timedelta
from typing import Optional

from notion_client import AsyncClient

from schemas import BulletinExtraction, ParishRecord

from .base import DatabaseClient


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

    async def get_parishes_to_process(self, stale_days: int = 7) -> list[ParishRecord]:
        """Get enabled parishes with data older than stale_days."""
        all_parishes = await self._get_all_parishes()
        cutoff = date.today() - timedelta(days=stale_days)
        return [
            p for p in all_parishes if p.enabled and (p.last_run is None or p.last_run < cutoff)
        ]

    async def get_parish(self, parish_id: str) -> Optional[ParishRecord]:
        """Get a single parish by ID."""
        response = await self._client.databases.query(
            database_id=self._database_id,
            filter={"property": "ParishID", "rich_text": {"equals": parish_id}},
        )
        if not response["results"]:
            return None
        return self._row_to_parish_record(response["results"][0])

    async def get_bulletin_group(self, group_id: str) -> list[ParishRecord]:
        """Get all parishes that share a bulletin (same Bulletin Group ID)."""
        response = await self._client.databases.query(
            database_id=self._database_id,
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
        if site and (site.adoration.times or site.adoration.is_perpetual):
            properties["Adoration"] = self._text_property(truncate(adore_json))
        if extraction.events:
            properties["Events"] = self._text_property(truncate(events_json))
        if extraction.events_summary:
            properties["Events Summary"] = self._text_property(
                truncate(extraction.events_summary)
            )

        # Update parish contact info (parish-level)
        info = extraction.parish_info
        if info.name and not skip_name_update:
            properties["Name"] = {"title": [{"text": {"content": info.name}}]}
        if info.phone:
            properties["Phone Number"] = self._text_property(info.phone)
        if info.website:
            properties["Website"] = self._text_property(info.website)

        # Update site-specific address info
        if site:
            if site.address:
                properties["Street Address"] = self._text_property(site.address)
            if site.city:
                properties["City"] = self._text_property(site.city)
            if site.zipcode:
                properties["Zip Code"] = self._text_property(site.zipcode)

        await self._client.pages.update(page_id=page_id, properties=properties)

    # Private helpers

    async def _get_all_parishes(self, cursor: Optional[str] = None) -> list[ParishRecord]:
        """Fetch all parishes with pagination."""
        if cursor:
            response = await self._client.databases.query(
                database_id=self._database_id, start_cursor=cursor
            )
        else:
            response = await self._client.databases.query(database_id=self._database_id)

        results = [self._row_to_parish_record(row) for row in response["results"]]

        if response.get("has_more"):
            results.extend(await self._get_all_parishes(cursor=response["next_cursor"]))

        return results

    async def _get_parish_page_id(self, parish_id: str) -> Optional[str]:
        """Get the Notion page ID for a parish."""
        response = await self._client.databases.query(
            database_id=self._database_id,
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

        return ParishRecord(
            parish_id=self._get_property(row, "ParishID"),
            name=self._get_property(row, "Name"),
            enabled=self._get_property(row, "Enable"),
            publisher=self._get_property(row, "Bulletin Publisher"),
            last_run=last_run,
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
