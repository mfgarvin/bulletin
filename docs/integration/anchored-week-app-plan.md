# App plan — implement `anchored_week`

**For:** an agent working in `~/Code/massgpt_app_o1_preview` (Introibo).
**Scraper side:** done (bulletin-v2 v2.5.32). The export emits the key already.
**Authoritative shape:** the `anchored_week` section of
`../bulletin-v2/EXPORT_SHAPE_CHANGES.md`. Read it first; this file is the work
plan, that file is the contract.

## Why

Six confession slots in the diocese read **"the Thursday before the First
Friday"** — the devotional confession before a First Friday, and at `0116`
before a First Saturday. They are monthly, but they are **not** an ordinal
weekday of the month, so `weeks_of_month` cannot express them. Until now they
were exported as plain weekly entries, so the app advertises a confession 52
times a year for something that happens 12.

They are not cancellations and must not be rendered as such. Nothing is being
suspended; the slot simply falls on a date the ordinal vocabulary can't name.

## The rule

```json
"anchored_week": { "weekday": "Friday", "weeks_of_month": [1], "offset_days": -1 }
```

Read as: **the slot falls `offset_days` from the 1st Friday of the month.**
Resolve the anchor date first, then step by the offset.

Affected entries in today's export — all `confession`, all Thursday:

| parish | start | anchor | offset |
|---|---|---|---|
| `1794` | 15:30 | Friday | −1 |
| `0116` | 18:30 | **Saturday** | **−2** |
| `sc-p` | 19:00 | Friday | −1 |
| `2492` | 15:00 | Friday | −1 |
| `sa-o`  | 20:00 | Friday | −1 |
| `1318` | 08:00 | Friday | −1 |

## The good news: one method is the whole integration

`lib/utils/schedule_parser.dart` already routes **every** recurrence decision
through `ScheduleEntry.occursOn()` — `findNextOccurrence` (line ~325), the
bounded 400-day scan (line ~361), the availability sweeps in
`filtered_parish_list_page.dart`. The moment `occursOn` understands the anchor,
"what's next", "soonest" and "on now" are all correct with no further work.

`occursOn` today computes `n` and `isLast` from `day`, then calls
`listed(weeks)`. **The anchored case is the identical math on a different
date.** Extract the ordinal test into a helper that takes a `DateTime`, then
call it with either `day` or the resolved anchor:

```dart
final target = anchoredWeek == null
    ? day
    : day.subtract(Duration(days: anchoredWeek!.offsetDays));
if (anchoredWeek != null && target.weekday != anchoredWeek!.weekday) return false;
// n / isLast / listed(...) unchanged, computed on `target`
```

`DateTime` arithmetic rolls over month and year ends by itself, so the
previous-month case needs no special code — see *Traps*.

## Changes, all in `lib/utils/schedule_parser.dart`

1. **An `AnchoredWeek` value class** — `int weekday` (ISO 1–7),
   `List<int> weeksOfMonth`, `int offsetDays`. Plus a
   `final AnchoredWeek? anchoredWeek` field on `ScheduleEntry`, documented in
   the same voice as `weeksOfMonth` (the existing doc comments are the house
   style; match them).
2. **`isMonthly`** (line ~117) → `|| anchoredWeek != null`. This is what lights
   up grouping and the chip downstream, so do it before anything else.
3. **`occursOn`** (line ~123) → as above.
4. **`recurrenceKey`** (line ~148) → add a branch, e.g.
   `'a${weekday}:${offsetDays}:${weeksOfMonth.join(',')}'`. Required, not
   cosmetic: `mass_schedule_card.dart:466` builds its grouping key from it, and
   without a distinct value an anchored entry can merge into a weekly row and
   render as if it happened every week.
5. **`fromJson`** (line ~222) → parse the object.
   **`_parseWeeks` is reusable verbatim** — the nested `weeks_of_month` has the
   identical `1..5, -1` domain. Use the existing `_dayMap` for the weekday.
   Emit the field only when all three parts are valid.
6. **`ordinalShortLabel` / `ordinalDescription`** → see the decision below.

Nothing outside this file should need to change.

## The one design decision

`ordinalShortLabel` returns **"1st"** for `weeks_of_month: [1]`. Do **not**
reuse that here — this slot is not the 1st Thursday, and in a Friday-start
month its date is not even in that month. The chip is ~64px so "Bef 1st Fri"
will not fit.

**Recommended:** emit **"Monthly"** for the chip — honest, fits, claims nothing
false — and give `ordinalDescription` the full sentence, *"The Thursday before
the 1st Friday of the month"*, for anywhere with room. `notes` already carries
the prose on every one of these entries, guaranteed by the exporter, so the
chip is not the only thing telling the user.

If you disagree, say so rather than inventing a third option silently.

## Traps

1. **Do not approximate as `weeks_of_month: [1]`.** That was considered and
   rejected on the server side. Over 2026–2031 it names the wrong date in 10 of
   72 months (14%) for a Friday anchor and 28% for `0116`'s Saturday anchor —
   and it fails in the expensive direction, sending someone to an empty church
   a week late.
2. **Do not clamp the anchor to the month.** When a month begins on the anchor
   weekday, the slot lands in the *previous* month, and in January the previous
   *year*. `DateTime` handles this; hand-rolled day arithmetic does not.
3. **Do not route this through `cancelled`.** It shares none of that path — no
   `cancelled_badge.dart`, no `TextDecoration.lineThrough`, no ` CANCELLED`
   span, no dimmed ink. An anchored entry is a normal entry that occurs less
   often, and in its occurring week it is a completely ordinary confession.
4. **Parse defensively**, exactly as `_parseWeeks` does. Missing field,
   `offset_days` of `0` or outside `−6…6`, unparseable weekday, empty week list
   → discard the whole object and treat the entry as **weekly**. That is
   today's behaviour, so a cached export predating this change is unaffected.

## Tests

`test/schedule_parser_test.dart` has a `monthly-ordinal recurrence` group at
line ~437. Add a sibling group and follow its conventions.

**The case that earns its keep is 2027-01** — it rolls back across a month
*and* a year boundary:

| month | true date, Friday anchor | naive "1st Thursday" would say |
|---|---|---|
| 2026-09 | 2026-09-03 | same |
| 2026-12 | 2026-12-03 | same |
| **2027-01** | **2026-12-31** | 2027-01-07 ✗ |
| 2027-05 | 2027-05-06 | same |

And for `0116`'s Saturday anchor, **2027-05** is the one that breaks: true
**2027-04-29**, naive 2027-05-06.

Cover:

- `fromJson` builds the field; malformed variants (bad weekday, `offset_days:
  0`, offset out of range, empty/absent week list, not-an-object) all fall back
  to weekly.
- `occursOn` true on the true date and false on every other Thursday of that
  month, for 2026-09 through 2027-08 on both anchors.
- The **invariant worth asserting directly: exactly one occurrence per calendar
  month, never zero and never two.** Scan a year of days and count. This is the
  property that catches an off-by-one in the offset, which date-by-date
  assertions can miss.
- `occursOn` false on a non-Thursday.
- `findNextOccurrence` from a date in an off week returns the next true date
  and does not skip the entry — this is the behaviour the whole change is for.
- `recurrenceKey` differs from a weekly entry's and from a `weeks_of_month: [1]`
  entry's.

## Done when

`flutter test` passes, and a parish page for `2492` shows its Thursday 15:00
confession in the week containing the Thursday before the first Friday and not
in the other weeks — with the Saturday 15:00 and 17:00 slots unaffected in
every week.
