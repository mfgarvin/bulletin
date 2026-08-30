"""Post-extraction cleanup of LLM output, run before anything is saved.

Everything here guards against a failure mode we have actually seen in
`export.json`, not a hypothetical one. Unambiguous fixes are applied and
reported as repairs; anything needing a judgement call is only flagged, so it
surfaces in the Notion `Issue Log` for manual triage.
"""

import re
from dataclasses import dataclass, field
from typing import Iterable

from definitions import VERIFIED_PERPETUAL_PARISHES
from schemas import (
    AdorationTime,
    BulletinExtraction,
    ConfessionTime,
    MassTime,
    SiteInfo,
)

# Bogus end-of-slot values. 240 is the one the model reaches for when a slot
# ends at midnight — it appeared on 8 adoration slots across 5 parishes, every
# one of which said "until Midnight" or "overnight" in its own note. 2400 is
# the other spelling of the same mistake (currently rejected by the schema
# bound, so it only appears if that bound is ever loosened).
MIDNIGHT_SENTINELS = (240, 2400)

# A confession slot at or above this many minutes gets flagged for review. Set
# at two hours: it catches both known instances of the ampersand-as-range
# misread (3h45m at the Cathedral, 2h00m at St. Vincent de Paul Elyria) and
# accepts one standing false positive, St. Brendan's genuine 3-hour Saturday.
LONG_CONFESSION_MINUTES = 120

_APPOINTMENT_RE = re.compile(
    r"\b(appointment|call the (parish )?office|by request)\b", re.IGNORECASE
)
# A note that *leads* with appointment language describes an availability, not a
# scheduled slot. St. Columbkille prints "…and by appointment" as the tail of its
# Reconciliation sentence, and the model has turned that trailing clause into a
# slot of its own with an invented day and time.
_APPOINTMENT_ONLY_RE = re.compile(
    r"^\s*\(?\s*(and |or )?(by )?(appointment|arrangement)"
    r"|^\s*\(?\s*(please )?call the|^\s*\(?\s*(available )?(up)?on request",
    re.IGNORECASE,
)
# Only a note that *leads* with the cancellation describes its own entry.
# "Dedication of the Altar; no 9:00 AM Mass this day" is a real 11:00 Mass
# whose note happens to mention a different, cancelled one.
_CANCELLED_RE = re.compile(r"^\s*\(?\s*(no mass|mass cancel|cancelled)", re.IGNORECASE)
_VIGIL_RE = re.compile(r"\bvigil\b", re.IGNORECASE)
_CLOSED_RE = re.compile(r"\bclosed\b|\bnot available\b", re.IGNORECASE)
# An adoration chapel's "hours needing coverage" list is a staffing appeal, not
# its schedule — those are the hours it is *thinly attended*. St. Columbkille
# prints one, and the model has emitted its thirteen open hours as the adoration
# schedule, which advertises a perpetual chapel as adoring only at 4 AM Monday.
_COVERAGE_RE = re.compile(
    r"needing coverage|need(s|ed)? (an )?adorer|adorers? (are )?needed|"
    r"open hour|hour of need|hours? (still )?(open|available|to fill|uncovered)|"
    r"sign[- ]?up|commit(ment)? (to|for) (an )?hour|substitute",
    re.IGNORECASE,
)
# `notes` is published text - it renders in the app under the parish's own
# name. Anything about how the extraction went belongs in `extraction_notes`,
# which stays internal. St. Eugene shipped "Holy Day schedule lists 11:00 AM &
# 7:00 PM (day not specified); see extraction_notes." to end users.
_META_NOTE_RE = re.compile(
    r"extraction[_ ]notes?|\bsee notes?\b|per (the )?rules?\b|"
    r"(day|date|time|year|start|end|start time|end time)s?\s+"
    r"(is |are |was |were )?(not|never)\s+(explicitly\s+|clearly\s+)?"
    r"(specified|stated|given|provided|listed|indicated)|"
    r"not (explicitly |clearly )?(specified|stated|given|provided|listed) "
    r"(in|by) the bulletin|"
    r"\b(estimated|assumed|inferred|approximated|guessed)\b|"
    r"bulletin (does not|doesn't|never) (state|say|specify|list|give)|"
    r"see (the )?extraction|as extracted|per schema|\bLLM\b|"
    r"needs? confirmation|may need confirmation|unclear from the bulletin",
    re.IGNORECASE,
)
# Scrubbing works at two granularities so a note that is half useful keeps its
# useful half: parentheticals come off first (that is where the apology usually
# lives - "(start time estimated ~30 minutes after Mass)"), then whatever is
# left is split into sentences and clauses.
_NOTE_PAREN_RE = re.compile(r"\s*\([^()]*\)")
_NOTE_SEGMENT_RE = re.compile(r"[^.;]+[.;]?")

