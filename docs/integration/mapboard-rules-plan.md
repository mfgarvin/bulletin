# Mapboard: reading `Rules` and `Dated Masses` from `parish_data.json`

For the agent working in the mapboard repo. The board's driver is `main.py`
there; bulletin-v2 keeps an older copy as `reference.py`. The scraper side
shipped in v2.5.40. **Nothing changes on the board until this plan is
implemented.** The new keys are additive, and the current driver never reads
them.

## What the board gets wrong today

`parish_data.json` lists slots by weekday only, so every slot lights **every
week**:

| class | slots (2026-10-16) | what the board does now |
|---|---|---|
| monthly ordinal ("First Friday", "3rd Wednesday") | 54 | lights 4-5 weeks a month for 1 |
| monthly exclusion ("except First Fridays") | 1 | stays dark on the 1st Friday, when it should light every other week |
| anchored ("Thursday before First Friday") | 6 | lights every Thursday for 1 a month |
| cancelled this week | 14 | lights for a Mass the parish has called off |
| dated Masses (holidays, one-offs) | 22 on 19 parishes | never lights them |

Christmas is the reason to land this before December. Christmas Eve and Day
are dated Masses, which the board currently never shows.

## The shape

Each parish gets two new keys. The legacy five are **byte-identical** (that
was verified over all 189 parishes):

```json
{
  "ID": 109,
  "Name": "Saint Charles Borromeo Parish",
  "Mass Times":  { "...": "unchanged" },
  "Confessions": { "Saturday": [{"1500": 45}, {"1700": 30}], "Thursday": [{"1500": 45}] },
  "Adoration":   { "...": "unchanged" },

  "Rules": {
    "Confessions": { "Thursday": { "1500": { "anchored_week":
                       { "weekday": "Friday", "weeks_of_month": [1], "offset_days": -1 } } } }
  },
  "Dated Masses": {}
}
```

These are real values (`2492`, 2026-10-16). Elsewhere: Saint Colman has
`"Dated Masses": {"2026-10-18": [1300]}`, and Transfiguration has
`"Rules": {"Mass Times": {"Monday": {"830": {"cancelled_on": ["2026-10-12"]}}, ...}}`.

- `Rules[kind][weekday][start]` uses the same keys as the legacy list:
  `str(time)` for Masses and the dict key for confession/adoration. **A slot
  with no rule occurs every week.** Kinds and weekdays with no rules are left
  out, and `Rules` may be `{}`.
- A rule holds at most one of `weeks_of_month`, `excluded_weeks`,
  `anchored_week`, and it can also carry `cancelled_on`. Semantics for the
  first three are the normative ones in `EXPORT_SHAPE_CHANGES.md`. They are
  derived by the same `derive_ordinal` that feeds `export.json`.
- `cancelled_on` is a list of ISO dates, so **a cancellation expires on its
  own**. That holds however old the board's copy is. With night mode on, the
  copy is at most about a day old, but a dated rule needs no assumptions about
  that.
- `Dated Masses` holds only dates from the export day onward. Times are the
  same `HHMM` ints as `Mass Times`.

## The change

**Vendor `utils/schedule_rules.py` from bulletin-v2 unchanged.** It uses only
the stdlib, has no repo imports and parses as Python 3.7. It exposes:

```python
slots_on(parish: dict, kind: str, day: date) -> list
```

This returns the legacy element shape for that date: ints for Masses,
`{"1500": 45}` dicts for the others. Rules are already applied and dated
Masses are already merged in. It falls back cleanly when `Rules` /
`Dated Masses` are missing (an old cached file), so the board behaves as it
does today.

**1. Read slots by date.** In `thePastor()` there are three reads, one per
activity:

```python
massToday = parishCalendar["Mass Times"][weekday]
for value in parishCalendar["Confessions"][weekday]:
for value in parishCalendar["Adoration"][weekday]:
```

Replace each with `slots_on(parishCalendar, "<kind>", today)`, and replace the
`.get(weekday) is None` / `== {}` guards in front of them with an
empty-list check. The duration parsing that follows needs no change, because
the elements keep their shape. Leave `Is24Hour` alone.

**2. Optional: re-read `parishCalendar` every pass of the loop.** This isn't
needed for correctness on the board as deployed. `dataRefreshWatcher()` calls
`ingest()` daily after 12:00. `thePastor()` binds
`parishCalendar = rawjson[notionID]` when its thread starts, and with night
mode on (as deployed) `wakeUpParish()` restarts every thread each morning. So
Saturday's export (~10am ET) is loaded at noon and in use from Sunday
morning, which is when the week it covers begins.

It would matter if night mode were ever turned off (`enableNightMode = False`
is the repo default). Threads would then never restart, and no refresh would
reach them before a process restart. The change is one line at the top of
each pass through `thePastor()`'s `while` loop:

```python
parishCalendar = rawjson.get(notionID, parishCalendar)
```

`ingest()` replaces the whole object in a single assignment, so a thread sees
either the old file or the new one, never a mix.

Not part of this plan: `setID()` builds `allocation` at startup only, so a
parish newly added to the data or to `leds.json` still needs a restart to get
a thread. That is rare, and it is a separate change.

## Traps

1. **Debug mode uses a weekday name, not a date.** `chronos2()` sets
   `weekday = DEBUG_DAY` while `currentTime` keeps *today's* date, so a date
   computed from `currentTime` disagrees with the weekday being simulated.
   Add a `DEBUG_DATE` and derive `weekday` from it. Otherwise every rule in
   debug mode is evaluated against the wrong day.
2. **Compute the date once per tick, alongside `weekday`**, not per parish
   thread. A thread that reads the date a second after midnight while
   `weekday` is still yesterday's would pair Thursday's slots with Friday's
   rules.
3. **Dated Masses go through the Mass branch**, so their length comes from
   `liturgyDuration`, which is set by weekday (30 min Mon-Fri). A Christmas
   Day Mass on a Friday will light for 30 minutes. That is acceptable, but
   it is a known limitation.
4. **A dated Mass can share a time with a weekly one.** `slots_on` de-dupes,
   so if you merge them yourself, de-dupe too or the loop finds the same slot
   twice.
5. **Call `slots_on` on every pass, not once per thread.** Its result depends
   on the date, so a value cached across midnight would apply yesterday's
   rules. With step 2 done as well, a noon refresh also takes effect without
   waiting for the morning restart.

## Test dates

Run against the committed `parish_data.json`. Every one of these lights today
whether or not it should:

| date | parish | expect |
|---|---|---|
| 2026-12-31 (Thu) | Saint Charles Borromeo, confession 1500 | **lit**: the Thursday before First Friday of *January 2027* |
| 2027-01-07 (Thu) | same | dark: the 1st Thursday, not the one before the 1st Friday |
| 2026-04-30 / 2026-05-07 | same | lit / dark (May begins on a Friday) |
| 2026-10-18 (Sun) | Saint Colman | Masses 900, 1100 **and 1300** (dated) |
| 2026-10-25 (Sun) | Saint Colman | 900, 1100 only |
| 2026-10-12 (Mon) | Transfiguration, 830 | dark (cancelled_on) |
| 2026-10-19 (Mon) | Transfiguration, 830 | lit: the cancellation has expired |

The anchored rules in the export were checked in bulletin-v2 against an
independently computed date for each of the 72 months 2026-2031, and all 6
match. Port those cases as unit tests rather than re-deriving them.
