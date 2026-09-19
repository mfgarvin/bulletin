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
    DayOfWeek,
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
    # (day, time) -> replacement note, for a Mass whose time is right but whose
    # published note is wrong. `notes` is user-facing text, so a note welded
    # together by _dedupe_masses out of two contradictory labels is a defect
    # even when every time on the row is correct. None clears the note.
    mass_note_fixes: dict[tuple[str, int], str | None] = field(default_factory=dict)
    # Masses to add back. For a Mass the extractor *dropped* - the schedule is
    # right except that something is missing, so neither a remap (nothing to
    # remap from) nor a whole-list replacement (which would go stale the moment
    # the parish changed anything else) fits. The holiday-week displacement
    # class is the reason it exists: on a holiday week the day-by-day intentions
    # listing has no ordinary Mass on that weekday, so the model retracts a
    # standing Mass that is displaced for exactly one week.
    #
    # Idempotent for free - `_dedupe_masses` merges on
    # `(day, time, language, mass_date)`, so re-adding a Mass already present
    # collapses back to one entry. Applied after the remap, so an added Mass is
    # never itself remapped, and before the note fixes, so one can be relabelled.
    add_masses: list[MassTime] = field(default_factory=list)
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
        reason="the Saturday Vigil was recorded as 05:00; a vigil is an "
        "evening Mass. (Its Labor Day Monday 06:28 restoration was retired "
        "2026-09-12 - see the block below - but this one stays: the sanitizer "
        "only FLAGS a vigil at a morning time, so the extractor reproduces it). "
        "Confessions restored 2026-09-19: the run dropped the anchored "
        "'Thursday Before First Friday' slot, which its masthead prints right "
        "under the Saturday one - 'CONFESSION / Saturday | 3:30pm - 4:30pm / "
        "Thursday Before First Friday | 3:30 - 4:30pm'. This is one of the six "
        "rows v2.5.32 built `anchored_week` for, and it was deleted six days "
        "after that shipped. See the ORDINAL SLOTS block below",
        mass_time_fixes={("Saturday", 500): 1700},
        confession_times=[
            ConfessionTime(day=DayOfWeek.SATURDAY, start_time=1530, end_time=1630),
            ConfessionTime(
                day=DayOfWeek.THURSDAY,
                start_time=1530,
                end_time=1630,
                notes="Thursday before First Friday",
            ),
        ],
    ),
    "sc-p": ManualFix(
        reason="Saturday Vigil recorded as 04:00; a vigil is an evening Mass",
        mass_time_fixes={("Saturday", 400): 1600},
    ),
    "1687": ManualFix(
        reason="Friday 08:30 note was two contradictory labels merged by "
        "_dedupe_masses - the masthead's generic 'Mon/Wed/Fri 8:30 am in the "
        "Marian Chapel' and the week's listing 'School Mass (Church)'. Verified "
        "against the 2026-08-30 bulletin: Friday 08:30 is the school Mass and it "
        "is in the Church, so the Marian Chapel half is masthead bleed",
        mass_note_fixes={("Friday", 830): "School Mass (in the Church)"},
    ),
    "0512": ManualFix(
        reason="Saturday 'Vigil Mass' recorded as 05:30; a vigil is an evening "
        "Mass and confession runs 16:00-17:00 right before it, so 17:30",
        mass_time_fixes={("Saturday", 530): 1730},
    ),
    # --- 2026-09-19 run: wrong writes, each verified against its own bulletin -
    #
    # All eight carried the `LIKELY WRONG WRITE` prefix or the fabrication
    # flag. Seven were wrong; the eighth (1532) was half right and only the
    # wrong half is repaired here. Retire an entry once a later run has
    # re-extracted that slot correctly.
    "0164": ManualFix(
        reason="2026-09-19 invented a Thursday Mass on a day the parish has "
        "none. Masthead: 'Mass Schedule: Mon, Tues, Wed & Fri 9:00am', and the "
        "week's listing under '9:00 AM WEEKDAY MASS September 21-25' names "
        "Monday, Tuesday, Wednesday, Friday and skips Thursday",
        drop_masses={("Thursday", 900)},
    ),
    "st-matthew-akron-oh": ManualFix(
        reason="2026-09-19 added a Sunday 08:00 that is WEDNESDAY's Mass. The "
        "'Masses & Services' listing is two columns and the text layer "
        "interleaves them; by block coordinates '8:00AM People of the Parish' "
        "sits under 'Wednesday, September 23'. The row held exactly its own "
        "four Masses (Sun 1030, Wed 0800, Fri 0800, Sat 1600) after the "
        "2026-09-12 repair, and Wed 0800 is still stored, so this is a "
        "duplicate on the wrong day",
        drop_masses={("Sunday", 800)},
    ),
    "1101": ManualFix(
        reason="2026-09-19 added a Sunday 13:00 that belongs to `sa-o`. Its "
        "own `site_label` says so - '@ St. Agnes in Orrville' - and the "
        "bulletin prints '1:00pm Spanish Mass @ St. Agnes in Orrville' in the "
        "shared listing. St. Agnes publishes it on its own row",
        drop_masses={("Sunday", 1300)},
    ),
    "our-lady-of-angels-cleveland-oh": ManualFix(
        reason="2026-09-19 added the SUMMER Wednesday Mass alongside the "
        "school-year one, so the row published both. Masthead: 'Wednesday: "
        "9:30am (during school year) / 6:00pm (during summer)'. It is "
        "September; the 09:30 is stored and correct. Not reproduced on a "
        "second extraction either",
        drop_masses={("Wednesday", 1800)},
    ),
    "st-vitus-cleveland-oh": ManualFix(
        reason="2026-09-19 moved the Saturday confession to 03:30-03:50 - a "
        "20-minute window in the small hours, and an AM flip on the stored "
        "15:30. This bulletin prints NO confession times at all (its only "
        "schedule block is 'MASSES FOR THE WEEK'), so there is nothing on the "
        "page that could have moved it. Restores the pre-run values verbatim",
        confession_times=[
            ConfessionTime(day=DayOfWeek.SATURDAY, start_time=1530, end_time=1550),
            ConfessionTime(day=DayOfWeek.SUNDAY, start_time=1000, end_time=1030),
        ],
    ),
    "1734": ManualFix(
        reason="the 2026-09-19 run published this row's Thursday 11:00 Mass "
        "with cancelled=True, off a HOLIDAY POLICY line rather than anything "
        "about this week. Its masthead reads 'Monday - Thursday: 11:00 a.m. / "
        "Holy Days: 11:00 a.m. & 7:00 p.m. / No weekday Mass/ Memorial Day, "
        "4th of July, or Labor Day' - a standing rule about three civil "
        "holidays. Guard 5 in utils/cancellations.py now refuses it, so the "
        "flag will clear itself on the next run; this entry only stops the "
        "app showing a cancelled Thursday Mass in the meantime. Drop + add "
        "because there is no verb for clearing `cancelled` alone. "
        "RETIRE IT after the 2026-09-26 run",
        drop_masses={("Thursday", 1100)},
        add_masses=[MassTime(day=DayOfWeek.THURSDAY, time=1100)],
    ),
    # --- 2026-09-19: ORDINAL SLOTS deleted because this was not their week ----
    #
    # A monthly slot is legitimately absent from three bulletins in four, and
    # the extractor drops it on those three. Nothing holds it: a one-slot
    # removal is far under PARTIAL_RETRACTION_RATIO, so it is written. Three
    # rows lost one in this run alone, and `0882`'s First Friday Mass has been
    # flapping in and out for four consecutive snapshots.
    #
    # THESE ARE A TREADMILL, not a repair - restoring them does nothing about
    # the mechanism, and the same three will go again the next time their week
    # is not the covered one. The structural fix is the per-slot ledger in
    # docs/design/schedule-stability.md, which already has to hold a slot that
    # is absent for one week; an ordinal slot is the same thing on a longer
    # period. Until then, do not retire these.
    "23926": ManualFix(
        reason="2026-09-19 dropped the last-Wednesday confession. Bulletin: "
        "'Reconciliation / Saturday, 3:00-4:00 p.m. ... Last Wednesday of each "
        "month at 6:00 p.m.' The 18:00 carries weeks_of_month [-1] in the "
        "export",
        confession_times=[
            ConfessionTime(
                day=DayOfWeek.SATURDAY,
                start_time=1500,
                end_time=1600,
                notes="Confessions also available by appointment with a priest.",
            ),
            ConfessionTime(
                day=DayOfWeek.WEDNESDAY,
                start_time=1800,
                notes="Last Wednesday of each month; confessions also available "
                "by appointment with a priest.",
            ),
        ],
    ),
    # --- Retired 2026-09-12: the 2026-09-05 Labor Day displacement block ----
    #
    # Fifteen rows were repaired by hand after the Labor Day run published a
    # displaced weekday as the recurring one (twelve restorations and remaps,
    # plus 0244's invented Monday and 1088's 2027-dated Mass). Every one of
    # them was re-extracted correctly by the 2026-09-12 run, off a normal week
    # with no holiday in it, so the table is no longer describing anything the
    # pipeline gets wrong.
    #
    # They are removed rather than left inert because `add_masses` is the one
    # verb that can do harm by doing nothing new: it keeps restoring a Mass, so
    # it would outlive a genuine cancellation and quietly republish a Mass the
    # parish had stopped celebrating. The `mass_time_fixes` entries key on the
    # wrong stored time and would silently remap a future Monday 09:00 that was
    # real. Verified before removal (stored recurring Monday, 2026-09-12 run):
    # 1794 06:28, 0039 08:30, 0599 07:00, 1101 07:00, 1286 07:00, 1397 12:00,
    # 1733 08:30, 5493 11:30, st-mary-painesville 12:15, 0691 08:00, 1170
    # 08:30, st-matthias 08:30; 0244 and 1088 hold no Monday at all.
    #
    # 0036 is the exception and is retired for the opposite reason: the run
    # removed its Monday 08:00 and was RIGHT to. Its masthead reads "M, W |
    # 8:00 a.m. (at St. Joseph)", so that Mass is the cluster partner's and
    # belongs on 0138's row. The hand repair had restored a Mass to the wrong
    # parish.
    #
    # st-mary-painesville-oh's entry also recorded that its stored Monday 06:45
    # "was wrong and is correctly gone". That reading was mistaken - the
    # 2026-09-13 masthead splits the week "Monday, Tuesday & Thursday 6:45AM /
    # Monday, Wednesday & Friday 12:15PM", so Monday genuinely has both, and
    # the 2026-09-12 run restored the 06:45 on its own.
    #
    # The durable fix for the class is still docs/design/schedule-stability.md,
    # and it is still needed: the 2026-09-12 run produced four more instances
    # of the same shape with no holiday anywhere near them, off ordinary parish
    # life - see the "one-week cancellation" note at the end of this table.

    # --- 2026-09-12 run: wrong writes, each checked against its own bulletin -
    #
    # Method was the same as the Labor Day sweep: diff the recurring Masses and
    # confessions in notion_snapshot.json at e0db5cc (2026-09-10) against the
    # 2026-09-12 run, then read each changed slot off that row's own 2026-09-13
    # bulletin. 44 fields changed; these are the ones that changed wrongly.

    "0414": ManualFix(
        reason="the Sunday 08:30 Mass was republished as 08:00, which is the "
        "WEEKDAY time one line below it. The 2026-09-13 bulletin's LITURGY "
        "SCHEDULE block for Saint Ann Church reads Saturday 4:00PM / Sunday "
        "8:30AM / Sunday 11:30AM / M,T,W,F 8:00AM - the times print in a "
        "column below the day labels, so a column slip picks up the wrong one. "
        "Its Sunday 11:30 and the four weekday 08:00 Masses are right and are "
        "left alone. Flagged 'not reproduced on a second extraction' at the "
        "time, which was correct (2026-09-12)",
        mass_time_fixes={("Sunday", 800): 830},
    ),
    "olhc-lodi": ManualFix(
        reason="Lodi's Sunday Mass was republished on SATURDAY - a day slip, "
        "not a time one, so mass_time_fixes cannot express it. The 2026-09-13 "
        "masthead lists the Sunday Masses by site: '8:30 a.m. - Lodi Worship "
        "Site / 9:00 a.m. - Litchfield / 10:30 a.m. - Seville / 11:00 a.m. - "
        "Nova'. The stored Saturday vigils in this bulletin are 4:00pm Seville "
        "and 6:00pm Litchfield - Lodi has none, so the row was left publishing "
        "a Saturday morning Mass and no Sunday Mass at all. Its Monday 18:00 "
        "is right ('6:00 p.m. - Lodi Worship Site'). Flagged 'not reproduced' "
        "(2026-09-12)",
        drop_masses={("Saturday", 830)},
        add_masses=[MassTime(day=DayOfWeek.SUNDAY, time=830)],
    ),
    "1494": ManualFix(
        reason="the second Saturday confession slot was republished at 16:00, "
        "which is when the Mass it is supposed to follow STARTS. The bulletin "
        "prints 'Confession | Saturday | 3:00pm - 3:45pm & after 4:00pm Mass', "
        "and the slot kept its own note saying 'After the 4:00pm Mass' while "
        "moving on top of it. The vigil is 4:00pm and a Sunday-obligation Mass "
        "runs about an hour, so the stated start is 17:00, open-ended - the "
        "bulletin gives no end. The 15:00-15:45 slot is transcribed exactly "
        "and is unchanged. Flagged 'removed Saturday 1700 (still printed in "
        "bulletin)' AND 'not reproduced', both correct (2026-09-12)",
        confession_times=[
            ConfessionTime(day=DayOfWeek.SATURDAY, start_time=1500, end_time=1545),
            ConfessionTime(
                day=DayOfWeek.SATURDAY,
                start_time=1700,
                end_time=None,
                notes="After the 4:00pm Mass; and by appointment",
            ),
        ],
    ),
    "1060": ManualFix(
        reason="the run replaced a real Saturday confession with a one-off "
        "event. The masthead's RECONCILIATION line reads 'Saturday | 3:00 p.m. "
        "or by appointment' and the week's calendar repeats '3:00pm "
        "Confessions | Church'; what replaced it, Wednesday 18:00-19:00 noted "
        "'During Family Friendly Holy Hour', is a DATED calendar item - "
        "'Family Friendly Holy Hour ... Wednesday, September 16, 2026 | 6-7 "
        "p.m. in the Church' - and a holy hour is not a standing confession "
        "slot. No end time is printed for the Saturday slot, so it stays open- "
        "ended. Flagged 'not reproduced' (2026-09-12)",
        confession_times=[
            ConfessionTime(
                day=DayOfWeek.SATURDAY,
                start_time=1500,
                end_time=None,
                notes="Or by appointment",
            ),
        ],
    ),
    "20822": ManualFix(
        reason="the Saturday 5:00 pm English vigil was dropped, and this is "
        "the row where the masthead is NOT the best evidence. The 2026-09-13 "
        "WEEKEND MASS SCHEDULE block prints only the Spanish vigil ('SABADO "
        "(VIGILIA) - 7PM') alongside Sunday 9:30 English and 12PM Spanish, and "
        "the week's intentions listing happens to show no Saturday 5:00 either "
        "- but the offering table on the same page lists four Masses with "
        "attendance for September 05-06: 'Saturday 5:00 pm | 42', 'Sabado 7:00 "
        "pm | 40', 'Sunday 9:30 am | 125', 'Domingo 12:00 pm | 228'. A Mass "
        "with 42 people counted at it is a Mass. Its confession change the "
        "same run (Saturday 15:30 -> 18:00) is correct and untouched: the "
        "bulletin says 'Saturday (Sabado) - 6:00-6:45pm'",
        add_masses=[MassTime(day=DayOfWeek.SATURDAY, time=1700)],
    ),
    # --- RETIRED 2026-09-19: "0882" Friday 18:30 -----------------------------
    #
    # This entry was WRONG and had been deleting a real Mass. It was added on
    # 2026-09-12 reasoning that "there is no 6:30 of any kind in the document"
    # - true of that week's document, and true of three documents in four,
    # because the Mass is a FIRST FRIDAY Mass. Its two stored notes say so as a
    # matched pair, "First Fridays only" on the 18:30 and "Except on First
    # Fridays" on the 08:00, and the export derives weeks_of_month [1] and
    # excluded_weeks [1] from them.
    #
    # Settled by fetching the two First Friday editions (PO URLs are
    # date-constructed, so this costs one curl each). Both print it, and print
    # 08:00 on every other weekday:
    #
    #     20260802B:  Thursday, 8/6  8:00 AM   Friday,8/7  6:30 PM
    #     20260830B:  Thursday, 9/3  8:00 AM   Friday,9/4  6:30 PM
    #
    # The Mass had been flapping in and out of the row for four consecutive
    # snapshots, which is what this entry was doing to it.
    #
    # THE GENERAL LESSON, and it applies to the fabricated-time check itself:
    # a monthly slot is `recurring` but is printed about once a month, so on
    # the other three weeks `verify_times` sees a time the page never prints
    # and reports a fabrication. Before dropping anything that carries an
    # ordinal note, check an edition from the week it actually falls in.
    "1532": ManualFix(
        reason="the bulletin's WEEKDAY MASSES block prints 'Monday | 9:00am / "
        "Tuesday | 9:00am / Wednesday | 9:00am / Friday | 9:00pm' - the Friday "
        "PM is a typo, identified as one back in v2.5.19 and still uncorrected "
        "in the 2026-09-13 edition. The body settles it: 'On Friday, September "
        "18th at 9AM we will have an Anointing Mass'. The 2026-09-12 run "
        "transcribed the masthead literally and ADDED 21:00 beside the "
        "existing Friday 09:00, so the row published both. Dropping the 21:00 "
        "is the whole fix; rule 1 of prompt v3 (transcribe, never normalise) "
        "means the extractor will keep re-emitting it, so this is a treadmill "
        "until the parish corrects its own masthead. "
        "ALSO 2026-09-19: that run dropped Thursday AND Friday 09:00, and only "
        "Friday was wrong, so Friday is restored here. THURSDAY WAS RIGHT TO "
        "GO - the masthead lists Thursday 9:00am under MORNING PRAYER, not "
        "WEEKDAY MASSES, and the week's listing reads 'Sept. 24, 2026 | 9:00AM "
        "/ Weekday / Morning Prayer'. Friday is a real Mass with an intention "
        "('Sept. 25, 2026 | 9:00AM / Weekday / Elizabeth Goodrich')",
        drop_masses={("Friday", 2100)},
        add_masses=[MassTime(day=DayOfWeek.FRIDAY, time=900)],
    ),
    "0242": ManualFix(
        reason="a one-week substitution published as recurring. The masthead "
        "reads 'Daily Mass: 6:45 AM MTWThF / 8:30am Saturday / 12:15 PM MWF', "
        "so Wednesday is 06:45 and 12:15. The week's listing shows 'Wed Sept "
        "16 ... 6:45AM ... 8:30AM' - an 08:30 standing in for the 12:15 on one "
        "day - and the v2.5.11 rule (listing beats masthead) promoted it to "
        "weekly. The row kept its Wednesday 12:15, so this is an extra Mass "
        "rather than a swap, and its genuine Saturday 08:30 is untouched. "
        "Flagged 'not printed in bulletin - suspicious' AND 'not reproduced' "
        "(2026-09-12). "
        "ALSO 2026-09-19: the run dropped the First Saturday CONFESSION. "
        "Bulletin: 'SACRAMENT OF RECONCILIATION / Saturdays 4:00PM and 8:30AM "
        "first Saturdays of each month.' The 08:30 carries weeks_of_month [1] "
        "in the export, so it published correctly until this run. See the "
        "ORDINAL SLOTS note above - this half is a treadmill, the Wednesday "
        "drop is not",
        drop_masses={("Wednesday", 830)},
        confession_times=[
            ConfessionTime(day=DayOfWeek.SATURDAY, start_time=1600),
            ConfessionTime(
                day=DayOfWeek.SATURDAY,
                start_time=830,
                notes="First Saturday of each month",
            ),
        ],
    ),
    "visitation-of-mary-parish-akron-oh": ManualFix(
        reason="two entries belonging to the OTHER parish in this shared "
        "bulletin. It prints two headed blocks: under 'Visitation of Mary', "
        "Saturday 4:00PM vigil, Sunday 10:30AM and weekdays 8:00AM; under "
        "'St. John the Baptist', Sunday 10:00AM and Thursday 5:30PM. The "
        "added Sunday 12:00 is in neither - the only noon in the document is "
        "'St. Anne's Guild meets ... at 12 NOON'. The Thursday 17:00-17:30 "
        "confession sits directly before SJB's Thursday 17:30 Mass and "
        "visitation-of-mary-parish-akron-oh-sjb already holds it identically; "
        "this row's own Thursday Mass is 08:00. Its Saturday 15:00-15:30 "
        "confession is unchanged and kept (2026-09-12)",
        drop_masses={("Sunday", 1200)},
        confession_times=[
            ConfessionTime(day=DayOfWeek.SATURDAY, start_time=1500, end_time=1530),
        ],
    ),
    "visitation-of-mary-parish-akron-oh-sjb": ManualFix(
        reason="St. John the Baptist's Sunday Mass is 10:00AM, which this row "
        "already holds; the added Sunday 14:00 is in no block of the "
        "2026-09-13 bulletin. Its own note gives it away - '2026 schedule (see "
        "specific dates)' is extraction commentary of the kind v2.5.11 says "
        "must never reach `notes`, and it is the tell that the model was "
        "reading a dated listing rather than a standing schedule. Its Thursday "
        "17:30 Mass and the 17:00-17:30 confession before it are correct and "
        "are left alone (2026-09-12)",
        drop_masses={("Sunday", 1400)},
    ),
    "1905": ManualFix(
        reason="the added Friday 12:45 confession is St. Mary of the "
        "Oaks's, not St. Patrick's. The bulletin tags every line with a site "
        "code: 'Friday 12:30pm (SMO)' in the masthead and '12:45pm(SMO-C) | "
        "Confessions after Noon Mass' in the week's listing are both SMO, "
        "which is the 1905-smo row and already holds them. St. Patrick's own "
        "confession is the listing's '4:50-5:20 (STP-R)', stored correctly as "
        "Monday 16:50-17:20. Flagged 'not printed in bulletin - suspicious' "
        "AND 'not reproduced' (2026-09-12)",
        confession_times=[
            ConfessionTime(day=DayOfWeek.MONDAY, start_time=1650, end_time=1720),
        ],
    ),
    # Retired 2026-09-12, same day it was written, by `site_label`.
    #
    # It existed because the Our Lady of Victory bleed was only visible in a
    # note the model rewrote every run: the SITE_EXCLUSIONS note rule dropped
    # four of OLV's Masses off this row on the scheduled run, and a
    # re-extraction of the SAME bulletin an hour later emitted them with no
    # note at all, so the rule saw nothing and the bleed came straight back
    # within the afternoon. Hence a hand-stated drop of every 08:30 on the row.
    #
    # `site_label` removes the reason for it. The bulletin tags those lines
    # "Mass at Our Lady of Victory" and the model now transcribes the tag into
    # a field of its own instead of composing prose about it, so the exclusion
    # matches something stable. Two consecutive live extractions dropped all
    # four, Sunday 08:30 included - the entry that no rule could previously
    # reach - and the row now holds exactly its own Wed/Fri 08:00, Sat 16:00
    # and Sun 10:30.
    #
    # If an unlabelled copy ever comes back, this is the treadmill to restore:
    #     drop_masses={("Sunday", 830), ("Tuesday", 830), ("Thursday", 830)}
    # St. Matthew has no 08:30 of its own, which is what made that safe.


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
        # ("Saturday", 430) added 2026-09-19: a second Saturday Mass that is
        # the 16:30 vigil duplicated off a typo in the bulletin's OWN listing -
        # "SATURDAY, 19 SEPTEMBER 2026 ... 4:30 am People of the Cathedral
        # Parish", where the masthead says "4:30 pm (Sunday Vigil)" and the
        # following Saturday prints a bare "4:30". Prompt v3 rule 1 says
        # transcribe rather than normalise, so the extractor is right to copy
        # it and it has to be corrected here - the same shape as 1532's
        # "Friday | 9:00pm". Dropped rather than remapped: the correct 16:30 is
        # already stored, so a remap would merge a second entry onto it.
        drop_masses={("Saturday", 1800), ("Saturday", 1600), ("Saturday", 430)},
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

    # --- 2026-09-12: the one-week cancellation class, DELIBERATELY not here --
    #
    # Four rows lost a standing weekday Mass to a cancellation that lasts one
    # week (or two), by exactly the Labor Day mechanism with no holiday in it:
    # the v2.5.11 rule says the day-by-day intentions listing beats the
    # standing schedule box, and on these weeks the listing prints the standing
    # time with "NO MASS" written beside it.
    #
    #   shc / shc-pat  "Father Trask will be away the next two weeks so there
    #                  will not be any weekday Masses, Adoration or Confession
    #                  during that time at either parish." The listing prints
    #                  Mon 8:45 (St. Patrick), Wed 6:30pm (St. Patrick), Thu
    #                  8:45 and Fri 8:45 (Sacred Heart), each "NO MASS". All
    #                  four retracted; both rows now publish weekends only.
    #   0134           masthead "Monday, Tuesday, Friday: 7:30 AM"; listing
    #                  "TUESDAY, 9/15 ... 7:30 AM No Morning Mass".
    #   1831           masthead "Monday - Friday 8:30 a.m."; listing
    #                  "Thursday, September 17 ... No Mass".
    #
    # They are NOT repaired here, and the reason is the same one that retired
    # the Labor Day block above: `add_masses` keeps restoring a Mass forever,
    # so using it against a cancellation that is real *right now* would have
    # the app advertising Masses nobody is celebrating - for two weeks at
    # Oberlin. Under-publishing for a week is the cheaper error, and all four
    # self-heal as soon as the listing prints the Mass again, which is the
    # "dropped" shape from the Labor Day triage.
    #
    # What this class actually needs is memory, not a table: a per-slot ledger
    # with hysteresis (docs/design/schedule-stability.md), where a slot missing
    # once is held rather than dropped. Note the cheap partial that would catch
    # three of these four without one - the bulletins print the time AND the
    # words "NO MASS" on the same line, so a listing entry that names a time
    # next to a cancellation is a one-week suspension of a standing slot, not
    # evidence that the slot is gone. The holiday calendar cannot see any of
    # this; none of these weeks contains a holiday.

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


