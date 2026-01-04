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

    async def save_extraction(
        self,
        parish_id: str,
        extraction: BulletinExtraction,
        bulletin_url: str,
        log: list[str],
    ) -> None:
        """Save extraction results to Notion."""
        page_id = await self._get_parish_page_id(parish_id)
        if not page_id:
            raise ValueError(f"Parish not found: {parish_id}")

        # Serialize extraction data to JSON (mode='json' handles date serialization)
        mass_json = json.dumps([m.model_dump(mode='json') for m in extraction.mass_times])
        conf_json = json.dumps([c.model_dump(mode='json') for c in extraction.confession_times])
        adore_json = json.dumps(extraction.adoration.model_dump(mode='json'))
        events_json = json.dumps([e.model_dump(mode='json') for e in extraction.events])

        properties: dict = {
            "GPT Timestamp": self._text_property(date.today().isoformat()),
            "GPT Logs": self._text_property("\n".join(log)),
            "Link to latest bulletin": {"url": bulletin_url},
        }

        if extraction.mass_times:
            properties["Mass Times"] = self._text_property(mass_json)
        if extraction.confession_times:
            properties["Confessions"] = self._text_property(conf_json)
        if extraction.adoration.times or extraction.adoration.is_perpetual:
            properties["Adoration"] = self._text_property(adore_json)
        if extraction.events:
            properties["Events"] = self._text_property(events_json)
        if extraction.events_summary:
            properties["Events Summary"] = self._text_property(extraction.events_summary)

        # Update parish contact info if present
        info = extraction.parish_info
        if info.address:
            properties["Street Address"] = self._text_property(info.address)
        if info.city:
            properties["City"] = self._text_property(info.city)
        if info.zipcode:
            properties["Zip Code"] = self._text_property(info.zipcode)
        if info.phone:
            properties["Phone Number"] = self._text_property(info.phone)
        if info.website:
            properties["Website"] = self._text_property(info.website)

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

        return ParishRecord(
            parish_id=self._get_property(row, "ParishID"),
            name=self._get_property(row, "Name"),
            enabled=self._get_property(row, "Enable"),
            publisher=self._get_property(row, "Bulletin Publisher"),
            last_run=last_run,
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
