"""Derive `weeks_of_month` / `excluded_weeks` from an entry's own notes.

Implements the scraper side of the frozen spec in `EXPORT_SHAPE_CHANGES.md`
(the `weeks_of_month` / `excluded_weeks` section — normative, do not change
it). ~60 slots across ~40 parishes recur on an ordinal weekday of the month
("First Friday", "2nd and 4th Saturday", "Last Sunday") but are stored as
weekly, so everything that *computes* — "on now", "what's next", the mapboard —
treats them as every week.

The ordinal is derived **deterministically from the note text at export time**,
never asked of the LLM: a parser bug is fixed once in code, while an LLM field
re-rolls its mistakes every week. Export-time also means the ~13 adoration
slots benefit even though `UPDATE_ADORATION = False` means their stored rows
are never rewritten — the notes are already in Notion, and a parser
improvement reaches every stored row on the next export without touching them.

**Refuse rather than guess.** No entry may ever carry a *wrong* ordinal; a
refused note stays weekly-with-a-note, which is today's behaviour and renders
truthfully to a human. The refusals, per the spec:

- **The weekday in the phrase must be the entry's own day.** This covers
  "the Thursday before the First Friday" (not an ordinal of the month at all —
  when the first Friday falls on the 1st or 2nd, that Thursday is in the
  *previous* month) and cross-day subjects.
- **More than one ordinal-weekday phrase refuses.** Found live at
  `our-lady-of-victory`: "held at Saint Matthew on the 1st and 3rd Saturdays;
  at Our Lady of Victory on the 2nd and 4th Saturdays" — two subjects, and
  merging them says "every week", which is the bug being fixed. A single
  phrase can still carry a list ("2nd and 4th Saturday" is one phrase).
- **A clause labelling the slot weekly refuses.** Found live at `0116`:
  "Weekday Mass; First Saturday (also listed in schedule)" is the v2.5.11
  merged-label case — a *weekly* Mass whose note also carries the First
  Saturday devotion's label. Emitting `[1]` would hide a real weekly Mass
  three weeks a month, the exact inversion of this feature. Applies to
  inclusions only: "Weekday Mass (except on First Fridays)" is coherent.
- **"before"/"after" adjacent to the phrase** refuses even on a matching day.
- An ordinal with no weekday attached ("first week of the month", "4th of
  July") derives nothing.

Output domain per the spec: 1-5 and -1 (last), sorted ascending (-1 first),
de-duplicated, and the two keys are mutually exclusive. Only recurring entries
(`mass_date` null) may carry them — the caller enforces that.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

_ORDINALS = {
    "first": 1, "1st": 1,
    "second": 2, "2nd": 2,
    "third": 3, "3rd": 3,
    "fourth": 4, "4th": 4,
    "fifth": 5, "5th": 5,
    "last": -1,
}

_WEEKDAYS = {
    "monday", "tuesday", "wednesday", "thursday", "friday",
    "saturday", "sunday",
}

_ORD = r"(?:first|second|third|fourth|fifth|last|1st|2nd|3rd|4th|5th)"
_SEP = r"(?:\s*(?:,|&|and|or)\s*)"

# "first friday", "2nd and 4th saturdays", "first-friday", "last sunday"
_PHRASE_RE = re.compile(
    rf"(?P<ords>{_ORD}(?:{_SEP}{_ORD})*)[\s-]+"
    rf"(?P<weekday>monday|tuesday|wednesday|thursday|friday|saturday|sunday)s?\b",
    re.IGNORECASE,
)

_ORD_TOKEN_RE = re.compile(_ORD, re.IGNORECASE)

# Immediately before the phrase, these words mean the phrase anchors some
# *other* day ("the Thursday before First Friday") - never this entry.
_ANCHOR_RE = re.compile(r"\b(?:before|after|preceding|following|prior\s+to)\s*(?:the\s+)?$",
                        re.IGNORECASE)

# Before the phrase, these mark an exclusion ("except on First Fridays",
# "no Mass on the first Friday").
_EXCLUDE_RE = re.compile(r"\b(?:except|excluding|excepted|but\s+not|no\s+mass|omitted)\b"
                         r"[^.;]{0,30}$", re.IGNORECASE)

# A clause that calls the slot itself a weekly Mass ("Weekday Mass; First
# Saturday"). Anchored to the clause start so "after 6:00 PM daily Mass" -
# a time anchor inside a genuine First Monday note - does not trip it.
_WEEKLY_CLAUSE_RE = re.compile(r"^(?:weekday|daily)\s+mass(?:es)?\b", re.IGNORECASE)


def _has_weekly_clause(notes: str) -> bool:
    return any(
        _WEEKLY_CLAUSE_RE.match(clause.strip())
        for clause in re.split(r"[;.]", notes)
    )


def derive_ordinal(day: str, notes: Optional[str]) -> Optional[dict]:
    """{"weeks_of_month": [...]} | {"excluded_weeks": [...]} | None (weekly).

    `day` is the entry's own weekday ("Friday"). None means "emit nothing":
    either the note states no ordinal, or it does and we refuse to guess.
    """
    if not notes:
        return None

    matches = list(_PHRASE_RE.finditer(notes))
    if not matches:
        return None

    if len(matches) > 1:
        logger.info("ordinal refused (multiple phrases - two subjects): %r", notes)
        return None

    entry_day = day.strip().lower()
    weeks: set[int] = set()
    polarity: Optional[str] = None  # "include" | "exclude"

    for m in matches:
        if m.group("weekday").lower() != entry_day:
            logger.info(
                "ordinal refused (weekday %r is not the entry's %s): %r",
                m.group("weekday"), day, notes,
            )
            return None

        prefix = notes[: m.start()]
        if _ANCHOR_RE.search(prefix):
            logger.info("ordinal refused (anchored to another day): %r", notes)
            return None

        kind = "exclude" if _EXCLUDE_RE.search(prefix) else "include"
        if polarity is None:
            polarity = kind
        elif polarity != kind:
            logger.info("ordinal refused (mixed include/except): %r", notes)
            return None

        for token in _ORD_TOKEN_RE.findall(m.group("ords")):
            weeks.add(_ORDINALS[token.lower()])

    if not weeks:
        return None

    if polarity == "include" and _has_weekly_clause(notes):
        # "Weekday Mass; First Saturday" - the v2.5.11 merged-label case, a
        # weekly Mass also carrying the devotion's label. Only an inclusion
        # conflicts with a weekly claim; "Weekday Mass (except on First
        # Fridays)" is coherent and stays an exclusion.
        logger.info("ordinal refused (note also labels the slot weekly): %r", notes)
        return None

    key = "weeks_of_month" if polarity == "include" else "excluded_weeks"
    return {key: sorted(weeks)}
