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
    # (day, time) entries to remove outright. For a Mass the extractor invented
    # rather than mistimed — a feast heading read as its own celebration, a
    # neighbouring parish's Mass — where remapping the time would merge a
    # week-specific note into the recurring entry and repeat it forever.
    # Matched against the stored time, before mass_time_fixes runs.
    drop_masses: set[tuple[str, int]] = field(default_factory=set)
    # Replaces the confession slots outright. For a listing the extractor
    # misread structurally, where no per-time correction can express the fix
    # (one slot has to become two).
    confession_times: list[ConfessionTime] | None = None
    # Replaces the adoration slots outright. For a schedule the bulletin states
    # in prose ("Adoration is Thurs-Sun") while printing only a list of hours it
    # needs covered - there is nothing in the stored data to derive it from.
    adoration_times: list[AdorationTime] | None = None


# Keyed by ParishID, or by exact Name for rows whose ParishID is still empty.
#
# Retired 2026-08-21: the 1259 and st-vincent-de-paul-elyria-oh entries (both
# the v2.5.4 "&-as-range" confession misread) are gone. Both rows re-extracted
# on 2026-08-15 under the fixed prompt and the pipeline produced the corrected
# slots on its own, with better notes than the hand-stated ones - so keeping
# the fixes would have overwritten good live data with staler text.
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
    "0512": ManualFix(
        reason="Saturday 'Vigil Mass' recorded as 05:30; a vigil is an evening "
        "Mass and confession runs 16:00-17:00 right before it, so 17:30",
        mass_time_fixes={("Saturday", 530): 1730},
    ),
    "1285": ManualFix(
        reason="stored adoration was the bulletin's 'adorers are needed' list - "
        "eight overnight coverage slots plus a lone Thursday. The bulletin says "
        "'Adoration is Thurs-Sun'; the other adoration lines on that page belong "
        "to the other parishes sharing the bulletin (confirmed by hand 2026-08-05)",
        adoration_times=[
            AdorationTime(day=day, start_time=0, end_time=0, end_next_day=True,
                          notes="Adoration runs continuously Thursday through Sunday")
            for day in ("Thursday", "Friday", "Saturday")
        ] + [
            AdorationTime(day="Sunday", start_time=0, end_time=None,
                          notes="Adoration runs continuously Thursday through "
                                "Sunday; the bulletin does not state when it ends")
        ],
    ),
    "2492": ManualFix(
        reason="perpetual chapel carrying one stale slot - a Holy Thursday "
        "one-off ('Adoration in church until Midnight after the Mass of the "
        "Lord's Supper') stored as a recurring weekly Thursday, so it "
        "advertised 7pm-midnight every Thursday of the year (confirmed by hand "
        "2026-08-05)",
        adoration_times=[],
    ),
    "sfds-a": ManualFix(
        reason="the extraction is correct and reproducible - the bulletin's "
        "masthead states 'Adoration / Monday: 7:00-8:00 a.m., 6:00-10:00 p.m., "
        "Tuesday-Friday: 7:00 a.m.-10:00 p.m. (breaking for Masses)' and every "
        "run returns exactly that. It is stated here only because "
        "UPDATE_ADORATION = False means no ordinary run will ever write it. "
        "Re-check against the masthead if the parish changes its hours - this "
        "entry will keep restoring these times (read from the 2026-08-30 "
        "bulletin)",
        adoration_times=[
            AdorationTime(day="Monday", start_time=700, end_time=800),
            AdorationTime(day="Monday", start_time=1800, end_time=2200),
        ] + [
            AdorationTime(day=day, start_time=700, end_time=2200,
                          notes="Breaking for Masses")
            for day in ("Tuesday", "Wednesday", "Thursday", "Friday")
        ],
    ),
    "1259": ManualFix(
        reason="the Cathedral is the hardest row in the database and its errors "
        "are fabrication rather than misreading - see cathedral-1259 notes in "
        "CLAUDE.md. Against the 2026-08-30 bulletin, whose masthead reads "
        "'Saturday: 4:30 pm (Sunday Vigil) / 6:00 pm (Sunday Vigil at Immaculate "
        "Conception) / Sunday: 8:30, 11:00 am; 5:30 pm / Monday-Friday in the "
        "Chapel: 7:15 am, 12:00 pm' and 'Confessions ... Saturday: 3:00-4:00 pm': "
        "(1) a Sunday 10:30 Mass that appears NOWHERE in the document, standing "
        "in for the real 11:00 - the parish's live-streamed principal Mass, which "
        "was missing entirely; (2) Sunday 15:30 for the stated 5:30 pm; (3) the "
        "Oratory of the Immaculate Conception's 18:00 vigil folded inline (it "
        "belongs to immat-con-cle, and SITE_EXCLUSIONS cannot catch it because "
        "the model emitted no Oratory site to exclude), carrying a fabricated "
        "'(in the Chapel)' note - the chapel is a weekday space; (4) Saturday "
        "confession starting 15:30 against a stated 3:00 pm. Verified against "
        "the bulletin text layer 2026-08-30",
        mass_time_fixes={
            ("Sunday", 1030): 1100,
            ("Sunday", 1530): 1730,
        },
        # 1800 is the Oratory's vigil. 1600 was last year's shape of the same
        # recurring bug (a feast heading read as its own celebration) and is
        # inert this week - kept because the spurious 15th Mass has taken a
        # different form three weeks running, and an unmatched drop costs
        # nothing.
        drop_masses={("Saturday", 1800), ("Saturday", 1600)},
        # Only the Saturday start is wrong, but there is no per-slot confession
        # remap, so the full masthead listing is stated. Stable enough to state:
        # these times have not moved through the whole renovation.
        confession_times=[
            ConfessionTime(day="Saturday", start_time=1500, end_time=1600),
            ConfessionTime(day="Wednesday", start_time=1700, end_time=1725,
                           notes="Confession in the Chapel"),
        ] + [
            ConfessionTime(day=day, start_time=start, end_time=None)
            for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday")
            for start in (745, 1130)
        ],
    ),
    "sc-c": ManualFix(
        reason="stored adoration was Lent-only, published year-round. The single "
        "Sunday 12:30-13:00 slot's own note reads 'Sundays in Lent; includes "
        "Gorzkie Zale' - and 'Exact times not explicitly stated; estimated as "
        "immediately following Mass', so the end was invented too. Adoration has "
        "no seasonal encoding (no mass_date equivalent), so the schedule cannot "
        "be stated correctly; an empty adoration is closer to the truth than one "
        "advertising a Lenten devotion on an August Sunday. UPDATE_ADORATION is "
        "False, so no normal run would ever correct it (2026-08-21)",
        adoration_times=[],
    ),
    "1071-MIC": ManualFix(
        reason="three of four adoration slots were Lent-only, published "
        "year-round: Mon/Wed/Fri 7-8am 'with Fr. Anthony; offered ... until "
        "Easter Day'. Easter 2026 has passed, so they advertise a devotion "
        "that ended in April. The sanitizer flags rather than drops a seasonal "
        "slot sitting alongside a real one, which is right in general - here "
        "the notes state the end themselves, so it can be stated. The Thursday "
        "17:00-19:00 slot is the genuine year-round adoration and is kept "
        "(2026-08-24)",
        adoration_times=[
            AdorationTime(day="Thursday", start_time=1700, end_time=1900),
        ],
    ),
    "st-mel-cleveland-oh": ManualFix(
        reason="stored confession was St. Mark's, not St. Mel's - one Saturday "
        "15:00-16:00 slot whose own note reads 'Saturdays @ St. Mark'. St. Mel "
        "and St. Mark are a cluster; each bulletin prints the combined "
        "schedule. The 2026-08-10 extraction already had this right - it put "
        "the confession in the St. Mark site, which SINGLE_SITE_PARISHES then "
        "correctly filtered out, leaving the St. Mel site with zero "
        "confessions. It could not be written back because save_extraction() "
        "only writes a non-empty list, so the wrong value from an earlier run "
        "survived every correct run since. Pastor confirmed no confessions are "
        "heard at St. Mel (2026-08-21)",
        confession_times=[],
    ),
    # Four rows from the 2026-08-29 run's retraction warnings. In every case
    # the fresh extraction correctly returned NO confessions and the stored
    # value is the wrong one being kept - the v2.5.8 St. Mel shape, where
    # save_extraction() never writes an empty list so a bad value outlives
    # every correct run after it. Each was checked against the bulletin.
    "0582": ManualFix(
        reason="the bulletin's entire Confessions entry reads 'Please ask a "
        "priest before or after Mass' - an availability statement with no "
        "times at all (read off the 2026-08-30 cover, which is an image; the "
        "text layer is nothing but the ad pages). The four stored slots "
        "(Sun 08:00, Sun 18:00, Tue 09:00, Thu 09:00, all noted 'Please ask a "
        "priest before or after Mass') were manufactured by anchoring that "
        "clause to Mass times, and to times this parish does not even use - "
        "its Masses are Sun 09:00/17:00 and Tue/Thu 08:30. _fold_appointment_"
        "only() misses it because the note does not open with appointment "
        "language (2026-08-30)",
        confession_times=[],
    ),
    "0414-sp": ManualFix(
        reason="St. Philomena has no confessions of its own. The shared "
        "Communion of Saints bulletin states 'Reconciliation: Saturday 3pm, "
        "the first Wednesday of each month at 7pm IN ST. ANN CHURCH, and any "
        "time upon request' - both slots belong to the St. Ann row (0414), "
        "which holds them correctly. The stored slot even carries the note "
        "'St. Ann Church' (2026-08-30)",
        confession_times=[],
    ),
    "1855-james": ManualFix(
        reason="St. James has no confessions of its own. The shared bulletin "
        "lists 'Confessions: Saturday 3:00-4:00pm at St. Luke / Saturday "
        "3:00-3:30pm at St. Clement' - the two stored slots are those, with "
        "their own notes saying 'at St. Luke' and 'at St. Clement'. Both "
        "belong to 1855 and 1855-clem, which hold them (2026-08-30)",
        confession_times=[],
    ),
    "our-lady-help-of-christians-litchfield-oh": ManualFix(
        reason="the stored confession is the Lodi worship site's, and says so "
        "itself: 'First Monday of the month; after 6:00PM Mass (listed as Lodi "
        "Site in bulletin)'. The bulletin's schedule puts the Monday 6:00 pm "
        "Mass at Lodi, and olhc-lodi already holds this slot. No confession "
        "time is printed for Litchfield anywhere in the bulletin (2026-08-30)",
        confession_times=[],
    ),
    "scas-e": ManualFix(
        reason="the Monday 19:00-20:00 slot is a one-off event published "
        "weekly - its own note reads 'Collinwood Cluster Penance Service "
        "(held at St. Casimir's)', which is a seasonal communal service, not a "
        "standing Monday confession. Same shape as the seasonal-adoration "
        "class in v2.5.11, which the sanitizer only covers for adoration. The "
        "Saturday 16:00-17:00 slot is kept: it sits directly before the 17:00 "
        "vigil and has no such marker (2026-08-30)",
        confession_times=[
            ConfessionTime(day="Saturday", start_time=1600, end_time=1700),
        ],
    ),
    "sem-c": ManualFix(
        reason="the single stored confession is Lent-only published in "
        "August: its own note reads 'In preparation for Easter; before Sunday "
        "Mass', and the end time was invented on top of that ('also said to be "
        "available even during Stations of the Cross'). Same class as sc-c's "
        "Lenten adoration in v2.5.8. The current bulletin page contains no "
        "confession language at all (2026-08-30)",
        confession_times=[],
    ),
    "1823": ManualFix(
        reason="the bulletin says 'Thursdays no Mass / no hay misa los "
        "jueves', but the row published two Thursday Masses. Both come from "
        "the undated Holy Day policy line ('Solemnity/Holy Day Mass: Vigil "
        "Mass 7:00 pm; Holy Day 9:30 am'): the 07:00 is that 7:00 PM vigil, "
        "AM/PM-flipped, and the 09:30 is the Holy Day morning Mass landed on "
        "the one weekday with no Mass. The sanitizer now drops the 07:00 on "
        "its own (its 'Holy Day Vigil Mass' note no longer reads as a weekly "
        "vigil), but the 09:30 carries no note at all, so nothing can catch "
        "it. Weekday Masses are Mon/Tue/Fri 09:30 and Wed 18:00 (2026-08-30)",
        drop_masses={("Thursday", 700), ("Thursday", 930)},
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

    # Drops run first, so a dropped entry is matched on its stored time and
    # never picked up by a remapping below.
    if manual and manual.drop_masses:
        kept = []
        for mass in site.mass_times:
            if (mass.day.value, mass.time) in manual.drop_masses:
                notes.append(
                    f"mass: dropped {mass.day.value} {mass.time:04d} "
                    f"({manual.reason})"
                )
            else:
                kept.append(mass)
        site.mass_times = kept

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

    if manual and manual.confession_times is not None:
        notes.append(
            f"confession: replaced {len(site.confession_times)} stored slot(s) "
            f"with {len(manual.confession_times)} stated slot(s) ({manual.reason})"
        )
        site.confession_times = [
            t.model_copy(deep=True) for t in manual.confession_times
        ]

    # Stated before the sanitizer runs, so the replacement is validated and
    # deduplicated on the same path as anything the extractor produced.
    if manual and manual.adoration_times is not None:
        notes.append(
            f"adoration: replaced {len(site.adoration.times)} stored slot(s) with "
            f"{len(manual.adoration_times)} stated slot(s) ({manual.reason})"
        )
        site.adoration.times = [t.model_copy(deep=True) for t in manual.adoration_times]

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
