# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Notes / Pending Work

- **~40-50 parishes need self-hosted setup** - These parishes either don't have bulletins on the major publishers or self-host on their own websites. They need `Bulletin Page URL` configured in Notion.
- **Address discrepancies file** - `address_discrepancies.txt` contains 9 parishes with missing or incorrect addresses in Notion (verified 2026-01-06). Not committed to git.
- **Self-Hosted scraper enhancement** - *Done for the subpage case in v2.5.12*
  (`_find_pdf_in_subpages`), which unblocked `sfds-a`. Still open: **`sfa-gm`**
  (bulletin lives in Google Drive - not a PDF link at all) and **`sak-cle`**
  (dated subpages in Korean, so the slug carries no parseable date and the
  subpage ranking has nothing to sort on).
- **Two known gaps in the v2.5.11 sanitizer** (found 2026-08-29, not fixed):
  1. `_SEASONAL_ADORATION_RE` matches liturgical *names* only, so a one-off
     identified by a bare date in the note — `(May 4, 2026)`, `(April 8)`,
     `(listed Jan 21)` — still publishes weekly. **22 slots across ~17
     parishes**, mostly adoration. Not all are wrong: `scas-e`'s "Daily morning
     Mass (starting week of Aug 2)" is a genuine recurring entry with a start
     date, so this needs the same refuse-rather-than-guess handling.
  2. `_META_NOTE_RE` enumerates the subject word (`day|date|time|year|start|
     end`), so "location not specified in bulletin" and "exact hours not listed
     in bulletin" survive (3 slots). Generalise to `\w+`.
- **Monthly-ordinal schedules ("First Friday")** - 60 slots across ~40 parishes
  recur on an ordinal weekday but are stored as weekly, so anything that
  *computes* (the mapboard, "what's on today/soonest") treats them as every
  week. **The export shape is specified and frozen** in the
  `weeks_of_month` / `excluded_weeks` section of `EXPORT_SHAPE_CHANGES.md`, and
  the app is being built against it - that section is normative and must not
  change. `docs/design/monthly-recurrence.md` is SUPERSEDED (its `occurrences`
  array was withdrawn); keep it for the problem statement only. **The
  export.json side is implemented (v2.5.17): `utils/monthly_recurrence.py`
  derives the ordinal from `notes` at export time** — 58 derived, 14 refused,
  0 wrong, no new LLM output. Still open: the mapboard (`notion_to_json` /
  `parish_data.json`) does not carry the rule yet, so the LED board still
  renders these slots weekly.
- **Workflow action versions** (bumped 2026-08-29, `ce6a7f4`) - All three
  workflows moved from `actions/checkout@v4` / `actions/setup-python@v5` to
  `@v5` / `@v6`. Runners had begun force-running the old pins on Node 24
  because Node 20 is deprecated; that was a warning, but it becomes a failure
  of *all three workflows at once* whenever the forcing stops. Both majors are
  Node 24 bumps and nothing else, and `cache: 'pip'` restored from the same key
  afterwards. Verified by a dispatched `export-data.yml` run (deprecation
  warnings 1 -> 0); `gh-actions.yml` was not dispatched, since a full run costs
  ~12 min of OpenAI spend and would select no parishes anyway — it uses the
  same two steps. **`checkout@v7` and `setup-python@v7` already exist**
  (2026-07-20); v5/v6 clear the deprecation, so this is a treadmill, not a
  finished job.
- **Open from the 2026-08-29 warning triage** (v2.5.18) - three items left
  deliberately unfixed. (1) `1485` publishes a **monthly** reconciliation
  service as weekly; its note says "Monthly Reconciliation" with no ordinal, so
  the ordinal parser refuses it - confirm the ordinal with the parish and state
  it, or teach the extractor to read the calendar date. Its 2-hour span flag is
  a standing false positive, the second known one after St. Brendan's (`0290`);
  a `definitions.py` exemption set is the agreed fix if it gets noisy. (2)
  `ss-c`'s Sunday confession note "Before 9:30am Mass" is fabricated (Sunday
  Masses are 8:00 and 11:00) and `notes` is published text. (3) `0512`'s
  adoration is Prince of Peace's, on St. Andrew's row - harmless only because
  `UPDATE_ADORATION = False`.
- **GitHub integration** - Integrate Claude with GitHub for automated workflows or issue tracking.
- **Data change safety** - Add safeguards for when extracted data changes significantly (e.g., mass times suddenly very different). Could warn or require confirmation before overwriting.
- **Adoration in Events** - Sometimes adoration schedule appears in the Events listing instead of the dedicated Adoration field. May need extraction prompt adjustment or post-processing.

## Project Overview

Bulletin-V2 is a Python 3.12 async tool that extracts Catholic parish information from church bulletins. It downloads PDF bulletins from multiple sources, sends them directly to GPT-4o for structured extraction, then syncs data to a Notion database.

## Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Run against all enabled parishes with stale data (>7 days old)
python main.py --all

# Run against specific parish by ID
python main.py 1234

# Dry run (download and extract, but don't save to DB)
python main.py --dry-run --all

# Use Marker OCR instead of direct PDF (requires marker-pdf)
python main.py --method marker_ocr --all
```

**CLI flags:** `--all` process all stale parishes, `--dry-run` skip DB save, `--method` extraction method, `--stale-days N` staleness threshold, `-v` verbose

**Export utilities:**
```bash
# App-facing export -> export.json (12hr times, weekday groupings)
python -m utils.notion_to_app

# Mapboard export -> parish_data.json (keyed by Notion page ID, durations)
python -m utils.notion_to_json

# Raw property dump -> notion_snapshot.json (the only restorable archive)
python -m utils.notion_snapshot
python -m utils.notion_snapshot --restore notion_snapshot.json          # dry run
python -m utils.notion_snapshot --restore notion_snapshot.json --apply

# Repair stored Notion data (dry run, then apply)
python -m utils.notion_fixes
python -m utils.notion_fixes --apply
```

`utils/notion_fixes.py` replays the sanitizer over schedules already stored in
Notion and applies a table of per-record manual corrections. It writes directly,
bypassing the `UPDATE_*` locks in `database/notion.py` — that's the point of it,
since `UPDATE_ADORATION = False` means a normal run never rewrites adoration.
Idempotent and dry-run by default.

## Architecture

**Data flow:** Download content (PDF or webpage) → GPT structured extraction → Notion DB

**Core modules:**
- `main.py` - Async CLI entrypoint
- `schemas.py` - All Pydantic models (single source of truth)
- `extractor.py` - PDF → LLM extraction (single call for all data types)
- `definitions.py` - Explicit site-to-parish mappings for multi-site bulletins
- `sources/` - Bulletin download abstraction (Parishes Online, Discover Mass, eCatholic, Self-Hosted, Webpage)
- `database/` - Database abstraction (Notion implementation, easy to swap)
- `utils/retry.py` - Async retry with exponential backoff
- `utils/log_context.py` - Parish context for concurrent logging
- `utils/notion_to_app.py` - App-facing export -> `export.json`
- `utils/notion_to_json.py` - Mapboard export -> `parish_data.json`
- `utils/notion_snapshot.py` - Raw Notion dump -> `notion_snapshot.json`, and
  the restore path back into Notion

**Key design decisions:**
- Single LLM call extracts everything (mass, confession, adoration, events, parish info, events summary)
- Direct PDF to GPT-5.2 (no separate OCR step)
- Database abstraction layer for future flexibility
- Async throughout for better performance

**Notion database properties:**
- `Name` (title) - Parish name
- `ParishID` (rich_text) - Unique identifier
- `Enable` (checkbox) - Whether to process this parish
- `Bulletin Publisher` (select) - Source: "Parishes Online", "Discover Mass", "eCatholic", "Self-Hosted", "Webpage", "Other"
- `Bulletin Page URL` (url) - For self-hosted bulletins: URL of page containing the PDF link
- `Bulletin Group ID` (rich_text) - Links parishes sharing a bulletin (see Multi-Site Support)
- `GPT Timestamp` (rich_text) - Last extraction date
- `Mass Times` (rich_text) - JSON array of mass times
- `Confessions` (rich_text) - JSON array of confession times
- `Adoration` (rich_text) - JSON object with adoration schedule
- `Events` (rich_text) - JSON array of parish events
- `Events Summary` (rich_text) - Human-readable summary of events
- `GPT Logs` (rich_text) - Extraction log
- `Link to latest bulletin` (url) - URL of processed bulletin
- `Street Address`, `City`, `Zip Code`, `Phone Number`, `Website` (rich_text) - Parish contact info
- `LonLat` (rich_text) - Longitude,latitude coordinates for mapping
- `Issues` (status) - Issue tracking: "No Issues", "Warning", or "Error"
- `Issue Log` (rich_text) - Details of errors/warnings from last run

## Issue Tracking

The system automatically tracks processing issues in Notion:

**Status values (`Issues` field):**

Set by the pipeline:
- **No Issues** - Set on successful extraction (clears previous issues)
- **Warning** - Extraction succeeded but with warnings (e.g., no mass times found, unmatched sites)
- **Error** - Processing failed (e.g., download error, unsupported publisher)

Set by hand, and **never overwritten by a run** (see `PROTECTED_STATUSES` in
`database/notion.py`):
- **Manual** - Data is hand-maintained; no bulletin exists to scrape
- **Unsupported** - The scraper can't read this parish's site (JS-heavy page,
  Google Drive, etc.)

A run against a protected parish still writes its `Issue Log`, so the detail is
kept — only the status is left alone. Both protected statuses set
`invite_feedback: true` in `export.json`, which tells the app to encourage users
to report the real times.

**Issue Log field** contains details:
```
ERROR: Download failed: 404 Not Found
WARNING: No mass times found - may indicate extraction issue
WARNING: No match for site 'Unknown Chapel'
```

**End-of-run summary** prints all failures and warnings to the console for visibility.

**Filtering in Notion:** Use the `Issues` status to quickly find parishes needing attention.

## Multi-Site Support

Some parishes have multiple worship sites sharing one bulletin, or two parishes may share a single bulletin. The system handles this by:

1. **Extracting per-site data**: The LLM extracts each worship site separately with its own mass times, confessions, and adoration schedule
2. **Matching sites to database entries**: Sites are matched using explicit mappings in `definitions.py`
3. **Saving to multiple rows**: Each site's data is saved to its corresponding database entry

**Setup for multi-site parishes:**

1. Create a Notion entry for each worship site with its own address (for mapping)
2. Set `Bulletin Group ID` on all entries to the primary parish's `ParishID`
3. Add a mapping to `SITE_MAPPINGS` in `definitions.py`

**Notion setup:**

| ParishID | Name | Bulletin Group ID |
|----------|------|-------------------|
| parish-main | St. Mary Main Church | parish-main |
| parish-chapel | Holy Family Chapel | parish-main |

- **Primary site**: `Bulletin Group ID` equals own `ParishID` (or leave empty)
- **Secondary sites**: `Bulletin Group ID` equals primary's `ParishID`

**definitions.py setup:**

```python
SITE_MAPPINGS: dict[str, dict[str, str]] = {
    "parish-main": {
        "st mary": "parish-main",
        "holy family": "parish-chapel",
    },
}
```

- Key is the `bulletin_group_id` (primary parish's ID)
- Patterns are matched case-insensitively against extracted site names
- First matching pattern wins

**How it works:**
- Secondary sites are skipped during processing (they get data when the primary is processed)
- The bulletin is downloaded once from the primary site
- Extracted sites are matched to parishes using `SITE_MAPPINGS`
- Each site's schedules are saved to the correct database row

**Example** (Our Lady Help of Christians, 4 worship sites):

Notion:

| ParishID | Name | Bulletin Group ID |
|----------|------|-------------------|
| our-lady-help-of-christians-litchfield-oh | Our Lady Help of Christians - Litchfield | our-lady-help-of-christians-litchfield-oh |
| olhc-lodi | Our Lady Help of Christians - Lodi | our-lady-help-of-christians-litchfield-oh |
| olhc-nova | Our Lady Help of Christians - Nova | our-lady-help-of-christians-litchfield-oh |
| olhc-seville | Our Lady Help of Christians - Seville | our-lady-help-of-christians-litchfield-oh |

definitions.py:

```python
"our-lady-help-of-christians-litchfield-oh": {
    "litchfield": "our-lady-help-of-christians-litchfield-oh",
    "lodi": "olhc-lodi",
    "nova": "olhc-nova",
    "seville": "olhc-seville",
},
```

## Single-Site Override

Sometimes the LLM incorrectly extracts multiple "sites" from a bulletin that should be treated as a single parish (e.g., it splits the main church from an adoration chapel, or misinterprets section headers as separate locations).

To force a parish to always be treated as single-site, add its `ParishID` to `SINGLE_SITE_PARISHES` in `definitions.py`:

```python
SINGLE_SITE_PARISHES: set[str] = {
    "ss-c",
    "5493",
    "1285",
}
```

**How it works:**
- After extraction, if the parish is in this set and multiple sites were extracted, all data is merged into one site
- Mass times, confessions, and adoration schedules from all extracted sites are combined
- Address info is taken from the first extracted site
- The merged site uses the parish name from Notion

## Site Exclusions

Sometimes a bulletin lists a worship site that belongs to a **different
parish's row** — a neighbouring parish, or an oratory that has since acquired
its own bulletin. Merging it in publishes that parish's Masses under the wrong
name and address.

`SINGLE_SITE_PARISHES` cannot express this. Its name filter keeps only the
best-scoring sites, so it also discards *legitimate* secondary sites whose
names don't share distinctive words with the parish name. Add the parish to
`SITE_EXCLUSIONS` in `definitions.py` instead:

```python
SITE_EXCLUSIONS: dict[str, list[dict]] = {
    "1259": [
        {
            "match": "oratory of the immaculate conception",
            "unless": ("chapel", "temporary", "weekday", "renovation"),
            "note_match": "immaculate conception",
            "note_unless": ("solemnity", "feast", "holy day", "holyday"),
        },
    ],
}
```

- Keyed by `ParishID`; patterns match case-insensitively as substrings of the
  extracted site name (`match`), unless a guard term (`unless`) also matches —
  the model has been seen welding the excluded site's name onto the parish's
  own unnamed chapel
- `note_match`/`note_unless` (optional) extend the exclusion to **inline
  Masses**: a *recurring* Mass on a kept site whose `notes` match is dropped.
  This catches the other half of the leak, where the model never emits the
  other parish as a site but copies its Mass into this parish's list with the
  name in the note ("Sunday Vigil at Immaculate Conception"). Notes need their
  own pattern and guard: they rarely spell the full site name, and they mention
  the parish's own chapel incidentally. Dated Masses are never touched — a
  Holy Day note naming the excluded parish's *feast* is this parish's own Mass
- Applied in `collapse_sites()` **before** either collapse branch, so an
  excluded site can never be merged in
- Ignored if it would drop *every* site — saving an empty schedule over a good
  one is worse than the bleed; likewise a note rule matching every Mass on a
  site is ignored (isolated bleed is one or two entries, not a whole schedule)

**Worked example (the Cathedral).** `1259`'s bulletin lists three sites: the
Cathedral, its temporary weekday chapel during renovations, and the Oratory of
the Immaculate Conception. The first two are one parish and must merge — 10 of
its 15 Masses are the chapel's. The third is `immat-con-cle`, its own parish
with its own ICKSP bulletin that already publishes the same Saturday vigil.
`SINGLE_SITE_PARISHES` would have fixed the leak by dropping the chapel and
losing every weekday Mass; the exclusion drops only the Oratory.

Choosing between the three mechanisms:

| situation | mechanism |
|---|---|
| one bulletin, many rows that should each get data | `Bulletin Group ID` + `SITE_MAPPINGS` |
| bulletin lists other parishes; only this row matters | `SINGLE_SITE_PARISHES` |
| bulletin lists another parish's site, but this row has real secondary sites too | `SITE_EXCLUSIONS` |

## Environment Variables

Required in `.env`:
- `OPENAI_API_KEY` - GPT-4o API key
- `NOTION_API_KEY` - Notion API token
- `PARISH_DB_ID` - Notion database ID

## Field Update Controls

To prevent overwriting existing contact info in Notion, edit the flags at the top of `database/notion.py`:

```python
UPDATE_NAME = True      # Parish name
UPDATE_ADDRESS = True   # Street address
UPDATE_CITY = True      # City
UPDATE_ZIPCODE = True   # Zip code
UPDATE_PHONE = True     # Phone number
UPDATE_WEBSITE = True   # Website URL
```

Set any to `False` to preserve existing values. Useful when Notion has manually-corrected data that shouldn't be overwritten by extraction.

## Bulletin Sources

Five publisher types with different URL patterns:
1. **Parishes Online (PO):** `container.parishesonline.com/bulletins/14/{id}/{date}B.pdf`
2. **Discover Mass (DM):** Scraped from `discovermass.com/church/{id}`
3. **eCatholic (EC):** `files.ecatholic.com/{id}/bulletins/{date}.pdf`
4. **Self-Hosted (SH):** Generic scraper for parish websites - finds and downloads PDF links
5. **Webpage (WP):** Extracts bulletin content directly from HTML pages (no PDF)

**Self-Hosted Setup:**
1. Set `Bulletin Publisher` to "Self-Hosted"
2. Set `Bulletin Page URL` to the page containing the bulletin PDF link
3. The scraper finds PDF links on the page and ranks them — see
   [Bulletin Freshness](#bulletin-freshness) for how, and for the failure mode
   that ranking keeps producing

**Webpage Setup:**
1. Set `Bulletin Publisher` to "Webpage"
2. Set `Bulletin Page URL` to the page containing bulletin content (mass times, events, etc.)
3. The scraper extracts the main content, converts HTML to markdown, and sends to the LLM
4. For blog listing pages, the scraper auto-follows "Continue Reading" / "Read More" links to get full content

**JS-Heavy Sites (Wix, Squarespace, etc.):**
The Webpage source only works with static HTML. Sites that load content via JavaScript (Wix, Squarespace, some WordPress themes) will return empty or minimal content. To identify JS-heavy sites: if "View Page Source" shows little text but the rendered page has lots of content, it's JS-rendered.

Options for JS-heavy sites:
1. **Find a PDF link** - Many JS sites still host PDF bulletins; use Self-Hosted if you can find the direct PDF URL
2. **Use a different page** - Sometimes `/mass-times` or similar pages are static even if the homepage isn't
3. **Add headless browser support** - Would require adding Playwright/Selenium to render JS (not currently implemented due to complexity and resource requirements)

## Bulletin Freshness

**A successful run means "we downloaded *a* bulletin", never "we downloaded the
current one."** Nothing in the pipeline compares the bulletin's date to today.
A parish stuck on a months-old PDF extracts cleanly, saves cleanly, sets
`Issues` to "No Issues", and stamps `GPT Timestamp` with today's date. The row
looks healthier than one that honestly failed.

This has now bitten four times in three different ways, and every instance was
found by accident — never by the pipeline:

| found | parishes | cause | fixed in |
|---|---|---|---|
| 2026-07-26 | `olp-cle`, `olg-m` | keyword score outranked recency; a "Christmas Bulletin 2022" beat the live PDF | `03949b9` |
| 2026-08-10 | **108 of 109** PO/eCatholic | date walk only looked backwards; the Sunday-dated file is posted days early | v2.5.6 |
| 2026-08-10 | `sp-l` (456 days stale), `ss-c` | filename dialects the date parser couldn't read, so ranking fell back to keywords | v2.5.6 |
| 2026-08-10 | `hs-gh` (caught pre-merge) | a widened month-name pattern read a year as a day, ranking July above August | v2.5.6 |

Assume there is a fifth. When touching any of this, the question to ask is not
"does it download?" but "is what it downloaded the newest thing the site has?"

### How each source decides

- **Parishes Online / eCatholic** — construct `YYYYMMDD` filenames and probe.
  The walk runs from `LOOKAHEAD_DAYS` (3) in the future back to `LOOKBACK_DAYS`
  (30) ago, **newest first**, taking the first HTTP 200. The lookahead exists
  because the filename is the Sunday the bulletin *covers* and parishes upload
  days early; the Saturday cron would otherwise never see it. Don't raise it to
  7 — a parish posting a fortnight ahead could pull a mid-week run onto the
  following Sunday's file and skip a week of events. Note these hosts answer
  **403, not 404**, for a file that doesn't exist; only a 200 counts.
- **Discover Mass** — scrapes the parish page for its current-bulletin link.
  The URL is an opaque token with no date in it, so freshness can't be checked
  from the URL; compare tokens across runs instead (they rotate weekly).
- **Self-Hosted** — ranks every `.pdf` link on the page. See below.
- **Webpage** — no PDF; freshness is whatever the page currently renders.

### The Self-Hosted ranking model

Score = keyword score + recency bonus, sorted by that, then by parsed date.

- Keywords (`_score_link`): `.pdf` +10, bulletin-ish word in the href +20 or
  link text +15, any date-shaped run of digits +25, "current"/"latest"/"this
  week" +30, `archive`/`past`/`old` −20.
- Recency (`_recency_bonus`): ≤30 days old +100, ≤120 +60, ≤400 +25, older +5,
  up to 14 days *future* +90, further future or unparseable **0**.

Recency is deliberately larger than any keyword so a dated current bulletin
beats a stale file merely named "bulletin" — but it is only a bonus, so undated
`CurrentBulletin.pdf`-style links still win when nothing on the page parses.

**This is why an unparseable filename is dangerous rather than merely
unhelpful.** A date the parser can't read scores 0 recency, which is the same
score as a date it reads as implausibly far in the future. Either way the
current bulletin loses to any stale sibling that happens to parse — which is
exactly what happened to `sp-l` and `ss-c`. A parsing gap doesn't degrade the
pick; it inverts it.

### Filename dialects the parser handles

Real examples, all currently live. `_extract_date_raw()` tries these in order;
`_parse_numeric_triple()` covers the separated-numeric family.

| filename | reading | parishes |
|---|---|---|
| `20260809.pdf`, `20260726B-compressed.pdf` | `YYYYMMDD` | PO/EC, `st-basil-the-g` |
| `Bulletin-English-2026-08-09.pdf` | `YYYY-M-D` | `sc-c` |
| `8-9-26.pdf`, `bulletin_1-4-26.pdf` | `M-D-YY` | `sp-l` |
| `26_08_09_bulletin.pdf` | `YY-MM-DD` | `ss-c` |
| `August_9_bulletin_8x11.pdf`, `august_9_2026.pdf` | textual, `_` separator | `sp-l`, `bearer` |
| `JFuly 19. 2026.pdf` | textual, typo'd, spaces | `olp-cle` |
| `bulletin_AUGUST-2026.pdf` | month only → 1st of path month | `hs-gh` (monthly) |
| `/2026/08/file-1-4.pdf` | day from name, year+month from path | eCatholic docs layout |
| `CurrentBulletin.pdf`, `8-9.pdf`, `Clare8-9__178…pdf` | **undated** — keyword-ranked | `shc`, `sc-p`, `sc-l` |

Ambiguous triples resolve to `M-D-YY` (these are US parish sites) unless the
URL path names a month and only one reading agrees with it. Readings outside
2000..next year are discarded, as is anything more than 14 days future-dated —
a misread run of digits, not a bulletin.

### Rules for editing the date parser

1. **Widening a pattern is how you break a different parish.** Every separator
   you add to a month-name pattern is a chance for the day group to swallow
   part of a year (`bulletin_JULY-2026` → the 20th). Keep `(\d{1,2})(?!\d)`.
2. **Run the end-to-end check, not just unit cases.** The `hs-gh` regression
   passed every filename test written for it and only appeared when ranking the
   whole page. Ranking is what matters; a correct parse that loses is still a
   bug.
3. **A future date is a parse failure, not a scoop.** Never let one sort to the
   top on the theory that it's next week's bulletin.
4. **Prefer failing to parse over parsing wrong.** `date.min` costs the recency
   bonus; a wrong date can *win* it.

### Auditing freshness

Nothing does this automatically. After a full run, three checks in order of
how much they're worth:

```bash
# 1. What bulletin date did PO/eCatholic actually fetch? (URLs carry the date)
gh run view <run-id> --log | grep -E '(parishesonline|ecatholic)' | grep '200 OK' \
  | grep -oE '2026[0-9]{4}B?\.pdf' | tr -d 'B' | sort | uniq -c | sort -rn
