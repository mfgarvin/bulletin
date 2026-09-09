"""Which weekdays a bulletin's covered week cannot be trusted on.

The 2026-09-05 Labor Day run damaged 15 parishes in one class: on a holiday
week the day-by-day Mass intentions listing has no ordinary Mass on the
displaced weekday, and the v2.5.11 rule (the listing beats the standing
schedule box) makes the model retract or replace a Mass that is gone for
exactly one week.

**This has to be an external calendar, not a signal read off the extraction.**
The obvious cheap tell - "a dated Mass on the holiday plus a recurring change
on that weekday" - was measured against the 14 hand-verified damaged rows and
caught 6, missing every one of the three *replaced* rows, which are the only
shape that never self-heals. That is structural rather than unlucky: when the
model replaces, it has decided the holiday Mass IS the recurring Mass, so it
emits no dated Mass at all. The tell is absent exactly when it is needed, and
any signature derived from the extraction's own output inherits that flaw.

Nothing here decides whether a change is right. It says only "this weekday is
not evidence this week", which is what lets the caller keep the stored value
and reconsider next week - see `_splice_held_weekdays` in database/notion.py.
"""

from dataclasses import dataclass
from datetime import date, timedelta

# Cleveland is in the Province of Cincinnati, which transfers Ascension to the
# following Sunday - so it displaces no weekday. Flip this only against the
# diocesan calendar, never from memory.
ASCENSION_TRANSFERRED_TO_SUNDAY = True

# Observances listed for the record but which never hold a weekday. Empty on
# purpose: holding is a no-op when nothing changed on that weekday (the splice
# takes stored == new), so a quiet holiday like MLK or Presidents' Day costs
# nothing for the ~185 parishes that don't move a Mass, and the handful that DO
# move one are exactly the case this exists for. The 2026-08-30 finding that
# `1584` published seven Masses noted "Federal Holiday Mass (when a federal
# holiday falls on Monday)" is the evidence that even the quiet ones displace.
#
# This is the knob to reach for if a replay shows one of them is pure cost -
# name it here rather than deleting it, so the missing-Mass check still knows
# the day exists.
NON_DISPLACING: frozenset[str] = frozenset()

_WEEKDAY_NAMES = (
    "Monday", "Tuesday", "Wednesday", "Thursday",
    "Friday", "Saturday", "Sunday",
)


@dataclass(frozen=True)
class Observance:
    name: str
    # True for a Holy Day of obligation the faithful must attend, which is what
    # the "no dated Mass was extracted for it" check keys on. Civil holidays
    # displace a schedule but oblige nobody.
    obligation: bool = False


def easter(year: int) -> date:
    """Gregorian Easter (Butcher's algorithm). No dependency, ~10 lines."""
    a = year % 19
    b, c = divmod(year, 100)
    d, e = divmod(b, 4)
    g = (8 * b + 13) // 25
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 19 * l) // 433
    month, day = divmod(h + l - 7 * m + 90, 25)
    day = (h + l - 7 * m + 33 * month + 19) % 32
    return date(year, month, day)


def _nth_weekday(year: int, month: int, weekday: int, n: int) -> date:
    """The nth (1-based) `weekday` of a month; n = -1 means the last one."""
    if n < 0:
        day = date(year, month + 1, 1) - timedelta(days=1) if month < 12 \
            else date(year, 12, 31)
        while day.weekday() != weekday:
            day -= timedelta(days=1)
        return day
    day = date(year, month, 1)
    while day.weekday() != weekday:
        day += timedelta(days=1)
    return day + timedelta(weeks=n - 1)


def _observed(day: date) -> date | None:
    """The weekday a fixed-date civil holiday is *observed* on, if it moves.

    A parish keeps the liturgy on the real date, but the office - and with it
    the daily Mass - closes on the federal observance. `1584` was publishing
    seven phantom Masses whose own note read "Federal Holiday Mass (when a
    federal holiday falls on Monday)", so the observed day displaces too.
    """
    if day.weekday() == 5:      # Saturday -> observed Friday
        return day - timedelta(days=1)
    if day.weekday() == 6:      # Sunday -> observed Monday
        return day + timedelta(days=1)
    return None


