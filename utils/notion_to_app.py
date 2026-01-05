"""Export Notion parish data to app-friendly JSON format.

Formats times in 12-hour format and groups schedules by weekday.
"""

import asyncio
import json
import os
from datetime import datetime
from typing import Any

from notion_client import AsyncClient

from .notion_to_json import FullParishData, fetch_all_parishes

WEEKDAYS = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]


def convert_to_12h(time: int) -> str:
    """Convert 24hr int (e.g., 1630) to 12hr string (e.g., '4:30PM')."""
    time_str = str(time).zfill(4)
    try:
        dt = datetime.strptime(time_str, "%H%M")
        return dt.strftime("%-I:%M%p")
    except ValueError:
        return str(time)


def convert_to_12h_range(start: int, end: int) -> str:
    """Convert start/end times to a range string (e.g., '3:00PM to 4:00PM')."""
    return f"{convert_to_12h(start)} to {convert_to_12h(end)}"


def format_mass_times(mass_times: list[dict]) -> list[str]:
    """Format mass times grouped by weekday."""
    schedule: list[str] = []

    for weekday in WEEKDAYS:
        day_masses = []
        for mass in mass_times:
            if mass.get("day") == weekday:
                time_str = convert_to_12h(mass.get("time", 0))
                if mass.get("language"):
                    time_str += f" ({mass['language']})"
                if mass.get("notes"):
                    time_str += f" - {mass['notes']}"
                day_masses.append(time_str)

        if day_masses:
            schedule.append(f"{weekday}: {', '.join(day_masses)}")

    return schedule


def format_confession_times(confessions: list[dict]) -> list[str]:
    """Format confession times grouped by weekday."""
    schedule: list[str] = []

    for weekday in WEEKDAYS:
        day_confessions = []
        for conf in confessions:
            if conf.get("day") == weekday:
                time_range = convert_to_12h_range(
                    conf.get("start_time", 0), conf.get("end_time", 0)
                )
                if conf.get("notes"):
                    time_range += f" - {conf['notes']}"
                day_confessions.append(time_range)

        if day_confessions:
            schedule.append(f"{weekday}: {', '.join(day_confessions)}")

    return schedule


def format_adoration(adoration: dict) -> list[str]:
    """Format adoration schedule."""
    if adoration.get("is_perpetual"):
        return ["Perpetual Adoration (24/7)"]

    times = adoration.get("times", [])
    if not times:
        return []

    schedule: list[str] = []
    for weekday in WEEKDAYS:
        day_times = []
        for slot in times:
            if slot.get("day") == weekday:
                time_range = convert_to_12h_range(
                    slot.get("start_time", 0), slot.get("end_time", 0)
                )
                if slot.get("notes"):
                    time_range += f" - {slot['notes']}"
                day_times.append(time_range)

        if day_times:
            schedule.append(f"{weekday}: {', '.join(day_times)}")

    return schedule


def format_parish_for_app(parish: FullParishData) -> dict[str, Any]:
    """Convert parish data to app-friendly format."""
    return {
        "name": parish.name.split(",")[0] if parish.name else "",  # First part of name
        "parish_id": parish.parish_id,
        "address": parish.address,
        "city": parish.city,
        "zip_code": parish.zipcode,
        "phone": parish.phone,
        "website": parish.website,
        "mass_times": format_mass_times(parish.mass_times),
        "confessions": format_confession_times(parish.confessions),
        "adoration": format_adoration(parish.adoration),
        "events_summary": parish.events_summary,
    }


async def main() -> str:
    """Export all parish data in app-friendly format to export.json."""
    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["PARISH_DB_ID"]
    client = AsyncClient(auth=api_key)

    parishes = await fetch_all_parishes(client, database_id)

    # Only export enabled parishes with data
    export_data = [
        format_parish_for_app(p)
        for p in parishes
        if p.enabled and p.mass_times
    ]

    with open("export.json", "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    return f"Exported {len(export_data)} parishes to export.json"


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # Assume env vars are already set
    print(asyncio.run(main()))
