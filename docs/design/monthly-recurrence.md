# Monthly-ordinal schedules ("First Friday") — design

**Status:** planned, not implemented. Written 2026-08-24.

## The problem

60 schedule slots across ~40 parishes recur *monthly on an ordinal weekday* —
"First Friday", "Last Sunday", "2nd Tuesday" — and every one of them is stored
as happening **every** week, because nothing in the schema can say otherwise.

| shape | slots | ordinal-of-month? |
|---|---|---|
| First Friday | 24 | yes |
| other stated ordinal ("1st Thursday", "Last Sunday", "2nd Tuesday") | 16 | yes |
| First Saturday | 6 | yes |
| First other-weekday | 6 | yes |
| **"Thursday before the First Friday"** | 8 | **no** — see below |

Split by field: 20 confessions, 13 adoration, 11 Masses, and the rest picked up
by the wider re-scan.

**What is actually broken.** The published `notes` do say "First Friday of the
month", so a human reading the entry is not misled. What is wrong is everything
that *computes*: the LED mapboard lights these every Friday, and any "on now" /
"what's next" logic reads them as weekly. This is a correctness-of-logic bug
first and a display bug second — which is also why it blocks the "what's on
soonest / what's on at this parish today" feature.

## Why `mass_date` is the wrong tool

`mass_date` is the existing one-off mechanism, and it is materialized and
self-expiring: the bulletin states "Christmas, Dec 25", export drops it once
past. That is right for a fact with an expiry, and wrong here:

1. `ConfessionTime` and `AdorationTime` **have no date field at all**, and 33 of
   the 60 slots live there.
2. Christmas is a date; First Friday is a rule with no end. Materializing it in
   storage means regenerating dates forever.
3. Self-expiry inverts from feature to hazard. A dated row deletes itself when
   it passes, so a stalled weekly run would make First Friday slots silently
   *vanish* rather than merely go stale. In a project whose signature failure is
   "healthy-looking run, stale data", delete-on-stall is the wrong default.

`ParishEvent` already models this correctly — `frequency: EventFrequency` with a
`FIRST_FRIDAY` member, plus `event_date` for one-offs and `day_of_week`/`time`
for recurring. Events got both halves; the schedule models only ever got the
date half. This is extending an existing concept, not inventing one.

## Design

### Storage: keep the rule

One nullable field on `MassTime`, `ConfessionTime`, `AdorationTime`:

```python
weeks_of_month: list[int] | None = Field(
    default=None,
    description="Ordinal weeks this slot occurs. None = every week. "
                "1-5 for the Nth weekday of the month, -1 for the last.",
)
```

`None` means every week, so **every existing row is unchanged and the default is
backward compatible**. `[1]` is First Friday; `[-1]` is Last Sunday; `[2, 4]` is
2nd and 4th. A list rather than a scalar because "2nd and 4th Saturday" is real
and already in the data.

### Export: materialize a horizon

The app and mapboard should not implement calendar arithmetic, and "what's on
soonest" needs dates, not rules. So storage keeps the rule and **export resolves
it into concrete dates over a rolling horizon** (propose 90 days):

```json
{"day": "Friday", "start": "17:30", "weeks_of_month": [1],
 "occurrences": ["2026-09-04", "2026-10-02", "2026-11-06"]}
```

This gives both consumers what they need with no new logic: "on today" is a
membership test, "soonest" is a min. It also degrades safely — a consumer that
ignores `occurrences` and reads `weeks_of_month` still gets it right, and one
that ignores both sees today's behaviour.

Weekly slots (`weeks_of_month: null`) do **not** get `occurrences`; emitting 13
dates for every ordinary Sunday Mass would bloat the export for no gain.

## The LLM question — don't ask it

The obvious implementation is a new structured field for the model to fill, and
that is the wrong call: it adds an output to get wrong every week, on a
model whose recent behaviour has not earned that trust, and a regression would
be invisible until someone re-audited.

**It isn't necessary.** The extractor already writes the ordinal into `notes`
("First Friday of the month") and has done so reliably for a long time — that is
how all 60 slots were found. So derive the field **deterministically in
`utils/sanitize.py`** from text that already exists.

Measured against all 189 stored rows (prototype parser, 2026-08-24):

