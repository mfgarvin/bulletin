# Schedule stability: a slot ledger, and holidays as evidence suppression

Design note, 2026-09-09. Not implemented. Supersedes the "holiday table +
per-weekday write hold" sketch in `docs/notes/2026-09-05-holiday-week-triage.md`
(that note's diagnosis stands; only the proposed fix is widened here).

## The problem, stated once

Two pressures that look like one:

- **Noise.** Two runs of the same prompt over the same bytes agree on ~83% of
  parishes' recurring Masses (`studies/noise`). So a normal Saturday produces
  ~35-50 recurring-Mass diffs that mean nothing, and every one of them writes.
- **Displacement.** On a holiday week the day-by-day intentions listing has no
  ordinary Mass on weekday *D*, so the v2.5.11 rule (listing beats masthead)
  makes the model retract or replace a standing Mass that is displaced for
  exactly one week. 2026-09-05: 22 of 44 Mass diffs involved Monday, and three
  rows now publish the Labor Day time as their permanent recurring time.

Both are the same structural fact: **the pipeline has no memory.** Notion holds
one value; a run either overwrites it or doesn't. With a single sample there is
no way to tell a flap from a change, so every mechanism built so far
(`verify_changes`, `verify_times`, the retraction guards) is a way of asking one
sample to vouch for itself. The 09-05 triage measured how well that works: both
warning labels are right about half the time.

One-off liturgies are the opposite case and are already handled well — 43
parishes emitted the Labor Day Mass correctly as a **dated** Mass, zero as
recurring. Dated Masses cannot corrupt the recurring schedule and they expire on
their own. They should stay on a fast path, not be slowed by anything below.

## The mechanism: a per-slot ledger with hysteresis

New Notion rich_text property `Schedule History`, written in the same
`save_extraction` call (no extra round trip, ~1.2KB/parish, chunking already
handles it):

```json
{"version": 1,
 "last_source": "…/20260906B.pdf",
 "masses":      {"Monday|0830": {"runs": 14, "missed": 0, "seen": "2026-09-06", "state": "confirmed"},
                 "Monday|0900": {"runs": 1,  "missed": 0, "seen": "2026-09-06", "state": "provisional"}},
 "confessions": {"Saturday|1500": {…}}}
```

Recurring slots only, keyed `day|time`. Each run, for the site paired to the row:

| observation | effect |
|---|---|
| known slot present | `runs += 1`, `missed = 0` |
| known slot absent | `missed += 1` |
| unknown slot present | create `provisional`, `runs = 1` |
| `provisional` and `runs >= CONFIRM_RUNS` (2) | → `confirmed` |
| `provisional` and `missed >= 1` | deleted (it was a flap) |
| `confirmed` and `missed >= RETIRE_RUNS` (2) | → retired, dropped from publication |

**What gets published is the set of `confirmed` slots**, with the entry body
(notes, language) from the most recent observation — not "whatever this week's
extraction said." That is the whole change.

Two guards on what counts as evidence:

- **A repeated bulletin is not a second opinion.** Count an observation only
  when `bulletin_url`/`bulletin_date` differs from `last_source`. Otherwise
  `37345`, sitting on the same newsletter edition for a third week, confirms
  its own gaps. Free side effect: a ledger that stops advancing is a staleness
  detector, which is the one thing `check_freshness.py` can't see per-row.
- **Displaced weekdays contribute nothing** — see below.

**Bootstrapping:** on first run, seed the ledger from the row's stored schedule
as `confirmed` with `runs = CONFIRM_RUNS`. Day one changes nothing; the system
converges instead of resetting.

### What this does to the known cases

| row | today | with the ledger |
|---|---|---|
| `1170` Mon 0900 replacing 0830 | published, permanent | 0900 provisional → never published; 0830 `missed=1` → still published; next week 0900 is deleted |
| `1397` Mon 1200 dropped | gap published | `missed=1` → still published, self-heals |
| `0691`, `st-matthias`, `1101`, `0039`, `0599`, `1794` | same two shapes | same, no human action |
| ~35-50 weekly noise diffs | all write | none write; most are deleted as flaps next week |

Cost: a **genuine** change takes two runs to publish. Two fast paths keep that
tolerable — `utils.notion_fixes` as always, and a one-week promotion when
`verify_times` corroborates the change in the text layer (new time printed near
a weekday name, old time absent). That repurposes the fabrication check from a
weekly warning into a promotion accelerator, which is a better job for it.

## Holidays: suppress the evidence, don't hold the write

> **Status: shipped as the weekday hold in v2.5.26** (`utils/holidays.py` +
> `_splice_held_weekdays`), in the "splice the displaced weekday" form described
> below rather than as ledger evidence-suppression, because the hold turns out
> to *be* the memory: Notion keeps the pre-holiday value, so next week's diff
> already runs against a correct baseline. The ledger below is still the answer
> for the general noise case and for displacement no calendar can predict (a
> parish mission, a funeral, a priest away).

`definitions.py` gains `displaced_weekdays(week_start: date) -> dict[int, str]`
over the seven days a bulletin covers, from two computable sources:

- **Civil.** New Year's, MLK, Presidents, Memorial, Juneteenth, July 4, Labor,
  Thanksgiving *and the Friday after*, Christmas Eve/Day, New Year's Eve.
- **Liturgical.** Holy Days of obligation (Jan 1, Aug 15, Nov 1, Dec 8, Dec 25),
  Ash Wednesday and the Triduum, computed from Easter (Butcher's algorithm, ~10
  lines, no dependency). *Confirm the obligation list against the diocesan
  calendar before shipping:* the Province of Cincinnati transfers Ascension to
  Sunday, and the obligation is abrogated when Jan 1 / Aug 15 / Nov 1 falls on a
  Saturday or Monday — which changes whether extra Masses appear at all.

Rule: **when weekday *D* in the covered week is displaced, observations on
weekday *D* do not touch the ledger** — no `runs`, no `missed`, no new
provisional slots. The week is invisible on that weekday.

This is deliberately stronger than the triage note's "hold the write and warn."
Holding still lets a *repeated* displacement confirm a wrong slot, and the next
two hits both repeat: Thanksgiving (Thu 2026-11-26, plus the Friday) spans one
bulletin, but Christmas (Fri 2026-12-25) and New Year's (Fri 2027-01-01) land in
two consecutive bulletins that will show the same displaced weekday twice.
Hysteresis alone would confirm it. Skipping evidence cannot.

It also collapses the triage list: a diff on a displaced weekday is *expected*,
so it is reported as a count, not 22 warnings.

## One-off liturgies: the release valve, made cheap

Dated Masses (`mass_date` set) bypass the ledger entirely and publish on first
sight. Three additions:

1. **Window guard.** `mass_date` must land in `[bulletin_date - 7,
   bulletin_date + 120]`. Outside → drop and warn. This is `1088`'s
   `2027-09-07` Labor Day Mass, which will otherwise publish for 12 months, and
   the general year-typo class.
2. **Expire from storage**, not only at export. `notion_to_app` already drops
   past dated Masses; leaving them in Notion accretes and skews the retraction
   ratios.
3. **The positive check — warn on what is *missing*.** If the covered week
   contains a Holy Day of obligation or a major civil holiday and the parish
   emitted **no** dated Mass on it, say so. Every warning in the system today
   is about something that changed; for holidays the expensive error is
   absence — a user opening the app on Christmas morning and seeing the ordinary
   weekday schedule.

## Prerequisite: plumb the bulletin's own date

Everything above needs the covered week. Add `bulletin_date: date | None` to
`DownloadResult`:

- **PO / eCatholic** — the probe date that returned 200 *is* the covered Sunday.
  Exact, free, ~109 parishes.
- **Self-Hosted** — `_extract_date(url)` where it parses.
- **Discover Mass / Webpage** — `None`; fall back to the Sunday of the run week.

Second payoff: with `bulletin_date` in hand, `process_parish` can finally warn
when the bulletin it downloaded is weeks old, which CLAUDE.md's Bulletin
Freshness section says nothing does today.

## Triage, ranked by consequence

Replace the flat warning list (79 last run) with three tiers:

1. **Held writes** — partial retraction, corrupt JSON, site match failure.
   Needs a human this week.
2. **Pending state changes** — slots about to be promoted or retired next run.
   One look, and the cheapest possible moment to intervene.
3. **Expected** — displaced-weekday diffs and provisional flaps, as counts.

And spend `REEXTRACT_BUDGET` only on slots at a promotion/retirement boundary
rather than on every diff in arrival order. That is a much smaller set — most
diffs resolve themselves — so the cap stops binding and the re-extraction is
finally being spent on the decisions that matter.

## Rollout

0. **Repair the live rows first** — `0691`, `1170`, `st-matthias` publish wrong
   recurring Masses today; five more have gaps; `1088` has the 2027 date.
1. **Plumb `bulletin_date`.** Independently useful.
2. **Ledger in shadow mode**, one or two runs: write `Schedule History`, publish
   nothing from it, and log what it *would* have published versus what was
   written.
3. **Holiday table + evidence suppression + the dated-Mass window guard.** Safe
   on their own and the deadline is real: Thanksgiving is 2026-11-26 and
   Christmas 2026-12-25, both worse than Labor Day because they displace more
   than one weekday.
4. **Flip publication to the ledger** once shadow mode has 4-6 weeks of data —
   which lands before Advent if step 2 starts this month.
