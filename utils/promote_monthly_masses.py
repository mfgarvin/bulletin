"""Move a monthly Mass out of `events` and into the schedule where it belongs.

`extractor.py`'s events section carries a rule written before there was any way
to express an ordinal:

    Recurring monthly Masses (e.g., monthly Anointing of the Sick Mass on first
    Monday) belong here as events with `frequency: first_friday` or
    `other_recurring`, NOT in mass_times.

v2.5.17 then built `weeks_of_month`, derived at export time from an entry's
`notes`, and nobody went back. So the prompt still routes a monthly Mass into
`events` - and `notion_to_app` does not export `events` at all, only the
mapboard reads it. The Mass goes into a drawer nobody opens.

Found live: `olp-cle`'s **Igbo Language Mass, last Sunday of the month at 1:00
PM** sat in `events` while the row's `weeks_of_month Sunday 13:00 [-1]`
disappeared from `export.json` in the 2026-09-19 rebuild. A parishioner looking
for that Mass in the app finds nothing.

**This is deliberately a pass over the extraction rather than a prompt change.**
The data is already present and correctly described; it is only in the wrong
field. Rewriting the prompt rule to say "monthly Masses DO go in mass_times"
would also invite the 24 non-Mass events sitting beside them - "Coffee & Donuts
after Mass", "Children's Liturgy of the Word", "Healing Monday" - and v2.5.10
is unambiguous about what happens when a prompt rule is loosened near a
neighbour. Nothing here changes what the model does.

**It only ever ADDS a slot, and that is the whole safety argument.** The
tempting other half - annotating an existing Mass that an event says is monthly
- is the dangerous one, and two live rows prove it. `ss-c`'s masthead reads

    1st Friday Mass & Benediction: 6:30pm
    1st Saturday Mass & Lecture:   9:30am

Its weekday Masses are Monday-Friday 6:30pm, so the first line is the *ordinary
Friday Mass with Benediction added*, not a monthly Mass; marking that slot
`weeks_of_month: [1]` would hide a real Mass three Fridays in four. `2492`'s
"Mass and Rosary for Life" is the same shape on its weekly Saturday 08:00, and
`ss-c`'s German Mass is its weekly Sunday 11:00 sung in German on first
Sundays. This is the v2.5.17 `0116` refusal ("Weekday Mass; First Saturday")
arriving from the events side, and the answer is the same: refuse. So a slot
that already exists is left exactly alone, and the second line above - a
genuine extra Mass on a morning the parish has no other - is the only shape
that promotes.

Five conditions, all required:

1. the event recurs monthly (`first_friday`, `monthly`, `other_recurring`);
2. its NAME is a Mass - parentheticals stripped, "Mass" present, and no
   disqualifying head noun. Name only, never the description: "St. James
   Guided Tours (after 10:30am Mass on last Sundays)" is a tour;
3. it states a weekday AND a time, or there is no slot to make;
4. `derive_ordinal` parses an ordinal out of its own words. **This is what
   stops the cure being worse than the disease** - a promoted Mass with no
   parseable ordinal would publish every week, which is exactly the harm this
   exists to undo. Refuse rather than guess;
5. `(day, time)` is not already in `mass_times`.

Measured against all 2,575 stored events: **2 promotions, 0 false positives**
(`olp-cle` Sunday 13:00 Igbo, `ss-c` Saturday 09:30 First Saturday), with 45
Mass-mentioning monthly events correctly refused and every reason recorded.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from schemas import BulletinExtraction, MassTime
from utils.monthly_recurrence import derive_ordinal

logger = logging.getLogger(__name__)

# Frequencies that mean "not every week". `weekly`, `biweekly` and `one_time`
# are none of this pass's business: a weekly Mass is already in the schedule,
# and a one-off belongs to `mass_date`.
_MONTHLY_FREQUENCIES = frozenset({"first_friday", "monthly", "other_recurring"})

# A parenthetical qualifies a name, it does not decide what the thing IS -
# "German Weekend Mass (1st Sunday of every month)" is a Mass, and "Anointing
# of the Sick (at Mass)" is not. Stripped before the test either way.
_PARENTHETICAL_RE = re.compile(r"\([^)]*\)")

_MASS_RE = re.compile(r"\bmass(?:es)?\b", re.IGNORECASE)

# The head noun that settles it the other way. Every one of these was taken
# from a live event whose name contains "Mass": a social held after one, a
# devotion celebrated within one, an administrative task about one, or - the
# case worth naming - a Mass said somewhere that is not this parish.
# `1170`'s "Nursing Home Mass (Madison Health Care)" is real, monthly and
# correctly described, and publishing it on the parish's row would send someone
# to the wrong address, which is the `SITE_EXCLUSIONS` lesson in miniature.
_NOT_A_MASS_RE = re.compile(
    r"""\b(?:
          holy \s+ hour | coffee | donut | doughnut | breakfast | brunch
        | lunch | social | potluck | tour | registration | school
        | class(?:es)? | count | raffle | bake | festival | fundraiser
        | liturgy \s+ of \s+ the \s+ word | clow | novena
        | anointing \s+ of \s+ the \s+ sick | healing \s+ prayer
        | nursing \s+ home | fellowship | gathering | meeting
        | bereavement | support \s+ group | intentions? \s+ book
        )\b""",
    re.IGNORECASE | re.VERBOSE,
)


def _is_mass(name: Optional[str]) -> bool:
    """Is this event a Mass, judged by its name alone?"""
    if not name:
        return False
    bare = _PARENTHETICAL_RE.sub(" ", name)
    return bool(_MASS_RE.search(bare)) and not _NOT_A_MASS_RE.search(bare)


def _ordinal_note(day: str, event) -> Optional[str]:
    """The event's own words that state its ordinal, or None.

    Prefers the description, which is usually where the rule is spelled out
    ("Last Sunday of the month at 1:00 PM."), and falls back to the name
    ("First Saturday Mass & Lecture"). Whichever is returned becomes the
    published `notes`, so it has to read as a sentence about the Mass - and it
    is the parish's own text either way, never composed here.
    """
    for candidate in (getattr(event, "description", None), getattr(event, "name", None)):
        if candidate and derive_ordinal(day, candidate):
            return candidate.strip()
    return None


def promote_monthly_masses(
    extraction: BulletinExtraction, parish_id: str = ""
) -> list[str]:
    """Add monthly Masses parked in `events` to the site schedules.

    Returns one log line per promotion. Never removes the event: the pass is
    additive, the event listing is a reasonable place to read about it too, and
    leaving it means a later run that no longer needs this can simply stop
    calling the function.
    """
    events = getattr(extraction, "events", None) or []
    if not events or not extraction.sites:
        return []

    notes: list[str] = []

    for event in events:
        frequency = getattr(event, "frequency", None)
        frequency = getattr(frequency, "value", frequency)
        if frequency not in _MONTHLY_FREQUENCIES:
            continue

        name = getattr(event, "name", None)
        if not _is_mass(name):
            continue

        day = getattr(event, "day_of_week", None)
        day = getattr(day, "value", day)
        time = getattr(event, "time", None)
        if not day or time is None:
            continue

        note = _ordinal_note(day, event)
        if note is None:
            # Condition 4. A Mass promoted without a parseable ordinal would
            # publish weekly, which is worse than leaving it in the drawer.
            logger.info(
                "[%s] monthly Mass '%s' not promoted - no parseable ordinal in "
                "its own words",
                parish_id,
                name,
            )
            continue

        # Only the site that has no such slot gets it. A bulletin group's other
        # sites keep their own schedules; an event is parish-level, so without
        # this a two-site row would gain the Mass twice.
        for site in extraction.sites:
            if any(
                m.day.value == day and m.time == time and m.mass_date is None
                for m in site.mass_times
            ):
                continue

            try:
                site.mass_times.append(
                    MassTime(day=day, time=time, notes=note)
                )
            except Exception as e:  # a day/time the schema rejects
                logger.warning(
                    "[%s] could not promote monthly Mass '%s': %s",
                    parish_id,
                    name,
                    e,
                )
                continue

            notes.append(
                f"promoted monthly Mass from events: {day} {time:04d} "
                f"('{name}') - {derive_ordinal(day, note)}"
            )

    return notes