# A "Holy Day of Obligation" line is a standing policy ("on Holy Days we offer
# 8:15, 11:15 and 6:45"), not a Mass that happens every week. With no date the
# extractor has to pick a weekday, and it picks one essentially at random - so
# the parish ends up advertising three phantom Masses every Thursday.
_HOLY_DAY_RE = re.compile(r"\bholy\s?day", re.IGNORECASE)
# ...but a parish's standing Saturday vigil is often *also* its Holy Day vigil,
# and the note then says both ("Vigil Mass; Vigil of Holy Day", "Weekday Mass;
# Holy Day"). That entry is a real weekly Mass carrying an extra label, so it
# must survive. Only a note that describes the Mass as Holy-Day-only is safe to
# drop.
_WEEKDAYS = frozenset({"Monday", "Tuesday", "Wednesday", "Thursday", "Friday"})
_ORDINARY_MASS_RE = re.compile(
    r"vigil mass|weekday mass|daily mass|sunday mass|weekend mass|"
    r"regular(ly scheduled)? mass|usual mass",
    re.IGNORECASE,
)
# ...unless the ordinary words are *governed by* the Holy Day modifier rather
# than standing on their own. Sacred Heart Chapel prints "Solemnity / Holy Day
# Mass - Vigil Mass 7:00 pm", and the note "Holy Day Vigil Mass" contains the
# substring "vigil mass" while describing no weekly vigil at all: the parish
# has no Thursday Mass, and it was publishing one every week. Word order is
# what separates the two - in the genuine double label the ordinary word comes
# first ("Vigil Mass; Vigil of Holy Day"), so only a Holy Day modifier
# *preceding* the ordinary phrase, within the same clause, disqualifies it.
_HOLY_DAY_QUALIFIED_RE = re.compile(
    r"\b(?:holy\s?day|solemnity)\b[^.;]{0,24}?\b"
    r"(?:vigil|weekday|daily|sunday|weekend|regular(?:ly scheduled)?|usual)\s+mass",
    re.IGNORECASE,
)


def _has_independent_ordinary_label(note: str) -> bool:
    """True when the note calls this an ordinary Mass in its own right."""
    return bool(_ORDINARY_MASS_RE.search(_HOLY_DAY_QUALIFIED_RE.sub(" ", note)))

# Adoration tied to a season or to the Triduum, which the schema cannot encode.
# `MassTime` has `mass_date` for a one-off; `AdorationTime` has nothing - so a
# Holy Thursday slot is stored as a recurring weekly Thursday and republished
# every Thursday of the year. Deliberately does NOT match "First Friday" or
# "First Saturday": those recur genuinely, and though the schema encodes them
# just as poorly, they are a different problem (a monthly slot published
# weekly), not stale data.
_SEASONAL_ADORATION_RE = re.compile(
    r"holy\s?thursday|good\s?friday|holy\s?saturday|maundy|triduum|holy\s?week|"
    r"easter\s?vigil|palm\s?sunday|(mass of the )?(lord.s|last)\s?supper|"
    r"\brepository\b|\blent(en)?\b|\badvent\b|ash\s?wednesday|"
    r"forty\s?hours|\b40\s?hours\b|corpus\s?christi|divine\s?mercy\s?sunday|"
    r"\bchristmas\b|\beaster\b",
    re.IGNORECASE,
)

# Markers that a dated Mass really is a one-off rather than the weekly Mass
# restated under a date heading.
_DISTINCT_MASS_RE = re.compile(
    r"\boutdoor\b|\bpark\b|\bfestival\b|\bpicnic\b|\bdedication\b|\bcentennial\b|"
    r"\banniversar|\bbishop\b|\bordination\b|\bjubilee\b|\bgraduation\b",
    re.IGNORECASE,
)