# Expect one dominant date: the most recent Sunday, or the coming one.

# 2. Which bulletin_url values did NOT change since the previous export?
git diff <prev-export-commit> HEAD -- export.json | grep bulletin_url
# Discover Mass tokens rotate weekly, so an unchanged DM URL is suspicious.
# Unchanged is fine for CurrentBulletin.pdf-style and Webpage rows.

# 3. For Self-Hosted, re-rank each page live and eyeball the parsed dates.
#    SelfHostedSource()._find_best_pdf_link(html, page_url) plus
#    ._extract_date(url) over the "Self-Hosted" rows in Notion.
```

The strongest signal is in the extraction itself: the model usually writes
"Bulletin dated Sunday, August 9, 2026" into `GPT Logs` / `Notes`. Grepping
that against today's date catches staleness for *every* publisher including
Discover Mass, where the URL tells you nothing — but only ~1/3 of extractions
state a date, so it's a spot-check, not a sweep.

## Automation

GitHub Actions runs `python main.py --all` every Saturday at 14:37 UTC
(`.github/workflows/gh-actions.yml`).

**A scheduled run can be dropped, and it is silent when it is.** Actions cron is
best-effort; on 2026-08-29 the trigger never fired and *no run record was
created at all*, so the failure is invisible in the run list — it looks
identical to a quiet week. The workflow was enabled, the cron was intact on
`main`, there was no GitHub incident, and the repo was well inside the 60-day
inactivity window. The schedule was simply skipped.

The time is 14:37 rather than 14:00 because the top of the hour is the most
congested minute on the platform. That lowers the odds; it does not remove
them — it happened again the very next week (2026-08-30 session found no
scheduled run for 2026-08-29's slot; a manual dispatch covered it).

**`check-freshness.yml`** (Saturdays 17:07 UTC, added 2026-08-30) watches for
exactly this: `utils/check_freshness.py` asks the Actions API whether a
schedule-triggered processor run started today, and asks Notion whether the
enabled parishes were actually re-stamped (>25% of countable rows with a
`GPT Timestamp` older than 3 days = the run was skipped, died early, or an
out-of-band run moved rows off the Saturday cadence). It posts to
`NOTIFY_WEBHOOK_URL` only when something is wrong; a quiet week posts nothing.
It is deliberately its own workflow on its own cron — every other downstream
job chains off the processor via `workflow_run`, so they all go silent
together when the cron is dropped. The watcher can be dropped too, but two
independent schedules dropping the same Saturday is a much smaller
coincidence.

A dispatched run accepts a `stale_days` input (default 6); set it to `0` to
force every parish, which is what a scraper fix needs — otherwise `--all` finds
nothing stale and exits in 30 seconds. The scheduled run passes no inputs.

**Local worker.** A few parish sites block GitHub Actions' datacenter IPs but
load fine from a residential connection, so those parishes are processed from
home instead. Two equivalent ways to do that:

- `local_worker.sh` — bare script; pulls, manages a venv, reads `.env`.
- `Dockerfile` + `docker-compose.yml` + `docker/` — the same thing as a
  container with an internal cron (built for Unraid; see `docker/README.md`).
  The image carries dependencies and the worker scripts only: `/app` is cloned
  at container start and re-synced with `origin/main` before every run
  (`docker/sync-code.sh`), so **a code fix reaches the worker by being pushed,
  not by rebuilding**. Rebuild only for the base image, the worker scripts, or
  the dependency baseline. Each run logs the commit it executes; a failed
  update logs `STALE code` and runs the previous commit rather than skipping
  the week.

Both workers currently process `ss-c`, `st-basil-the-g`, and `olp-cle` — the
last two 403 from Actions' datacenter IPs but return 200 from a residential
connection.

Neither regenerates `export.json` — they only refresh those parishes' Notion
rows. The Saturday Actions job still rebuilds `export.json` / `parish_data.json`
from Notion, so the worker's cron default (Sat 09:00 local) runs ahead of it.

## Repo layout notes

- `docs/design/` — design notes for planned work, written before implementation.
- `docs/integration/` — how downstream consumers read the export (Flutter app).
- `docs/notes/` — **gitignored.** Local working notes and source data for
  hand-verification.
- `temp/`, `reference.py` — gitignored local scratch. `reference.py` is the LED
  mapboard driver, kept as a reference for what `export.json` has to feed; the
  mapboard repo owns it.

## Changelog

### v2.5.21 (2026-09-01) - Notes: no URLs, and no describing a slot listed elsewhere

Two note defects found by reading St. Columbkille and St. Leo in the app.

**URLs are never published in a note.** `1445` carried a booking link on three
confession slots — "or by appointment: koalendar.com/e/frjps". `notes` renders
as plain text, so it isn't even clickable; it is just a string of characters
under a confession time. `_strip_urls()` removes the URL and the connector that
introduced it ("Register at <url> for details" → "Register for details"),
keeping the sentence it was bolted onto: the three now read "or by
appointment". A note that was *only* a URL becomes `None`.

**A note must not describe a slot that is already its own entry.** St.
Columbkille's bulletin prints one sentence covering two slots — "Saturday,
2:30-3:45 PM, the Thursday before the First Friday 7:00-8:00 PM and by
appointment". The extractor correctly emits two slots (v2.5.4) but copied the
whole sentence into each, so the app's Saturday card described the Thursday
slot listed directly above it. `_drop_duplicate_slot_notes()` removes such a
clause, guarded twice: the clause must name a `(day, time)` that exists as
**another** entry, and must **not** name this entry's own weekday. The second
guard is what protects `olg-m`, whose Tuesday 00:00 covered-day adoration is
legitimately noted "Continuation of Monday 9:00am - Tuesday 8:00am perpetual
slot".

**The first version of this silently rewrote 90 notes, and the corpus replay is
the only reason that was caught.** Two bugs, both in the shared clause helper:

1. `_drop_clauses_where()` reassembled and whitespace-normalised every note it
   merely *inspected*, so "Vigil Mass (Eng.)" came back as "Vigil Mass (Eng. )"
   and "Or by appointment" as "by appointment" with nothing dropped. It now
   returns the note **verbatim unless a segment was actually dropped**.
2. Splitting clauses on `.` shredded the abbreviations these notes are full of
   ("(Eng.)", "St. Vitus", "H.S. Mass"). `_NOTE_CLAUSE_RE` splits on `;` and
   `,` only.

After both fixes the replay touches exactly the two intended rows. The lesson
is the standing one: a note pass that *reads* every note can damage every note,
so replay it over all 189 rows and read the before/after, not the count.

Applied to Notion and carried into `export.json`; the only parishes that
changed are `1445` and `sc-p`.

### v2.5.20 (2026-08-30) - The partial-retraction guard: a run can no longer gut a schedule

The gap v2.5.19 measured and named. `save_extraction()` writes **any non-empty
list**, so an extraction that comes back with a fraction of the schedule
silently replaces a correct one. `37345` demonstrated it live: 8 recurring
Masses to 3 on an edition that was a newsletter with no schedule block, every
weekday Mass gone, and the row would have reported "No Issues".

The empty case has been guarded since v2.5.8 — an empty list is never written,
and the run says what it declined to overwrite. **This is the same guard for
the partial case**, and it deliberately behaves identically: when a fresh
extraction drops more than `PARTIAL_RETRACTION_RATIO` (half) of the stored
*recurring* entries, the field is not written and a warning goes to `Issue Log`
and the run summary naming every dropped slot.

**It does not try to decide which side is right, and that is the design.**
v2.5.19 established that a large drop is sometimes a genuine correction —
`1584` shedding seven phantom "Federal Holiday" Masses was one — and that no
cheap signal separates the two (both reproduced on a second extraction; the
text-layer check is unavailable on exactly the bulletins that cause the
problem). So the guard makes the loss impossible to take silently and puts both
lists in front of a human, who settles it in one look and applies it with
`utils.notion_fixes`. Holding a correct change for one review cycle is a much
cheaper error than destroying a correct schedule.

Details that matter:

- **Dated Masses are excluded** from the comparison. A one-off legitimately
  disappears once its date passes, and counting that as a retraction would fire
  on every parish that had a holiday Mass last week.
- **`PARTIAL_RETRACTION_MIN_STORED` (3)** keeps it off small rows, where one
  slot flapping is already half the schedule.
- **Corrupt stored JSON never blocks a write** — that is the v2.5.1 alarm's
  business, and overwriting corruption with a good extraction is the repair
  path, not a loss.
- Masses and confessions are guarded independently; adoration is not written at
  all (`UPDATE_ADORATION = False`).

Verified: the two real cases from the 50-parish study are both held (`37345`
8→3, `1584` 13→7); a single flapping slot, an exactly-half drop, a 2-Mass row,
and an expiring dated Mass are all still written; a stored schedule reproduced
exactly is never held (checked against the live rows for `37345`, `1584`,
`1259` and `0582`, which also exercises multi-block JSON reads).

Expected rate: **2 of 50 parishes** in the study sample, so roughly 8 a week
across 189 — each one a schedule that would otherwise have been quietly cut.

### v2.5.19 (2026-08-30) - Measuring the verification layer; civil-holiday policy lines

**`studies/verification/`** answers the question v2.5.16 deferred: what does the
verification layer actually *say* on a run, and how much of it is worth
reading? 50 parishes sampled outside `studies/noise/roster.json` (those 100 are
over-read and several were hand-repaired, so they would understate the rate),
serial prefetch, production verification path, no writes. 0 extraction
failures, 5.1 minutes.

| | parishes |
|---|---|
| no warning of any kind | **40 (80%)** |
| change warnings | 9 (18%) — 6 reproduced, 3 self-labelled noise |
| fabrication warnings | **0** |
| sanitizer flags | 2 (4%) |

**All nine were then checked against their bulletins**, which produced the
finding that matters: **"reproduced" does not mean "correct".** Seven of the
nine read the *same bulletin file* the stored value came from, so a reproduced
diff is two draws agreeing against a third, not evidence about truth. Of the
six:

- **`1584` today was right** — the stored row held **7 phantom Masses**, one
  per weekday, from the masthead line "Federal Holiday 9:00 a.m." (see below).
- **`1905` today was right** — recovered a Sunday 11:00 the masthead prints.
- **`1548` today was right** — the Monday 6:30 pm novena-and-Mass is in the
  bulletin.
- **`1532` is a bulletin typo.** It prints `friday | 9:00pm` among Mon/Tue/Wed
  `9:00am`; the stored value transcribed it literally and this run silently
  corrected it to 09:00. Only the parish can settle it.
- **`37345` today was wrong, and this is the case that justifies the check.**
  Its stored 9 Masses became 4: the Aug 31 edition is a newsletter without a
  schedule block, so every weekday Mass vanished, and the "sensory friendly
  Mass at noon **on the second Sunday of each month**" was added as a *weekly*
  Sunday noon Mass. `save_extraction` writes any non-empty list, so this would
  have silently replaced a correct schedule with a quarter of one.
- **`30803` is unverifiable** — an image-only bulletin (1,298 chars of text).

**Two conclusions for the write-gating decision.** Gating on reproducibility
alone would have blocked three genuine corrections while still admitting
`37345`, which reproduced. The signal that separates `37345` is **how much was
removed** — 9 Masses to 4, losing every weekday — which is the natural
extension of the existing empty-extraction retraction warning to the *partial*
case, and is not built.

**Code — the Holy Day rule now covers civil holidays.** `1584` was publishing
seven 9:00 Masses whose own notes read *"Federal Holiday Mass (when a federal
holiday falls on Monday)"*. That is the v2.5.11 undated-policy-line bug exactly,
but `_HOLY_DAY_RE` matched only "holy day". It now also matches
`federal|legal|civic|national|public holiday`. Replayed over all 189 stored
rows: **7 drops, all at `1584`**, everything else untouched. Repaired via
`notion_fixes --apply`; the row now carries its 6 real Masses (its missing
Tuesday 08:30 is what this run's extraction adds, so it self-heals).

### v2.5.18 (2026-08-30) - Triaging the 2026-08-29 run's warnings

Eleven rows carried warnings. Every one was checked against its own bulletin;
**nine were true positives**, and the two that were not are documented below.
Six of the seven confession retraction warnings turned out to be the *stored*
value being wrong while the fresh extraction was right — the v2.5.8 St. Mel
shape, where `save_extraction()` never writes an empty list, so a bad value
outlives every correct run after it.

**Code fix — the Holy Day "labelled both" guard misfired on word order.**
`_drop_undated_holy_day_masses()` keeps a Mass whose note names both an
ordinary Mass and a Holy Day, because a parish's standing Saturday vigil is
often also its Holy Day vigil. But it tested for the ordinary phrase as a bare
substring, so Sacred Heart Chapel's `Thursday 07:00 "Holy Day Vigil Mass"` was
kept — "Holy Day **Vigil Mass**" contains `vigil mass` while describing no
weekly vigil at all. The bulletin says *"Thursdays no Mass / no hay misa los
jueves"*, and the row was publishing a Thursday Mass every week.

Word order separates them: in the genuine double label the ordinary word comes
first ("Vigil Mass; Vigil of Holy Day"). `_has_independent_ordinary_label()`
strips phrases where a Holy Day modifier *precedes* the ordinary words in the
same clause, then tests. Replayed over all 189 stored rows: **exactly one entry
changes** — 1823's — and every genuine double label is untouched.

**Data repaired** (`notion_fixes --apply`, 7 rows, verified after write):

| row | what was stored | why it was wrong |
|---|---|---|
| `0582` | 4 confession slots | The bulletin's whole Confessions entry is *"Please ask a priest before or after Mass"* — no times. The slots were manufactured by anchoring that clause to Mass times, and to times the parish doesn't use. Read off the cover image; its text layer is nothing but ad pages. |
| `0414-sp` | Sat 15:00, noted "St. Ann Church" | The bulletin puts Reconciliation *in St. Ann Church*; `0414` holds it. |
| `1855-james` | 2 slots, noted "at St. Luke"/"at St. Clement" | Both belong to `1855` and `1855-clem`, which hold them. |
| `olhc-litchfield` | Mon 18:00, noted "(listed as Lodi Site in bulletin)" | It is Lodi's; `olhc-lodi` holds it. |
| `sem-c` | Sun 10:20, "In preparation for Easter" | Lent-only, published in August; the end was invented too. |
| `scas-e` | Mon 19:00 "Collinwood Cluster Penance Service" | A seasonal communal service published weekly. Saturday 16:00 kept. |
| `1823` | Thu 07:00 + Thu 09:30 | Both from the undated Holy Day policy line ("Vigil 7:00 pm; Holy Day 9:30 am") on the one weekday with no Mass. The sanitizer now drops the 07:00 itself; the 09:30 carries no note, so nothing can catch it. |

Three of these — `0414-sp`, `1855-james`, `olhc-litchfield` — are the same
cluster-bleed shape, and each stored note *named the other parish*. That is a
usable signal: a confession whose note names a sibling row in the same bulletin
group is almost certainly that sibling's.

**The two non-bugs, and what they actually point at:**

- **`1485` "confession spans 2h00m"** is a genuine window — the bulletin's
  calendar prints *"4:30 pm monthly reconciliation (church)"* alongside a
  separate *"6:00 pm weekly sacrament of reconciliation"*. The flag is a false
  positive, but the row has a real defect the flag doesn't name: the monthly
  service is stored as **weekly**. Its note says "Monthly Reconciliation" with
  no ordinal, so `monthly_recurrence.py` correctly refuses it.
- **`0512` cross-site bleed** is real but currently harmless: the Mon/Tue/Wed
  notes referencing an 8:00 AM Mass are **adoration** entries, and those 8:00
  Masses are at Prince of Peace (`0512-peace`), not St. Andrew. `UPDATE_ADORATION
  = False` means nothing was written. It matters when the adoration capture is
  reviewed.

Also found, not repaired: **`ss-c`'s Sunday confession note "Before 9:30am
Mass"** is fabricated — the bulletin gives Sunday Masses as 8:00 and 11:00 and
the confession as "Sunday: 9-9:30am". The times are right; only the published
note invents a Mass. **`bearer`'s bulletin is a pure image** (1-char text
layer), which is why no site matched `Saint Lucy Mission`; that row is holding
Aug 16 data.

### v2.5.17 (2026-08-30) - export.json emits weeks_of_month / excluded_weeks

The scraper side of the frozen spec (see the normative section in
`EXPORT_SHAPE_CHANGES.md`; the app is built against it). **Derived at export
time, not stored**: `utils/monthly_recurrence.py` parses the ordinal from each
entry's `notes` inside `notion_to_app`'s emitters. Export-time is load-bearing
twice over — a parser fix reaches every stored row on the next rebuild without
re-running parishes, and the ~13 monthly adoration slots benefit despite
`UPDATE_ADORATION = False` meaning their rows are never rewritten.

Refuse-rather-than-guess, validated against every note in the live export and
hand-reviewed: **58 derived, 14 refused, 0 wrong.** Two refusal guards came
from that review, each catching a live entry the naive parser got wrong:

- **Multiple ordinal-weekday phrases refuse** — `our-lady-of-victory`'s
  "at Saint Matthew on the 1st and 3rd Saturdays; at Our Lady of Victory on
  the 2nd and 4th" would have merged to `[1,2,3,4]` ≈ weekly, the exact bug
  being fixed. ("2nd and 4th Saturday" is one phrase and still derives.)
- **A clause labelling the slot weekly refuses inclusions** — `0116`'s
  "Weekday Mass; First Saturday" is the v2.5.11 merged-label case; emitting
  `[1]` would hide a real weekly Mass three weeks a month. Exclusions stay
  coherent ("Weekday Mass (except on First Fridays)" → `excluded_weeks: [1]`).

Also refused, per spec: every "Thursday before First Friday" variant (the
phrase's weekday must be the entry's own day), and "4th of July" never parses
(the ordinal must attach to a weekday).

Verified: regenerating the export and stripping the two new keys reproduces
the previous export exactly (purely additive), and all 58 carrying entries
hold the spec invariants (domain 1-5/-1, sorted, deduped, keys mutually
exclusive, never on a dated Mass).

**The mapboard is still weekly** — `notion_to_json` does not carry the rule;
`reference.py` would need the same predicate. Deliberately left for later.

### v2.5.16 (2026-08-30) - Change verification: diff, reproduce, check the page

**Nothing asked whether a schedule change was real before it overwrote the
stored value.** The two failure modes pull opposite ways: ~17% of parishes show
a recurring-Mass diff from extraction noise alone (studies/noise), while a
fabricated time *is* a change from a correct stored value (1259's 10:30
replaced a right 11:00). An LLM judging "does this change make sense?" fails
both at once — the fabricated 10:30 is the most plausible time in the diocese,
and the stored value is not ground truth either (St. Mel). So the arbiters are
cheaper and grounded:

**`utils/verify_changes.py`**, called from `process_parish` before the save,
flag-only:

1. **Diff** the new recurring Masses and confessions against what Notion holds
   (`NotionClient.get_stored_schedules()`; adoration excluded — the
   `UPDATE_ADORATION` lock means a diff there would warn forever). A side that
   is empty or corrupt is not diffable: first extractions, retractions, and
   the v2.5.1 alarm each have their own handling.
2. **Reproduce.** On any diff, re-extract once from the same downloaded bytes
   through the same collapse + sanitize path (`_pair_sites()` mirrors the save
   step's site-parish pairing read-only, for both runs). A change the second
   run does not reproduce is labelled noise — "distrust this week's value".
   Budgeted (`REEXTRACT_BUDGET` = 40/run) so a prompt regression that changes
   everything cannot double the OpenAI bill; over budget still warns,
   labelled unverified.
3. **Check the page.** Changed slots are looked up in the text layer with the
   verify_times renderings, gated the same way (the page must verify >=80% of
   its own Mass times before any absence claim is made). Old time printed +
   new time absent is the damning combination and says so.

**Measured over the study corpus** (run 0 as "stored", run 1 as "new", run 2 as
the re-extraction — same bytes, so every diff is pure noise): 26 warnings
across 100 parishes, 20 correctly self-labelled as noise, and the 6
"reproduced" ones are real instabilities worth seeing (`sa-o`'s Monday
05:15/17:15 AM-PM flap among them). **Expect roughly 35-50 warned parishes per
week at 189 parishes; that rate is the point of the flag-only phase.** It is
the extractor's true noise floor made visible, and the argument for the
natural next step once trusted: gating the write on reproducibility instead of
saving whichever sample arrived first.

**Live validation on `1259`**: the dry run extracted Saturday confession at
14:30 (the stored, hand-verified value is 15:00; the bulletin prints
"3:00-4:00 pm") and the check flagged it triple-labelled: `added Saturday 1430
(not printed in bulletin - suspicious), removed Saturday 1500 (still printed
in bulletin) [NOT reproduced on a second extraction - likely extraction
noise]`. The Mass schedule matched stored exactly and produced no warning.

### v2.5.15 (2026-08-30) - Note-level site exclusions; the IC vigil leak

The deterministic fix v2.5.10 proposed and never implemented. The Cathedral's
masthead prints "Saturday: 6:00 pm (Sunday Vigil at Immaculate Conception)",
and in about half of runs the model never emits the Oratory as a site — it
copies that Mass inline into the Cathedral's own list, where site-level
`SITE_EXCLUSIONS` cannot see it, and the Cathedral publishes IC's vigil at the
wrong address (IC's own row already publishes it).

`SITE_EXCLUSIONS` rules gain optional `note_match`/`note_unless`:
`_apply_site_exclusions()` now also drops a **recurring** Mass on a kept site
whose note matches. Notes get their own pattern and guard because they rarely
spell the full site name and mention the parish's own chapel incidentally
(which would trip the site-name `unless` terms). Dated Masses are exempt, and
`note_unless` covers the feast-name collision — a December "Solemnity of the
Immaculate Conception" Mass is the Cathedral's own.

Validated over all 50 stored `1259` study runs across 7 conditions: the raw
extractions carry the Sat 18:00 leak in 16; the pipeline now removes every one
whose note names IC (16 -> 8 survivors, all with no-signal notes like "Sunday
Vigil" — those remain `notion_fixes` territory via `drop_masses`). Zero
firings on any other parish; a unit check confirms the dated Dec 8 feast Mass
and a "Holy Day"-noted Mass survive.

### v2.5.14 (2026-08-30) - Fabrication check: extracted Mass times verified against the bulletin's own text

**The worst extraction failures are fabrications, not misreadings** — `1259`
published a Sunday 10:30 Mass whose digits appear nowhere in its bulletin
(`8:30/10:30` is the diocese's most common Sunday pattern; the prior overrode
the page), and the run said "No Issues". Prompt tuning against this class was
tried wide and stopped (v2.5.10): every rule fixed its target and broke a
neighbour. This check attacks it from outside the model: a time the document
never prints is checkable by looking.

**`utils/verify_times.py`**, called from `process_parish` after the sanitizer.
It renders each recurring Mass time the way a bulletin prints it ("10:30",
"10.30", "8:30am" glued, "11 :00" split by PDF extraction, bare "9 and 11
a.m." lists, "noon") and greps the PDF text layer via PyMuPDF. Masses only
(confession/adoration starts are legally *derived* under v2.5.4 rules),
recurring only, **flags only** — nothing is dropped, so it cannot cause the
regressions that stopped the prompt work. It checks the *downloaded* bytes,
not what the LLM saw: `compress_if_needed` rasterizes oversized PDFs, and
`1259` is one of the three it compresses.

**The design that makes it shippable is the self-gate.** A first measurement
flagged 23% of all recurring Masses — because a bulletin masthead is very
often an image even when the body has a good text layer, so "absent from the
text" usually means "the schedule block is a scan". Instead of a hand-kept
allowlist, the check gates on the bulletin's own hit rate: misses are reported
only when >= 80% of the extraction's recurring times (and >= 5 of them) ARE
found in the text — proof the schedule lives in the text layer, making a
missing time a real signal. Below the gate it logs and stays silent; an
unverifiable document is a property of the parish, not a weekly warning.

Two pattern bugs accounted for most of the original 23%: a trailing `\b`
fails on "8:30am" (digit→letter is not a word boundary — replaced with
`(?!\d)`), and "11 :00" (a real artifact of PyMuPDF extraction at
`our-lady-help-of-christians`) needed optional spaces around the separator.

**Validated over 1,250 parish-runs** (4 noise-study conditions x cached
bulletins, driven through the production function on Pydantic models): 7
warnings total, on exactly 3 slots — `1259` Sunday 10:30, `0670` Thursday
11:15, `2452` Sunday 19:00 — **each hand-verified as a time its bulletin never
prints** (0670's Thursday Mass is 8:15am; 2452's only Sunday Mass is 10:00am).
**Zero false positives.** The decisive test from the prototype session also
passed: against the Aug 30 `1259` bulletin (intact 36k-char text layer), all
14 true recurring Masses verify and the fabricated 10:30 does not. A live
`--dry-run` of `1259` through the full pipeline ran the check clean end-to-end.

**Known limit:** a *wholly* fabricated schedule is indistinguishable from an
image-only bulletin, so the gate catches isolated fabrication inside an
otherwise-verifiable document — which is the failure mode the model actually
exhibits. Image-only scans (`0080`, `13722`) remain uncheckable without OCR.

### v2.5.13 (2026-08-30) - A restorable archive; the export docs were inverted

**There was no way to revert a bad run, and the two files that looked like an
archive could not provide one.** `export.json` and `parish_data.json` are
committed every Saturday (61 snapshots back to 2024-07-10), but both are
*derived* views. `notion_to_app` drops `Events`, every log field, the publisher
and enable flags — and, decisively, it drops dated Masses already in the past
and nulls coordinates outside Ohio. You cannot restore from a file that
discarded the data on the way out. Nothing in the repo read either file back
into Notion; the data flowed one way only.

**`utils/notion_snapshot.py`** writes the properties as Notion holds them (189
parishes, 24 properties, ~1.8MB), and restores from one. It runs in
`export-data.yml` after the two exports, so git history is the archive — the
arrangement `export.json` already uses, which keeps diffs meaningful and avoids
a file per week. Rows are sorted by `ParishID` (page id breaking ties, so rows
with no ParishID still order stably) and properties by key, so a week-to-week
diff shows real changes rather than reordering. Long values are joined across
blocks on read and re-chunked on write — the v2.5.1 rule, or a schedule field
comes back unparseable.

**Restore is deliberately partial, and today proved why.** `OPERATIONAL_FIELDS`
— `Enable`, `Issues`, `Bulletin Publisher`, `Bulletin Page URL`, `Bulletin
Group ID` — are a human's classification of a parish, not data a run produced.
Replaying a week-old snapshot over `sfds-a` would have re-disabled it and
restored its `Unsupported` status, undoing the work of enabling it that same
morning, and `Unsupported` is exactly what `PROTECTED_STATUSES` exists to
defend. They are held back unless `--all-fields` is passed, which is the flag
to use when undoing such a change *is* the point.

Dry-run by default like `notion_fixes`, `--parish` to scope to one row, and a
property whose type has changed underneath is refused rather than guessed at.

Verified: a restore from a just-taken snapshot is a no-op; a tampered snapshot
is detected field-by-field with the data fields caught and the operational ones
held; `--all-fields` catches all four; and a live write round-trip (set a value,
read it back from Notion, restore it to empty) works in both directions.

**Docs corrected: the two export utilities were described the wrong way round.**
CLAUDE.md said `notion_to_json` exports "raw Notion data to export.json (all
fields as-is)". It writes `parish_data.json` (mapboard format, keyed by page
ID); `notion_to_app` writes `export.json`; and **neither is raw**. That matters
more than a naming slip, because someone reaching for an archive would have
reached for the wrong file and believed it held everything.

### v2.5.12 (2026-08-30) - Self-Hosted follows subpages; PDF viewers decoded

**St. Francis de Sales, Akron (`sfds-a`) was unreachable for two separate
reasons, and either one alone was enough to hide the bulletin.**

1. *The PDF is one level down.* `stfparish.com/our-message/bulletins/` is a
   WordPress post grid - one post per Sunday, and **not a single `.pdf` on the
   page**. `_find_best_pdf_link()` correctly returned nothing, and the parish
   read as having no bulletin at all.
2. *The PDF is behind a viewer.* Each post embeds it with PDFEmbedder Premium,
   whose iframe `src` is `https://stfparish.com/?pdfemb-data=<base64>` - a
   base64url'd JSON blob holding the real URL. The existing iframe branch tests
   for `.pdf` in the `src`, which that string does not contain. So even after
   following the link, the subpage would still have looked empty.

