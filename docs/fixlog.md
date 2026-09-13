# Fixlog

One row per repair applied after a Saturday run, kept so the *shape* of the
repairs can be read across weeks rather than one run at a time. The changelog in
CLAUDE.md records what was built; this records what kept breaking.

**Why a separate file.** Each week's triage answers "is this row right?" — a
question about one parish. The question that decides what to build next is "what
keeps being wrong?", and nothing in the repo could answer it: `MANUAL_FIXES`
shows only what is *currently* stated (retired entries vanish), the run logs are
in Actions and age out, and `notion_snapshot.json` records values without
reasons. A repair's reason is the thing worth keeping and the thing nothing kept.

## How to append

After triaging a run, add a row per repaired field to the table below and
recount the tallies. Keep the categories stable — a new one is only worth
opening when a cause genuinely doesn't fit, since the point is comparability.
One row per **field** on a parish, not per slot: `0342` losing three of St.
Jerome's Masses in one write is one row.

`resolution` says which mechanism absorbed it, and is the column that matters
most, because it separates *fixed* from *patched*:

| resolution | meaning |
|---|---|
| `structural` | a rule now prevents it — `definitions.py`, sanitizer, a guard |
| `manual` | a `MANUAL_FIXES` entry; inert once the extractor agrees |
| `treadmill` | a `MANUAL_FIXES` entry the extractor is expected to undo |
| `held` | a guard declined the write; nothing repaired, nothing lost |
| `deferred` | known wrong, deliberately not repaired — reason in the notes |

## Categories

| category | what it means |
|---|---|
| `cluster-bleed` | another parish's or site's schedule published on this row |
| `one-week-cancellation` | a standing slot retracted because the listing suspended it for one week |
| `holiday-displacement` | `one-week-cancellation` where a public holiday is the cause, so the calendar can see it coming |
| `dated-as-recurring` | a one-off or seasonal event published weekly |
| `monthly-as-weekly` | a genuine monthly-ordinal slot published weekly |
| `layout-slip` | the right document, the wrong cell — a time taken from an adjacent row or column |
| `fabrication` | a time that appears nowhere in the document |
| `bulletin-typo` | the source is wrong and the extractor transcribed it faithfully |
| `masthead-incomplete` | the standing schedule box omits a Mass the rest of the bulletin evidences |
| `note-defect` | times correct, published `notes` wrong |
| `publisher-broken` | the bulletin could not be fetched, or the wrong one was |

---

## 2026-09-12

154 parishes, 152 succeeded, 44 fields written. 13 rows repaired, 4 left
deliberately, 2 download failures. Bulletin freshness clean (135 on the
2026-09-13 edition, 11 on 09-06, 1 monthly).

