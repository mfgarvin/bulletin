"""What week does this bulletin cover, and is it current?

`DownloadResult.bulletin_date` is the first thing in this pipeline that knows
when a bulletin is *for*. Two things need it:

- **Freshness.** CLAUDE.md's standing complaint is that nothing compares the
  bulletin's date to today, so a parish stuck on a months-old PDF extracts
  cleanly, saves cleanly, and reports "No Issues" - the row looks healthier than
  one that honestly failed. Four instances so far, every one found by accident.
  `staleness_warning()` is the first automatic check.
- **The covered week**, which the holiday calendar needs in order to ask "does
  this bulletin's week contain a civil holiday or a Holy Day?"

Both must keep a *read* date and an *inferred* one distinguishable, so nothing
here invents a date: `bulletin_date` stays None for Discover Mass (opaque
tokens) and Webpage (no file), and a caller that needs a week anyway calls
`run_week()` explicitly.
"""

from datetime import date, timedelta
from typing import Optional

# US parish bulletins are named for the Sunday they cover and run Sunday
# through Saturday.
WEEK_LENGTH = 7

# A weekly bulletin is at most ~6 days old when a Saturday run picks up the
# previous Sunday's file, and is often dated *tomorrow* (the v2.5.6 lookahead).
# Past 14 days a parish has missed two uploads, or - far more likely, and the
# reason this exists - the ranking picked a stale file.
STALE_BULLETIN_DAYS = 14

# Monthly bulletins are legitimately up to a month old, so the weekly threshold
# would flag them every single week. Keyed by ParishID.
MONTHLY_BULLETIN_PARISHES: dict[str, int] = {
    "hs-gh": 45,  # Holy Spirit, Garfield Heights - one issue per month
}


# How far ahead of its own bulletin a dated Mass may legitimately sit. Measured
# over all 75 dated Masses in the database (2026-09-05 snapshot): 71 fall within
# -7..+14 days, three more inside +60, and exactly one lies beyond - `1088`'s
# Labor Day Mass dated **2027**-09-07, 367 days out, which would have published
# for twelve months. So this window drops one entry across 189 rows and that
# entry is the bug. 120 leaves roughly double the headroom over the furthest
# real one; an Advent bulletin advertising Christmas is about 26 days out.
DATED_MASS_HORIZON_DAYS = 120

# A bulletin can name a Mass a few days before the Sunday it covers (the week's
# listing usually opens on the preceding Saturday). The observed minimum is -3.
DATED_MASS_BACKSTOP_DAYS = 7


def implausible_dated_masses(
    sites, bulletin_date: Optional[date]
) -> list[tuple[object, str]]:
    """Dated Masses too far from their own bulletin to be that bulletin's.

    Returns `(mass, reason)` pairs. Empty when the source could not date the
    bulletin - no date, no claim, the same rule the rest of this module follows.

    A year typo is the failure this exists for, and it is unusually expensive:
    a dated Mass is published until its date passes, so a Mass mis-dated into
    next year advertises itself for twelve months, while every other dated-Mass
    error expires within the week. That asymmetry is why these are dropped
    rather than flagged - the project flags what only a bulletin can settle, and
    a Mass 367 days from its own bulletin is not one of those.
    """
    if bulletin_date is None:
        return []
    found = []
    for site in sites:
        for mass in site.mass_times:
            if mass.mass_date is None:
                continue
            offset = (mass.mass_date - bulletin_date).days
            if -DATED_MASS_BACKSTOP_DAYS <= offset <= DATED_MASS_HORIZON_DAYS:
                continue
            found.append((
                mass,
                f"dated Mass {mass.day.value} {mass.time:04d} on "
                f"{mass.mass_date.isoformat()} is {offset} days from this "
                f"bulletin ({bulletin_date.isoformat()}), outside "
                f"[-{DATED_MASS_BACKSTOP_DAYS}, +{DATED_MASS_HORIZON_DAYS}] - "
                f"almost always a year typo, and a dated Mass publishes until "
                f"its date passes. Dropped"
                + (f" (note: {mass.notes})" if mass.notes else "")
            ))
    return found


def week_of(day: date) -> tuple[date, date]:
    """The Sunday-through-Saturday week containing `day`, as (start, end)."""
    start = day - timedelta(days=(day.weekday() + 1) % 7)
    return start, start + timedelta(days=WEEK_LENGTH - 1)


def run_week(today: Optional[date] = None) -> tuple[date, date]:
    """The week a run should assume when the bulletin's own date is unknown.

    The *coming* Sunday's week, matching what the Saturday job downloads: the
    v2.5.6 lookahead means a Saturday run normally gets tomorrow's bulletin, so
    the week under discussion starts tomorrow, not six days ago.
    """
    today = today or date.today()
    if today.weekday() == 5:  # Saturday - the scheduled run's own day
        return week_of(today + timedelta(days=1))
    return week_of(today)


def covered_week(
    bulletin_date: Optional[date], today: Optional[date] = None
) -> tuple[tuple[date, date], bool]:
    """The week this bulletin covers, and whether that was read or assumed.

    Returns ((start, end), exact). `exact` is False when the source could not
    date the bulletin and the run's own week was substituted - callers that
    care (anything about to *hold* a write) should weigh the two differently.
    """
    if bulletin_date is None:
        return run_week(today), False
    return week_of(bulletin_date), True


def staleness_warning(
    bulletin_date: Optional[date], parish_id: str, today: Optional[date] = None
) -> Optional[str]:
    """Warn when the downloaded bulletin is too old to be the current one.

    None when the bulletin is current, or when the source couldn't date it -
    silence here means "no claim", never "fine". Discover Mass and Webpage rows
    are unjudgeable this way and always will be; their freshness has to be
    checked by comparing URLs across runs.
    """
    if bulletin_date is None:
        return None
    today = today or date.today()
    age = (today - bulletin_date).days
    limit = MONTHLY_BULLETIN_PARISHES.get(parish_id, STALE_BULLETIN_DAYS)
    if age <= limit:
        return None
    return (
        f"Bulletin is {age} days old (dated {bulletin_date.isoformat()}, limit "
        f"{limit}) - the run succeeded but the schedule and events extracted "
        f"from it are stale. Check that the source is still ranking the current "
        f"file to the top."
    )
