"""Reference predicate for the mapboard's `Rules` / `Dated Masses` keys.

`parish_data.json` keeps its legacy weekday lists, which say only "this slot
happens on Fridays". The `Rules` sidecar (see `notion_to_json._rules`) says
*which* Fridays. This module is the one place that reads it. It is
stdlib-only, Python 3.7-compatible and free of repo imports, so the mapboard can
vendor the file as-is. The Pi loads the export once at startup and keeps it for
as long as it stays up, so every rule here has to be answerable for an
arbitrary date, not for "this week".

A slot with no rule occurs every week, which is exactly the old behaviour - so a
consumer that ignores this module still renders what it always rendered.
"""

from __future__ import annotations

import calendar
from datetime import date, timedelta
from typing import Optional

# Spelled out rather than taken from `calendar.day_name`, which follows the
# process locale. Monday first, matching `date.weekday()`.
WEEKDAYS = (
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
)
_WEEKDAY_NUMBERS = {name: i for i, name in enumerate(WEEKDAYS)}


def _ordinal_matches(day: date, weeks: list[int]) -> bool:
    """Whether `day` is the nth (or last, -1) of its weekday in its month."""
    n = (day.day - 1) // 7 + 1
    is_last = day.day + 7 > calendar.monthrange(day.year, day.month)[1]
    return n in weeks or (is_last and -1 in weeks)


def occurs_on(rule: Optional[dict], day: date) -> bool:
    """Whether a slot carrying `rule` happens on `day`.

    `day` is assumed to fall on the slot's own weekday already, since the caller
    found the slot under that weekday. Key precedence follows
    EXPORT_SHAPE_CHANGES.md: `anchored_week`, then `weeks_of_month`, then
    `excluded_weeks`. The deriver never emits more than one of them.
    """
    if not rule:
        return True
    if day.isoformat() in rule.get("cancelled_on", ()):
        return False

    anchored = rule.get("anchored_week")
    if anchored:
        anchor = day - timedelta(days=anchored["offset_days"])
        if anchor.weekday() != _WEEKDAY_NUMBERS[anchored["weekday"]]:
            return False
        return _ordinal_matches(anchor, anchored["weeks_of_month"])
    if "weeks_of_month" in rule:
        return _ordinal_matches(day, rule["weeks_of_month"])
    if "excluded_weeks" in rule:
        return not _ordinal_matches(day, rule["excluded_weeks"])
    return True


def slots_on(parish: dict, kind: str, day: date) -> list:
    """A parish's `kind` slots ("Mass Times", "Confessions", "Adoration") on `day`.

    Returns the same element shape as the legacy weekday list: ints for Masses,
    `{"start": minutes}` dicts for the other two. Dated Masses for `day` are
    appended to the Mass list. Perpetual adoration (`Is24Hour`) has no slots
    and is not handled here.
    """
    weekday = WEEKDAYS[day.weekday()]
    rules = parish.get("Rules", {}).get(kind, {}).get(weekday, {})
    out = []
    for slot in parish.get(kind, {}).get(weekday, []) or []:
        start = str(slot) if isinstance(slot, int) else next(iter(slot))
        if occurs_on(rules.get(start), day):
            out.append(slot)
    if kind == "Mass Times":
        for t in parish.get("Dated Masses", {}).get(day.isoformat(), []):
            if t not in out:
                out.append(t)
        out.sort()
    return out
