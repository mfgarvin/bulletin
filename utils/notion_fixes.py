"""One-off repair pass over the Notion database.

Fixes the data problems found in the 2026-07 validation of `export.json`. Two
kinds of fix:

1. **Sanitizer replay** — runs `utils/sanitize.py` over the schedules already
   stored in Notion. The scraper now runs this on every extraction, but stored
   rows predate it, and `UPDATE_ADORATION = False` means adoration rows are
   never rewritten by a normal run. This script writes directly, bypassing the
   `UPDATE_*` locks in `database/notion.py` by design.

2. **Manual fixes** — per-record corrections that no general rule can derive
   (a dropped decimal point in a coordinate, a misspelled parish name, an
   AM/PM flip on a specific Mass).

Dry-run by default; pass --apply to write.

    python -m utils.notion_fixes            # show what would change
    python -m utils.notion_fixes --apply    # write it

Safe to re-run: every fix is idempotent, and a second pass reports no changes.
"""

import argparse
import asyncio
import json
import os
from dataclasses import dataclass, field
from typing import Any

from notion_client import AsyncClient

from schemas import (
    AdorationSchedule,
    AdorationTime,
    BulletinExtraction,
    ConfessionTime,
    MassTime,
    SiteInfo,
)
from utils.sanitize import sanitize_extraction

try:
    from .notion_to_json import FullParishData, fetch_all_parishes
except ImportError:
    from notion_to_json import FullParishData, fetch_all_parishes


@dataclass
class ManualFix:
    """A correction that has to be stated, not derived.

    Every field is optional; only what's set gets written. `mass_time_fixes`
    maps (day, wrong_time) -> right_time so an AM/PM flip can be corrected
    without touching the rest of the schedule.
    """

    reason: str
    name: str | None = None
    lonlat: str | None = None
    address: str | None = None
    is_perpetual: bool | None = None
    mass_time_fixes: dict[tuple[str, int], int] = field(default_factory=dict)


# Keyed by ParishID, or by exact Name for rows whose ParishID is still empty.
MANUAL_FIXES: dict[str, ManualFix] = {
    "0689": ManualFix(
        reason="latitude lost its decimal point (41099421 -> 41.099421)",
        lonlat="-81.5669225,41.099421",
    ),
    "2224": ManualFix(
        reason="LonLat held two coordinate pairs concatenated; kept the first "
        "(E 83rd St, Cleveland), dropped the stray Canton-area pair",
        lonlat="-81.6292108,41.4950223",
    ),
    "1794": ManualFix(
        reason="Saturday Vigil recorded as 05:00; a vigil is an evening Mass",
        mass_time_fixes={("Saturday", 500): 1700},
    ),
    "sc-p": ManualFix(
        reason="Saturday Vigil recorded as 04:00; a vigil is an evening Mass",
        mass_time_fixes={("Saturday", 400): 1600},
    ),
    "ss-cosmas-damian-twinsburg-oh": ManualFix(
        reason="flagged perpetual, but every note says the chapel closes "
        "overnight 7pm-9am",
        is_perpetual=False,
    ),
    # This row has no ParishID yet, so it is matched by name.
    "Saint Elizabeth of Hungry, Cleveland": ManualFix(
        reason="parish name misspelled; Street Address held the ZIP (44104), "
        "which is already in Zip Code - blanked rather than guessed",
        name="Saint Elizabeth of Hungary, Cleveland",
        address="",
    ),
}

def _to_site(parish: FullParishData) -> SiteInfo:
    """Rebuild the typed schedule objects from the JSON stored in Notion.

    Rows written before `end_next_day` existed simply lack the key, and the
    model default (False) is then corrected by the sanitizer.
    """
    return SiteInfo(
        site_name=parish.name,
        mass_times=[MassTime(**m) for m in parish.mass_times],
        confession_times=[ConfessionTime(**c) for c in parish.confessions],
        adoration=AdorationSchedule(
            is_perpetual=bool(parish.adoration.get("is_perpetual", False)),
            times=[AdorationTime(**a) for a in parish.adoration.get("times", [])],
        ),
    )


def _dump_masses(site: SiteInfo) -> str:
    return json.dumps([m.model_dump(mode="json") for m in site.mass_times])


def _dump_confessions(site: SiteInfo) -> str:
    return json.dumps([c.model_dump(mode="json") for c in site.confession_times])


def _dump_adoration(site: SiteInfo) -> str:
    return json.dumps(site.adoration.model_dump(mode="json"))