**`_find_pdf_in_subpages()`** runs only when the page itself yields no PDF, so
the ~14 parishes that already resolve keep their exact path and request count.
It ranks same-host links by the usual keyword score plus recency bonus, skips
pagination (`/page/N/` walks the archive *backwards*), and fetches at most
`SUBPAGE_FETCH_LIMIT` (3) of them newest-first.

**It returns the first subpage that has a PDF rather than pooling the PDFs it
finds**, and that is the freshness-critical decision. These files are named
alike within a month - `Francis-Akron-8-30-compressed.pdf`,
`-8-23-`, `-8-16-`, all sitting in `/2026/08/` - and none of those filenames
parses. All three fall to the eCatholic path fallback, which reads the *first*
number in the name as the day and returns **2026-08-08 for all three**. Pooled,
that is a three-way tie broken arbitrarily; a run could serve a fortnight-old
bulletin and report "No Issues". The post slug
(`/august-30-2026-twenty-second-sunday-in-ordinary-time/`) is the only
trustworthy date on the site, so the ordering of the *subpages* is what decides,
and the PDF's own filename never gets a vote.

`_extract_date_raw()` now strips a trailing slash before taking the last path
segment, or every slug would date to the empty string.

**`_resolve_embedded_pdf()`** decodes what a viewer wraps around the file:
`pdfemb-data` (base64 JSON), and a `file`/`url`/`pdf`/`src` query parameter,
which covers PDF.js and the Google/Office viewers. Bad base64 or bad JSON is
skipped, not raised - a broken embed must not take down a page that also has a
working link.

