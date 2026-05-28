"""Export Notion parish data to app-friendly JSON format.

Emits fully structured schedules — no display-string formatting. The consuming
app receives day-of-week, 24h HH:MM strings, language/notes/mass_date fields
as separate keys, so it never needs to regex-parse a flat schedule line.

See EXPORT_SHAPE_CHANGES.md for the migration notes handed to the Introibo app.
"""

import asyncio
import json
import logging
import os
import re
from typing import Any

from notion_client import AsyncClient

try:
    from .notion_to_json import FullParishData, fetch_all_parishes
except ImportError:
    from notion_to_json import FullParishData, fetch_all_parishes

logger = logging.getLogger(__name__)

# Word-boundary keywords that mark a dated Mass entry as private (not for
# public listing). Word boundaries matter: "memorial" alone would catch
# "Memorial Day Mass", which IS public, so it's deliberately NOT on this list.
# If we ever need to filter "Memorial Mass for <name>" specifically, do it
# with a pattern, not a bare keyword.
PRIVATE_MASS_KEYWORDS = ("wedding", "funeral", "nuptial", "rehearsal")
_PRIVATE_RE = re.compile(
    r"\b(" + "|".join(PRIVATE_MASS_KEYWORDS) + r")\b", re.IGNORECASE
)


def _is_private_mass(entry: dict) -> bool:
    """True if this dated Mass entry looks private (wedding, funeral, etc).

    Only inspects `notes`. Regular weekly Masses (mass_date == null) are
    never considered private regardless of notes.
    """
    if not entry.get("mass_date"):
        return False
    notes = entry.get("notes") or ""
    return bool(_PRIVATE_RE.search(notes))


def _hhmm(t: int | None) -> str | None:
    """Convert a 24h integer (e.g. 1630) to a zero-padded HH:MM string."""
    if t is None:
        return None
    return f"{t // 100:02d}:{t % 100:02d}"


def _structured_mass(mass_times: list[dict]) -> list[dict[str, Any]]:
    """Structured mass entries.

    Regular weekly masses have mass_date == null.
    Holiday/special-occasion masses carry a YYYY-MM-DD mass_date.
    Sorted by (mass_date or far-past, weekday-order, time) so the app can
    iterate without re-sorting.
    """
    weekday_order = {
        "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
        "Thursday": 4, "Friday": 5, "Saturday": 6,
    }
    out = []
    for m in mass_times:
        day = m.get("day")
        time = m.get("time")
        if not day or time is None:
            continue
        if _is_private_mass(m):
            logger.info(
                "filtered private mass: %s %s — %s",
                m.get("mass_date"), m.get("day"), m.get("notes"),
            )
            continue
        out.append({
            "day": day,
            "start": _hhmm(time),
            "mass_date": m.get("mass_date"),  # null or "YYYY-MM-DD"
            "language": m.get("language"),
            "notes": m.get("notes"),
        })
    out.sort(key=lambda e: (
        e["mass_date"] or "",
        weekday_order.get(e["day"], 99),
        e["start"] or "",
    ))
    return out


def _structured_ranges(items: list[dict]) -> list[dict[str, Any]]:
    """Shared shape for confessions and adoration time slots."""
    weekday_order = {
        "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
        "Thursday": 4, "Friday": 5, "Saturday": 6,
    }
    out = []
    for item in items:
        day = item.get("day")
        start = item.get("start_time")
        end = item.get("end_time")
        if not day or start is None or end is None:
            continue
        out.append({
            "day": day,
            "start": _hhmm(start),
            "end": _hhmm(end),
            "notes": item.get("notes"),
        })
    out.sort(key=lambda e: (weekday_order.get(e["day"], 99), e["start"] or ""))
    return out


def _structured_adoration(adoration: dict) -> dict[str, Any]:
    """Adoration block. Always carries is_perpetual + times (possibly empty).

    Mirrors the internal Pydantic shape so there's no information loss.
    """
    return {
        "is_perpetual": bool(adoration.get("is_perpetual", False)),
        "times": _structured_ranges(adoration.get("times", [])),
    }


def _split_lonlat(lonlat: str | None) -> tuple[float | None, float | None]:
    """Split the legacy 'lon,lat' string into separate floats.

    Returns (latitude, longitude). Either may be None if the field is missing
    or malformed.
    """
    if not lonlat:
        return None, None
    try:
        lon_s, lat_s = lonlat.split(",")
        return float(lat_s.strip()), float(lon_s.strip())
    except (ValueError, AttributeError):
        return None, None


def format_parish_for_app(parish: FullParishData) -> dict[str, Any]:
    """Convert parish data to the structured app-friendly format."""
    latitude, longitude = _split_lonlat(parish.lonlat)

    return {
        "name": parish.name or "",
        "parish_id": parish.parish_id,
        "address": parish.address,
        "city": parish.city,
        "zip_code": parish.zipcode,
        "phone": parish.phone,
        "website": parish.website,
        "latitude": latitude,
        "longitude": longitude,
        "bulletin_url": parish.bulletin_url,
        "timestamp": parish.last_run,
        "schedules": {
            "mass": _structured_mass(parish.mass_times),
            "confession": _structured_ranges(parish.confessions),
            "adoration": _structured_adoration(parish.adoration),
        },
        "events_summary": parish.events_summary,
    }


async def main() -> str:
    """Export all parish data in structured format to export.json."""
    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["PARISH_DB_ID"]
    client = AsyncClient(auth=api_key)

    parishes = await fetch_all_parishes(client, database_id)
    export_data = [format_parish_for_app(p) for p in parishes]

    with open("export.json", "w", encoding="utf-8") as f:
        json.dump(export_data, f, ensure_ascii=False, indent=2)

    return f"Exported {len(export_data)} parishes to export.json"


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    print(asyncio.run(main()))
