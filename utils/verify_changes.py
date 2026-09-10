"""Verify schedule changes against reproducibility and the bulletin's text.

The pipeline's standing blind spot: a new extraction silently replaces the
stored schedule, and nothing asks whether the change is real. The two failure
modes this leaves open pull in opposite directions —

- **Extraction noise.** Two runs of the identical prompt over identical bytes
  agree on only ~83% of parishes' recurring Masses (studies/noise), so most
  week-to-week diffs are the model flapping, not the parish changing anything.
- **A wrong value that looks like a change.** The Cathedral's Saturday
  confession keeps coming back 15:30 against a printed "3:00-4:00 pm"; the
  Sunday 10:30 fabrication *was* a change from a correct stored 11:00.

An LLM judging "does this change make sense?" fails both ways at once: the
fabricated 10:30 is the most plausible-looking time in the diocese, and the
stored value it replaced is not ground truth either (St. Mel's wrong confession
survived months of correct runs). The arbiters that actually work are cheaper:

1. **Diff** the new recurring schedule against what Notion holds (Masses and
   confessions; adoration is locked by UPDATE_ADORATION and never written, so
   a diff there would warn forever).
2. **Reproduce.** Re-extract once from the same downloaded bytes. A change the
   second run does not reproduce is noise — say so. Budgeted per run so a bad
   week cannot double the OpenAI bill.
3. **Check the page.** For a changed slot, ask the text layer which side is
   printed. Old time printed, new time absent = the extraction moved away from
   the page — the damning combination. Both absent = derived or image-layer
   times, and no claim is made (same gating philosophy as verify_times).

**Flag-only.** Nothing here changes what is saved; warnings land in Issue Log
and the end-of-run summary via `warn()`. Run it a few Saturdays and measure the
alarm rate before letting it gate anything.
"""

import logging
import re
from typing import Awaitable, Callable, Optional

from schemas import ConfessionTime, MassTime, SiteInfo
from utils.verify_times import (
    MIN_HIT_RATE,
    MIN_TEXT_CHARS,
    MIN_VERIFIED_TIMES,
    _extract_text,
    _normalize,
    _renderings,
)

logger = logging.getLogger(__name__)

# Re-extractions available per run (process-wide). ~17% of parishes show a
# recurring-Mass diff from noise alone, so a normal Saturday needs ~30-40;
# the cap exists so a prompt regression that changes *everything* costs one
# bounded batch of retries, not a doubled run.
REEXTRACT_BUDGET = 40
_budget = REEXTRACT_BUDGET

# Pairings: parish_id -> the (possibly merged) site about to be saved there.
Pairings = dict[str, SiteInfo]


def _mass_slots(site: SiteInfo) -> set[tuple[str, int]]:
    return {
        (m.day.value, m.time) for m in site.mass_times if m.mass_date is None
    }


def _confession_slots(site: SiteInfo) -> set[tuple[str, int]]:
    return {(c.day.value, c.start_time) for c in site.confession_times}


def _stored_mass_slots(raw: list[dict]) -> set[tuple[str, int]]:
    return {
        (m.day.value, m.time)
        for m in (MassTime(**d) for d in raw)
        if m.mass_date is None
    }


def _stored_confession_slots(raw: list[dict]) -> set[tuple[str, int]]:
    return {(c.day.value, c.start_time) for c in (ConfessionTime(**d) for d in raw)}


def _time_in_text(time: int, text: str) -> bool:
    return any(re.search(p, text) for p in _renderings(time))


# How far from the digits a corroborating word may sit. A masthead prints
# "Sunday: 8:30, 11:00 am" well inside this; an office-hours line and the Mass
# grid are further apart than this in every case checked.
_CONTEXT_CHARS = 40

_DAY_ABBREV = {
    "Sunday": r"sun", "Monday": r"mon", "Tuesday": r"tue", "Wednesday": r"wed",
    "Thursday": r"thu", "Friday": r"fri", "Saturday": r"sat",
}


def _time_in_text_near(time: int, day: str, text: str) -> bool:
    """Is this time printed *as this day's Mass*, rather than merely present?

    The bare `_time_in_text` grep is what made "(still printed in bulletin)"
    worth about half. Seven hand-checks in the 2026-09-05 triage came back 4
    true, 3 false, and every false one was the digits appearing somewhere
    unrelated: `0138`'s "removed Sunday 0830 (still printed)" matched the
    **office hours** line `8:30 am - 4:30 pm`; `immat-con-cle`'s bogus 6:30am
    confession was "confirmed" by the Friday **6:30 pm** Mass; `0885`'s matched
    the Thu/Fri 12:10 entries while the removed Tue/Wed 12:10 were phantoms. In
    all three the *new* extraction was right and the label argued for the wrong
    side.

    So the digits must sit within `_CONTEXT_CHARS` of **this entry's own
    weekday** or the word Mass. Keying on the entry's own day rather than any
    weekday is deliberate and is what separates the three false positives:
    `0138`'s office hours say "monday-friday", never Sunday.

    This is the label only. `_time_in_text` still backs `_text_is_verifiable`,
    whose hit-rate gate was validated over 1,250 parish-runs in v2.5.14 and
    should not be perturbed by a change to the labelling rule.
    """
    abbrev = _DAY_ABBREV.get(day)
    if not abbrev:
        return _time_in_text(time, text)
    # The entry's OWN weekday, and nothing else. Accepting "mass" as an
    # alternative was tried and is far too weak - in a bulletin the word sits
    # near almost every time printed, so `immat-con-cle`'s Friday "6:30 pm
    # mass" went on corroborating a Monday 6:30am confession exactly as before.
    context = re.compile(rf"\b({day.lower()}|{abbrev}\.?)\b")
    for pattern in _renderings(time):
        for match in re.finditer(pattern, text):
            window = text[
                max(0, match.start() - _CONTEXT_CHARS):
                match.end() + _CONTEXT_CHARS
            ]
            if context.search(window):
                return True
    return False


