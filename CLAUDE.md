# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Notes / Pending Work

- **~40-50 parishes need self-hosted setup** - These parishes either don't have bulletins on the major publishers or self-host on their own websites. They need `Bulletin Page URL` configured in Notion.
- **Address discrepancies file** - `address_discrepancies.txt` contains 9 parishes with missing or incorrect addresses in Notion (verified 2026-01-06). Not committed to git.
- **Self-Hosted scraper enhancement** - Some parish bulletin pages (e.g., sfds-a, sak-cle) link to weekly subpages that contain the actual PDF, rather than having PDF links directly on the main page. A potential enhancement would be to follow links one level deep to find PDFs. Affected parishes: sfa-gm (Google Drive), sfds-a (weekly subpages), sak-cle (dated subpages in Korean).
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
# Export raw Notion data to export.json (all fields as-is)
python -m utils.notion_to_json

# Export app-friendly format (12hr times, weekday groupings)
python -m utils.notion_to_app

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
- `utils/notion_to_json.py` - Export raw Notion data to JSON
- `utils/notion_to_app.py` - Export app-friendly formatted data

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

GitHub Actions runs `python main.py --all` every Saturday at 2 PM UTC (`.github/workflows/gh-actions.yml`)

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

- `docs/integration/` — how downstream consumers read the export (Flutter app).
- `docs/notes/` — **gitignored.** Local working notes and source data for
  hand-verification.
- `temp/`, `reference.py` — gitignored local scratch. `reference.py` is the LED
  mapboard driver, kept as a reference for what `export.json` has to feed; the
  mapboard repo owns it.

## Changelog

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