**Verified end-to-end against all 15 live Self-Hosted pages, before and after.
The only line that changed was `sfds-a`'s**, from "none found" to the Aug 30
PDF; the other 14 resolve byte-identical URLs. Ranking is what matters here, so
this is the check that counts - see the freshness rules above.

A real extraction off the Aug 30 bulletin gives one site, the correct Manchester
Road address, 12 recurring Masses matching the hand-entered directory data, a
dated Sept 12 festival vigil (4:00pm replacing the 5:00pm), two confession
slots, and a genuine Mon-Fri adoration schedule - none of which the row had.

**Live as of 2026-08-30.** The row moved `Webpage` -> `Self-Hosted`, was
enabled, and had `Issues` moved off **Unsupported** by hand - that is a
`PROTECTED_STATUSES` value, so a successful run would never have cleared it and
the row would have kept `invite_feedback: true` while publishing good data. A
real run then saved 12 Masses, 2 confessions and 18 events with no warnings.

Its adoration was **not** saved: `UPDATE_ADORATION = False`. See Pending Work.

### v2.5.11 (2026-08-24) - Published notes, Holy Day policy lines, seasonal adoration

Three problems found from one parish (St. Eugene, `1734`), all diocese-wide.

**1. `notes` is published text; the extractor was writing extraction commentary
into it.** St. Eugene shipped `Holy Day schedule lists 11:00 AM & 7:00 PM (day
not specified); see extraction_notes.` to end users. `notes` renders in the app
under the parish's own name — "see extraction_notes" is meaningless to a reader
and the hedging reads as the parish being unsure when its own Masses are.
`extraction_notes` already exists for this and stays internal.

Prompt now says so explicitly, and `_scrub_meta_notes()` in `utils/sanitize.py`
is the backstop. It works through `_drop_clauses()`, which removes matching
parentheticals first and then matching sentences/clauses, so a note that is
half fact and half apology keeps the fact: *"After Mass (start time estimated
~30 minutes after Mass)"* → *"After Mass"*. A note that is entirely commentary
becomes `None`. 7 rows scrubbed.

**2. Holy Day schedules published as weekly Masses — 12 parishes, 27 phantom
Masses.** A bulletin prints a standing line ("Holy Days: 8:15, 11:15, 6:45 pm")
with no date, because the date moves every year. `mass_date` is the only way to
say "one specific day", so an extractor that keeps the line must invent a
weekday — and picks one arbitrarily. St. Thomas More was advertising three
Masses every Thursday, on a day it has no weekday Mass at all.

The prompt now answers the user's question directly: **when the standing
schedule box and the day-by-day Mass intentions listing disagree, the intentions
listing wins.** The box states policy across the year; the listing states what is
actually celebrated in the week the bulletin covers. So — emit dated Masses with
`mass_date` if the intentions listing shows the Holy Day falling inside this
bulletin's week, otherwise omit the times entirely and describe the policy in
`extraction_notes`. There is no Holy Day this week; there is nothing to publish.

`_drop_undated_holy_day_masses()` enforces it, and **it needed three guards
before it was safe** — the first two drafts each deleted real Masses:

