# `export.json` Shape Changes — for the Introibo App

This is a **breaking** change to `export.json`. The scraper now emits fully
structured schedules instead of display strings. The app no longer needs
`schedule_parser.dart` (or any regex parsing of schedule lines) — every field
arrives pre-parsed.

## Old shape (deprecated)

```json
{
  "name": "St. Sebastian Parish",
  "parish_id": "0689",
  "address": "476 Mull Ave",
  "city": "Akron, OH",
  "zip_code": "44320",
  "phone": "330-836-2233",
  "website": "https://www.stsebastian.org",
  "lonlat": "-81.5446,41.0780",
  "bulletin_url": "...",
  "timestamp": "2026-05-27",
  "mass_times":  ["Sunday: 9:00AM, 11:00AM (Spanish)", "Saturday: 4:30PM - Vigil Mass"],
  "confessions": ["Saturday: 3:00PM to 3:30PM"],
  "adoration":   ["Perpetual Adoration (24/7)"],
  "events_summary": "..."
}
```

## New shape

```json
{
  "name": "St. Sebastian Parish",
  "parish_id": "0689",
  "address": "476 Mull Ave",
  "city": "Akron, OH",
  "zip_code": "44320",
  "phone": "330-836-2233",
  "website": "https://www.stsebastian.org",
  "latitude": 41.0780,
  "longitude": -81.5446,
  "bulletin_url": "...",
  "timestamp": "2026-05-27",
  "invite_feedback": false,
  "schedules": {
    "mass": [
      {"day": "Sunday",   "start": "09:00", "mass_date": null,         "language": null,      "notes": null},
      {"day": "Sunday",   "start": "11:00", "mass_date": null,         "language": "Spanish", "notes": null},
      {"day": "Saturday", "start": "16:30", "mass_date": null,         "language": null,      "notes": "Vigil Mass"},
      {"day": "Wednesday","start": "16:00", "mass_date": "2025-12-24", "language": null,      "notes": "Christmas Eve Vigil"},
      {"day": "Wednesday","start": "00:00", "mass_date": "2025-12-25", "language": null,      "notes": "Midnight Mass"}
    ],
    "confession": [
      {"day": "Saturday", "start": "15:00", "end": "15:30", "notes": null}
    ],
    "adoration": {
      "is_perpetual": true,
      "times": []
    },
  },
  "events_summary": "..."
}
```

## What changed

| Old | New | Notes |
|---|---|---|
| `mass_times: string[]` | `schedules.mass: object[]` | Each entry has `day`, `start` (HH:MM), `mass_date`, `language`, `notes` |
| `confessions: string[]` | `schedules.confession: object[]` | `day`, `start`, `end`, `notes` |
| `adoration: string[]` | `schedules.adoration: {is_perpetual, times}` | `times: [{day, start, end, notes}]`. `is_perpetual: true` means 24/7; render once, don't enumerate `times` (will be empty) |
| `lonlat: "lon,lat"` | `latitude: float`, `longitude: float` | Both null when no coords; the old comma-string is gone |

## Field-by-field

### `schedules.mass`

- **`day`**: full English weekday (`"Sunday"` … `"Saturday"`)
- **`start`**: `"HH:MM"` 24-hour, zero-padded (`"09:00"`, `"16:30"`)
- **`mass_date`**: `null` for regular weekly Mass; ISO `"YYYY-MM-DD"` for holiday / one-off Masses (Christmas, Easter, Holy Days, etc.)
- **`language`**: null = English; otherwise free-text (`"Spanish"`, `"Latin"`, `"Vietnamese"`, etc.)
- **`notes`**: optional free text (`"Vigil Mass"`, `"Christmas Eve"`, `"First Friday only"`)

Entries are pre-sorted: by `mass_date` (regular masses first, holidays after), then by weekday, then by start time.

### Holiday / dated Mass handling

Regular weekly Mass → `mass_date: null`. Always show.

Dated Mass → `mass_date: "YYYY-MM-DD"`. Show only when the date is upcoming
(suggested window: today through +7 days). Dated Masses whose date has already
passed are now dropped at export time, so the app will not receive last
December's Christmas schedule from a parish that hasn't re-scraped. The
`day` field still reflects the day of week the date falls on, so a Mass on
Christmas Day 2025 has `day: "Thursday"` and `mass_date: "2025-12-25"`.

This is the headline reason for the rewrite — dated Masses were silently
dropped by the old string formatter.

**Privacy filter (server-side).** Dated Masses whose `notes` match
private-event keywords (`wedding`, `funeral`, `nuptial`, `rehearsal`, with
word boundaries) are removed from `export.json` by the scraper. The app
should treat every remaining `mass_date` entry as publicly listable.
"Memorial Day Mass", "Easter Vigil", "First Communion Mass", parish patron
feasts etc. all pass through — only personal sacraments tied to specific
families are stripped. If a wedding ever appears in the export, that's a
scraper bug, not an app filtering responsibility.

### `schedules.confession` and `schedules.adoration.times`

Same shape: `{day, start, end, end_next_day, notes}`. Both `start` and `end`
are `"HH:MM"`.

