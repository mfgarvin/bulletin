"""Export Notion parish data to JSON file."""

import asyncio
import json
import os
from dataclasses import dataclass
from typing import Any, Optional

from notion_client import AsyncClient


@dataclass
class FullParishData:
    """Complete parish data from Notion."""

    parish_id: str
    name: str
    enabled: bool
    publisher: str
    last_run: Optional[str]
    bulletin_url: Optional[str]
    address: Optional[str]
    city: Optional[str]
    zipcode: Optional[str]
    phone: Optional[str]
    website: Optional[str]
    mass_times: list[dict]
    confessions: list[dict]
    adoration: dict
    events: list[dict]
    events_summary: Optional[str]


async def fetch_all_parishes(client: AsyncClient, database_id: str) -> list[FullParishData]:
    """Fetch all parishes with full data from Notion."""
    parishes: list[FullParishData] = []
    cursor: Optional[str] = None

    while True:
        if cursor:
            response = await client.databases.query(
                database_id=database_id, start_cursor=cursor
            )
        else:
            response = await client.databases.query(database_id=database_id)

        for row in response["results"]:
            parishes.append(_row_to_full_parish(row))

        if not response.get("has_more"):
            break
        cursor = response["next_cursor"]

    return parishes


def _get_property(row: dict, name: str) -> Any:
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


def _parse_json_field(value: str) -> Any:
    """Parse a JSON string field, returning empty structure on failure."""
    if not value or not value.strip():
        return []
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return []


def _row_to_full_parish(row: dict) -> FullParishData:
    """Convert a Notion row to FullParishData."""
    adoration_raw = _get_property(row, "Adoration")
    adoration = {"is_perpetual": False, "times": []}
    if adoration_raw:
        try:
            adoration = json.loads(adoration_raw)
        except json.JSONDecodeError:
            pass

    return FullParishData(
        parish_id=_get_property(row, "ParishID"),
        name=_get_property(row, "Name"),
        enabled=_get_property(row, "Enable"),
        publisher=_get_property(row, "Bulletin Publisher"),
        last_run=_get_property(row, "GPT Timestamp") or None,
        bulletin_url=_get_property(row, "Link to latest bulletin") or None,
        address=_get_property(row, "Street Address") or None,
        city=_get_property(row, "City") or None,
        zipcode=_get_property(row, "Zip Code") or None,
        phone=_get_property(row, "Phone Number") or None,
        website=_get_property(row, "Website") or None,
        mass_times=_parse_json_field(_get_property(row, "Mass Times")),
        confessions=_parse_json_field(_get_property(row, "Confessions")),
        adoration=adoration,
        events=_parse_json_field(_get_property(row, "Events")),
        events_summary=_get_property(row, "Events Summary") or None,
    )


def parish_to_dict(parish: FullParishData) -> dict:
    """Convert FullParishData to a dictionary for JSON export."""
    return {
        "parish_id": parish.parish_id,
        "name": parish.name,
        "enabled": parish.enabled,
        "publisher": parish.publisher,
        "last_run": parish.last_run,
        "bulletin_url": parish.bulletin_url,
        "contact": {
            "address": parish.address,
            "city": parish.city,
            "zipcode": parish.zipcode,
            "phone": parish.phone,
            "website": parish.website,
        },
        "mass_times": parish.mass_times,
        "confessions": parish.confessions,
        "adoration": parish.adoration,
        "events": parish.events,
        "events_summary": parish.events_summary,
    }


async def main() -> str:
    """Export all Notion parish data to export.json."""
    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["PARISH_DB_ID"]
    client = AsyncClient(auth=api_key)

    parishes = await fetch_all_parishes(client, database_id)

    # Export as dict keyed by parish name
    export_data = {p.name: parish_to_dict(p) for p in parishes}

    with open("export.json", "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    return f"Exported {len(parishes)} parishes to export.json"


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    print(asyncio.run(main()))