# Note keyword -> value for the structured `language` field. Ordered: first
# match wins, so bilingual/multi-language phrasings precede the singles.
LANGUAGE_KEYWORDS: list[tuple[re.Pattern, str]] = [
    (re.compile(r"english\s*(&|and|/)\s*spanish", re.IGNORECASE), "English & Spanish"),
    (re.compile(r"english\s*(&|and|/)\s*italian", re.IGNORECASE), "English & Italian"),
    (re.compile(r"english\s*(&|and|/)\s*polish", re.IGNORECASE), "English & Polish"),
    (re.compile(r"\bbi-?lingual\b", re.IGNORECASE), "Bilingual"),
    (re.compile(r"\bspanish\b|\bespa\w*ol\b", re.IGNORECASE), "Spanish"),
    (re.compile(r"\bpolish\b", re.IGNORECASE), "Polish"),
    (re.compile(r"\blatin\b", re.IGNORECASE), "Latin"),
    (re.compile(r"\bslovenian\b", re.IGNORECASE), "Slovenian"),
    (re.compile(r"\bslovak\b", re.IGNORECASE), "Slovak"),
    (re.compile(r"\bcroatian\b", re.IGNORECASE), "Croatian"),
    (re.compile(r"\bhungarian\b", re.IGNORECASE), "Hungarian"),
    (re.compile(r"\bvietnamese\b", re.IGNORECASE), "Vietnamese"),
    (re.compile(r"\bkorean\b", re.IGNORECASE), "Korean"),
    (re.compile(r"\bitalian\b", re.IGNORECASE), "Italian"),
]


@dataclass
class SanitizeReport:
    """Findings split by who needs to act on them.

    `repairs` are unambiguous fixes already applied — informational, they go in
    the extraction log. `flags` need a human to look at the bulletin, so they
    become warnings and land in the Notion Issue Log.
    """

    repairs: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    def repair(self, msg: str) -> None:
        self.repairs.append(msg)

    def flag(self, msg: str) -> None:
        self.flags.append(msg)

    def extend(self, other: "SanitizeReport", prefix: str = "") -> None:
        self.repairs.extend(prefix + m for m in other.repairs)
        self.flags.extend(prefix + m for m in other.flags)


def _merge_notes(*notes: str | None) -> str | None:
    """Join distinct notes with '; ', preserving order and dropping blanks."""
    seen: list[str] = []
    for n in notes:
        n = (n or "").strip()
        if n and n not in seen:
            seen.append(n)
    return "; ".join(seen) or None


