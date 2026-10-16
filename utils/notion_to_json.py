"""Export Notion parish data to JSON file for reference.py (mapboard)."""

import asyncio
import json
import logging
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any, Optional

from notion_client import AsyncClient

try:
    from .bulletin_week import run_week
    from .monthly_recurrence import derive_ordinal
except ImportError:
    from bulletin_week import run_week
    from monthly_recurrence import derive_ordinal

logger = logging.getLogger(__name__)


@dataclass
class FullParishData:
    """Complete parish data from Notion."""

    notion_id: str
    parish_id: str
    name: str
    enabled: bool
    publisher: str
    issues: str
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
        # Join every block: values over 2000 chars are written across several.
        return "".join(item["plain_text"] for item in prop[prop_type])
    elif prop_type == "checkbox":
        return prop["checkbox"]
    elif prop_type == "url":
        return prop["url"] or ""
    elif prop_type == "select":
        select = prop["select"]
        return select["name"] if select else ""
    elif prop_type == "status":
        status = prop["status"]
        return status["name"] if status else ""

    return ""


def _parse_json_field(value: str, field: str = "?", parish: str = "?") -> Any:
    """Parse a JSON string field, returning an empty structure on failure.

    A parse failure means stored data is corrupt and the parish silently loses
    that schedule, so it is logged loudly. This is exactly how truncated
    schedules went unnoticed: the export just showed an empty list.
    """
    if not value or not value.strip():
        return []
    try:
        return json.loads(value)
    except json.JSONDecodeError as e:
        logger.error(
            "CORRUPT %s for parish %s (%d chars): %s - exporting as empty",
            field, parish, len(value), e,
        )
        return []