- A dated Holy Day Mass is never touched. That is the correct output.
- A note saying *both* ("Vigil Mass; Vigil of Holy Day", "Weekday Mass; Holy
  Day") describes a real weekly Mass carrying an extra label. Kept and flagged.
  Without this, St. John Nepomucene lost its genuine Saturday 4pm vigil.
- **A Holy Day entry at a time the parish offers as a plain Mass on ≥2 other
  weekdays is probably the daily Mass with the label merged onto it.** Kept,
  label stripped, flagged. `_dedupe_masses` merges on
  `(day, time, language, mass_date)` and joins notes, so when the extractor
  emits both the daily Mass and the Holy Day line at the same slot, the plain
  entry's empty note loses and the Holy Day label is the only survivor.
  St. Eugene's own `GPT Logs` read *"merged duplicate Monday 1100 entry"*.
  **7 of the 12 parishes hit this**, and without the guard each would have lost
  a real daily Mass.

The pass therefore runs **before** `_clean_masses`, so a fresh extraction drops
the Holy Day entry before dedupe can merge it into anything; an exact plain twin
at the same `(day, time)` is dropped outright rather than kept, since the real
Mass is right there.

**3. Seasonal and Triduum adoration published year-round — 24 parishes.**
`AdorationTime` has no `mass_date` and no season field, so a Holy Thursday slot
can only be stored as a recurring weekly Thursday. **19 parishes were
advertising their Holy Thursday 2026 adoration as their standing schedule** —
"Thursday 8:00–10:00 pm, adoration at the repository", every Thursday of the
year — plus 5 more on Lenten or Divine Mercy schedules. `UPDATE_ADORATION` is
`False`, so no ordinary run would ever have corrected any of them.

This is the *fifth* instance of the same class: `2492` (v2.5.5, Holy Thursday)
and `sc-c` (v2.5.8, Lent-only) were each fixed by hand as one-offs. They were
not one-offs.

`_drop_seasonal_adoration()` uses the same conservative guard as
`_drop_coverage_hours`: drop only when there is no real schedule to lose (a
perpetual chapel, or a listing that is *entirely* seasonal); flag otherwise. A
mixed listing is a judgement call, and deleting into a live schedule on a note
match is how a good slot disappears. Two rows flagged; `1071-MIC` got a
`ManualFix` (three Lent-only slots whose notes state their own end, "until
Easter Day", alongside one genuine Thursday).

The pattern deliberately does **not** match "First Friday"/"First Saturday" —
see below.

**Applied 2026-08-24** via `python -m utils.notion_fixes --apply` (two passes,
48 rows). No parish lost its whole Mass schedule; every drop was checked against
what the day retained.

**Known false positive:** `st-mary-painesville-oh` has a genuine weekly Monday
silent adoration whose note reads "preparing for Corpus Christi". It flags every
week. Treat like St. Brendan's long confession span — add a `definitions.py`
exemption if it gets noisy, don't narrow the pattern.

**Unfixed and larger: monthly slots published weekly.** The same scan found
**44 slots across 34 parishes** noted "First Friday", "First Saturday",
"Thursday before First Friday" — 20 confessions, 13 adoration, 11 Masses. These
are not stale; they recur genuinely. But nothing in the schema expresses "the
first Friday of the month", so each is stored as *every* Friday. St. Leo the
Great publishes a 6-hour First Friday adoration as a weekly one. This needs a
recurrence field, not a note pattern, and is deliberately out of scope here —
the seasonal regex excludes it so a later fix can address it properly.

### v2.5.10 (2026-08-21) - The compression ladder started too low; a targeted study of 1259

**`utils/pdf_compress.py` first rung was 150 DPI.** The Cathedral's bulletin is
47.7MB against a 47.2MB limit — it needed a **1.1%** reduction and got a **91%**
one, rasterized to 4.4MB. 300 DPI/q90 fits at 17.3MB. `_COMPRESSION_STEPS` now
starts at `(300, 90)` and steps down gently.

**This is free.** Both conditions billed *exactly* 144,010 prompt tokens — the
API's image tokenization does not depend on the file's byte size — so the old
ladder was paying full price for a quarter of the linear resolution. The only
cost is wall time (243s -> 400s for 10 extractions). Three of 100 cached
bulletins are over the limit (`1259`, `1241`, `1236`).

Every step still **destroys the text layer**: `_rasterize()` rebuilds each page
as a JPEG, so a bulletin whose text layer says `Sunday: 8:30, 11:00 am †; 5:30 pm`
arrives as pixels. Resolution is the only defence, which is why the mildest
step that fits is the right one.

**New: `studies/noise/signature_recurring.py` and `studies/noise/truth.json`.**
The aggregate table cannot say whether a row is *right* — only whether it is
*stable*, and a parish can be stably wrong. `truth.json` holds hand-read
schedules with the exact cached bulletin cited; the signature scores each run
against it. This immediately showed something 5 repeats of self-agreement had
hidden: the `baseline` condition got Sunday 11:00 wrong in **5 of 5** runs.

**What the 1259 study found** (4 conditions, 30 runs, same cached 2 Aug 2026
bulletin). Truth is 14 recurring Masses: Sat 16:30; Sun 8:30, 11:00, 17:30;
Mon-Fri 7:15 and 12:00.

| condition | DPI | exactly correct | Sunday 11:00 right | core slots always present |
|---|---|---|---|---|
| baseline (5) | 150 | 0/5 | 0/5 | 13/16 |
| promptfix (5) | 150 | 1/5 | 4/5 | 13/18 |
| sitexcl (10) | 150 | 0/10 | 1/10 | 7/21 |
| dpi300 (10) | 300 | 0/10 | 3/10 | 13/19 |

Two conclusions, one of which killed a theory:

- **Resolution fixes structure, not the Sunday line.** At 300 DPI the schedule
  stops falling apart — Masses/run tightened from 11-16 to 14-15 and the core
  went 7 -> 13 — but Sunday 11:00 stayed wrong 7/10.
- **`promptfix`'s 4/5 was luck.** The prompt has not changed since it ran, and
  `SITE_EXCLUSIONS` fired in only 1 of 10 runs, so `sitexcl` is a same-prompt
  replay at n=10 sixteen days later. Pooled, Sunday 11:00 is right in 8 of 30.
  n=5 on an unstable slot will happily show a gain that is not there — the
  v2.5.5 lesson, again.

**The errors are not perceptual.** Grepping the bulletin's own text layer:

- `10:30` — the single most common error (6/10 at 300 DPI) — **appears nowhere
  in the document**. The model is not misreading it, it is inventing it.
  `8:30/10:30` is an extremely common parish Sunday pattern; this looks like a
  prior overriding the page.
- `11:30` appears only under `Confessions: 7:45 am & 11:30 am` — confession
  bleed into the Mass list.
- `Sunday 06:00 "Sunday Vigil (early)"` is `6:00 pm (Sunday Vigil at Immaculate
  Conception)`, printed under the `Saturday:` heading, relocated to Sunday and
  flipped to AM.

So the remaining lever is the prompt, and the targets are transcription
fidelity, day-heading scoping, and confession/Mass separation — not legibility.

**Prompt v3 (`promptv3`), four rules added to `extractor.py`:**

1. *Transcribe, never normalise* (TIME ENCODING) — every recorded time must
   appear in those digits on the page; a common pattern is not evidence.
2. *A time belongs to the heading it is printed under* (TIME ENCODING) — day
   labels scope times; a time under Confessions is never a Mass.
3. *A parenthetical describes a Mass, it does not move it* (Mass rules) — under
   `Saturday:`, "6:00 pm (Sunday Vigil at Immaculate Conception)" is Saturday
   1800.
4. *Sites* — a temporary renovation worship space is not a separate site; never
   build a site name from two things on the page; typographic keys change only
   WHERE an entry happens.

Result on the same cached bulletin, all conditions scored against `truth.json`:

| condition | DPI | exactly correct | Sun 11:00 | Sun 10:30 | Sun 06:00 | Sat 18:00 leak |
|---|---|---|---|---|---|---|
| baseline | 150 | 0/5 | 0/5 | 3/5 | 0/5 | 4/5 |
| promptfix | 150 | 1/5 | 4/5 | 1/5 | 2/5 | 1/5 |
| sitexcl | 150 | 0/10 | 1/10 | 3/10 | 1/10 | 1/10 |
| dpi300 | 300 | 0/10 | 3/10 | 6/10 | 4/10 | 2/10 |
| **promptv3** | 300 | **7/10** | **10/10** | **0/10** | **0/10** | 5/10 |

**Exactly correct went 1-in-30 to 7-in-10**, and the two fabrications the
signature was built to catch went to zero. Sunday 11:00 — wrong in 22 of the
previous 30 runs — is now right every time. Site naming also settled: 9/10 runs
emit the Oratory cleanly as its own site (so `SITE_EXCLUSIONS` drops it) and
fold the temporary chapel, which is rule 4 working as intended.

**One regression, and it is real: the Saturday 18:00 leak doubled, 2/10 -> 5/10.**
Rule 4 made the model better at separating the Oratory *as a site* while it
still copies the 6:00 pm vigil into the Cathedral's own Mass list — which is
what the bulletin literally prints ("Saturday: 4:30 pm (Sunday Vigil) / 6:00 pm
(Sunday Vigil at Immaculate Conception)"). `SITE_EXCLUSIONS` works at site
level and cannot see an inline Mass, so half of runs put an 18:00 Mass on the
Cathedral row.

Fighting this in the prompt means arguing with the page: from the Cathedral
parish's point of view that Mass *is* theirs, held at IC. The product decision
(IC owns it) is ours, so enforce it in our layer — extend `SITE_EXCLUSIONS` to
also drop Masses whose `notes` name an excluded site. Not yet implemented.

**Wide regression: 50 parishes x 5 repeats (`promptv3-wide`), paired against
`promptfix` on batch 1. Verdict: do NOT ship v3 as it stands.**

Aggregates behaved exactly as the README predicts — every category inside the
noise band, so they settle nothing:

| category | promptfix | promptv3 | delta | bootstrap CI | |
|---|---|---|---|---|---|
| mass | 94% | 96% | +1.9% | [-0.1%, +4.0%] | noise |
| confession | 98% | 98% | -0.3% | [-4.0%, +2.9%] | noise |
| adoration | 93% | 95% | +2.3% | [-3.5%, +7.6%] | noise |

The pre-registered signatures barely moved either: `vigil_before_noon` 4 -> 2
(n too small to mean anything), `mass_noted_confession` **0 -> 0**,
`sites_total` 295 -> 295, `repairs` 118 -> 107, `flags` 4 -> 6.

So the per-parish movers were checked **against their actual bulletins**, which
is the only thing that can tell "found more truth" from "hallucinated more".
Three of the negative movers are real, and each has a mechanism:

- **`0080` confession 100% -> 43%.** Its bulletin (an image-only scan; the text
  layer is just the ad pages) reads `Confession / Saturday: 3 - 4p.m. Church /
  and by Appointment` — **Saturday only**. v3 invented Mon/Tue/Thu/Fri
  15:00-16:00 in 2 of 5 runs. `promptfix` was right 5/5. Suspect **rule 2**,
  whose "a day label governs every time beneath it" interacts badly with the
  v2.5.4 day-range distribution rule.
- **`our-lady-of-victory-tallmadge-oh` mass 80% -> 59%.** v3 pulled in
  `Sun 10:30` (3/5) and `Sat 16:00` (2/5), which the bulletin prints as
  `10:30 AM: (Mass at St. Matthew)` and `4:00 PM: (Mass at Saint Matthew)` —
  **the partner parish's Masses**. `promptfix` never did this. One v3 run
  correctly split St. Matthew into its own site (and `SINGLE_SITE_PARISHES`
  dropped it); the other four folded them inline where nothing can catch them.
  This is the v2.5.8 cluster-bleed class, made *worse*.
- **`0216` adoration 100% -> 20%.** The bulletin states it twice and
  inconsistently: "every Wednesday after the 9:00am Mass till 8:30pm" in prose,
  "9am-8:30pm: Adoration" in the calendar. `promptfix` was stably 09:30 (the
  v2.5.4 weekday-Mass presumption); v3 flaps 09:00/09:30/10:00. **Rule 1
  contradicts the Mass-duration rule** — "transcribe, never normalise" reads as
  a prohibition on computing a start from a stated anchor.

**What to keep.** Rule 1 is the one that produced the 1259 win (10:30
fabrication 6/10 -> 0/10) and rule 3 removed the relocated vigil (4/10 -> 0/10).
Rule 2 fixed nothing measurable and is the prime suspect for `0080`.

Proposed v4, not yet run:
1. Keep rule 1, with an explicit carve-out: a start computed from a stated
   anchor ("after the 9:00 am Mass") is not a normalisation.
2. **Drop rule 2.**
3. Keep rule 3 unchanged.
4. Keep rule 4, plus a new clause that also fixes the Saturday 18:00 leak: *a
   Mass held at a different named parish belongs in THAT parish's site entry,
   not this one.* At 1259 that routes the 6:00 pm IC vigil into the Oratory
   site, where `SITE_EXCLUSIONS` already drops it.

**Prompt v4** — rule 1 given a carve-out (a start placed from a stated anchor is
derived, not normalised), **rule 2 deleted**, rule 3 unchanged, rule 4 extended
with *a Mass held at another named church belongs in THAT church's site entry*.
Run wide (`promptv4-wide`, 50 x 5, 0 errors, 23 min).

**Both v3 regressions fixed, and the first aggregate win this project has
recorded:**

| check | truth | promptfix | v3 | v4 |
|---|---|---|---|---|
| `0080` weekday confession slots | 0 | 0 | 8 | **0** |
| `our-lady-of-victory` runs w/ St. Matthew Masses | 0 | 0/5 | 4/5 | **0/5** |
| `vigil_before_noon` (roster total) | 0 | 4 | 2 | **0** |
| `sites_total` (spurious-site canary) | — | 295 | 295 | **295** |
| `1259` runs exactly correct | — | 1/5 | 5/5 | 4/5 |
| `1259` IC vigil leak | absent | 1/5 | 4/5 | 2/5 |

Mass jaccard **94% -> 96%, CI [+0.3%, +4.1%] — RESOLVES.** Every aggregate
delta previously measured in this project spanned zero; this is the first that
does not. Confession (-0.9%) and adoration (+2.7%) remain noise.

Rule 4 works the way it was meant to: `runs_collapsed` rose 39 -> 50 (the model
splits another parish's Masses into their own site more often) while
`sites_total` held at 295 (our layer then routes or drops them). It did **not**
invent sites, which was the stated risk.

**One new regression, and it is mine: the rule 1 carve-out licensed inventing
end times.** `1259` confession stability fell 94% -> 37%; its open-ended
confession slots went 50/60 -> 20/60, the model emitting `Mon 745-800`,
`Mon 1130-1200` where the bulletin says only "Monday-Friday in the Chapel: 7:45
am & 11:30 am". That is exactly the v2.5.4 bug. The carve-out says a value
"derived from the page stays derived", and the model generalised it from starts
to ends.

It is **concentrated, not systemic**: of the roster-wide -34 open-ended slots,
-30 are `1259` and no other parish moves by more than 2. v3 did not have it
(50/60), so the carve-out is the cause.

**v5 (`promptv5-wide`) tightened the carve-out to STARTS ONLY. It fixed its
target and the prompt line of work was then STOPPED. `extractor.py` is
unchanged on disk; the four rules are preserved in
`studies/noise/prompt-v5.patch`.**

v5 did what it was asked: roster-wide open-ended confession slots went back to
**29%** (promptfix 30%, v4 23%), and every earlier win held — `0080` weekday
confessions 0, `our-lady-of-victory` St. Matthew Masses 0/5,
`vigil_before_noon` 0, `sites_total` 295, `1259` exact 4/5 against 1/5 for
promptfix.

**But adoration degrades monotonically with each iteration**, and every case
was confirmed by reading the bulletin:

| check (of 5 runs) | truth | promptfix | v3 | v4 | v5 |
|---|---|---|---|---|---|
| `0042` Holy-Thursday 19:00-22:00 slot as recurring | 0 | 0 | 2 | 2 | **5** |
| `0069` runs with adoration dropped entirely | 0 | 0 | 0 | 0 | **4** |
| `21865` perpetual chapel enumerating hours | 0 | 0 | 0 | 0 | **1** |
| `0080` duplicate Wednesday sub-slot | 0 | 0 | 0 | 1 | **3** |

`0042`'s bulletin lists "Adoration/Exposition - Thursdays from Noon - 7PM"
under recurring items and "Adoration after the Mass of the Lord's Supper (7 PM)
until 10 PM" under **DATED EVENTS** (Holy Thursday). v5 pulls the Triduum
one-off into the weekly schedule in all five runs. `21865` is the v2.5.5
perpetual-chapel bug returning, with First Friday/First Saturday devotions
recorded as weekly.

**The aggregate called all of this noise** (adoration v4->v5 -1.9%, CI
[-5.4%, +1.1%]). Only the bulletins showed it.

**Why stop rather than write v6.** Each iteration fixed its target and broke
something adjacent — rule 2 broke `0080` confessions, the v4 carve-out broke
`1259` ends, v5 broke adoration in four parishes. That is not convergence. And
the headline gain does not survive scrutiny: mass jaccard was +2.1% CI
[+0.3%, +4.1%] for v4 but +1.2% CI [-0.6%, +3.3%] for v5, while **v4 vs v5 is
-0.9% CI [-2.5%, +0.6%] — the two are statistically indistinguishable.** At
n=50 this harness cannot separate them from each other or reliably from zero,
so further prompt tuning cannot be judged, only guessed at.

Four wide conditions, ~16M prompt tokens. What it bought is not a shipped
prompt but a set of findings that were invisible before and three of which were
live in production data: fabricated Sunday times, cluster bleed at
`our-lady-of-victory`, invented weekday confessions at `0080`, and the `1259`
masthead conflation.

Two residues v5 should not chase in the prompt:
- The `1259` IC vigil leak is down to 2/5 but not gone. The deterministic fix is
  the note-level `SITE_EXCLUSIONS` extension, which does not depend on the model
  agreeing with us.
- `0216` adoration is stable again (1 distinct start, so the carve-out did its
  job) but settles on 10:00, which matches neither the prose ("after the 9:00am
  Mass", ~09:30 by the weekday presumption) nor the calendar ("9am-8:30pm").
  The bulletin contradicts itself; only a `notion_fixes` entry can settle it.

**Narrow regression check** (14 parishes x 3 repeats, `promptv3-reg`, vs `promptfix`):
8 of 14 unchanged across all three categories, and 7 of the 9 stable canaries
held at 100% on Masses. Mean deltas were mass +2.4%, confession -4.5%,
adoration +8.9% — **all inside the noise band**, and n=3 gives three pairs per
parish, so those numbers carry even less weight than the n=5 aggregates v2.5.5
warned about. No systematic breakage detected; nothing here is evidence of a
confession regression either.

**Why the masthead is hard**, for whoever edits that prompt: it is a single
narrow column carrying two separately-addressed parish headers (the Cathedral,
and the Oratory of the Immaculate Conception at 4129 Superior), a colour key
(`Items in Red will be in the Chapel at 1404 E 9th St.`), a `†` livestream
marker, and a Masses block whose Sunday line mixes black and green ink. The
temporary chapel has **no proper name** — only that description. Asked to name
the site the weekday Masses belong to, the model reaches up the column and
borrows the Oratory's name, producing "The Oratory of the Immaculate Conception
(Temporary Weekday Chapel)" — a site that does not exist. The bulletin never
says it. This is why `SITE_EXCLUSIONS` needs its `unless` guard.

### v2.5.9 (2026-08-21) - Site exclusions; the Cathedral and the Oratory

**`SITE_EXCLUSIONS`** in `definitions.py`, applied by `_apply_site_exclusions()`
from `collapse_sites()` before either collapse branch. See the **Site
Exclusions** section above for the mechanism and when to reach for it over
`SINGLE_SITE_PARISHES`.

The Cathedral (`1259`) was publishing the Oratory of the Immaculate
Conception's Saturday 18:00 vigil under its own name and address.
`immat-con-cle` is its own parish with its own ICKSP bulletin that already
publishes that Mass, so this was a duplicate at the wrong location. It is
dropped rather than re-noted as a Cathedral Mass, because IC owns that row.

The Cathedral's **temporary weekday chapel still merges** — that was the whole
constraint. 10 of its 15 Masses are the chapel's weekday 7:15 and 12:00, so the
`SINGLE_SITE_PARISHES` name filter would have traded one wrong Mass for ten
missing ones plus most of the weekday confessions.

**`ManualFix.drop_masses`** — a new `set[(day, time)]` field for removing a Mass
outright, alongside the existing `mass_time_fixes` remap. Applied before the
remap and before the sanitizer.

It exists because the Aug 23 re-run gave `1259` a spurious 15th Mass: `Saturday
16:00 "Special Mass: Passion of St. John the Baptist"`. That is the feast kept
at the normal 4:30pm vigil, not an extra 4:00pm Mass — the extractor read a
day-by-day feast heading as its own celebration. Remapping 1600 -> 1630 would
have worked mechanically (the sanitizer merges duplicates on
`(day, time, language, mass_date)`) but `_merge_notes` would have folded a
week-specific feast name into the recurring vigil entry and republished it every
Saturday. Deleting is the correct shape, and nothing expressed it.

`drop_masses` is deliberately narrower than a whole-schedule replacement: a
stated 14-Mass list would go stale the moment the parish changed anything,
whereas this removes one entry and leaves the rest to the extractor.

**Watch `1259` — two consecutive runs produced a different spurious 15th Mass**
(the Oratory vigil, then the feast heading). Its bulletin prints a day-by-day
Mass-intentions listing with liturgical titles, and the extractor keeps reading
parts of it as the recurring schedule. The structural fix is a prompt change,
not another manual entry.

Guard: an exclusion matching *every* site is ignored and logged, not applied.
Verified against the real site names (Oratory present, Oratory absent,
Oratory-only, and an unrelated parish) plus a live re-run of `1259`, which
cleared the stray vigil. Note the model does not split the same way every week:
on the 2026-08-23 bulletin it folded the chapel into the Cathedral site itself
and emitted only two sites, so no collapse was needed — the exclusion still
fired correctly.

### v2.5.8 (2026-08-21) - Cluster bleed: one parish publishing another's schedule

**Reported by St. Mark's pastor:** "Mel's has the right times limited to Mel's
but lists confessions which are at Mark's. Mark's has all the mass times for
both places." Two different bugs, and in both the extractor had already done
the right thing — the pipeline discarded it.

St. Mark (`1776`) and St. Mel (`st-mel-cleveland-oh`) are a cluster with
*separate* bulletins from *different* publishers (PO and Discover Mass), each
printing the combined schedule. That is not the `Bulletin Group ID` case (one
bulletin, many rows); it is the `SINGLE_SITE_PARISHES` case.

**Bug 1 — the blind-merge branch.** The model correctly split Mark's bulletin
into two sites. `collapse_sites()` then hit the `group_size == 1` branch, which
calls `merge_sites()` with **no name filtering**, folding St. Mel's four Masses
into St. Mark's row. Mel escaped only because it was already in
`SINGLE_SITE_PARISHES`, which routes to the filtering branch instead.

Fix: `1776` added to `SINGLE_SITE_PARISHES`. Verified end-to-end against the
Aug 23 bulletin — Mark now returns its own four (Sat 16:30 vigil, Sun/Mon/Thu
8:30) and its own Sat 15:00-15:45 confession, with no St. Mel entries.

**Bug 2 — a correct extraction that could not be written.** Mel's stored
confession was St. Mark's, its own note reading "Saturdays @ St. Mark". But the
2026-08-10 run *already had this right*: it put that confession in the St. Mark
site, which the filter then correctly discarded, leaving Mel with zero
confessions. `save_extraction()` guards every field with `if site and
site.confession_times:` — an empty list is never written — so the wrong value
from an earlier run survived every correct run that followed. Repaired via
`utils.notion_fixes` (Mel's `Confessions` is now `[]`).

**This is a general hole, so runs now say what they declined to overwrite.**
(v2.5.20 extends the same guard to the *partial* case — a run that drops more
than half the stored recurring entries no longer writes that field either.)
Blanking automatically is not safe — one bad scan would wipe a good schedule —
but silence is how a wrong value lives for months while the row reads "No
Issues". `save_extraction()` now returns a list of retraction warnings for any
field the extraction came back empty on while Notion still holds a value;
`process_parish` feeds them through `warn()`, so they reach `Issue Log` and the
end-of-run summary. Adoration is deliberately excluded — `UPDATE_ADORATION` is
a standing lock, so it would warn on every parish every week. Corrupt stored
JSON counts as "present" so this warning never stands in for the v2.5.1 alarm.

**Sweep of every other `Collapsed N sites` row** (189 rows, checking which took
the blind-merge path): 17 were on the safe filtering path, 3 were blind merges.

- `2452` St. John Nepomucene — the two "sites" are its church and its
  mailing/rectory address. Merging is correct; this is what the branch is for.
- `sc-c` St. Casimir — merges in "Shrine Church of Saint Stanislaus", which is
  its own row (`0242`, 16 Masses). Added to `SINGLE_SITE_PARISHES`.
- `1259` Cathedral of St. John — **left alone, needs a decision.** It absorbs
  the Oratory of the Immaculate Conception's Saturday 18:00 vigil, which
  belongs to `immat-con-cle`. But `SINGLE_SITE_PARISHES` is the wrong tool: the
  name filter would keep only the main Cathedral site and **drop the "Cathedral
  Weekday Worship Space (Temporary Chapel)" site that supplies all its weekday
  Masses and most of its confessions.** Correct behavior is "merge sites 1+2,
  drop site 3", which neither branch expresses. Needs a per-parish site
  exclusion.

**Two stale `ManualFix` entries retired** (`1259`, `st-vincent-de-paul-elyria-oh`,
the v2.5.4 "&-as-range" confession misread). Both rows re-extracted 2026-08-15
under the fixed prompt and the pipeline produced the corrected slots on its own,
with better notes than the hand-stated ones — applying the fixes would have
overwritten good live data with staler text. Worth noting the general lesson:
a `notion_fixes` entry showing "would write" does **not** mean the data is still
broken, only that stored differs from stated. Check which one is right first.

**Also applied:** `0512` St. Andrew the Apostle, Saturday "Vigil Mass" stored as
05:30 -> 17:30. That one is a treadmill — the sanitizer only *flags* a vigil at a
morning time, so the extractor keeps reproducing it and the next run will undo
the repair.

**`scas-e` and `sc-c` are NOT duplicates** — an earlier read of this was wrong.
Two distinct Cleveland parishes: `scas-e` at 18022 Neff Road (44119,
Collinwood, saintcasimirparish.org, Webpage) and `sc-c` at 8223 Sowinski Avenue
(44103, St. Clair-Superior, stcasimir.com, Self-Hosted). Six miles apart,
separate bulletins, disjoint schedules.

**`sc-c` adoration dropped.** Its single Sunday 12:30-13:00 slot was Lent-only
published year-round - its own note read "Sundays in Lent; includes Gorzkie
Zale", and "Exact times not explicitly stated; estimated as immediately
following Mass", so the end was invented too. Adoration has no seasonal
encoding, so it cannot be stated correctly; empty beats advertising a Lenten
devotion on an August Sunday. `UPDATE_ADORATION = False` meant no normal run
would ever have corrected it.

### v2.5.7 (2026-08-14) - The Docker worker tracks GitHub instead of a frozen copy

**Problem:** the image was built with `COPY . .`, so the container ran whatever
the working tree held at build time. That made v2.5.6's Self-Hosted date-parser
fix **structurally unreachable by the machine that needed it most**: `ss-c` and
`st-basil-the-g` are processed only by the local worker, precisely because they
403 from Actions' datacenter IPs — and the fix for their filename dialects
could not reach that worker without someone remembering to rebuild the image.
The container would have kept downloading `26_07_26_bulletin.pdf` every
Saturday and stamping it with the current date, which is this project's
signature failure: a run that looks healthy while serving stale data.

**Fix:** the image now carries dependencies and the three worker scripts only.
`/app` is cloned at container start and re-synced with `origin/$BRANCH`
immediately before every run, by new **`docker/sync-code.sh`** (called from both
`entrypoint.sh` and `run-worker.sh`). Shipping a scraper fix is a `git push`;
rebuild only for the base image, the worker scripts, or the dependency
baseline. `local_worker.sh` already worked this way — the container was the
outlier.

Degraded modes are distinguished, because they call for different behavior:

| situation | exit | behavior |
|---|---|---|
| synced with origin | 0 | runs; logs `code: <old> -> <new>` and the subject line |
| fetch failed, checkout present | 1 | **runs anyway** on the old commit, logging `WARNING: update FAILED` and `continuing with STALE code` |
| no usable checkout | 2 | runs nothing |

Running last week's scraper beats skipping the week, but silently is how this
project gets bitten, so it is loud and greppable. Every run logs the commit it
is about to execute (`worker start — … @ 9f44ac5`).

Dependencies reinstall only when the pulled `requirements.txt` no longer
matches the hash in `/var/lib/bulletin-worker/requirements.sha256`. That marker
lives in the container, not in `/app`: stored alongside the code it would go
stale the moment a container is recreated against a persistent `/app` mount,
and a needed install would be skipped. `.docker-env` moved out of `/app` for
the related reason that the directory now gets `reset --hard` on every run.

**`olp-cle`** (Our Lady of Peace) joined `ss-c` and `st-basil-the-g` in the
worker's parish list — it started returning 403 to Actions and had been stuck
on its July 19 bulletin. Re-ranked live from a residential connection it
resolves `August 9 2026-1.pdf` correctly. The Unraid template also had
`STALE_DAYS` defaulting to 7, which v2.5.3 established is the off-by-one that
makes a weekly job skip itself; it is 6 now, like everywhere else.

Verified against the real repo with a scratch `APP_DIR` and stubbed
`pip`/`python`: fresh clone, already-current, behind-by-N, `AUTO_UPDATE=false`,
unreachable branch, bad repo URL, non-empty non-git directory, and
requirements-changed all produce the right output and exit code; `entrypoint.sh`
writes the crontab and a `-rw-------` env snapshot, and refuses to start on an
unreachable repo.

**Install notes** (`docker/README.md`) were rewritten for an actual Unraid
install. The build no longer opens with `git clone` — stock Unraid 6.x has no
`git` — so the tarball route is primary. Outbound HTTPS to github.com is now a
runtime dependency, not just a build one. Added the expected startup output to
check against, and corrected the first-run advice: `DRY_RUN=true` still
downloads bulletins and calls OpenAI, so it costs the same as a real run.

**Data: `GPT Timestamp` shifted back 2 days on 156 enabled rows** (154 from
`2026-08-10`, 2 from `2026-07-26`). A dispatched run on Monday 2026-08-10 had
re-stamped nearly the whole database, and with `stale_days=6` the Saturday
2026-08-15 job computes a cutoff of `2026-08-09` — every one of those rows
would have failed the strict `<` and been skipped, leaving the weekly run to
process the two parishes it cannot reach anyway. Confirmed through
`get_parishes_to_process()` itself: 2 rows before the shift, 156 after. This is
the v2.5.3 off-by-one wearing different clothes — any out-of-band run moves
every row it touches onto a cadence the scheduled job can miss.

### v2.5.6 (2026-08-10) - Date-walk looks forward; every PO/eCatholic parish was a week behind

**Bug:** Parishes Online and eCatholic name each bulletin for the **Sunday it
covers**, and parishes upload it days early. Both sources started their date
walk at `datetime.now()` and only stepped *backwards*, so the coming Sunday's
file was structurally unreachable. The weekly job runs Saturday ~13:00 UTC —
one day before every filename it most wants.

Found via St. Charles Borromeo (`2492`), serving the Aug 2 bulletin on Aug 10.
The Aug 8 run walked `20260808` → `20260803` (all 403) and took `20260802`,
while `20260809.pdf` had been sitting on the server since **Fri Aug 7 23:45
GMT** — 13½ hours before the run started.

**This was not one parish.** Of the 109 PO/eCatholic parishes that downloaded
successfully in that run, **108 took the Aug 2 bulletin**. Re-probing all 109
for their Aug 9 file: **106 were already uploaded before the run began**, 2
landed after, 1 serves no `last-modified`. Effectively the entire PO/eCatholic
set — the large majority of the database — had been publishing a week-old
bulletin every week for as long as the scrapers have existed.

**Fix:** `LOOKAHEAD_DAYS = 3` in `sources/ecatholic.py` and
`sources/parishes_online.py`; the loop is now
`range(LOOKAHEAD_DAYS, -LOOKBACK_DAYS, -1)`, newest date first, so the first
200 still wins. Backward coverage is unchanged at 30 days.

Three days is deliberate, not round. It reaches the coming Sunday from a
Saturday run, but is too short to jump a whole week: a parish that posts
unusually early can't pull a mid-week manual run onto the *following* Sunday's
bulletin, which would silently skip the current week's events. Raising it to 7
reintroduces exactly that risk.

Verified by replaying the Aug 8 walk against the live server: `2492` now
resolves `20260809.pdf`. `21865` correctly still takes Aug 2 — it genuinely has
no Aug 9 file, and the fallback is unchanged.

**Self-Hosted date parsing, found while auditing the above.** Two parishes were
refreshing daily yet extracting a months-old PDF, because `_extract_date_raw()`
couldn't read their filenames and ranking fell back to the keyword score. This
is the same failure mode v2.5.3's predecessor (`03949b9`) fixed for
`olpchurch.com` — two more filename dialects it didn't cover.

- `sp-l` Saint Peter, Loudonville was serving a bulletin from **2025-05-11**,
  456 days old. Its current `8-9-26.pdf` parsed as nothing: the old pattern
  required a `-`/`_` *before* the month, and that filename opens with it.
  Neither candidate parsed, so `5-11-25_bulletin.pdf` won on `bulletin`, +20.
- `ss-c` Saint Stephen was serving **2026-07-26**. Its filenames are `YY_MM_DD`
  (`26_08_09_bulletin.pdf`), which nothing handled, so it fell to the eCatholic
  path fallback — that reads `/2026/08/` for year+month and the *first* number
  in the name as the day, yielding **Aug 26**, a future date the parser rightly
  distrusts and zeroes. The stale July file parsed cleanly and outranked it.

`_parse_numeric_triple()` replaces the old single pattern. It finds any
separated numeric triple, generates every reading the digit-widths permit
(`YYYY-M-D`, `M-D-YYYY`, `M-D-YY`, `YY-MM-DD`), and keeps the ones that are real
dates in 2000..next year. Ambiguity resolves to `M-D-YY` (US parish sites),
unless the URL path states a month and only one reading agrees. `8-9-26` and
`26_08_09` are each unambiguous once invalid readings are discarded.

The textual-month pattern also learned that a CMS may substitute `_` or `-` for
the space (`august_9_2026.pdf`, which never parsed). That widening immediately
caused a regression the end-to-end check caught: `bulletin_JULY-2026.pdf` read
the year's first two digits as the day, giving Jul 20 — and for the August file,
a fictional Aug 20 that is >14 days out, zeroed, so a **monthly** bulletin
ranked its July issue above its August one. The day now can't run into a longer
number (`(\d{1,2})(?!\d)`), and both fall back to the 1st of their path month.

Checked against all 14 enabled Self-Hosted parishes, live: 2 fixed, 0
regressions, `hs-gh` back to its August issue. `ss-c` and `st-basil-the-g` are
processed by the local worker, so they pick this up on the worker's next run,
not from Actions.

### v2.5.5 (2026-08-05) - Noise study; adorer-coverage hours; stated durations

**New: `studies/noise/`** — a repeatable harness for measuring how much the
extraction disagrees with itself. See its README for the method. Three scripts:
`sample.py` freezes a roster, `run.py` prefetches bulletins once then extracts N
times per condition, `analyze.py` scores stability. `roster.json` is committed —
it is the frozen sample that makes conditions comparable. `cache/` (~1GB of
bulletin bytes) and `results/` (~5MB per condition) are gitignored working data.

Three design points carry the whole thing:

- **It measures the production layer.** The v2.5.4 pilot measured raw
  `extract()` and *overstated* the noise: `0036` and `0138` scored 30%/41% on
  recurring Masses because the model couldn't decide between one site and
  three, but both are in `SINGLE_SITE_PARISHES` and the collapse step resolves
  exactly that. So the collapse block was lifted out of `process_parish` into
  **`main.collapse_sites()`**, and the harness calls it and then
  `sanitize_extraction()` — production's two steps, in order.
  `process_parish` calls the same function, so the two cannot drift.
- **The bytes are held fixed.** Bulletins change weekly, so re-downloading would
  measure the parish, not the model. Every bulletin is cached once and every
  repeat of every condition reads those exact bytes.
- **Downloads are serial**, with a delay, in their own phase. Only Discover Mass
  self-limits; PO and eCatholic would otherwise take a concurrent burst per
  host. It runs once for the whole study — every later condition is cache-only.

**Baseline** (50 parishes x 5 repeats, 250/250 clean, 37 min, 3.6M prompt
tokens) settled the pilot's open question immediately: **site-count churn was
0/50**, and 0/100 on the wider run. The pilot's largest apparent instability was
an artifact of the layer it measured, not a property of the model.

| category | identical | jaccard | core |
|---|---|---|---|
| recurring Masses | 83% | 96% | 93% |
| dated Masses | 72% | 84% | 67% |
| confession | 91% | 87% | 70% |
| adoration | 86% | 83% | 68% |

`jaccard` high with `core` well below it is the signature throughout: a typical
run is mostly right, but ~30% of distinct confession/adoration slots flap at
least once in five runs. Instability is *concentrated* — only ~32 unstable
confession and ~34 adoration slots across 50 parishes.

**Bug 1 — a request for adorers read as the adoration schedule.** St.
Columbkille (`sc-p`) runs a perpetual chapel and prints the hours it is
short-handed. In 3 of 5 runs the model published those 13 hours as the
schedule, with `is_perpetual: true` set alongside — so a 24/7 chapel advertised
adoration *only* at 4 AM Monday and 10 AM Friday. The notes said the quiet part
out loud: *"Adoration chapel hour(s) needing coverage"*.

Fix, in the prompt: when adoration is perpetual, `is_perpetual: true` **is** the
whole schedule and `times` stays empty; and hours listed as needing adorers,
open hours, or sign-up slots are a staffing appeal, never a schedule. The clause
saying that listing specific hours disproves perpetual was softened to "gives
the hours it is open", so a coverage list no longer defeats `is_perpetual`.

`_drop_coverage_hours()` in `utils/sanitize.py` is the backstop, because a
prompt rule cannot be trusted to hit 5/5. It drops coverage-noted slots in three
shapes only: a perpetual chapel; a slot inside a **covered day** (`0->0` with
`end_next_day`); or a listing that is *entirely* coverage requests. A genuinely
mixed listing — a real schedule annotating one hour "adorers needed for 3 PM" —
is left alone. Replayed over all 1,000 stored extractions it touches exactly two
parishes.

**Bug 2 — a stated duration is not an invented end.** Immaculate Conception
(`immat-con-cle`) prints "30 minutes prior to Holy Mass". That states the length
of the window, so the end is *given*: before an 8:00 Mass, 7:30-8:00. But
v2.5.4's rule against computing an end from Mass timing did not distinguish a
stated duration from a bare anchor ("confessions after the 8:15 Mass"), and one
run in five dropped all nine end times. Fix: a "**a stated duration IS a stated
end**" carve-out in the confession rules, with the matching exception added to
the TIME ENCODING section so the two rules no longer contradict each other.

**Validation, and a finding about validation.** A second condition re-ran the
same 50 cached bulletins under the new prompt, plus 50 new parishes (500
extractions, 0 errors, 49 min, 7.7M prompt tokens). The paired A/B showed
confession jaccard +9% and core +22% — but a bootstrap over parishes put **every
category delta inside the noise band**, the confession gain included (core CI
-1% to +47%). Between-parish variance swamps aggregate metrics at n=50.

This is v2.5.4's lesson one level up: it is not enough to have a same-prompt
control, the *metric* has to be a targeted failure signature. Those resolve
cleanly:

| signature (batch 1, 5 runs) | before | after |
|---|---|---|
| perpetual chapels enumerating hours | 8 runs | 0 |
| adoration slots at perpetual chapels | 49 | 0 |
| open-ended confession slots | 143 | 133 |

Per-parish, `sc-p` adoration went 44% -> 100% (and confession 87% -> 100%, a
spurious vigil-anchored slot gone) and `immat-con-cle` confession 60% -> 100%
with ends present 9/9 in all five runs. The adoration fix generalized to
`21865`, a second perpetual chapel that was never targeted.

The duration carve-out is surgical: of the 10 fewer open-ended confession slots,
**9 are `immat-con-cle`**; every other parish moved +/-0.2-0.4 slots/run in both
directions. Nothing here argues for loosening v2.5.4's Mass-timing rule.

**Known unfixed — St. Edward (`1285`).** Its adoration genuinely runs
Thursday-Sunday continuously and the bulletin lists the hours needing adorers
*within* that span. The baseline published those 8 coverage hours as the
schedule in all 5 runs; the new prompt correctly rejects them but emits **no
adoration at all in 4 of 5 runs**, losing the Thu-Sun fact. Only the one run
that also emitted a `Thursday 0->0 +1d` covered day is right, and that is the
case `_drop_coverage_hours()` now handles. The remaining gap is a prompt one:
connecting "adoration Thurs-Sun + a needed-hours list" to the covered-day
encoding. Judge that change by a targeted metric, not an aggregate.

**Data repair applied 2026-08-05** via `python -m utils.notion_fixes --apply`,
which wrote three rows. `sc-p` turned out to be already clean. The sanitizer
replay found **two parishes the study never sampled** carrying the same bug —
`1608` Sacred Heart ("Adorers needed (Divine Mercy Chapel)") and `1236` Holy
Family ("Open hour currently in need of committed adorers") — both perpetual
chapels advertising three coverage hours as their entire schedule. Both now
read `is_perpetual: true` with no enumerated hours.

`ManualFix` gained an **`adoration_times`** field for `1285`, whose schedule
could not be derived from anything stored: the bulletin states it in prose
("Adoration is Thurs-Sun") while printing only the hours it needs covered, and
the other adoration lines on that page belong to the other parishes sharing the
bulletin. It is now Thursday/Friday/Saturday as covered days plus a Sunday slot
with an open end, since the bulletin never says when Sunday's adoration stops.
The replacement is stated before the sanitizer runs, so it is validated and
deduplicated on the same path as anything the extractor produces.

Downstream still shows the old values until `export.json` is rebuilt — the
Saturday Actions job does that from Notion.

### v2.5.4 (2026-08-05) - Confession time lists; optional `end_time`

**Bug 1 — a list of times read as a range.** The Cathedral of St. John (`1259`)
prints "Monday-Friday in the Chapel: 7:45 am & 11:30 am" — two short slots
bracketing the 12:00 Mass. The extraction read the `&` as a range and stored one
slot of `07:45 → 11:30`, advertising 3h45m of weekday confessions on four days.
`st-vincent-de-paul-elyria-oh` had the same misread (`09:00 → 11:00`, note
"After the 8:00 AM & 10:00 AM Masses").

Fix: the confession rules in `extractor.py` now state that `&`/`and`/`,` between
times means separate slots, that only a dash / "to" / "until" makes a range,
that a day range distributes ("Monday-Friday: 7:45 & 11:30" is ten slots), and
that a multi-hour confession window is the rare case. Verified by re-extraction:
Mon/Tue/Thu/Fri now come back as separate `07:45` and `11:30` entries.

`utils/sanitize.py` gains `_check_confession_spans()`, which **flags** (never
repairs) any confession slot ≥ `LONG_CONFESSION_MINUTES` (120). Only the
bulletin can settle whether a long window is real, so it lands in `Issue Log`
for triage. St. Brendan's (`0290`) genuine 3-hour Saturday is a known standing
false positive; if that gets noisy, add a `definitions.py` exemption set rather
than raising the threshold — 150 would stop catching the St. Vincent case, which
is exactly 120.

**Bug 2 — `start == end` meant two opposite things.** `ConfessionTime.end_time`
and `AdorationTime.end_time` were required, so a bulletin giving a start and no
end ("confessions after the 8:15 Mass") had nowhere to put it. The model
repeated the start, and the two consumers disagreed about what that meant:

- the mapboard (`utils/notion_to_json.py`) read it as "no end stated" and
  dropped the slot;
- the Introibo app read it as a **24-hour window** — it rendered "All day", and
  because identical endpoints also made its `crossesMidnight` true, those slots
  reported themselves *in progress for a full day*. Saint Columbkille's
  "confessions after the 4:00 PM Vigil" claimed confession was underway at 3 AM.

Both were right about their own data, because `00:00 → 00:00` with
`end_next_day: true` **is** a real covered day — that's how the middle days of a
multi-day adoration arrive (St. Albert the Great Tue/Wed/Thu, St. Edward Thu).
`end_next_day` is what separates the two cases, and it always did.

Fix: `end_time` is now `Optional[int]`, defaulting to `None`, on both models.
Null means "start known, end unknown"; `start == end` no longer carries meaning
anywhere.

- `extractor.py`: omit `end_time` rather than repeating the start, for both
  confessions and adoration. The global "omit the entry if a time isn't stated"
  rule was reworded — it is about a missing *start*; a slot with a known start
  and unknown end is kept.
- `utils/sanitize.py`: null guards throughout `_clean_ranges()` and the
  appointment merge (a stated end wins over `None` rather than being discarded
  by it), plus a repair that normalizes a legacy `start == end` to `None`.
  Guarded on `end_next_day` being false and the start being non-zero, so a
  covered day is never touched.
- `utils/notion_to_app.py`: emits `"end": null` and keeps the slot. It
  previously required an end and would have **dropped every open-ended slot**
  under the new schema. The app already handles a null end — same path every
  Mass entry takes — rendering a bare start time and never counting it as in
  progress.
- `utils/notion_to_json.py`: `_has_end_time()` handles null. No midnight
  special-case; `end_next_day` is the signal, and a covered day still exports
  at 1440.

**Data repair:** `python -m utils.notion_fixes --apply` rewrote the three
affected rows on 2026-08-05 — St. Peter North Ridgeville (`0141`), St. Joseph
(`0138`, adoration, which `UPDATE_ADORATION = False` means a normal run would
never fix), and St. Columbkille (`sc-p`). The four covered-day adoration rows
were correctly left alone.

**App side** (`~/Code/massgpt_app_o1_preview`, separate repo): `ScheduleEntry`
now reads `end_next_day` from the JSON instead of inferring it from
`end <= start`, which was the root cause of the 24-hour in-progress bug. The two
repos can ship in either order.

**A/B validation.** The prompt changes were checked against 20 parishes
(stratified by publisher, plus the 8 this work touched), extracting the *same
cached PDF bytes* under the old and new prompt/schema pairs. The new prompt was
run twice to establish a noise floor, which turned out to matter: two runs of the
identical prompt agree on only 65-80% of parishes, so a raw old-vs-new diff
mostly measures model nondeterminism. Judge prompt changes by systematic
metrics, not diff counts.

| metric | old | new | new (2nd) | final |
|---|---|---|---|---|
| confession spans ≥2h (the `&`-as-range tell) | 8 | 0 | 0 | 0 |
| notes admitting an estimated end | 2 | 0 | 0 | 0 |
| appointment-only slots (invented day/time) | 0 | 1 | 0 | 0 |
| open-ended slots | 3 | 27 | 26 | 25 |

Every "lost" end time checked by hand was the old prompt *inventing* one:
St. Joseph 13722 `9:30am - Adoration` became `9:30-10:30`; `0141` "After the
8:15am Mass" became `9:00-9:30`; `0054` gained an adoration slot the bulletin
never mentions (that text is its confession schedule). One old note said the
quiet part out loud: *"end time estimated by bulletin context."*

**Two fixes came out of the A/B:**

- *"By appointment" was becoming its own slot.* St. Columbkille prints
  "Saturday, 2:30-3:45 PM, the Thursday before the First Friday 7:00-8:00 PM and
  by appointment" — two slots and a trailing availability. The model invented a
  day and time for the availability, and separately anchored a confession to a
  Vigil Mass time lifted from the Mass sidebar. Making "no end" a legal encoding
  had lowered the cost of emitting a slot from a vague anchor. The prompt now
  says an appointment clause is a note on the listed slots, and that a Mass time
  elsewhere on the page is not evidence of confession at that Mass.
  `_fold_appointment_only()` in `utils/sanitize.py` is the backstop: it drops a
  slot whose note *opens* with appointment language and states no end, folding
  the note into the real slots. Narrow by design — a real window annotated "or
  by appointment" keeps its stated end.

- *Mass durations, scoped to placing starts.* The bulletins say "confessions
  after the 8:00 AM Mass" without saying when Mass ends. The prompt now supplies
  the presumption (~1 hour Sunday/vigil, ~30 min weekday) for the single purpose
  of placing a start that is anchored to a Mass, with an explicit prohibition on
  using it to synthesize an end. St. Vincent de Paul went `9:00-9:30` (old,
  invented end) → `8:00` open-ended (wrong start, during Mass) → `9:00`
  open-ended (correct).

### v2.5.3 (2026-08-04) - Browser-TLS fallback; fix weekly job skipping itself

**Bug 1 — the weekly run silently processed nothing.** The 2026-08-01 Actions
run reported success in 1m7s (vs 11m29s the week before) having found *2*
parishes to process. `get_parishes_to_process()` computed
`cutoff = today - 7 days` and kept rows where `last_run < cutoff`. The job runs
every 7 days, so a row stamped by last Saturday's run lands *exactly* on the
cutoff, fails the strict `<`, and is skipped — every parish refreshing every 14
days instead of 7, with `Issues` still reading "No Issues" from the stale run.

Fix: `stale_days` defaults to **6** in `database/notion.py`, `main.py`, and the
worker's `STALE_DAYS` (`docker/run-worker.sh`, `docker-compose.yml`).

**Bug 2 — a 403 that no User-Agent could fix.** `basilthegreat.org` sits behind
a Cloudflare **managed challenge** (`cf-mitigated: challenge`), which
fingerprints the TLS/JA3 handshake, not the headers. Every header combination
tested returned 403 from a residential IP — current Safari UA, a full Chrome
header set with `Sec-Fetch-*`, and no UA at all. A browser UA over a Python TLS
stack is a *detectable mismatch*, so it was slightly worse than sending nothing.

Fix: new `sources/fetch.py` with a `Fetcher` async context manager. It fetches
via httpx as before, and only on a "refused" status (403/429/503 — never a 404)
retries through `curl_cffi`, which replays a real browser's TLS and HTTP/2
fingerprint. Used by `sources/self_hosted.py` and `sources/webpage.py`, the two
scrapers that hit arbitrary parish sites. Verified: `st-basil-the-g` goes
403 → fallback → 200 and now pulls its PDF; `ss-c` and the three Webpage
parishes never leave the httpx path.

The fallback is second, not first, so the ~190 parishes that answer plain httpx
keep their existing path and cost. `curl_cffi>=0.16.0` added to
`requirements.txt`; if it's missing the fetch degrades to the original 403
rather than crashing.

**Possible follow-up:** St. Basil may have been a TLS block all along rather
than an IP block, in which case it no longer needs the local worker. Worth
confirming from an Actions run before shrinking `PARISHES`.

### v2.5.2 (2026-08-03) - Fix mapboard export losing 24-hour slots

**Bug:** `utils/notion_to_json.py` (the mapboard export) never learned about the
`end_next_day` flag that v2.5.0 introduced. It inferred overnight spans from
`end < start` alone, which is correct for `20:00 → 00:00` but wrong for a full
24-hour span, where `end == start` — those exported as a duration of **0** and
the LED stayed dark all day. Hit St. Albert the Great (Tue/Wed/Thu) and
St. Edward (Thu). `utils/notion_to_app.py` got the `end_next_day` handling in
v2.5.0; this file was missed.

**Fixes:**
- `_calculate_duration()` takes `end_next_day` and adds 24h when it is set
- New `_has_end_time()` drops slots where `start == end` and the flag is false —
  those are open-ended extractions ("after the 8:15am Mass"), not zero-length
  slots, and the mapboard has no way to render an unknown end. Applied in
  `_group_confessions()` and `_format_adoration()`; `is_perpetual`
  short-circuits before the guard, so 24/7 chapels are unaffected.

Affected: 3 open-ended slots dropped (St. Peter North Ridgeville,
St. Joseph, St. Columbkille), 4 restored to 1440. Zero-duration slots in the
export are now 0.

### v2.5.1 (2026-07-22) - Fix silent JSON truncation

**Bug:** Every JSON field over 2000 characters was being stored corrupt, and the
corruption was invisible. `save_extraction()` truncated values to fit Notion's
2000-char limit, which sliced JSON mid-token; `_parse_json_field()` then caught
the `JSONDecodeError` and returned `[]`. The parish exported with an empty
schedule while its run reported "No Issues".

Found via Saint Paschal Baylon (`5493`), which extracted 18 Masses and exported
zero. A scan then found **153 of 189 rows** with unparseable JSON — 152 of them
in `Events` (latent, since `Events` isn't exported yet) and one in `Mass Times`.

**Cause:** Notion's 2000-char cap is *per rich-text block*, not per property. A
property holds an array of blocks, so the data always fit — it was being written
into a single block.

**Fixes:**
- `_text_property()` splits values across as many blocks as needed (verified
  round-tripping 15,750 chars byte-identical against the live API)
- Both `_get_property()` readers join all blocks instead of taking `items[0]` —
  without this the writer fix alone would still truncate on read
- Removed the `truncate()` helper from `save_extraction()`
- `_parse_json_field()` and the adoration parse now log `CORRUPT <field> for
  parish <name>` instead of silently yielding an empty list

**Recovery:** 5493 was re-run and is correct (16 Masses; its Events now spans 3
blocks). The other 152 rows have truncated `Events` stored from before the fix —
unrecoverable in place, but each is rewritten by the next successful run, so the
weekly job repairs them. Nothing else reads `Events` today.

### v2.5.0 (2026-07-22) - Extraction Sanitizer

**New:** `utils/sanitize.py` runs on every extraction before it's logged or saved,
from a validation pass over 189 records in `export.json`. Findings split two ways:
unambiguous **repairs** (applied, written to `GPT Logs`) and **flags** (become
warnings, land in `Issue Log` for manual triage).

**Repairs:**
- Bogus midnight end-times (`240`/`2400`) → `00:00` + `end_next_day: true`
- `00:00–00:00` "time not specified" slots → dropped (read as real midnight downstream)
- Cancellations encoded as Masses (note leading with "No Mass") → dropped
- Duplicate entries on `(day, time, language, mass_date)` → merged, notes joined
- Appointment-style addenda ("or by appointment") at the same `(day, start)` →
  folded into the primary slot instead of a second entry
- Dated Masses that merely restate the recurring weekly Mass → dropped
  (kept when notes show a real difference: outdoor, festival, dedication, …)
- `language` backfilled from note keywords (Spanish, Latin, Slovenian, bilingual, …)

**Flags (never auto-repaired — only the bulletin can settle them):**
- A Mass described as a vigil at a morning time (AM/PM flip)
- `is_perpetual: true` with no hours, closure notes, or a handful of slots
- Confession/adoration notes referencing a Mass time the site's own Mass list
  lacks (missing Masses, or cross-site bleed from a shared bulletin)

**Schema:** `ConfessionTime`/`AdorationTime` gain `end_next_day: bool`. Midnight
is `0` with the flag set; `2400` and `240` are never valid.

**Export (`utils/notion_to_app.py`):**
- `end_next_day` emitted on all range entries (backfilled from `end < start`
  for rows written before the field existed)
- Dated Masses in the past are dropped at export time
- Coordinates outside Ohio's bounding box are emitted as `null` and logged

**Prompt (`extractor.py`):** explicit time-encoding rules (midnight, overnight
spans, never `0` as a placeholder), no-duplicates rule, appointment-addendum
rule, vigil-is-evening rule, and `language` into the structured field.

**Data repair:** `utils/notion_fixes.py` applied the same cleanup to the rows
already in Notion (35 parishes written on 2026-07-22). `UPDATE_ADORATION` is
`False`, so adoration rows are never rewritten by a normal run — that script is
the way to touch them.

**`VERIFIED_PERPETUAL_PARISHES`** (in `definitions.py`): parishes hand-verified
as genuine 24/7 adoration chapels. Exempt from the `is_perpetual` flag so they
don't reappear in the Issue Log every week.

**Protected statuses:** `Manual` and `Unsupported` are hand-set classifications
that `save_extraction()` and `save_issue()` now preserve instead of stamping
over. They drive the new `invite_feedback` boolean in `export.json`.

**Dependency:** `python-dotenv` added to `requirements.txt` — the export
utilities and `utils/notion_fixes.py` call `load_dotenv()` inside a
`try/ImportError`, so without it they'd fail with a bare `KeyError` on
`NOTION_API_KEY` instead of loading `.env`.

### v2.4.3 (2026-01-11) - Issue Tracking

**New feature:** Automatic issue tracking in Notion database.

**Changes:**
- Added `Issues` (status) and `Issue Log` (rich_text) fields to track processing issues
- On successful extraction: clears issues (status → "No Issues")
- On failure: sets status → "Error" with error details in Issue Log
- On warnings: sets status → "Warning" with warning details in Issue Log
- End-of-run summary prints all failures and warnings to console
- Added `ProcessResult` dataclass to track success/error/warnings per parish
- Added `save_issue()` method to `NotionClient`

**Notion setup:**
1. Add `Issues` property as Status type with options: "No Issues", "Warning", "Error"
2. Add `Issue Log` property as Text type

### v2.4.2 (2026-01-11) - Single-Site Override

**New feature:** Force specific parishes to be treated as single-site when the LLM extracts multiple sites from a shared bulletin.

**Behavior:**
1. **Filters** extracted sites to those matching the parish name (based on distinctive words like "Mary", "Paschal", etc.)
2. **Merges** all matching sites into one (combines schedules from Church + Chapel locations)

This handles two scenarios:
- Bulletins that list multiple unrelated parishes → keeps only the matching one
- Bulletins with multiple on-site locations (Church, Chapel, Shrine) → merges them together

**Changes:**
- Added `SINGLE_SITE_PARISHES` set in `definitions.py`
- Added `filter_and_merge_matching_sites()` function in `main.py`
- Uses regex to extract distinctive words from parish name (strips punctuation, stop words)
- Scores sites by word matches, keeps all with best score, merges if multiple

**Usage:**
```python
SINGLE_SITE_PARISHES: set[str] = {
    "ss-c",
    "5493",
}
```

**Example:** For "Saint Paschal Baylon" processing a bulletin with:
- "Saint Paschal Baylon Church" (score: 2) → kept
- "Saint Ann Shrine (on Saint Paschal Baylon campus)" (score: 2) → kept
- Both merged into one entry with combined mass times

### v2.4.1 (2026-01-11) - Notion API Reliability

**Bug fix:** Improved reliability of Notion database saves. Previously ~20% of parishes would fail to save when processing large batches due to API rate limits and transient errors.

**Changes:**
- Added retry logic to all Notion API calls (3 attempts with exponential backoff)
- Added rate limiting (2 concurrent requests, 400ms delay) to stay within Notion's 3 req/sec limit
- Reduced parish processing concurrency from 10 to 5
- Added logging for successful saves: `Saved extraction to Notion for parish: {id}`

**Technical details:**
- New `_rate_limited_call()` wrapper in `database/notion.py`
- New `_query_database()` and `_update_page()` methods with `@retry_async` decorator
- Retries on `APIResponseError`, `TimeoutError`, and `ConnectionError`

### v2.4.0 (2026-01-07) - Holiday Mass Support

**New feature:** Mass schedule now distinguishes between regular weekly masses and holiday/special occasion masses.

**Schema changes:**
- `MassTime.mass_date`: New optional field for holiday masses (e.g., Christmas, Easter, Holy Days)
  - `null` for regular weekly masses (shown every week)
  - Specific date (e.g., `2025-12-24`) for holiday masses (shown only on that date)

**Extraction changes:**
- Updated `SYSTEM_PROMPT` in `extractor.py` to extract both regular and holiday masses
- LLM now captures Christmas, Easter, and Holy Day masses with specific dates
- Holiday masses include descriptive notes (e.g., "Christmas Eve (Vigil of Christmas)")

**App integration:**
- Apps can filter masses by date: show regular masses (`mass_date == null`) plus any holiday masses within the current week
- Holiday masses automatically expire after their date
- Use `mass_date` presence to badge/highlight special masses in the UI

**Testing:**
- Added `test_extraction.py` for standalone testing without Notion
- Usage: `python test_extraction.py <pdf_file>`

### v2.3.0 (2026-01-07) - Webpage Bulletin Support

**New feature:** Parishes that have bulletin information directly on a webpage (not in a PDF) can now be processed.

**Schema changes:**
- `DownloadResult.content_type`: New field to indicate content type ("pdf", "html", or "text")
- `BulletinExtractor.extract()`: Now accepts `content_type` parameter

**New functionality:**
- `sources/webpage.py`: Scrapes HTML pages, extracts main content, converts to markdown
- `extractor._extract_from_text()`: New method for processing text/markdown content
- Content cleaning: Removes navigation, sidebars, headers, footers before extraction
- Uses `markdownify` library for HTML→markdown conversion
- Auto-follows "Continue Reading" / "Read More" links on blog listing pages
- Field update controls: `UPDATE_NAME`, `UPDATE_ADDRESS`, `UPDATE_CITY`, `UPDATE_ZIPCODE`, `UPDATE_PHONE`, `UPDATE_WEBSITE` flags in `database/notion.py`

**Setup:**
1. Add "Webpage" option to `Bulletin Publisher` select in Notion
2. Set `Bulletin Page URL` to the page containing bulletin content
3. The scraper extracts main content area and sends markdown to the LLM

### v2.2.1 (2026-01-06) - Self-Hosted Scraper Fixes

**Bug fixes for the self-hosted bulletin scraper:**

1. **Fixed PDF link detection**: Previously, navigation links with "bulletin" in the text (e.g., a "Bulletins" menu item) were incorrectly selected over actual PDF links. Now only `.pdf` links are considered as candidates.

2. **Added date-based sorting**: When multiple PDFs have the same relevance score, the scraper now selects the most recent bulletin by extracting and comparing dates from URLs.

3. **Improved date extraction**: Now correctly parses multiple date formats:
   - `MM-DD-YY` in filename (e.g., `bulletin_1-4-26.pdf` → Jan 4, 2026)
   - `YYYY-MM-DD` in filename or path
   - Month-day in filename with year inferred from URL path (e.g., `/2025/12/file-1-4.pdf` → Jan 4, 2026, handling year rollover)

### v2.2.0 (2026-01-06) - Self-Hosted Bulletin Support

**New feature:** Parishes that self-host bulletins on their own websites can now be processed using a generic scraper.

**Schema changes:**
- `ParishRecord.bulletin_url`: Optional URL for self-hosted bulletin pages
- `BulletinSource.download()`: Now accepts optional `bulletin_url` parameter

**New functionality:**
- `sources/self_hosted.py`: Generic scraper that finds PDF links on parish bulletin pages
- Scoring algorithm prioritizes links with "bulletin" in URL/text and recent dates
- Notion field `Bulletin Page URL` stores the page containing the PDF link

**Setup:**
1. Add "Self-Hosted" option to `Bulletin Publisher` select in Notion
2. Create `Bulletin Page URL` (url) property in Notion
3. For each self-hosted parish, set publisher to "Self-Hosted" and provide the bulletin page URL

### v2.1.0 (2026-01-05) - Multi-Site Support

**New feature:** Parishes with multiple worship sites (or parishes sharing a bulletin) can now have each site extracted and saved to its own database entry.

**Schema changes:**
- `SiteInfo` model: Per-site data (site_name, address, mass_times, confessions, adoration)
- `BulletinExtraction.sites`: Array of `SiteInfo` (replaces flat mass_times/confessions/adoration)
- `ParishRecord.bulletin_group_id`: Links parishes sharing a bulletin
- `ParishContact`: Now parish-level only (name, phone, website, email); address moved to `SiteInfo`

**New functionality:**
- `definitions.py`: Explicit site-to-parish mappings (`SITE_MAPPINGS`)
- LLM prompt instructs extraction of each worship site separately
- `match_sites_to_parishes()`: Uses explicit mappings to link extracted sites to database entries
- `NotionClient.get_bulletin_group()`: Fetches all parishes in a bulletin group
- `save_extraction(site_index=N)`: Saves specific site's data to a parish entry

**Processing flow:**
- Secondary sites (where `bulletin_group_id` != `parish_id`) are skipped
- Primary processes bulletin, extracts all sites, matches to parishes via `SITE_MAPPINGS`, saves each

### v2.0.4 (2026-01-05) - Logging Context

- Added `utils/log_context.py`: Uses `contextvars` to track parish ID through async calls
- Retry warnings/errors now include parish prefix: `[1234] St. Mary - _extract_direct failed...`
- Fixes interleaved log messages when processing multiple parishes concurrently

### v2.0.3 (2026-01-05) - Concurrency & Reliability

**Performance:**
- Concurrent processing: Parishes now process 7 at a time using `asyncio.Semaphore`
- OpenAI flex priority: Uses `service_tier="flex"` for reduced API costs

**Reliability:**
- Retry logic: OpenAI calls retry up to 3 times on timeout/connection errors with exponential backoff
- Discover Mass rate limiting: Global lock serializes all DM requests with 10-second delays to prevent lockout

**UX:**
- Logs now show parish name alongside ID: `[1234] St. Mary's Parish - Downloading...`

### v2.0.0 (2026-01-04) - Complete Rewrite

**Breaking Changes:**
- CLI simplified: Removed `-m`, `-c`, `-e`, `-i` flags. Now extracts everything in one call.
- Environment variables: Removed `BULLETIN_ASSISTANT_ID`, `AZURE_ENDPOINT`, `AZURE_KEY`.

**New Features:**
- Events extraction: Extracts parish events (retreats, fish fries, bible studies) with one-time and recurring support.
- Database abstraction: `database/base.py` protocol makes it easy to swap Notion for Supabase, Postgres, etc.
- Extraction method choice: `--method direct_pdf` (default) or `--method marker_ocr`.
- Async throughout: Uses `asyncio` and `httpx` for better batch performance.

**Cost/Performance Improvements:**
- Single LLM call: Extracts mass, confession, adoration, parish info, and events in one GPT-4o request (was 1-4 separate calls).
- No Azure OCR: Sends PDFs directly to GPT-4o, eliminating Azure Document Intelligence cost.

**Code Quality:**
- `schemas.py`: Single source of truth for all Pydantic models with proper validation.
- `sources/`: Clean abstraction per bulletin publisher.
- `database/`: Protocol-based abstraction for storage.
- `utils/retry.py`: Async retry decorator with exponential backoff.
- Time validation: Validates 24hr format (0-2359), days use `DayOfWeek` enum.

**Deleted Files:**
- `info_extract.py` - Legacy OpenAI Assistants API
- `structured_output_extract.py` - Replaced by `extractor.py`
- `download_bulletins.py` - Replaced by `sources/`
- `dm_find_url.py` - Merged into `sources/discover_mass.py`
- `notion_stuff.py` - Replaced by `database/notion.py`
- `ocr.py` - No longer needed (was Azure Document Intelligence)
- `try_gemini.py` - Unused experimental code
- `notion_to_json.py`, `notion_to_app.py` - Export scripts

**Migration from v1:**
```bash
# Update .env (remove BULLETIN_ASSISTANT_ID, AZURE_ENDPOINT, AZURE_KEY)
# Update dependencies
pip install -r requirements.txt

# Old CLI → New CLI
python main.py -avmec      →  python main.py --all
python main.py -m -c 1234  →  python main.py 1234
```