def _minutes_between(start: int, end: int) -> int:
    """Length of a same-day HHMM slot, in minutes."""
    return (end // 100 * 60 + end % 100) - (start // 100 * 60 + start % 100)


def _clean_ranges(
    items: list[ConfessionTime] | list[AdorationTime],
    label: str,
    report: SanitizeReport,
) -> list:
    """Normalize, deduplicate and merge a list of start/end slots.

    `end_time` is optional: None means the bulletin stated a start and no end.
    Every check below either skips those or handles them explicitly - a slot
    with no end has nothing to compare against.
    """
    cleaned: list = []

    for item in items:
        if item.end_time is not None and item.end_time in MIDNIGHT_SENTINELS:
            report.repair(
                f"{label}: repaired bogus end time {item.end_time} on "
                f"{item.day.value} -> midnight (00:00 next day)"
            )
            item.end_time = 0
            item.end_next_day = True

        # start == end == 0 is the model's way of saying "time not specified".
        # It reads downstream as a real midnight slot, so drop it. A genuinely
        # covered day is 00:00-00:00 with end_next_day set, and is kept.
        if item.start_time == 0 and item.end_time == 0 and not item.end_next_day:
            report.repair(
                f"{label}: dropped {item.day.value} slot with unspecified time"
                f"{' - ' + item.notes if item.notes else ''}"
            )
            continue

        # An end repeating the start is how the model used to say "no end
        # stated". That collides with a real 24-hour span (same endpoints,
        # end_next_day set), so normalize it to the unambiguous encoding.
        if (
            item.end_time == item.start_time
            and not item.end_next_day
            and item.start_time != 0
        ):
            report.repair(
                f"{label}: {item.day.value} {item.start_time:04d} repeats its start "
                "as its end - recorded as having no stated end time"
            )
            item.end_time = None

        # A slot that ends before it starts crosses midnight by definition.
        if (
            item.end_time is not None
            and item.end_time < item.start_time
            and not item.end_next_day
        ):
            report.repair(
                f"{label}: {item.day.value} {item.start_time:04d}-{item.end_time:04d} "
                "ends before it starts - marked as crossing midnight"
            )
            item.end_next_day = True

        cleaned.append(item)

    return _dedupe_ranges(cleaned, label, report)


def _dedupe_ranges(items: list, label: str, report: SanitizeReport) -> list:
    """Collapse exact duplicates, then fold appointment-style addenda in.

    Two slots at the same (day, start) that differ only by "also by appointment"
    are one slot with a longer note, not two slots.
    """
    by_key: dict[tuple, ConfessionTime | AdorationTime] = {}
    for item in items:
        key = (item.day, item.start_time, item.end_time, item.end_next_day)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = item
        else:
            existing.notes = _merge_notes(existing.notes, item.notes)
            report.repair(
                f"{label}: merged duplicate {item.day.value} "
                f"{item.start_time:04d} slot"
            )

    # Second pass: same (day, start), different end, one of them an
    # appointment-style addendum. Keep the widest window, merge the notes.
    by_start: dict[tuple, ConfessionTime | AdorationTime] = {}
    merged: list = []
    for item in by_key.values():
        key = (item.day, item.start_time)
        prev = by_start.get(key)
        if prev is None:
            by_start[key] = item
            merged.append(item)
            continue

        notes_pair = (prev.notes or "", item.notes or "")

        # Same (day, start), one with a stated end and one without. The
        # bulletin printed the same slot in two places — a schedule box giving
        # "5:00-5:25 pm" and a "this week" listing giving only "5:00 pm
        # Confession". The open-ended one adds nothing, so keep the end.
        if (prev.end_time is None) != (item.end_time is None):
            prev.notes = _merge_notes(*notes_pair)
            if prev.end_time is None:
                prev.end_time = item.end_time
                prev.end_next_day = item.end_next_day
            report.repair(
                f"{label}: merged open-ended duplicate of {item.day.value} "
                f"{item.start_time:04d} into the slot that states an end"
            )
            continue

        if any(_APPOINTMENT_RE.search(n) for n in notes_pair):
            prev.notes = _merge_notes(*notes_pair)
            # Keep the widest window. With one end unknown there is no widest,
            # so a stated end wins over None rather than being discarded by it.
            ends = [e for e in (prev.end_time, item.end_time) if e is not None]
            prev.end_time = max(ends) if ends else None
            report.repair(
                f"{label}: merged appointment addendum into {item.day.value} "
                f"{item.start_time:04d} slot"
            )
        else:
            merged.append(item)

    return merged


def _clean_masses(masses: list[MassTime], report: SanitizeReport) -> list[MassTime]:
    """Drop non-Masses, backfill language, dedupe, and flag suspicious entries."""
    kept: list[MassTime] = []

    for mass in masses:
        notes = mass.notes or ""

        # A cancellation is not a Mass. The model sometimes encodes "No Mass on
        # Friday" as a Mass entry whose note says it doesn't happen.
        if _CANCELLED_RE.search(notes):
            report.repair(
                f"mass: dropped cancellation encoded as a Mass "
                f"({mass.day.value} {mass.time:04d} - {notes})"
            )
            continue

        if mass.time == 0 and not re.search(r"midnight", notes, re.IGNORECASE):
            report.repair(
                f"mass: dropped {mass.day.value} 00:00 entry with no midnight-Mass "
                f"evidence{' - ' + notes if notes else ''}"
            )
            continue

        # Backfill the structured language field from the note.
        if not mass.language and notes:
            for pattern, language in LANGUAGE_KEYWORDS:
                if pattern.search(notes):
                    mass.language = language
                    report.repair(
                        f"mass: {mass.day.value} {mass.time:04d} language "
                        f"backfilled from notes -> {language}"
                    )
                    break

        # A vigil is by definition an evening Mass; a morning one is an AM/PM
        # flip. Not auto-repaired: the note may be referring to some *other*
        # Mass ("see Saturday Vigil at 4:30pm" on a Friday morning entry).
        if _VIGIL_RE.search(notes) and mass.time < 1200:
            report.flag(
                f"mass: {mass.day.value} {mass.time:04d} is described as a vigil "
                "but is a morning time - possible AM/PM error"
            )

        kept.append(mass)

    return _dedupe_masses(kept, report)


def _dedupe_masses(masses: list[MassTime], report: SanitizeReport) -> list[MassTime]:
    """Collapse exact duplicates and dated Masses that restate a recurring one."""
    by_key: dict[tuple, MassTime] = {}
    for mass in masses:
        key = (mass.day, mass.time, mass.language, mass.mass_date)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = mass
        else:
            existing.notes = _merge_notes(existing.notes, mass.notes)
            report.repair(
                f"mass: merged duplicate {mass.day.value} {mass.time:04d} entry"
            )

    result = list(by_key.values())
    recurring = {(m.day, m.time) for m in result if m.mass_date is None}

    # A dated Mass at the same day/time as a recurring one is only a real
    # one-off if something about it differs — a different venue, an outdoor
    # setting, a special celebration. Otherwise it is the weekly Mass restated
    # under a date heading, and the app would show it twice.
    kept: list[MassTime] = []
    for mass in result:
        if (
            mass.mass_date is not None
            and (mass.day, mass.time) in recurring
            and not _DISTINCT_MASS_RE.search(mass.notes or "")
        ):
            report.repair(
                f"mass: dropped dated Mass {mass.mass_date} {mass.day.value} "
                f"{mass.time:04d} - restates the recurring weekly Mass"
            )
            continue
        kept.append(mass)

    return kept


def _check_adoration(
    site: SiteInfo, report: SanitizeReport, verified_perpetual: bool = False
) -> None:
    """Flag is_perpetual claims the schedule itself contradicts.

    `is_perpetual` means "24/7 chapel" and is independent of `times`. It is
    never auto-cleared: only the bulletin can settle it. Parishes listed in
    VERIFIED_PERPETUAL_PARISHES have been checked by hand and are exempt.
    """
    adoration = site.adoration
    if not adoration.is_perpetual or verified_perpetual:
        return

    if not adoration.times:
        report.flag(
            "adoration: flagged perpetual with no hours listed - verify it is "
            "really a 24/7 chapel"
        )
    elif any(_CLOSED_RE.search(t.notes or "") for t in adoration.times):
        report.flag(
            "adoration: flagged perpetual but a note describes closures - "
            "is_perpetual is probably wrong"
        )
    elif len(adoration.times) < 7:
        report.flag(
            f"adoration: flagged perpetual but only {len(adoration.times)} time "
            "slot(s) listed - verify"
        )


def _drop_coverage_hours(site: SiteInfo, report: SanitizeReport) -> None:
    """Drop adoration slots that are really a request for adorers.

    A perpetual chapel appealing for volunteers prints the hours it is thinly
    covered. Read as a schedule those hours invert the truth: a 24/7 chapel
    ends up advertising adoration *only* at 4 AM Monday and 10 AM Friday.

    Coverage hours are only safe to drop when we independently know when
    adoration actually happens, or when there is no schedule to lose:

    - a perpetual chapel — `is_perpetual` already says "always", so an
      enumerated hour is redundant;
    - a listing that also carries a covered day (`0->0` with `end_next_day`),
      which is how continuous multi-day adoration is encoded. St. Edward runs
      Thursday-Sunday and prints the hours it is short-handed *within* that
      span; the span is the schedule and the appeal is noise inside it;
    - a listing that is *entirely* coverage requests — the bulletin printed an
      appeal and no schedule at all.

    Anything else is left alone. A real schedule that annotates one hour
    "adorers needed for 3 PM" keeps every slot, because there the bulletin is
    telling us both when adoration happens and where it is short-handed.
    """
    adoration = site.adoration
    if not adoration.times:
        return

    coverage = [t for t in adoration.times if _COVERAGE_RE.search(t.notes or "")]
    if not coverage:
        return

    covered_day = any(
        t.start_time == 0 and t.end_time == 0 and t.end_next_day
        for t in adoration.times
    )
    if adoration.is_perpetual:
        keep = [t for t in adoration.times if t not in coverage]
        where = "at a perpetual chapel"
    elif covered_day:
        keep = [t for t in adoration.times if t not in coverage]
        where = "inside a continuously covered day"
    elif len(coverage) == len(adoration.times):
        keep = []
        where = "that were the entire adoration listing"
    else:
        return  # a real schedule with a shortfall noted on some hours

    dropped = len(adoration.times) - len(keep)
    adoration.times = keep
    report.repair(
        f"adoration: dropped {dropped} slot(s) {where} whose notes describe hours "
        "needing adorers - a staffing appeal, not the schedule"
    )


def _drop_clauses(note: str | None, pattern: re.Pattern) -> str | None:
    """Remove the parts of a note that match `pattern`, keeping the rest.

    Parentheticals are considered first, then sentences and clauses, because a
    note is usually a real fact with an aside bolted on in brackets. Returns
    None if nothing publishable survives.
    """
    if not note or not pattern.search(note):
        return note

    text = _NOTE_PAREN_RE.sub(
        lambda m: "" if pattern.search(m.group()) else m.group(), note
    )
    kept = [
        seg.strip()
        for seg in _NOTE_SEGMENT_RE.findall(text)
        if seg.strip(" .;,") and not pattern.search(seg)
    ]
    if not kept:
        return None
    out = re.sub(r"\s+", " ", " ".join(kept)).strip()
    return out.strip(" ;,-").rstrip(";,") or None


def _scrub_note(note: str | None) -> str | None:
    """Strip extraction commentary from a note, keeping the publishable part.

    "After Mass (start time estimated ~30 minutes after Mass)." keeps "After
    Mass". A note that is entirely commentary becomes None.
    """
    return _drop_clauses(note, _META_NOTE_RE)


def _scrub_meta_notes(site: SiteInfo, report: SanitizeReport) -> None:
    """Remove extraction commentary from every published `notes` field.

    `notes` renders in the app beneath the parish's own name; `extraction_notes`
    is the internal channel and is never published. The prompt now says so, but
    a prompt rule is not enforcement - St. Eugene published "(day not
    specified); see extraction_notes." to end users for months.
    """
    scrubbed = 0
    for item in (
        list(site.mass_times)
        + list(site.confession_times)
        + list(site.adoration.times)
    ):
        cleaned = _scrub_note(item.notes)
        if cleaned != item.notes:
            item.notes = cleaned
            scrubbed += 1
    if scrubbed:
        report.repair(
            f"notes: scrubbed extraction commentary from {scrubbed} published "
            "note(s) - that text belongs in extraction_notes"
        )


def _drop_undated_holy_day_masses(
    masses: list[MassTime], report: SanitizeReport
) -> list[MassTime]:
    """Drop recurring Masses that are really the parish's Holy Day policy.

    Bulletins print a standing line - "Holy Days of Obligation: 8:15 am, 11:15
    am, 6:45 pm" - with no date, because the date changes every year. It is not
    a weekly Mass, but `mass_date` is the only way to say "one specific day",
    so an extractor that keeps the line has to invent a weekday for it. It
    picks one arbitrarily, and the parish then advertises three Masses every
    Thursday that nobody celebrates.

    Only *undated* entries are dropped. A Holy Day Mass that carries a
    `mass_date` is a real dated Mass (the Assumption on Aug 15) and is correct
    as-is - that is exactly what the extractor should be producing instead.
    """
    # Weekdays on which each time is offered as a plain recurring Mass. A Holy
    # Day entry landing on a time the parish already says it offers most
    # weekdays is very likely the daily Mass with the Holy Day label merged
    # onto it - `_dedupe_masses` merges on (day, time, language, mass_date) and
    # joins the notes, so the plain entry's empty note loses to the Holy Day
    # one and the only surviving evidence is the label. St. Eugene's Monday
    # 11:00 is exactly this: its own GPT log reads "merged duplicate Monday
    # 1100 entry", and dropping it would have deleted a real daily Mass.
    plain_weekdays: dict[int, set[str]] = {}
    plain_slots: set[tuple[str, int]] = set()
    for mass in masses:
        if mass.mass_date is None and not _HOLY_DAY_RE.search(mass.notes or ""):
            plain_slots.add((mass.day.value, mass.time))
            if mass.day.value in _WEEKDAYS:
                plain_weekdays.setdefault(mass.time, set()).add(mass.day.value)

    kept, dropped, ambiguous = [], [], []
    for mass in masses:
        note = mass.notes or ""
        if mass.mass_date is not None or not _HOLY_DAY_RE.search(note):
            kept.append(mass)
            continue

        if _has_independent_ordinary_label(note):
            # Doubles as a real weekly Mass - keep it, but say so, because the
            # extractor conflated two schedules into one entry.
            kept.append(mass)
            ambiguous.append((mass, "is labelled both a regular Mass and a Holy Day Mass"))
        elif (mass.day.value, mass.time) in plain_slots:
            # The plain daily Mass is still here as its own entry, so this is
            # just the Holy Day listing restating it. Drop the copy; the real
            # one survives untouched. (Only reachable before dedupe runs.)
            dropped.append(mass)
        elif len(plain_weekdays.get(mass.time, set()) - {mass.day.value}) >= 2:
            # Keeping it means calling it the daily Mass, so the Holy Day label
            # has to go with that decision - leaving it would publish "Holy Day
            # schedule lists 11:00 AM & 7:00 PM" under an ordinary Monday Mass.
            mass.notes = _drop_clauses(mass.notes, _HOLY_DAY_RE)
            kept.append(mass)
            ambiguous.append(
                (mass, "carries a Holy Day label but matches this parish's daily "
                       "Mass time on other weekdays, so it may be the daily Mass "
                       "with the label merged in")
            )
        else:
            dropped.append(mass)

    for mass, why in ambiguous:
        report.flag(
            f"mass: {mass.day.value} {mass.time:04d} {why}; kept as recurring. "
            "Confirm the parish really offers it every week"
        )

    if dropped:
        shown = ", ".join(f"{m.day.value} {m.time:04d}" for m in dropped)
        report.repair(
            f"mass: dropped {len(dropped)} undated Holy Day entr(y/ies) ({shown}) - "
            "a Holy Day schedule with no date is a standing policy, not a weekly "
            "Mass, and was being published on an arbitrary weekday"
        )
    return kept


def _drop_seasonal_adoration(site: SiteInfo, report: SanitizeReport) -> None:
    """Drop adoration slots that only happen in one season or on one feast.

    `AdorationTime` has no `mass_date` and no season field, so a Holy Thursday
    slot can only be stored as a recurring weekly Thursday - and is then
    republished every Thursday of the year. Nineteen parishes in the diocese
    were advertising their Holy Thursday 2026 adoration as their standing
    schedule, and because `UPDATE_ADORATION = False` no ordinary run would ever
    have corrected it.

    The guard matches `_drop_coverage_hours`: only drop when there is no real
    schedule to lose - a perpetual chapel, or a listing that is *entirely*
    seasonal. A mixed listing (a genuine weekly Holy Hour plus a Lenten extra)
    is flagged instead, because dropping into a live schedule on a note match
    is how a good slot gets deleted.
    """
    adoration = site.adoration
    if not adoration.times:
        return

    seasonal = [t for t in adoration.times if _SEASONAL_ADORATION_RE.search(t.notes or "")]
    if not seasonal:
        return

    if adoration.is_perpetual:
        where = "at a perpetual chapel"
    elif len(seasonal) == len(adoration.times):
        where = "that were the entire adoration listing"
    else:
        report.flag(
            f"adoration: {len(seasonal)} of {len(adoration.times)} slot(s) are "
            "described as seasonal or Triduum-only but sit alongside a real "
            "schedule - adoration has no way to encode a season, so these will "
            "publish year-round. Verify against the bulletin"
        )
        return

    adoration.times = [t for t in adoration.times if t not in seasonal]
    report.repair(
        f"adoration: dropped {len(seasonal)} seasonal/Triduum slot(s) {where} - "
        "adoration has no date or season encoding, so they were being published "
        "every week of the year"
    )


def _cross_check_mass_references(site: SiteInfo, report: SanitizeReport) -> None:
    """Flag confession/adoration notes that key off a Mass the site lacks.

    "Adoration one hour before the 8:00 AM Mass" on a day with no 8:00 AM Mass
    means either the Mass list is incomplete or another parish's block bled in
    from a shared bulletin.
    """
    mass_times = {(m.day, m.time) for m in site.mass_times}
    if not mass_times:
        return

    ref = re.compile(r"(\d{1,2})[:.](\d{2})\s*([ap])\.?m\.?\s+mass", re.IGNORECASE)
    slots: Iterable = [*site.confession_times, *site.adoration.times]
    for slot in slots:
        match = ref.search(slot.notes or "")
        if not match:
            continue
        hour, minute = int(match.group(1)), int(match.group(2))
        meridiem = match.group(3).lower()
        if meridiem == "p" and hour != 12:
            hour += 12
        elif meridiem == "a" and hour == 12:
            hour = 0
        if (slot.day, hour * 100 + minute) not in mass_times:
            report.flag(
                f"{slot.day.value} note references a {match.group(0)} that is not "
                "in this site's Mass list - missing Masses or cross-site bleed"
            )


def _fold_appointment_only(items: list, label: str, report: SanitizeReport) -> list:
    """Drop slots that are really just "…and by appointment", keeping the note.

    A bulletin's "Saturday 2:30-3:45 PM … and by appointment" is two facts: a
    scheduled window, and a standing availability with no day or time. When the
    model emits the availability as its own slot it has to invent both, so the
    entry is a fabricated time wearing a real note.

    Deliberately narrow: only a slot whose note *opens* with appointment
    language, that states no end, and that is not the only slot left. A real
    window annotated "or by appointment" keeps its stated end and is untouched.
    """
    if len(items) < 2:
        return items

    keep, folded = [], []
    for item in items:
        if (
            item.end_time is None
            and item.notes
            and _APPOINTMENT_ONLY_RE.search(item.notes)
        ):
            folded.append(item)
        else:
            keep.append(item)

    if not keep:  # every slot looked like an addendum; trust the extraction
        return items

    for item in folded:
        for survivor in keep:
            survivor.notes = _merge_notes(survivor.notes, item.notes)
        report.repair(
            f"{label}: dropped {item.day.value} {item.start_time:04d} slot that was "
            f"only an appointment note - folded into the scheduled slots"
        )
    return keep


def _check_confession_spans(
    confessions: list[ConfessionTime], report: SanitizeReport
) -> None:
    """Flag confession windows long enough to suggest a misread time list.

    Parishes schedule confessions in short blocks, usually bracketing a Mass.
    A multi-hour window is occasionally real (a long Saturday afternoon), but
    it is also what "7:45 am & 11:30 am" turns into when the model reads an
    ampersand as a range — which is how the Cathedral of St. John came to
    advertise 3h45m of weekday confessions. Only the bulletin can tell the two
    apart, so this flags rather than repairs.
    """
    for item in confessions:
        if item.end_time is None or item.end_next_day:
            continue
        minutes = _minutes_between(item.start_time, item.end_time)
        if minutes < LONG_CONFESSION_MINUTES:
            continue
        report.flag(
            f"confession: {item.day.value} {item.start_time:04d}-{item.end_time:04d} "
            f"spans {minutes // 60}h{minutes % 60:02d}m - verify it is one window "
            "and not two separate times read as a range"
        )


def sanitize_extraction(
    extraction: BulletinExtraction, parish_id: str | None = None
) -> SanitizeReport:
    """Clean an extraction in place. Returns repairs made and issues to review.

    `parish_id` is only used to look up per-parish exemptions; omit it and
    every check applies.
    """
    combined = SanitizeReport()
    verified_perpetual = parish_id in VERIFIED_PERPETUAL_PARISHES

    for site in extraction.sites:
        prefix = f"[{site.site_name}] " if len(extraction.sites) > 1 else ""
        report = SanitizeReport()

        # Before _clean_masses, whose dedupe would otherwise merge a Holy Day
        # entry into the real Mass at the same (day, time) and leave the Holy
        # Day note as the only survivor - which is how 1734's genuine Monday
        # 11:00 daily Mass ended up labelled a Holy Day Mass.
        site.mass_times = _drop_undated_holy_day_masses(site.mass_times, report)
        site.mass_times = _clean_masses(site.mass_times, report)
        site.confession_times = _clean_ranges(
            site.confession_times, "confession", report
        )
        site.confession_times = _fold_appointment_only(
            site.confession_times, "confession", report
        )
        _check_confession_spans(site.confession_times, report)
        site.adoration.times = _clean_ranges(site.adoration.times, "adoration", report)
        _drop_coverage_hours(site, report)
        _drop_seasonal_adoration(site, report)
        # Last of the content passes: it only rewrites `notes`, and the passes
        # above match on note text, so scrubbing earlier would hide a slot from
        # the check that is meant to catch it.
        _scrub_meta_notes(site, report)
        _check_adoration(site, report, verified_perpetual)
        _cross_check_mass_references(site, report)

        combined.extend(report, prefix)

    return combined
