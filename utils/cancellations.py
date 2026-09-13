"""Keep a standing slot that the bulletin says is cancelled this week.

The failure this exists for is the largest single category of damage the
pipeline produces, and the holiday calendar can only see part of it.

v2.5.11 told the extractor that when the standing schedule box and the
day-by-day intentions listing disagree, the listing wins - which is right,
because the box states policy across the year and the listing states what is
actually celebrated this week. But on a week where a Mass is *suspended*, the
listing does not say "this Mass does not exist"; it prints the Mass's own time
with NO MASS written beside it:

    8:45 am .. at St. Patrick .................. NO MASS
    7:30 AM  No Morning Mass
    Thursday, September 17    No Mass
    11:30 am Mass: no Mass (Saint Ann Shrine)

The model reads that correctly, concludes there is no Mass, and omits the slot.
The pipeline then writes a schedule with a standing Mass permanently missing.
v2.5.26 handles the subset where a public holiday is the cause, by freezing the
displaced weekday. The 2026-09-12 run produced four more rows with no holiday
anywhere near them - two parishes whose priest was away for a fortnight, a
feast-day substitution, and a school Mass - so the calendar was never the
general answer.

**The bulletin states it directly, and for every cause.** So rather than infer
from a calendar, read the page: a stored slot that the new extraction dropped,
whose own time is still printed next to its own weekday AND next to a
cancellation phrase, is suspended - not retracted. It is restored with
`cancelled=True`, which is what the export and the app read.

Three properties worth stating, because each is a deliberate limit:

- **It only ever restores what was already stored.** Nothing here invents a
  slot, and a slot the parish has genuinely dropped disappears normally - the
  cancellation phrase has to be there, next to that slot's own time.
- **The flag re-derives every run and therefore expires by itself.** Next
  week's bulletin prints the Mass normally, the extraction emits it, and it is
  written with `cancelled` back at its default False. Nothing has to remember
  to clear it.
- **It refuses rather than guesses.** An unverifiable bulletin (an image-only
  scan) restores nothing, exactly as the v2.5.14 gate refuses to make absence
  claims about a document it cannot read.

**Four guards, and three of them were written by a diocese-wide sweep**
(2026-09-13, 150 rows with a usable text layer, run against that week's live
bulletins). The first cut passed a 13-row replay and still produced **7 false
positives out of 15 hits** at full scale. None of the three failures were
reachable at 13 rows, which is the argument for sweeping before trusting
anything that writes:

1. `no` matched inside a word. `5493` prints "8:30 am Mass: +Mark **Balza-no**
   10:30 am Mass" - the tail of a surname, a time and the word Mass, which read
   as "no 10:30 am mass" and cancelled three Sunday Masses at a parish that had
   cancelled nothing. Fixed with a word boundary.
2. A phrase naming a time was applied to slots at other times. `olp-cle`'s
   "there is no 5:30pm Mass" cancelled its Sunday 09:00 and 11:00. Now a time
   inside the phrase must match the slot (`_same_time`).
3. A standing exclusion read as a cancellation. `1714`'s masthead is "Daily
   Mass Monday, Tuesday, Wednesday, and Friday - 8:30 am [ no Mass on Thursday
   ]", and `1311`'s is "Tuesday & Friday - 9 am; no Mass on Monday, Wednesday &
   Thursday morning" - both describe a schedule that has never included those
   days, and both cancelled the Friday Mass printed beside them. Now a day
   named after the phrase must be the slot's own day.

Guard 3 requires the connector "on"/"for" and that is load-bearing: making it
optional swings the rule too far and rejects real cancellations, because in a
day-by-day listing the text after the phrase is simply the *next* entry.
`0240` prints "8:30 a.m. No Mass / Wednesday, September 16 ... 8:30 a.m. No
Mass", and an optional connector reads Wednesday as the day Monday's
cancellation applies to. A day qualifies a cancellation only when the sentence
says it does.

After all four: **8 hits across 150 rows, 0 false positives**, and every true
positive from the 13-row replay retained.

**Known limit.** A cancellation that names its day and then restates the time
afterwards - "no Mass on Thursday, September 17 at 8:30 am" - is refused,
because the intervening day label trips rule 2. Both shapes bulletins actually
use are handled ("no 8:30 Mass on Thursday" puts the time inside the phrase;
the listing form puts the day first), so this is a miss in the safe direction.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from schemas import SiteInfo
from utils.verify_changes import (
    Pairings,
    _CONTEXT_CHARS,
    _stored_confession_slots,
    _stored_mass_slots,
    _DAY_ABBREV,
)
from utils.verify_times import (
    MIN_TEXT_CHARS,
    _extract_text,
    _normalize,
    _renderings,
)

logger = logging.getLogger(__name__)

# The cancellation phrase sits on the same printed line as the time, but that
# line can be long - "8:45 am .. at St. Patrick .................. NO MASS" is
# about 50 characters end to end, and leader dots are common in these listings.
# Wider than _CONTEXT_CHARS for that reason; the weekday still has to be within
# the tighter window, so this does not loosen which slot we are talking about.
_CANCEL_CONTEXT_CHARS = 90

# How far back to look for the day header a time sits under. Much wider than
# the adjacency window, and that is not a loosening: the discrimination in rule
# 1 comes from taking the NEAREST preceding label, not from how far away it is,
# and every negative case below still fails on "nearest" alone. Searching too
# short a span is what actually breaks it - a real listing separates the header
# from the time with the day's readings, so `0134` prints "TUESDAY, 9/15: Our
# Lady of Sorrows (1 Cor 12:12-14 ... Jn 19:25-27) 7:30 AM No Morning Mass",
# about 110 characters, and a 90-character window finds no header at all and
# then falls through to the *following* day.
_DAY_SCOPE_CHARS = 400

# Deliberately narrow. "Cancelled" on its own is not accepted, because parish
# bulletins cancel picnics and meetings far more often than Masses, and a
# window this size will happily contain one. The phrase has to name the
# liturgy: "no (morning|evening|...) mass", "mass is cancelled", "no
# confessions". Spanish included because the bilingual bulletins print both
# ("Thursdays no Mass / no hay misa los jueves").
_CANCELLATION_RE = re.compile(
    r"""
      \b no \s* (?P<t1> \d{1,2} [:.]? \d{0,2} \s* (?:am|pm)? )? \s*
         (?: public | morning | evening | daily | weekday )? \s*
         (?: mass(?:es)? | confession(?:s)? | liturg\w+ )
    | (?: mass(?:es)? | confession(?:s)? ) \s+
         (?: is | are | will \s+ be | has \s+ been | have \s+ been ) \s+ cancell?ed
    | \b no \s+ hay \s+ misa
    | misa \s+ cancelada
    """,
    re.IGNORECASE | re.VERBOSE,
)

# A phrase may name the day it applies to right after itself - "no Mass on
# Thursday". That is a STANDING exclusion in a masthead, not a cancellation of
# whatever time happens to sit near it, and it is how 1714 ("Monday, Tuesday,
# Wednesday, and Friday - 8:30 am [ no Mass on Thursday ]") and 1311 ("Tuesday
# & Friday - 9 am; no Mass on Monday, Wednesday & Thursday morning") both
# attached Thursday's absence to a Friday Mass.
# "on"/"for" is REQUIRED, and that is the whole point of the pattern. Without
# it the tail simply runs into the NEXT entry of a day-by-day listing - 0240
# prints "8:30 a.m. no Mass / Wednesday, September 16 ... 8:30 a.m. no Mass",
# so an optional connector reads Wednesday as the day Monday's cancellation
# applies to and rejects a real one. A day only qualifies a cancellation when
# the sentence says it does.
_APPLIES_TO_RE = re.compile(r"\s*(?:on|for)\s+(?P<days>[^.;!?]{0,60})", re.IGNORECASE)


# Plurals are not optional polish: a masthead says "Thursdays no Mass" and a
# listing says "Thursday, September 17", and both have to register as the same
# day boundary. Over-matching here (a bare "sun" or "fri" in prose) costs a
# miss, never a wrong restore, because an unexpected day label only ever makes
# this stricter.
_ANY_DAY_RE = re.compile(
    r"\b(sun|mon|tues?|wed(?:nes)?|thur?s?|fri|satur?)(?:day)?s?\b\.?",
    re.IGNORECASE,
)


def _same_time(stated: str, time: int) -> bool:
    """Does a time written inside a cancellation phrase mean this slot?

    Compared on the printed digits rather than by parsing to 24-hour, because
    the phrase rarely says am/pm and a bulletin writes the same Mass as "5:30",
    "5:30pm" and "17:30" in different places. Hour-and-minute equality modulo
    12 is the honest test; being wrong here only costs a missed cancellation.
    """
    digits = re.sub(r"[^\d]", "", stated)
    if not digits:
        return True
    if len(digits) <= 2:
        hh, mm = int(digits), 0
    else:
        hh, mm = int(digits[:-2]), int(digits[-2:])
    return (hh % 12, mm) == ((time // 100) % 12, time % 100)


def _cancelled_near(time: int, day: str, text: str) -> Optional[str]:
    """Is this slot's own time printed beside a cancellation for its own day?

    Returns the matched phrase (for the log) or None.

    Distance alone is not enough here, and v2.5.29 already established why:
    "(still printed in bulletin)" was worth 4-true/3-false precisely because a
    window of characters around a time catches whatever else the column
    happens to print. Requiring the entry's own weekday inside that window
    fixed most of it but not all - the residual case named there is
    `immat-con-cle`'s "monday-thursday: 7:30am friday: 6:30pm", where the right
    weekday IS in the window and `friday:` intervenes.

    A wrong answer costs more here than it does for a label, because this one
    writes: restoring a slot the parish has actually dropped would republish it
    AND mark it cancelled. So this does the day *scoping* v2.5.29 deferred:

    1. A time belongs to the nearest weekday label BEFORE it - that is how a
       day-by-day listing is printed. That label must be this slot's own day.
       (If nothing precedes it, the first label after it must be, which covers
       a masthead line like "7:30 AM Monday, Tuesday, Friday".)
    2. The cancellation phrase must not be separated from the time by ANY
       weekday label, its own included. A day label starts a new entry, so a
       phrase on the far side of one describes a different day.

    Rule 2 is what rejects "Tuesday 7:30 AM No Morning Mass / Thursday 7:30 AM
    Fr. Smith" when asked about Thursday: the cancellation sits before the
    Thursday header, so it is Tuesday's. Rule 1 alone lets that through.
    """
    abbrev = _DAY_ABBREV.get(day)
    if not abbrev:
        return None
    own = re.compile(rf"^({day.lower()}|{abbrev})", re.IGNORECASE)

    for pattern in _renderings(time):
        for match in re.finditer(pattern, text):
            lo = max(0, match.start() - _CANCEL_CONTEXT_CHARS)
            hi = match.end() + _CANCEL_CONTEXT_CHARS

            # (1) whose entry is this time in?
            scope_lo = max(0, match.start() - _DAY_SCOPE_CHARS)
            before = list(_ANY_DAY_RE.finditer(text, scope_lo, match.start()))
            if before:
                if not own.match(before[-1].group(0)):
                    continue
            else:
                after = _ANY_DAY_RE.search(text, match.end(), hi)
                if not after or not own.match(after.group(0)):
                    continue

            # (2) a cancellation with no day boundary between it and the time
            for hit in _CANCELLATION_RE.finditer(text, lo, hi):
                if hit.end() <= match.start():
                    gap = (hit.end(), match.start())
                elif hit.start() >= match.end():
                    gap = (match.end(), hit.start())
                else:
                    gap = (match.start(), match.start())
                if _ANY_DAY_RE.search(text, *gap):
                    continue

                # (3) if the phrase names a TIME, it must be this slot's time.
                # "there is no 5:30pm Mass" says nothing about the 9:00 - and
                # olp-cle's Labor Day notice cancelled two Sunday Masses that
                # were never in question, because the digits went unchecked.
                stated = hit.group("t1")
                if stated and not _same_time(stated, time):
                    continue

                # (4) if the phrase names a DAY right after itself, it must be
                # this slot's day. A masthead's "no Mass on Thursday" is the
                # standing schedule, not a cancellation of the Friday Mass
                # printed beside it.
                tail = _APPLIES_TO_RE.match(text, hit.end())
                if tail:
                    named = list(_ANY_DAY_RE.finditer(tail.group("days")))
                    if named and not any(own.match(d.group(0)) for d in named):
                        continue

                return hit.group(0).strip()
    return None


def _restore(
    site: SiteInfo,
    stored_rows: list[dict],
    dropped: set[tuple[str, int]],
    text: str,
    kind: str,
    model,
    day_key: str,
    time_key: str,
) -> list[str]:
    """Put back each dropped slot the page shows as cancelled this week."""
    notes = []
    by_slot = {(r.get(day_key), r.get(time_key)): r for r in stored_rows}
    for day, time in sorted(dropped):
        row = by_slot.get((day, time))
        if row is None:
            continue
        phrase = _cancelled_near(time, day, text)
        if not phrase:
            continue
        restored = row.copy()
        restored["cancelled"] = True
        try:
            entry = model(**restored)
        except Exception as e:  # a stored row that no longer validates
            logger.warning("could not restore cancelled %s %s: %s", day, time, e)
            continue
        if kind == "Mass":
            site.mass_times.append(entry)
        else:
            site.confession_times.append(entry)
        notes.append(
            f"{kind} {day} {time:04d} kept as CANCELLED this week - the "
            f"bulletin prints this time beside {phrase!r}, so it is suspended "
            f"rather than dropped from the standing schedule"
        )
    return notes


def restore_cancelled_slots(
    pairings: Pairings,
    stored: dict[str, tuple[Optional[list[dict]], Optional[list[dict]]]],
    source_bytes: bytes,
    content_type: str = "pdf",
    text: Optional[str] = None,
) -> list[str]:
    """Restore, as cancelled, any stored slot the page says is off this week.

    Mutates the sites in `pairings` - the same objects the save step writes -
    and returns one note per restoration for `GPT Logs`.

    Adoration is not covered. `UPDATE_ADORATION = False` means a run never
    writes it, so there is nothing here to preserve; the field exists on
    AdorationTime so a `notion_fixes` entry can state one.
    """
    from schemas import ConfessionTime, MassTime  # local: avoids a cycle

    if text is None:
        text = _normalize(_extract_text(source_bytes, content_type))

    # Deliberately NOT gated on `_text_is_verifiable`, and the distinction is
    # the whole reason this is safe.
    #
    # That gate (v2.5.14) governs *absence* claims: "this time is not printed"
    # is meaningless on a bulletin whose schedule block is a scan, so a
    # document has to prove its text layer carries the schedule before the run
    # is allowed to say a time is missing. This check makes the opposite kind
    # of claim - it asserts that a cancellation phrase IS printed next to this
    # slot's own time - and a positive find is self-verifying. An image-only
    # bulletin simply matches nothing and restores nothing.
    #
    # Applying the gate here would also invert it. Its hit rate is measured
    # over the Masses the extraction KEPT, and a parish that just lost most of
    # its weekday Masses has few left: Sacred Heart Oberlin came out of the
    # 2026-09-12 run with two, under the 5-time minimum, so the one row most
    # damaged by this failure would have been the one row ineligible for the
    # repair. Only a real text layer is required.
    if len(text) < MIN_TEXT_CHARS:
        return []

    notes: list[str] = []
    for pid, site in pairings.items():
        if pid not in stored:
            continue
        stored_masses, stored_confessions = stored[pid]
        if stored_masses:
            dropped = _stored_mass_slots(stored_masses) - {
                (m.day.value, m.time) for m in site.mass_times if m.mass_date is None
            }
            if dropped:
                notes += _restore(
                    site, stored_masses, dropped, text, "Mass",
                    MassTime, "day", "time",
                )
        if stored_confessions:
            dropped = _stored_confession_slots(stored_confessions) - {
                (c.day.value, c.start_time) for c in site.confession_times
            }
            if dropped:
                notes += _restore(
                    site, stored_confessions, dropped, text, "Confession",
                    ConfessionTime, "day", "start_time",
                )
    return notes