def observances(year: int) -> dict[date, Observance]:
    """Every date in `year` whose week's schedule is liable to be unusual."""
    out: dict[date, Observance] = {}

    def add(when: date, name: str, obligation: bool = False) -> None:
        # First writer wins, so Christmas stays "Christmas" rather than being
        # relabelled by the civil holiday that shares the date.
        out.setdefault(when, Observance(name, obligation))

    # --- Holy Days of obligation (Latin Church, United States) --------------
    # Note the obligation is abrogated when Jan 1 / Aug 15 / Nov 1 falls on a
    # Saturday or Monday. That changes whether EXTRA Masses appear, not whether
    # the day is unusual, so it is recorded on the Observance and does not
    # affect holding.
    for month, day, name in (
        (1, 1, "Mary, Mother of God"),
        (8, 15, "Assumption"),
        (11, 1, "All Saints"),
        (12, 8, "Immaculate Conception"),
        (12, 25, "Christmas"),
    ):
        when = date(year, month, day)
        abrogated = (
            (month, day) in ((1, 1), (8, 15), (11, 1))
            and when.weekday() in (5, 0)  # Saturday or Monday
        )
        add(when, name, obligation=not abrogated)

    # --- Other liturgical days that reshape a weekday ------------------------
    paschal = easter(year)
    add(paschal - timedelta(days=46), "Ash Wednesday")
    add(paschal - timedelta(days=3), "Holy Thursday")
    add(paschal - timedelta(days=2), "Good Friday")
    add(paschal - timedelta(days=1), "Holy Saturday")
    add(paschal + timedelta(days=1), "Easter Monday")
    if not ASCENSION_TRANSFERRED_TO_SUNDAY:
        add(paschal + timedelta(days=39), "Ascension", obligation=True)

    # --- Civil holidays -----------------------------------------------------
    fixed = (
        (date(year, 1, 1), "New Year's Day"),
        (date(year, 6, 19), "Juneteenth"),
        (date(year, 7, 4), "Independence Day"),
        (date(year, 11, 11), "Veterans Day"),
        (date(year, 12, 24), "Christmas Eve"),
        (date(year, 12, 25), "Christmas Day"),
        (date(year, 12, 31), "New Year's Eve"),
    )
    for when, name in fixed:
        add(when, name)
        shifted = _observed(when)
        if shifted is not None:
            add(shifted, f"{name} (observed)")

    for month, weekday, n, name in (
        (1, 0, 3, "Martin Luther King Jr. Day"),
        (2, 0, 3, "Presidents' Day"),
        (5, 0, -1, "Memorial Day"),
        (9, 0, 1, "Labor Day"),
        (10, 0, 2, "Columbus Day"),
        (11, 3, 4, "Thanksgiving"),
    ):
        when = _nth_weekday(year, month, weekday, n)
        add(when, name)
        if name == "Thanksgiving":
            # The Friday after is a closure at essentially every parish office
            # and displaces the daily Mass as reliably as the day itself.
            add(when + timedelta(days=1), "Day after Thanksgiving")

    return out


def displaced_weekdays(start: date, end: date) -> dict[str, str]:
    """Weekday name -> observance, for every holiday inside [start, end].

    The weekday names are `DayOfWeek` values, so a caller can compare them to
    schedule entries directly.
    """
    found: dict[str, str] = {}
    for year in {start.year, end.year}:
        for when, observance in observances(year).items():
            if not (start <= when <= end):
                continue
            if observance.name in NON_DISPLACING:
                continue
            name = _WEEKDAY_NAMES[when.weekday()]
            if name in found:
                found[name] = f"{found[name]}, {observance.name}"
            else:
                found[name] = observance.name
    return found


def obligations_in(start: date, end: date) -> dict[date, str]:
    """Holy Days of obligation inside [start, end], abrogated ones excluded.

    For the inverse check: a covered week containing one of these and no dated
    Mass extracted for it is a *missing* liturgy, which is the expensive error
    on a holiday - every other warning in this pipeline is about something that
    changed.
    """
    return {
        when: o.name
        for year in {start.year, end.year}
        for when, o in observances(year).items()
        if o.obligation and start <= when <= end
    }