**`end_next_day`** (bool) marks a slot that runs past midnight. `day` is always
the day the slot *starts*. An overnight adoration slot is:

```json
{"day": "Friday", "start": "22:00", "end": "06:00", "end_next_day": true, "notes": null}
```

and a slot that runs until midnight is `"end": "00:00"` with
`end_next_day: true`. Do not infer this from `end < start` — read the flag. It
is always present, and is `false` for ordinary same-day slots.

Slots with no stated time are omitted entirely rather than emitted as
`00:00–00:00`, so an all-zero slot no longer means "time unknown".

### `schedules.adoration`

```json
{ "is_perpetual": false, "times": [{"day": "Tuesday", "start": "08:30", "end": "19:40", "notes": null}] }
```

Or for perpetual:

```json
{ "is_perpetual": true, "times": [] }
```

If `is_perpetual: true`, render a single "Perpetual Adoration" card and skip
the times array (it'll be empty). Otherwise enumerate `times` like confessions.

### Monthly-ordinal recurrence — `weeks_of_month` / `excluded_weeks`

**Status: specified and frozen; emitted since v2.5.17 (2026-08-30) by
`utils/monthly_recurrence.py` at export time — 58 entries derived, 14 refused,
hand-reviewed.** Additive — no existing key
changes. This section is normative and **supersedes the `occurrences` proposal
in `docs/design/monthly-recurrence.md`**, which is withdrawn (see *Why no
resolved dates* below).

60 slots across ~40 parishes recur on an **ordinal weekday of the month** —
"First Friday", "Last Sunday", "2nd and 4th Saturday" — and today every one is
exported as recurring *every* week. The `notes` text already says the ordinal,
so a human reading the card is not misled; everything that **computes** — "on
now", "what's next", the LED mapboard — is wrong.

Two optional keys carry the rule. They may appear on **any** schedule entry:
`schedules.mass[]`, `schedules.confession[]`, and `schedules.adoration.times[]`.

```json
{
  "day": "Friday",
  "start": "17:30",
  "end": "18:15",
  "end_next_day": false,
  "notes": "First Friday of the month",

  "weeks_of_month": [1]
}
```

```json
{
  "day": "Friday",
  "start": "08:15",
  "notes": "Weekday Mass (except on First Fridays)",

  "excluded_weeks": [1]
}
```

#### Semantics

| key | type | meaning |
|---|---|---|
| `weeks_of_month` | `int[]` or absent | The slot occurs **only** in these ordinal weeks |
| `excluded_weeks` | `int[]` or absent | The slot occurs **every** week **except** these |

- **Absent means every week.** The keys are emitted **only when non-null**, so
  every entry in today's export keeps exactly its current meaning and byte-level
  shape. Absent, `null`, and `[]` are all "weekly" — the app must not
  distinguish them.
- **Value domain: `1`–`5`, and `-1` for last.** `1` = the first such weekday of
  the month, `-1` = the last. Values are sorted ascending and de-duplicated
  (`-1` sorts first). Nothing else is ever emitted; anything else is malformed
  and must be discarded.
- **`5` and `-1` are different.** A 5th Friday exists in only some months, and
  when it doesn't the slot simply doesn't occur that month. `-1` always exists,
  and in a 5-Friday month it is the 5th, not the 4th. Both are real in the data.
- **The two keys are mutually exclusive.** Never both non-null on one entry. If
  a consumer ever sees both, `weeks_of_month` wins.
- **Mutually exclusive with `mass_date`.** A dated one-off is a date, not a
  rule; it never carries either key.
- **`notes` keeps stating the ordinal.** That is a guarantee, not a
  coincidence: the scraper *derives* these fields from the note text, so the
  human-readable form can't silently diverge from the machine-readable one, and
  a consumer that ignores both keys still renders something truthful.

#### Coverage — refuse rather than guess

The ordinal is derived deterministically by the scraper from `notes`, not asked
of the LLM (a parser bug is fixed once in code; an LLM field re-rolls its
mistakes every week). Prototype coverage over all stored rows: **50 of 60
derived, 10 refused, 0 wrong.**

**No entry will ever carry a *wrong* ordinal, but not every monthly slot will
carry one.** The residue stays weekly-with-a-note, which is today's behaviour.
The known refusals:

- **8 slots read "the Thursday before the First Friday."** That is genuinely
  not an ordinal of the month — when the first Friday falls on the 1st or 2nd,
  the Thursday before it is in the *previous* month. Approximating it as "first
  Thursday" would be wrong about a third of the year. These stay `null`
  deliberately; do not special-case them in the app.
- Notes naming two subjects (one bulletin listing two parishes' ordinals) are
  refused rather than merged.

#### Why no resolved dates

An earlier draft also emitted `occurrences` — a rolling 90-day array of
resolved ISO dates — so the app would need no calendar arithmetic. It is
dropped for three reasons:

1. **It goes stale, in the direction that matters.** The app is offline-first
   and can be showing a cached export of arbitrary age. Past the horizon the
   array empties, and the fallback is a choice between "renders weekly again"
   (the bug being fixed) and "never happens" (hiding a real Mass). A rule has no
   horizon.
2. **Two representations of one fact can disagree**, and nothing said which
   wins.
3. **`export.json` is committed on every rebuild and diffed as a freshness
   audit** (see *Auditing freshness* in `CLAUDE.md`). Rolling date arrays would
   rewrite themselves for 60 entries every Saturday whether or not anything real
   changed, adding permanent noise to that signal.

The arithmetic this pushes to the consumer is ten lines, has no timezone or
locale dimension, and is fully unit-testable with no data dependency.

#### Consumer contract

The whole rule reduces to one predicate — *does this entry occur on this
calendar day?* Given a `DateTime day` already known to be the right weekday:

```dart
// Which ordinal weekday-of-month is this date? 1st..5th.
final n = ((day.day - 1) ~/ 7) + 1;
// Is it the last one of its weekday in this month?
final daysInMonth = DateTime(day.year, day.month + 1, 0).day;
final isLast = day.day + 7 > daysInMonth;

if (weeksOfMonth != null) {
  return weeksOfMonth.contains(n) || (weeksOfMonth.contains(-1) && isLast);
}
if (excludedWeeks != null) {
  return !(excludedWeeks.contains(n) || (excludedWeeks.contains(-1) && isLast));
}
return true; // weekly
```

Required behaviour:

1. **Parse defensively.** Not a list, or empty after discarding non-integers and
   out-of-domain values, → treat as weekly. A cached export predating this
   change must behave exactly as it does today.
2. **Route every recurrence decision through the one predicate.** Any place
   that answers "is it on today" or "when is it next" from `day` alone is wrong
   for these entries. Rolling forward to the next occurrence means scanning
   candidate days rather than adding `7`; a `[5]`-only slot can be ~3 months
   out, so bound the scan (400 days is safe) and fall back to weekly if the scan
   finds nothing.
3. **Don't collapse a monthly entry with a weekly one.** Any UI that groups
   entries sharing a time and note into a single multi-day row must include the
   ordinal in its grouping key, or "First Friday 5:30pm" and a weekly "Tuesday
   5:30pm" merge into one row that reads as both being weekly.
4. **The weekday filter is fine unchanged.** A First Friday entry does occur on
   Fridays; it is the *date* questions ("today", "tomorrow", "this week",
   "soonest") that need the predicate.

### `latitude` / `longitude`

Plain floats. Either may be `null` if the parish has no geocoded address.
The old `lonlat` comma-string is removed.

Coordinates outside Ohio's bounding box (lat 38.4–42.3, lon −84.8…−80.5) are
rejected at export time and emitted as `null` — every parish in this database
is in the Diocese of Cleveland, so an out-of-range value is a typo (a dropped
decimal point, usually), not a distant parish. The app should keep handling
`null` coords rather than trusting whatever arrives.

### `invite_feedback`

Boolean, always present. `true` means this parish's schedule was **never
machine-verified from a bulletin** — either it's hand-maintained static info
(no bulletin exists to scrape) or the scraper can't read the parish's website
(JS-heavy pages, Google Drive-hosted PDFs).

These are the records where a churchgoer looking at the screen knows more than
we do, so the app should surface a "are these times right? let us know"
affordance on them. It's currently `true` for 13 of 189 parishes.

`false` means the times came from a parsed bulletin on the `timestamp` shown.
That's not a correctness guarantee, so feedback is still welcome everywhere —
this flag is about where to *encourage* it.

Derived from the `Issues` status in Notion (`Manual` or `Unsupported`); the app
never sees the underlying status.

### Unchanged

- `name`, `parish_id`, `address`, `city`, `zip_code`, `phone`, `website`,
  `bulletin_url`, `timestamp`, `events_summary` all unchanged.

## Migration steps for Introibo

1. Update `Parish.fromJson` in `lib/models/parish.dart`:
   - Read `latitude` / `longitude` as nullable doubles; drop `lonlat` parsing.
   - Read `schedules.mass`, `schedules.confession`, `schedules.adoration` as
     structured lists/objects instead of `List<String>`.
2. Delete `lib/utils/schedule_parser.dart` and its callers — all data arrives pre-parsed.
3. Add a filter in the Mass schedule view: hide entries whose `mass_date` is
   non-null and in the past; visually flag entries whose `mass_date` is non-null
   and within the next 7 days as "Special / Holiday Mass" using the `notes` text.
4. Adoration view: branch on `is_perpetual` before rendering.
5. Drop the legacy `www` key tolerance — the scraper has only emitted
   `website` for a while.
6. Read `invite_feedback` and show a feedback prompt on parishes where it's
   `true`.

## Not yet addressed (future work)

These were suggestions in the original notes that are deferred:

- **Per-section timestamps** (`timestamps.mass_times`, `timestamps.confessions`, …) — would require diff-tracking in the Notion sync layer. Not done.
- **Structured `events` list** (separate from `events_summary` prose) — the underlying extraction already produces structured events; they're just not exported yet. Easy follow-up if the app wants them.
- **PDF-vs-extracted discrepancy detection** — out of scope.
