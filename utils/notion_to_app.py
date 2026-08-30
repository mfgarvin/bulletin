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
from datetime import date
from typing import Any

from notion_client import AsyncClient

try:
    from .notion_to_json import FullParishData, fetch_all_parishes
    from .monthly_recurrence import derive_ordinal
except ImportError:
    from notion_to_json import FullParishData, fetch_all_parishes
    from monthly_recurrence import derive_ordinal

logger = logging.getLogger(__name__)

# Word-boundary keywords that mark a dated Mass entry as private (not for
# public listing). Word boundaries matter: "memorial" alone would catch
# "Memorial Day Mass", which IS public, so it's deliberately NOT on this list.
# If we ever need to filter "Memorial Mass for <name>" specifically, do it
# with a pattern, not a bare keyword.
PRIVATE_MASS_KEYWORDS = ("wedding", "funeral", "nuptial", "rehearsal")

# Issue statuses meaning "this parish's schedule was never machine-verified":
# "Manual" is hand-entered static info (no bulletin to scrape), "Unsupported"
# is a parish whose site the scraper can't read (JS-heavy pages, Google Drive).
# Both are exactly the cases where a user in the app is a better source of
# truth than we are, so the export invites feedback on them.
FEEDBACK_STATUSES = frozenset({"Manual", "Unsupported"})
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


def _structured_mass(mass_times: list[dict], today: str) -> list[dict[str, Any]]:
    """Structured mass entries.

    Regular weekly masses have mass_date == null.
    Holiday/special-occasion masses carry a YYYY-MM-DD mass_date; those in the
    past are dropped so a stale bulletin can't keep advertising last Christmas.
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
        mass_date = m.get("mass_date")
        if mass_date and mass_date < today:
            logger.info(
                "filtered past-dated mass: %s %s — %s",
                mass_date, m.get("day"), m.get("notes"),
            )
            continue
        entry = {
            "day": day,
            "start": _hhmm(time),
            "mass_date": mass_date,  # null or "YYYY-MM-DD"
            "language": m.get("language"),
            "notes": m.get("notes"),
        }
        # weeks_of_month / excluded_weeks: emitted only when derived, and
        # never on a dated Mass (mutually exclusive with mass_date per spec).
        if not mass_date:
            entry.update(derive_ordinal(day, m.get("notes")) or {})
        out.append(entry)
    out.sort(key=lambda e: (
        e["mass_date"] or "",
        weekday_order.get(e["day"], 99),
        e["start"] or "",
    ))
    return out


def _structured_ranges(items: list[dict]) -> list[dict[str, Any]]:
    """Shared shape for confessions and adoration time slots.

    `end_next_day` marks a slot that crosses midnight, so the app never has to
    infer it from `end < start`. Rows written before that field existed are
    backfilled here from the same comparison.

    `end` is null when the bulletin gave a start and no end ("confessions after
    the 8:15 Mass"). The app renders those as a bare start time and never counts
    them as in progress. Only `start` is required — dropping the slot for want
    of an end would lose a real, scheduled confession.
    """
    weekday_order = {
        "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
        "Thursday": 4, "Friday": 5, "Saturday": 6,
    }
    out = []
    for item in items:
        day = item.get("day")
        start = item.get("start_time")
        end = item.get("end_time")
        if not day or start is None:
            continue
        entry = {
            "day": day,
            "start": _hhmm(start),
            "end": _hhmm(end),
            "end_next_day": bool(
                item.get("end_next_day", end < start if end is not None else False)
            ),
            "notes": item.get("notes"),
        }
        # weeks_of_month / excluded_weeks, emitted only when derived.
        entry.update(derive_ordinal(day, item.get("notes")) or {})
        out.append(entry)
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


# Every parish in this database is in the Diocese of Cleveland, so anything
# outside Ohio's bounding box is a data-entry error, not a distant parish.
# A dropped decimal point (41099421.0 for 41.099421) fails this handily, and
# shipping it would put a pin somewhere off the map.
OHIO_BOUNDS = (38.4, 42.3, -84.8, -80.5)  # lat_min, lat_max, lon_min, lon_max


def _validated_coords(
    latitude: float | None, longitude: float | None, name: str
) -> tuple[float | None, float | None]:
    """Drop coordinates that can't be real, logging what was rejected."""
    if latitude is None or longitude is None:
        return latitude, longitude

    lat_min, lat_max, lon_min, lon_max = OHIO_BOUNDS
    if not (lat_min <= latitude <= lat_max and lon_min <= longitude <= lon_max):
        logger.warning(
            "dropped out-of-range coordinates for %s: %s, %s", name, latitude, longitude
        )
        return None, None
    return latitude, longitude


def format_parish_for_app(parish: FullParishData, today: str) -> dict[str, Any]:
    """Convert parish data to the structured app-friendly format."""
    latitude, longitude = _validated_coords(
        *_split_lonlat(parish.lonlat), parish.name or parish.parish_id
    )

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
        "invite_feedback": parish.issues in FEEDBACK_STATUSES,
        "schedules": {
            "mass": _structured_mass(parish.mass_times, today),
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
    today = date.today().isoformat()
    export_data = [format_parish_for_app(p, today) for p in parishes]

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