def _row_to_full_parish(row: dict) -> FullParishData:
    """Convert a Notion row to FullParishData."""
    name = _get_property(row, "Name")
    adoration_raw = _get_property(row, "Adoration")
    adoration = {"is_perpetual": False, "times": []}
    if adoration_raw:
        try:
            adoration = json.loads(adoration_raw)
        except json.JSONDecodeError as e:
            logger.error(
                "CORRUPT Adoration for parish %s (%d chars): %s - exporting as empty",
                name, len(adoration_raw), e,
            )

    return FullParishData(
        notion_id=row["id"],
        parish_id=_get_property(row, "ParishID"),
        name=_get_property(row, "Name"),
        enabled=_get_property(row, "Enable"),
        publisher=_get_property(row, "Bulletin Publisher"),
        issues=_get_property(row, "Issues"),
        last_run=_get_property(row, "GPT Timestamp") or None,
        bulletin_url=_get_property(row, "Link to latest bulletin") or None,
        address=_get_property(row, "Street Address") or None,
        city=_get_property(row, "City") or None,
        zipcode=_get_property(row, "Zip Code") or None,
        phone=_get_property(row, "Phone Number") or None,
        website=_get_property(row, "Website") or None,
        lonlat=_get_property(row, "LonLat") or None,
        mass_times=_parse_json_field(_get_property(row, "Mass Times"), "Mass Times", name),
        confessions=_parse_json_field(_get_property(row, "Confessions"), "Confessions", name),
        adoration=adoration,
        events=_parse_json_field(_get_property(row, "Events"), "Events", name),
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


def _calculate_duration(start: int, end: int, end_next_day: bool = False) -> int:
    """Calculate duration in minutes between two 24hr times.

    `end_next_day` cannot be inferred from `end < start` alone: a full 24hr
    span has `end == start`, which would otherwise measure as zero.
    """
    start_mins = (start // 100) * 60 + (start % 100)
    end_mins = (end // 100) * 60 + (end % 100)
    # Handle overnight (e.g., 2300 to 0100)
    if end_next_day or end_mins < start_mins:
        end_mins += 24 * 60
    return end_mins - start_mins


def _has_end_time(start: int, end: int | None, end_next_day: bool) -> bool:
    """Whether a slot states an end time.

    A null `end` means the extraction never found one ("after the 8:15am Mass").
    The mapboard has no way to show an open-ended window, so those are dropped
    rather than exported as a duration of 0 — which reads as "nothing here"
    anyway.

    `start == end` is also treated as open-ended, for rows written before the
    schema allowed a null end. With `end_next_day` set those same endpoints mean
    a full 24 hours instead, which is a real window and is kept.
    """
    if end is None:
        return False
    return start != end or end_next_day


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
        if day and start is not None:
            next_day = bool(conf.get("end_next_day"))
            if not _has_end_time(start, end, next_day):
                continue
            duration = _calculate_duration(start, end, next_day)
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
        if day and start is not None:
            next_day = bool(slot.get("end_next_day"))
            if not _has_end_time(start, end, next_day):
                continue
            duration = _calculate_duration(start, end, next_day)
            grouped[day].append({str(start): duration})
    return dict(grouped)


_SUNDAY_FIRST = (
    "Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday",
)


def _cancelled_on(day: str, last_run: Optional[str]) -> Optional[str]:
    """The date a `cancelled` flag is about, or None if it cannot be dated.

    `cancelled` means "not this week", and the Pi keeps whatever it loaded at
    startup indefinitely, so an undated flag would keep the slot dark until the
    next restart - weeks, possibly. Resolved against the week the run assumed
    (`run_week` of its own date, as the pipeline does for an undated bulletin),
    the cancellation expires by itself. No run date, no claim: the slot stays
    weekly, which is what the old board shows anyway.
    """
    if not last_run or day not in _SUNDAY_FIRST:
        return None
    try:
        run_day = date.fromisoformat(last_run[:10])
    except ValueError:
        return None
    week_start, _ = run_week(run_day)
    return (week_start + timedelta(days=_SUNDAY_FIRST.index(day))).isoformat()


def _entry_rule(entry: dict, last_run: Optional[str]) -> dict:
    """Everything that narrows one weekly entry to particular dates."""
    day = entry.get("day")
    rule = dict(derive_ordinal(day, entry.get("notes")) or {})
    if entry.get("cancelled"):
        when = _cancelled_on(day, last_run)
        if when:
            rule["cancelled_on"] = [when]
    return rule


def _rules(
    entries: list[tuple[str, int, dict]], parish: str, kind: str
) -> dict[str, dict[str, dict]]:
    """Group per-entry rules by the weekday + start key the legacy lists use.

    The legacy lists are keyed by start time alone, so two entries sharing a
    slot share its rule. If either is unconditional the slot is weekly; if they
    disagree, refuse and leave it weekly rather than pick one - the board then
    shows what it always has.
    """
    by_slot: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for day, start, rule in entries:
        by_slot[(day, str(start))].append(rule)

    out: dict[str, dict[str, dict]] = defaultdict(dict)
    for (day, start), rules in by_slot.items():
        if not all(rules):
            continue
        if any(r != rules[0] for r in rules[1:]):
            logger.warning(
                "conflicting %s rules for %s %s %s - exporting weekly: %s",
                kind, parish, day, start, rules,
            )
            continue
        out[day][start] = rules[0]
    return dict(out)


def _schedule_rules(parish: FullParishData) -> dict[str, dict]:
    """The `Rules` sidecar: which weeks each legacy slot actually occurs in.

    Only entries the legacy lists export are considered, with the same filters,
    so every rule key names a slot the Pi already has. Empty kinds are omitted.
    """
    run = parish.last_run
    masses = [
        (m["day"], m["time"], _entry_rule(m, run))
        for m in parish.mass_times
        if m.get("day") and m.get("time") is not None and not m.get("mass_date")
    ]
    ranges: dict[str, list[dict]] = {"Confessions": parish.confessions}
    if not parish.adoration.get("is_perpetual"):
        ranges["Adoration"] = parish.adoration.get("times", [])

    rules = {"Mass Times": _rules(masses, parish.name, "Mass Times")}
    for kind, items in ranges.items():
        kept = [
            (it["day"], it["start_time"], _entry_rule(it, run))
            for it in items
            if it.get("day") and it.get("start_time") is not None
            and _has_end_time(
                it["start_time"], it.get("end_time"), bool(it.get("end_next_day"))
            )
        ]
        rules[kind] = _rules(kept, parish.name, kind)
    return {kind: r for kind, r in rules.items() if r}


def _dated_masses(mass_times: list[dict], today: str) -> dict[str, list[int]]:
    """Dated Masses from today on, keyed by date: `{"2026-12-24": [1600, 2400]}`.

    Kept out of the weekday lists, which the old board reads as every week.
    Past dates, private Masses and cancelled ones are dropped, as in export.json.
    """
    try:
        from .notion_to_app import _is_private_mass
    except ImportError:
        from notion_to_app import _is_private_mass

    grouped: dict[str, list[int]] = defaultdict(list)
    for m in mass_times:
        when, time = m.get("mass_date"), m.get("time")
        if not when or time is None or when < today:
            continue
        if m.get("cancelled") or _is_private_mass(m):
            continue
        if time not in grouped[when]:
            grouped[when].append(time)
    return {when: sorted(times) for when, times in sorted(grouped.items())}


def parish_to_dict(
    parish: FullParishData, parish_id: int, today: Optional[str] = None
) -> dict:
    """Convert FullParishData to a dictionary for reference.py consumption.

    The first five keys are the legacy shape and are unchanged. `Rules` and
    `Dated Masses` are additive: a board that does not read them lights every
    slot weekly, as before. See `utils/schedule_rules.py` for the reader.
    """
    today = today or date.today().isoformat()
    return {
        "ID": parish_id,
        "Name": parish.name,
        "Mass Times": _group_mass_times(parish.mass_times),
        "Confessions": _group_confessions(parish.confessions),
        "Adoration": _format_adoration(parish.adoration),
        "Rules": _schedule_rules(parish),
        "Dated Masses": _dated_masses(parish.mass_times, today),
    }


async def main() -> str:
    """Export Notion parish data to parish_data.json for reference.py."""
    api_key = os.environ["NOTION_API_KEY"]
    database_id = os.environ["PARISH_DB_ID"]
    client = AsyncClient(auth=api_key)

    parishes = await fetch_all_parishes(client, database_id)

    # Export as dict keyed by NotionID
    today = date.today().isoformat()
    export_data = {
        p.notion_id: parish_to_dict(p, idx + 1, today)
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