def _text(value: str) -> dict:
    return {"rich_text": [{"text": {"content": value}}]}


def plan_fixes(parish: FullParishData) -> tuple[dict[str, Any], list[str]]:
    """Work out what to change for one parish.

    Returns (Notion properties to write, human-readable descriptions).
    """
    properties: dict[str, Any] = {}
    notes: list[str] = []

    try:
        # Two independent copies: one stays pristine as the comparison
        # baseline, the other is what every fix mutates.
        original = _to_site(parish)
        site = _to_site(parish)
    except Exception as e:  # malformed stored JSON - report, never guess
        return {}, [f"SKIPPED: stored schedule JSON could not be parsed ({e})"]

    before = (
        _dump_masses(original),
        _dump_confessions(original),
        _dump_adoration(original),
    )

    manual = MANUAL_FIXES.get(parish.parish_id) or MANUAL_FIXES.get(parish.name)

    # Manual Mass-time corrections run before the sanitizer, so a repaired
    # vigil is deduplicated against the rest of the schedule normally.
    if manual and manual.mass_time_fixes:
        for mass in site.mass_times:
            new_time = manual.mass_time_fixes.get((mass.day.value, mass.time))
            if new_time is not None:
                notes.append(
                    f"mass: {mass.day.value} {mass.time:04d} -> {new_time:04d} "
                    f"({manual.reason})"
                )
                mass.time = new_time

    if manual and manual.is_perpetual is not None:
        if site.adoration.is_perpetual != manual.is_perpetual:
            notes.append(
                f"adoration: is_perpetual {site.adoration.is_perpetual} -> "
                f"{manual.is_perpetual} ({manual.reason})"
            )
            site.adoration.is_perpetual = manual.is_perpetual

    report = sanitize_extraction(BulletinExtraction(sites=[site]), parish.parish_id)
    after = (_dump_masses(site), _dump_confessions(site), _dump_adoration(site))

    notes.extend(report.repairs)
    # Flags are judgement calls; surface them but change nothing.
    notes.extend(f"(flagged, not changed) {f}" for f in report.flags)

    if after[0] != before[0]:
        properties["Mass Times"] = _text(after[0])
    if after[1] != before[1]:
        properties["Confessions"] = _text(after[1])
    if after[2] != before[2]:
        properties["Adoration"] = _text(after[2])

    if manual:
        if manual.lonlat is not None and manual.lonlat != (parish.lonlat or ""):
            notes.append(f"LonLat: {parish.lonlat!r} -> {manual.lonlat!r} ({manual.reason})")
            properties["LonLat"] = _text(manual.lonlat)
        if manual.name is not None and manual.name != parish.name:
            notes.append(f"Name: {parish.name!r} -> {manual.name!r} ({manual.reason})")
            properties["Name"] = {"title": [{"text": {"content": manual.name}}]}
        if manual.address is not None and manual.address != (parish.address or ""):
            notes.append(
                f"Street Address: {parish.address!r} -> {manual.address!r} "
                f"({manual.reason})"
            )
            properties["Street Address"] = _text(manual.address)

    return properties, notes


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply", action="store_true", help="write changes (default: dry run)"
    )
    args = parser.parse_args()

    client = AsyncClient(auth=os.environ["NOTION_API_KEY"])
    parishes = await fetch_all_parishes(client, os.environ["PARISH_DB_ID"])
    print(f"Fetched {len(parishes)} parishes\n")

    changed = flagged = written = 0

    for parish in parishes:
        properties, notes = plan_fixes(parish)
        if not notes:
            continue

        label = parish.parish_id or "(no id)"
        print(f"--- [{label}] {parish.name}")
        for note in notes:
            marker = " " if note.startswith("(flagged") else "*"
            print(f"  {marker} {note}")

        if properties:
            changed += 1
            fields = ", ".join(sorted(properties))
            print(f"  -> writes: {fields}")
            if args.apply:
                await client.pages.update(page_id=parish.notion_id, properties=properties)
                await asyncio.sleep(0.4)  # Notion allows ~3 req/sec
                written += 1
        else:
            flagged += 1
        print()

    print("=" * 60)
    if args.apply:
        print(f"Wrote {written} parishes. {flagged} more have flags needing review.")
    else:
        print(
            f"DRY RUN: {changed} parishes would be written, "
            f"{flagged} more have flags needing review."
        )
        print("Re-run with --apply to write.")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass
    asyncio.run(main())
