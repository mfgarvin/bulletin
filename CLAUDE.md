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
3. The scraper finds PDF links on the page, prioritizing those with "bulletin" in the URL/text and recent dates

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

## Automation

GitHub Actions runs `python main.py --all` every Saturday at 2 PM UTC (`.github/workflows/gh-actions.yml`)

**Local worker.** A few parish sites block GitHub Actions' datacenter IPs but
load fine from a residential connection, so those parishes are processed from
home instead. Two equivalent ways to do that:

- `local_worker.sh` — bare script; pulls, manages a venv, reads `.env`.
- `Dockerfile` + `docker-compose.yml` + `docker/` — the same thing as a
  container with an internal cron (built for Unraid; see `docker/README.md`).
  Code is baked into the image, so updating means rebuilding.

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
