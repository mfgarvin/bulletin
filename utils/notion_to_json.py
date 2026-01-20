"""Export Notion parish data to JSON file for reference.py (mapboard)."""

import asyncio
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Optional

from notion_client import AsyncClient


@dataclass
class FullParishData:
    """Complete parish data from Notion."""

    notion_id: str
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
    lonlat: Optional[str]
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
        notion_id=row["id"],
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
        lonlat=_get_property(row, "LonLat") or None,
        mass_times=_parse_json_field(_get_property(row, "Mass Times")),
        confessions=_parse_json_field(_get_property(row, "Confessions")),
        adoration=adoration,
        events=_parse_json_field(_get_property(row, "Events")),
        events_summary=_get_property(row, "Events Summary") or None,
    )


def _group_mass_times(mass_times: list[dict]) -> dict[str, list[int]]:
    """Group mass times by weekday, returning just the time integers.

    Input: [{"day": "Sunday", "time": 900}, {"day": "Sunday", "time": 1100}]
    Output: {"Sunday": [900, 1100]}
    """
    grouped: dict[str, list[int]] = defaultdict(list)
    for mt in mass_times:
        day = mt.get("day")
        time = mt.get("time")
        # Skip holiday masses (those with mass_date set)
        if day and time is not None and not mt.get("mass_date"):
            grouped[day].append(time)
    # Sort times within each day
    return {day: sorted(times) for day, times in grouped.items()}


def _calculate_duration(start: int, end: int) -> int:
    """Calculate duration in minutes between two 24hr times."""
    start_mins = (start // 100) * 60 + (start % 100)
    end_mins = (end // 100) * 60 + (end % 100)
    # Handle overnight (e.g., 2300 to 0100)
    if end_mins < start_mins:
        end_mins += 24 * 60
    return end_mins - start_mins


def _group_confessions(confessions: list[dict]) -> dict[str, list[dict[str, int]]]:
    """Group confessions by weekday with start time and duration.

    Input: [{"day": "Saturday", "start_time": 1500, "end_time": 1600}]
    Output: {"Saturday": [{"1500": 60}]}
    """
    grouped: dict[str, list[dict[str, int]]] = defaultdict(list)
    for conf in confessions:
        day = conf.get("day")
        start = conf.get("start_time")
        end = conf.get("end_time")
        if day and start is not None and end is not None:
            duration = _calculate_duration(start, end)
            grouped[day].append({str(start): duration})
    return dict(grouped)


def _format_adoration(adoration: dict) -> dict:
    """Format adoration schedule for reference.py.

    Input: {"is_perpetual": true, "times": [...]}
    Output: {"Is24Hour": true} or {"Saturday": [{"900": 60}]}
    """
    if adoration.get("is_perpetual"):
        return {"Is24Hour": True}

    times = adoration.get("times", [])
    if not times:
        return {}

    grouped: dict[str, list[dict[str, int]]] = defaultdict(list)
    for slot in times:
        day = slot.get("day")
        start = slot.get("start_time")
        end = slot.get("end_time")
        if day and start is not None and end is not None:
            duration = _calculate_duration(start, end)
            grouped[day].append({str(start): duration})
    return dict(grouped)


def parish_to_dict(parish: FullParishData, parish_id: int) -> dict:
    """Convert FullParishData to a dictionary for reference.py consumption."""
    return {
        "ID": parish_id,
        "NotionID": parish.notion_id,
        "Mass Times": _group_mass_times(parish.mass_times),
        "Confessions": _group_confessions(parish.confessions),
        "Adoration": _format_adoration(parish.adoration),
    }


async def main() -> str:
    """Export Notion parish data to parish_data.json for reference.py."""
    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["PARISH_DB_ID"]
    client = AsyncClient(auth=api_key)

    parishes = await fetch_all_parishes(client, database_id)

    # Export as dict keyed by parish name, with sequential IDs
    export_data = {
        p.name: parish_to_dict(p, idx + 1)
        for idx, p in enumerate(parishes)
    }

    with open("parish_data.json", "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    return f"Exported {len(parishes)} parishes to parish_data.json"


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # Assume env vars are already set
    print(asyncio.run(main()))