| parish | field | change applied | category | cause | resolution |
|---|---|---|---|---|---|
| `0342` | Mass | dropped Sun 0900, Wed 0830, Fri 0830 | cluster-bleed | both worship sites named "… (Holy Redeemer Roman Catholic Parish)", so the `SINGLE_SITE_PARISHES` name filter scored them equally and merged St. Jerome's schedule onto Holy Redeemer's address | structural |
| `st-matthew-akron-oh` | Mass | dropped Sun/Tue/Thu 0830 | cluster-bleed | Our Lady of Victory's Masses copied inline; the note naming OLV is present in some runs and absent in others | treadmill |
| `1905` | Conf | replaced with Mon 1650–1720 | cluster-bleed | St. Mary of the Oaks's Friday 12:45 confession put on St. Patrick's row; the bulletin site-codes every line (`SMO` / `STP`) | manual |
| `visitation-of-mary-parish-akron-oh` | Conf | dropped Thu 1700 | cluster-bleed | St. John the Baptist's pre-Mass confession; the `-sjb` row already holds it | manual |
| `our-lady-help-of-christians-litchfield-oh` | Conf | cleared | cluster-bleed | Lodi's slot, note says so itself; **third recurrence** of an existing entry | treadmill |
| `olhc-lodi` | Mass | Sat 0830 → Sun 0830 | layout-slip | masthead lists Sunday Masses by site; Lodi's 8:30 landed on Saturday, leaving the row with no Sunday Mass | manual |
| `0414` | Mass | Sun 0800 → 0830 | layout-slip | times print in a column under the day labels; picked up the weekday 8:00 one line down | manual |
| `1494` | Conf | Sat 1600 → 1700 | layout-slip | "after 4:00pm Mass" placed *at* 16:00, on top of the Mass it follows | manual |
| `1060` | Conf | Wed 1800 → Sat 1500 | dated-as-recurring | a dated "Family Friendly Holy Hour" replaced the standing Saturday confession | manual |
| `0242` | Mass | dropped Wed 0830 | dated-as-recurring | a one-week 08:30 substituting for the 12:15 promoted to weekly | manual |
| `visitation-…-sjb` | Mass | dropped Sun 1400 | fabrication | in no block of the bulletin; its own note read "2026 schedule (see specific dates)" | manual |
| `visitation-of-mary-parish-akron-oh` | Mass | dropped Sun 1200 | fabrication | only noon in the document is a guild meeting | manual |
| `0882` | Mass | dropped Fri 1830 | fabrication | no 6:30 of any kind in the document; caught by the v2.5.14 check | manual |
| `0582` | Conf | cleared (4 slots) | fabrication | image-only bulletin; the whole Confessions entry is "Please ask a priest before or after Mass", anchored to times the parish doesn't use. **Third recurrence** | treadmill |
| `1532` | Mass | dropped Fri 2100 | bulletin-typo | masthead prints "Friday \| 9:00pm" among 9:00am weekdays; the body says 9AM. Known since v2.5.19, still uncorrected at source | treadmill |
| `20822` | Mass | added Sat 1700 | masthead-incomplete | English vigil absent from the masthead *and* this week's listing, but the offering table counts 42 people at it | manual |
| `1687` | Mass | note relabelled | note-defect | `_merge_notes` welding a generic masthead label to a specific weekly one. **Second recurrence** | treadmill |
| `shc`, `shc-pat` | Mass | — | one-week-cancellation | "Father Trask will be away the next two weeks"; listing prints 4 standing times each marked NO MASS, all retracted. Both rows left publishing weekends only | deferred |
| `0134` | Mass | — | one-week-cancellation | masthead Mon/Tue/Fri 7:30; listing "TUESDAY 9/15 — 7:30 AM No Morning Mass" | deferred |
| `1831` | Mass | — | one-week-cancellation | masthead Mon–Fri 8:30; listing "Thursday, September 17 — No Mass" | deferred |
| `st-jerome-cleveland-oh` | Mass | — | one-week-cancellation | extraction dropped Wed/Fri/Sat 0830; partial-retraction guard declined the write | held |
| `our-lady-of-angels-cleveland-oh` | Conf | — | — | dropped 2 of 3 stored slots; partial-retraction guard declined the write | held |
| `sh-n` -> `40500` | all | row re-keyed and re-run | publisher-broken | misclassified as `Webpage`. The site dropped `.html` paths *and* moved the schedule off that page; it was really **eCatholic site 40500** the whole time. Fixed by renaming the ParishID to `40500` and setting the publisher to eCatholic, since `ECatholicSource` builds its path from the ParishID. 3 weeks stale when found; nothing in the pipeline raised it — a human noticed | structural |
| `scas-e` | — | — | publisher-broken | empty "Request error" from Actions; page returns 200 from a residential connection | deferred |

Deliberately **not** repaired: the four `one-week-cancellation` rows. `add_masses`
restores a Mass forever, so using it against a suspension that is real right now
would advertise Masses nobody is celebrating — for two weeks at Oberlin. All
four self-heal when the listing prints the Mass again.

Also retired this run: the whole 2026-09-05 Labor Day block (15 entries, every
one re-extracted correctly off a normal week), and `st-matthew-akron-oh`'s
treadmill, which `site_label` made unnecessary within the same afternoon.

### Shipped in response (v2.5.30)

| trend | what shipped | still open |
|---|---|---|
| `one-week-cancellation` | `cancelled` on the schedule models and in `export.json`; `utils/cancellations.py` sets it from the page. Replay: 5 restorations over the 13 rows that lost a Mass, exactly the 5 correct ones, 0 false | `1831`'s shape - its listing prints "No Mass" with **no time beside it**, so there is nothing to key on. Needs the ledger |
| `cluster-bleed` | `site_label` - the printed location tag, transcribed verbatim - matched by `SITE_EXCLUSIONS` alongside notes | the prompt change has had no wide study; 11 cached bulletins is not 50 x 5 |
| budget spent in arrival order | `LARGE_DIFF_RESERVE`: 15 of the 40 re-extractions drawable only by diffs of 3+ slots | not a true global sort; concurrency makes that a restructure |
| labels unreliable alone | the conjunction now prefixes `LIKELY WRONG WRITE - ` | — |

Two categories got nothing and are the next candidates: `layout-slip` (3 rows -
a time taken from an adjacent column or row, which `site_label` does not
address) and `monthly-as-weekly`, which is still export-time only, so the
mapboard renders those slots weekly and a removal like `1094`'s First Friday
loses the Mass entirely rather than demoting it.

## 2026-09-05 (backfilled)

Labor Day. 62 of 152 parishes (41%) warned against a predicted 35–50, and the
excess was one class. Reconstructed from the retired `MANUAL_FIXES` block and
`docs/notes/2026-09-09-labor-day-monday-sweep.md`; 20 Mondays changed, 15 wrongly.