def _text_is_verifiable(text: str, site: SiteInfo) -> bool:
    """Same gate as verify_times: the page must verify most of its own times.

    Without this, "the new time is absent from the text" is usually just "the
    schedule block is an image", and every changed slot would look suspicious.
    """
    if len(text) < MIN_TEXT_CHARS:
        return False
    slots = _mass_slots(site)
    if not slots:
        return False
    found = sum(1 for _, t in slots if _time_in_text(t, text))
    return found >= MIN_VERIFIED_TIMES and found / len(slots) >= MIN_HIT_RATE


def _describe(
    kind: str,
    added: set[tuple[str, int]],
    removed: set[tuple[str, int]],
    text: str,
    verifiable: bool,
) -> str:
    """One line per schedule kind: what changed, and what the page says."""
    parts = []
    for label, slots, damning in (("added", added, "not printed in bulletin"),
                                  ("removed", removed, "still printed in bulletin")):
        for day, time in sorted(slots):
            entry = f"{label} {day} {time:04d}"
            if verifiable:
                in_text = _time_in_text_near(time, day, text)
                if label == "added" and not in_text:
                    entry += f" ({damning} - suspicious)"
                elif label == "removed" and in_text:
                    entry += f" ({damning})"
            parts.append(entry)
    return f"{kind} changed vs stored: " + ", ".join(parts)


async def verify_schedule_changes(
    pairings: Pairings,
    stored: dict[str, tuple[Optional[list[dict]], Optional[list[dict]]]],
    reextract: Callable[[], Awaitable[Optional[Pairings]]],
    source_bytes: bytes,
    content_type: str = "pdf",
    held_weekdays: Optional[dict[str, str]] = None,
) -> list[str]:
    """Compare each site about to be saved against what Notion holds.

    Args:
        pairings: parish_id -> new site, as the save step will pair them.
        stored: parish_id -> (mass dicts, confession dicts) from Notion;
            None for a field whose stored JSON is corrupt (not diffable),
            missing key for a row that could not be fetched.
        reextract: re-runs extract + collapse + sanitize on the same bytes and
            returns fresh pairings; called at most once, only when something
            changed, and only while the run's budget lasts.
        source_bytes/content_type: the downloaded bulletin, for the text check.
        held_weekdays: weekdays the save step is freezing because the covered
            week contains a holiday. Changes there are EXPECTED - the listing
            disagreeing with the standing schedule is what a holiday week looks
            like - and they are not being written, so warning about them is
            pure noise. 22 of the 79 warnings on the 2026-09-05 run were this.
            They are dropped from the diff, and a parish left with nothing else
            produces no warning at all.

    Returns warning strings for `warn()`. Empty when nothing changed.
    """
    global _budget

    diffs: dict[str, dict[str, tuple[set, set]]] = {}
    for pid, site in pairings.items():
        if pid not in stored:
            continue
        stored_masses, stored_confessions = stored[pid]
        per_kind: dict[str, tuple[set, set]] = {}

        # A side that is empty (or corrupt) is not a diffable schedule: a first
        # extraction "adds" everything, an empty new one is already covered by
        # the retraction warnings, and corrupt JSON is the v2.5.1 alarm's job.
        def unheld(slots: set[tuple[str, int]]) -> set[tuple[str, int]]:
            if not held_weekdays:
                return slots
            return {s for s in slots if s[0] not in held_weekdays}

        if stored_masses:
            old = _stored_mass_slots(stored_masses)
            new = _mass_slots(site)
            if new and old != new:
                added, removed = unheld(new - old), unheld(old - new)
                if added or removed:
                    per_kind["Recurring Masses"] = (added, removed)
        if stored_confessions:
            old = _stored_confession_slots(stored_confessions)
            new = _confession_slots(site)
            if new and old != new:
                added, removed = unheld(new - old), unheld(old - new)
                if added or removed:
                    per_kind["Confessions"] = (added, removed)

        if per_kind:
            diffs[pid] = per_kind

    if not diffs:
        return []

    # Step 2: does a second extraction of the same bytes agree with the change?
    confirmed: Optional[Pairings] = None
    repro_note = ""
    if _budget > 0:
        _budget -= 1
        try:
            confirmed = await reextract()
        except Exception as e:  # verification must not fail the parish
            logger.warning("re-extraction failed: %s", e)
            repro_note = " [re-extraction failed - unverified]"
    else:
        repro_note = " [re-extraction budget spent - unverified]"

    text = _normalize(_extract_text(source_bytes, content_type))

    warnings: list[str] = []
    for pid, per_kind in diffs.items():
        site = pairings[pid]
        verifiable = _text_is_verifiable(text, site)
        prefix = f"'{pid}': " if len(pairings) > 1 else ""

        for kind, (added, removed) in per_kind.items():
            qualifier = repro_note
            if confirmed is not None:
                second = confirmed.get(pid)
                if second is None:
                    qualifier = " [second extraction matched no site - unverified]"
                else:
                    slots = (
                        _mass_slots(second)
                        if kind == "Recurring Masses"
                        else _confession_slots(second)
                    )
                    reproduced = added <= slots and not (removed & slots)
                    qualifier = (
                        " [reproduced on a second extraction]"
                        if reproduced
                        else " [NOT reproduced on a second extraction - likely "
                        "extraction noise; distrust this week's value]"
                    )
            warnings.append(
                prefix + _describe(kind, added, removed, text, verifiable) + qualifier
            )

    return warnings
