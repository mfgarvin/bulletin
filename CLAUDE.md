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
```

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

## Changelog

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