# Notion's 2000-character cap is per BLOCK, not per property, and this script
# wrote a single block - so any repair to a row whose schedule JSON exceeds it
# failed with "rich_text[0].text.content.length should be <= 2000" and the row
# was left unrepaired while the summary still counted it. Found 2026-09-19 on
# 1259, whose Mass Times are 2010 characters; its confession repair had gone
# through on the same pass because that field is shorter, which is what made
# it look like a success. This is the v2.5.1 rule, which database/notion.py
# has followed since but this file never did. Readers join every block, so the
# value round-trips exactly.
_NOTION_BLOCK_CHARS = 2000


def _text(value: str) -> dict:
    if not value:
        return {"rich_text": []}
    return {
        "rich_text": [
            {"text": {"content": value[i : i + _NOTION_BLOCK_CHARS]}}
            for i in range(0, len(value), _NOTION_BLOCK_CHARS)
        ]
    }


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

    # Restorations run after the remap (so they are not themselves remapped)
    # and before the note fixes (so a restored Mass can be relabelled).
    if manual and manual.add_masses:
        for mass in manual.add_masses:
            notes.append(
                f"mass: added {mass.day.value} {mass.time:04d} "
                f"({manual.reason})"
            )
            site.mass_times.append(mass.model_copy(deep=True))

    # Note corrections run after the time remap, so a fix can be keyed to the
    # corrected time rather than the stored one.
    if manual and manual.mass_note_fixes:
        for mass in site.mass_times:
            key = (mass.day.value, mass.time)
            if key in manual.mass_note_fixes:
                new_note = manual.mass_note_fixes[key]
                if mass.notes != new_note:
                    notes.append(
                        f"mass: {mass.day.value} {mass.time:04d} note "
                        f"{mass.notes!r} -> {new_note!r} ({manual.reason})"
                    )
                    mass.notes = new_note

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
