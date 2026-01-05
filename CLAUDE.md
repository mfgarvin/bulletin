# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

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

**Data flow:** Download PDF → GPT-4o structured extraction → Notion DB

**Core modules:**
- `main.py` - Async CLI entrypoint
- `schemas.py` - All Pydantic models (single source of truth)
- `extractor.py` - PDF → LLM extraction (single call for all data types)
- `sources/` - Bulletin download abstraction (Parishes Online, Discover Mass, eCatholic)
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
- `Bulletin Publisher` (select) - Source: "Parishes Online", "Discover Mass", "eCatholic", "Other"
- `GPT Timestamp` (rich_text) - Last extraction date
- `Mass Times` (rich_text) - JSON array of mass times
- `Confessions` (rich_text) - JSON array of confession times
- `Adoration` (rich_text) - JSON object with adoration schedule
- `Events` (rich_text) - JSON array of parish events
- `Events Summary` (rich_text) - Human-readable summary of events
- `GPT Logs` (rich_text) - Extraction log
- `Link to latest bulletin` (url) - URL of processed bulletin
- `Street Address`, `City`, `Zip Code`, `Phone Number`, `Website` (rich_text) - Parish contact info

## Environment Variables

Required in `.env`:
- `OPENAI_API_KEY` - GPT-4o API key
- `NOTION_API_KEY` - Notion API token
- `PARISH_DB_ID` - Notion database ID

## Bulletin Sources

Three publishers with different URL patterns:
1. **Parishes Online (PO):** `container.parishesonline.com/bulletins/14/{id}/{date}B.pdf`
2. **Discover Mass (DM):** Scraped from `discovermass.com/church/{id}`
3. **eCatholic (EC):** `files.ecatholic.com/{id}/bulletins/{date}.pdf`

## Automation

GitHub Actions runs `python main.py --all` every Saturday at 2 PM UTC (`.github/workflows/gh-actions.yml`)

## Changelog

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