| parish | field | change applied | category | cause | resolution |
|---|---|---|---|---|---|
| `0691`, `1170`, `st-matthias-…` | Mass | Mon 0900 → 0800 / 0830 / 0830 | holiday-displacement | *replaced* — the holiday time published as the recurring one. Does not self-heal | manual |
| `0036`, `0039`, `0599`, `1101`, `1286`, `1397`, `1733`, `1794`, `st-mary-painesville-oh` | Mass | Monday restored | holiday-displacement | *dropped* — listing had no ordinary Monday Mass | manual |
| `5493` | Mass | restored Mon 1130 | holiday-displacement | *dropped*, second-Mass variant — the row kept a Monday and read as healthy | manual |
| `0244` | Mass | dropped Mon 1100 | holiday-displacement | *invented* — a Monday Mass on a day the parish has none | manual |
| `1088` | Mass | dropped the 2027-09-07 entry | fabrication | year typo; a dated Mass publishes until its date passes, so it would have advertised for 12 months | structural (v2.5.27 window guard) |

---

## Trends

Two runs is not a trend line, and the 09-05 row set is a holiday week, so it is
not a fair sample of an ordinary one. What it can already say:

**1. One cause dominates both runs, and the holiday was incidental to it.**
15 rows on 09-05 and 6 on 09-12 are the same mechanism: the v2.5.11 rule (the
day-by-day intentions listing beats the standing schedule box) meeting a week in
which the listing is *not* representative. Labor Day made it visible; priest
absence, a feast substitution and a school Mass produced it again with no holiday
anywhere near. **`utils/holidays.py` can only ever see the calendar-visible
subset**, so the holiday hold shipped in v2.5.26 is a partial fix by
construction, not by tuning — which is the argument for the per-slot ledger in
`docs/design/schedule-stability.md` moving ahead of anything else queued.

Worth noting the cheap partial nobody has built: in 3 of the 4 rows left deferred
this week, the bulletin **prints the time and the words "NO MASS" on the same
line**. A listing entry naming a time next to a cancellation is a one-week
suspension of a standing slot, and it is deterministic to detect. The sanitizer
already has `_CANCELLED_RE` for the *opposite* problem (a cancellation emitted as
a Mass); it has nothing for this one, and the schema is why — see trend 3.

**2. Cluster bleed is the second cause and it is not converging.** 5 rows this
week, and 3 of the 5 are **recurrences of entries that already exist**
(`olhc-litchfield`, `0582`, and `st-matthew` within the same afternoon). Every
mechanism built for it so far keys on something the model rewrites each run — a
site name, or a note. `0342` is the encouraging counter-example: its site name
reliably contains "jerome", so a `SITE_EXCLUSIONS` rule closed it for good and
that row is marked `structural`. **The rule that predicts which is which: bleed
is fixable structurally when the discriminator is in the bulletin's own layout
(a site heading, a site code like `SMO`/`STP`), and a treadmill when it is only
in prose the model paraphrases.**

**3. Two open defects are schema gaps, not extraction gaps.** `one-week-
cancellation` has no encoding — `MassTime` offers "every week" (`mass_date` null)
or "one specific day", and there is no way to say *"weekly, except this week"*.
So a correct extractor has only two legal outputs for "Monday 8:45 — NO MASS" and
both lose the slot. `monthly-as-weekly` is the same shape one level up and was
answered at export time (v2.5.17) rather than in the schema, which is why the
mapboard still renders those slots weekly. Neither is reachable by prompt work.

**4. Nothing surfaces a row that has been failing for weeks.** `sh-n` sat in
`Error` for three consecutive Saturdays, publishing a schedule that was also
*wrong* (Mon-Fri 08:00 against the parish's actual Mon-Thu 07:00), and the only
reason it came up was a human reading the failure list. `Error` setting
`invite_feedback` (v2.5.25) tells the *app*; it tells nobody who can fix it.
`check-freshness.yml` already owns a webhook and an independent cron.

**5. The verification labels earned their keep this week, when read as a pair.**
Every repair keyed on a *changed* value carried at least one flag, and where both
fired — "still printed in bulletin" **and** "not reproduced" — the run was wrong
every time (`1494`, `1060`, `0414`). Neither label alone was reliable: `0342`'s
three "not printed in bulletin — suspicious" additions were printed verbatim and
correct. **Both labels agreeing is the signal; either alone is a prompt to look.**

### Recount each run

| | 09-05 | 09-12 |
|---|---|---|
| fields written | ~44 Mass diffs | 44 |
| rows repaired | 15 | 13 |
| `structural` | 1 | 1 |
| `treadmill` | 0 | 5 |
| `deferred` | 0 | 6 |
| `held` by a guard | 0 | 2 |
