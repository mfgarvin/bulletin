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

_APPOINTMENT_RE = re.compile(
    r"\b(appointment|call the (parish )?office|by request)\b", re.IGNORECASE
)
# Only a note that *leads* with the cancellation describes its own entry.
# "Dedication of the Altar; no 9:00 AM Mass this day" is a real 11:00 Mass
# whose note happens to mention a different, cancelled one.
_CANCELLED_RE = re.compile(r"^\s*\(?\s*(no mass|mass cancel|cancelled)", re.IGNORECASE)
_VIGIL_RE = re.compile(r"\bvigil\b", re.IGNORECASE)
_CLOSED_RE = re.compile(r"\bclosed\b|\bnot available\b", re.IGNORECASE)
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


def _clean_ranges(
    items: list[ConfessionTime] | list[AdorationTime],
    label: str,
    report: SanitizeReport,
) -> list:
    """Normalize, deduplicate and merge a list of start/end slots."""
    cleaned: list = []

    for item in items:
        if item.end_time in MIDNIGHT_SENTINELS:
            report.repair(
                f"{label}: repaired bogus end time {item.end_time} on "
                f"{item.day.value} -> midnight (00:00 next day)"
            )
            item.end_time = 0
            item.end_next_day = True

        # start == end == 0 is the model's way of saying "time not specified".
        # It reads downstream as a real midnight slot, so drop it.
        if item.start_time == 0 and item.end_time == 0 and not item.end_next_day:
            report.repair(
                f"{label}: dropped {item.day.value} slot with unspecified time"
                f"{' - ' + item.notes if item.notes else ''}"
            )
            continue

        # A slot that ends before it starts crosses midnight by definition.
        if item.end_time < item.start_time and not item.end_next_day:
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
        if any(_APPOINTMENT_RE.search(n) for n in notes_pair):
            prev.notes = _merge_notes(*notes_pair)
            prev.end_time = max(prev.end_time, item.end_time)
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

        site.mass_times = _clean_masses(site.mass_times, report)
        site.confession_times = _clean_ranges(
            site.confession_times, "confession", report
        )
        site.adoration.times = _clean_ranges(site.adoration.times, "adoration", report)
        _check_adoration(site, report, verified_perpetual)
        _cross_check_mass_references(site, report)

        combined.extend(report, prefix)

    return combined