| | |
|---|---|
| parsed | **50 / 60** |
| refused (correctly, no guess) | 10 |
| false positives | 2 — both fixable, see below |

The 8 "Thursday before First Friday" notes are refused by an explicit anchored-
phrase check rather than mis-parsed, and a slot on Saturday whose note mentions
"first Wednesday" is correctly *not* given the Wednesday ordinal.

This inverts the risk profile in the way that matters: parser failures are
inspectable, reproducible, and fixed once in code, where an LLM field re-rolls
its mistakes every run.

### Two false positives the parser must handle

Both were found in the prototype and are the reason this needs a real test
corpus, not a spot check.

1. **Negation.** `0882` St. Colman: `"Weekday Mass (except on First Fridays)"`
   parsed as `[1]` — exactly inverted. It is the Mass held every Friday *but*
   the first. Needs an exclusion check (`except`, `other than`, `no ... on`),
   and arguably an `excluded_weeks` counterpart; until then such a note must be
   **refused and flagged**, never guessed.
2. **Two subjects in one note.** Our Lady of Victory:
   `"Reconciliation is at Our Lady of Victory on the 2nd and 4th Saturdays; at
   Saint Matthew ..."` merged both parishes' ordinals into `[1,2,3,4]`. A note
   naming more than one ordinal group, or another parish, must be refused.

Rule: **refuse and flag rather than guess.** A slot left weekly is the status
quo; a slot given the wrong ordinal is a new, confident, wrong answer.

### Prompt change: secondary, and additive only

Ask the model to *keep* stating the ordinal in `notes` (it already does) so the
parser keeps its input. Populating `weeks_of_month` directly can be added later
as a cross-check against the parser — agreeing is a useful signal, and
disagreement is a flag — but the parser stays the source of truth.

## The anchored case — leave it alone

8 slots read "the Thursday before the First Friday" (the First Friday devotion:
confessions the evening before). That is **not** an ordinal of the month — when
the first Friday is the 1st or 2nd, the Thursday before it falls in the
*previous* month. Approximating it as "first Thursday" would be wrong roughly a
third of the year.

Options, in preference order:

1. **Leave as note-only** (recommended for v1). Status quo for 8 slots; the note
   already reads correctly to a human.
2. Add an `anchor` concept (`{"before": "first_friday"}`) later, once the
   ordinal path is proven.

Do not fold these into `weeks_of_month`.

## Rollout

Storage and export changes are backward compatible, so the repos can ship
independently and in any order — but the parser should land and be audited
before the export starts emitting `occurrences`.

1. **Schema + sanitizer** (this repo). Add the field; add
   `_derive_weeks_of_month()` with the negation and multi-subject guards; flag
   every refusal. No export change yet. Verify by replaying all 189 rows and
   reading every derived value by hand — 60 slots is small enough to check
   individually, and this is the step where a wrong ordinal would go unnoticed.
2. **Backfill** via `python -m utils.notion_fixes --apply` (the sanitizer replay
   path already does this; no `ManualFix` entries needed).
3. **Export** (`utils/notion_to_app.py`, `utils/notion_to_json.py`). Emit
   `weeks_of_month` and the resolved `occurrences`. Update
   `EXPORT_SHAPE_CHANGES.md` — this is the consumer contract.
4. **Mapboard** (`reference.py` is the local copy; the mapboard repo owns it).
   Stop lighting a monthly slot every week.
5. **Flutter app.** Use `occurrences` for "on today" / "what's next"; render the
   ordinal in the schedule view.

## Validation

Judge this by a **targeted signature**, not an aggregate — the v2.5.5 lesson.
The metric is: *of the 60 known monthly slots, how many carry the correct
`weeks_of_month`, hand-verified against the bulletin?* Target is 50+ derived, 0
wrong, remainder refused-and-flagged. Aggregate schedule-stability metrics will
not move and will not tell you anything.

Also re-run the ordinal scan after the first live extraction under the new
prompt, to confirm the model did not stop writing the ordinal into `notes` — the
parser has no input if it does, and that failure would be silent.

## Out of scope

- Seasonal recurrence (Lent-only adoration). Handled in v2.5.10 by dropping it;
  a `season` field is a separate design.
- `excluded_weeks` (the St. Colman inverse). Refuse-and-flag for now.
- Anchored recurrences ("the Thursday before the First Friday").
