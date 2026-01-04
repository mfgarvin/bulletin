# Changelog

## v2.0.0 - 2026-01-04

Complete rewrite of the bulletin parser for simplicity, cost reduction, and reliability.

### Architecture Changes

**Before (v1):**
```
PDF → Azure Document Intelligence (OCR) → Markdown → Multiple GPT-4o calls → Notion
      ($0.01/page)                        (1 call per data type)
```

**After (v2):**
```
PDF → GPT-4o (direct PDF input, single call) → Notion
      (vision tokens, ~$0.02-0.05 per bulletin)
```

### File Structure Changes

**Deleted (10 files):**
| File | Reason |
|------|--------|
| `info_extract.py` | Legacy OpenAI Assistants API - replaced by `extractor.py` |
| `structured_output_extract.py` | Old extraction logic - replaced by `extractor.py` |
| `download_bulletins.py` | Monolithic downloader - split into `sources/` module |
| `dm_find_url.py` | DiscoverMass scraper - merged into `sources/discover_mass.py` |
| `notion_stuff.py` | Notion client - replaced by `database/notion.py` with abstraction |
| `ocr.py` | Azure Document Intelligence wrapper - no longer needed |
| `try_gemini.py` | Incomplete experimental code - removed |
| `notion_to_json.py` | Export script - removed (can recreate if needed) |
| `notion_to_app.py` | Export script - removed (can recreate if needed) |
| `main.py` (old) | Replaced with new async implementation |

**Created (13 files):**
| File | Purpose |
|------|---------|
| `main.py` | New async CLI entrypoint with simplified flags |
| `schemas.py` | All Pydantic models in one place (single source of truth) |
| `extractor.py` | PDF extraction using GPT-4o structured output |
| `database/__init__.py` | Database module exports |
| `database/base.py` | `DatabaseClient` abstract protocol |
| `database/notion.py` | Notion implementation of `DatabaseClient` |
| `sources/__init__.py` | Sources module with factory function |
| `sources/base.py` | `BulletinSource` abstract protocol |
| `sources/parishes_online.py` | Parishes Online downloader |
| `sources/discover_mass.py` | Discover Mass downloader (includes scraping) |
| `sources/ecatholic.py` | eCatholic downloader |
| `utils/__init__.py` | Utilities module exports |
| `utils/retry.py` | Async retry decorator with exponential backoff |

**Updated (4 files):**
| File | Changes |
|------|---------|
| `requirements.txt` | Removed Azure deps, pinned notion-client==2.1.0 |
| `.env.template` | Removed BULLETIN_ASSISTANT_ID, AZURE_* variables |
| `.github/workflows/gh-actions.yml` | Updated CLI command, removed Azure secrets |
| `README.md` | Rewritten with new usage instructions |

### Breaking Changes

- **CLI simplified**: Removed `-m`, `-c`, `-e`, `-i` flags. Now extracts everything in one call.
- **Environment variables**: Removed `BULLETIN_ASSISTANT_ID`, `AZURE_ENDPOINT`, `AZURE_KEY`. Only need `OPENAI_API_KEY`, `NOTION_API_KEY`, `PARISH_DB_ID`.

### New Features

- **Events extraction**: Now extracts parish events (retreats, fish fries, bible studies, etc.) with support for both one-time and recurring events.
- **Events summary**: Generates a human-readable 2-3 sentence summary of parish activities and upcoming events.
- **Database abstraction**: Easy to swap Notion for another database (Supabase, Postgres, etc.).
- **Extraction method choice**: `--method direct_pdf` (default) or `--method marker_ocr`.
- **Async throughout**: Better performance for batch processing.

### Notion Property Changes

| Old Name | New Name | Description |
|----------|----------|-------------|
| `GPT Results` | `Mass Times` | JSON array of mass times |
| `Confession Testing` | `Confessions` | JSON array of confession times |
| `Adoration Testing` | `Adoration` | JSON object with adoration schedule |
| *(new)* | `Events` | JSON array of parish events |
| *(new)* | `Events Summary` | Human-readable summary of events |

### Improvements

- **Single LLM call**: Extracts mass times, confession times, adoration, parish info, and events in one GPT-4o request (was 1-4 separate calls).
- **No Azure OCR dependency**: Sends PDFs directly to GPT-4o, eliminating the Azure Document Intelligence step and cost.
- **Better validation**: Time fields validated as proper 24hr format (0-2359), days use enum instead of free-form strings.
- **Cleaner code structure**:
  - `schemas.py` - Single source of truth for all Pydantic models
  - `sources/` - Bulletin download abstraction per publisher
  - `database/` - Database abstraction layer
  - `utils/` - Shared utilities (retry decorator)

### Removed

- `info_extract.py` - Legacy OpenAI Assistants API approach
- `structured_output_extract.py` - Replaced by `extractor.py`
- `download_bulletins.py` - Replaced by `sources/` module
- `dm_find_url.py` - Merged into `sources/discover_mass.py`
- `notion_stuff.py` - Replaced by `database/notion.py`
- `ocr.py` - No longer needed (was Azure Document Intelligence)
- `try_gemini.py` - Unused experimental code
- `notion_to_json.py`, `notion_to_app.py` - Export scripts (can recreate if needed)

### Migration

1. Update `.env` - remove `BULLETIN_ASSISTANT_ID`, `AZURE_ENDPOINT`, `AZURE_KEY`
2. Run `pip install -r requirements.txt` (slimmer dependencies)
3. Update any scripts using old CLI flags:
   - `python main.py -avmec` → `python main.py --all`
   - `python main.py -m -c 1234` → `python main.py 1234`
