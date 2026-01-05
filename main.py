"""Parish bulletin processor - async CLI entrypoint."""

import argparse
import asyncio
import logging
import re
import sys

from openai import AsyncOpenAI

from database import NotionClient
from extractor import BulletinExtractor, ExtractionMethod
from schemas import BulletinExtraction, ParishRecord, SiteInfo
from sources import get_source_for_publisher
from utils.log_context import set_parish_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def normalize_name(name: str) -> str:
    """Normalize a name for fuzzy matching."""
    # Lowercase, remove punctuation, collapse whitespace
    name = name.lower()
    name = re.sub(r"[^\w\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    # Remove common prefixes/suffixes
    for prefix in ["st ", "saint ", "our lady of ", "church of "]:
        if name.startswith(prefix):
            name = name[len(prefix) :]
    for suffix in [" church", " catholic church", " parish", " chapel", " mission"]:
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name.strip()


def match_sites_to_parishes(
    sites: list[SiteInfo], parishes: list[ParishRecord]
) -> list[tuple[SiteInfo, ParishRecord | None]]:
    """Match extracted sites to parish records by name similarity.

    Returns list of (site, matched_parish) tuples. matched_parish is None if no match.
    """
    results: list[tuple[SiteInfo, ParishRecord | None]] = []
    unmatched_parishes = list(parishes)

    for site in sites:
        site_norm = normalize_name(site.site_name)
        best_match: ParishRecord | None = None
        best_score = 0

        for parish in unmatched_parishes:
            parish_norm = normalize_name(parish.name)

            # Exact match after normalization
            if site_norm == parish_norm:
                best_match = parish
                best_score = 100
                break

            # Substring containment
            if site_norm in parish_norm or parish_norm in site_norm:
                score = 80
                if score > best_score:
                    best_match = parish
                    best_score = score

            # Word overlap
            site_words = set(site_norm.split())
            parish_words = set(parish_norm.split())
            overlap = len(site_words & parish_words)
            if overlap > 0:
                score = overlap * 20
                if score > best_score:
                    best_match = parish
                    best_score = score

        if best_match and best_score >= 40:
            results.append((site, best_match))
            unmatched_parishes.remove(best_match)
        else:
            results.append((site, None))

    return results


async def process_parish(
    parish: ParishRecord,
    extractor: BulletinExtractor,
    db: NotionClient,
    dry_run: bool = False,
) -> bool:
    """Process a single parish bulletin. Returns True on success.

    For multi-site parishes (bulletin_group_id set), this will:
    1. Download the bulletin once from the primary parish
    2. Extract all sites from the bulletin
    3. Match and save each site to its corresponding parish record
    """
    parish_id = parish.parish_id
    parish_name = parish.name
    publisher = parish.publisher

    # Set context for logging across all async calls
    set_parish_context(parish_id, parish_name)
    log_entries: list[str] = []

    def log(msg: str):
        logger.info(f"[{parish_id}] {parish_name} - {msg}")
        log_entries.append(msg)

    try:
        # Skip secondary sites - they'll be processed when primary is processed
        if parish.is_secondary_site:
            log(f"Skipping secondary site (primary: {parish.bulletin_group_id})")
            return True  # Not a failure, just skip

        # Skip unsupported publishers
        if publisher in ["Other", ""]:
            log(f"Skipping unsupported publisher: {publisher}")
            return False

        # Get all parishes in this bulletin group (for multi-site matching)
        group_parishes: list[ParishRecord] = []
        if parish.bulletin_group_id:
            group_parishes = await db.get_bulletin_group(parish.bulletin_group_id)
            if len(group_parishes) > 1:
                log(f"Multi-site bulletin group: {len(group_parishes)} parishes")
        if not group_parishes:
            group_parishes = [parish]

        # 1. Download bulletin
        source = get_source_for_publisher(publisher)
        log(f"Downloading from {source.name}...")

        # Rate limit delay if needed
        if source.rate_limit_delay > 0:
            log(f"Waiting {source.rate_limit_delay}s (rate limit)...")
            await asyncio.sleep(source.rate_limit_delay)

        result = await source.download(parish_id)
        if not result.success:
            log(f"Download failed: {result.error}")
            return False

        log(f"Downloaded bulletin ({len(result.pdf_bytes)} bytes)")

        # 2. Extract (single LLM call)
        log("Extracting information...")
        extraction: BulletinExtraction = await extractor.extract(result.pdf_bytes)

        # Log extraction summary
        total_masses = sum(len(s.mass_times) for s in extraction.sites)
        total_confessions = sum(len(s.confession_times) for s in extraction.sites)
        log(
            f"Found: {len(extraction.sites)} sites, {total_masses} masses, "
            f"{total_confessions} confessions, {len(extraction.events)} events"
        )

        for site in extraction.sites:
            has_adoration = site.adoration.is_perpetual or len(site.adoration.times) > 0
            log(
                f"  Site '{site.site_name}': {len(site.mass_times)} masses, "
                f"{len(site.confession_times)} confessions"
                + (" (perpetual adoration)" if site.adoration.is_perpetual else "")
                + (f" ({len(site.adoration.times)} adoration times)" if site.adoration.times else "")
            )

        if extraction.extraction_notes:
            log(f"Notes: {extraction.extraction_notes}")

        # 3. Validate
        if len(extraction.sites) == 0 or all(len(s.mass_times) == 0 for s in extraction.sites):
            log("WARNING: No mass times found - may indicate extraction issue")

        # 4. Match sites to parishes and save
        if not dry_run:
            if len(extraction.sites) == 1 and len(group_parishes) == 1:
                # Simple case: one site, one parish
                await db.save_extraction(
                    parish_id=parish_id,
                    extraction=extraction,
                    bulletin_url=result.url,
                    log=log_entries,
                    site_index=0,
                )
                log("Saved to database")
            else:
                # Multi-site: match sites to parishes
                matches = match_sites_to_parishes(extraction.sites, group_parishes)

                for site_idx, (site, matched_parish) in enumerate(matches):
                    if matched_parish:
                        await db.save_extraction(
                            parish_id=matched_parish.parish_id,
                            extraction=extraction,
                            bulletin_url=result.url,
                            log=log_entries,
                            site_index=site_idx,
                        )
                        log(f"Saved site '{site.site_name}' → {matched_parish.name}")
                    else:
                        log(f"WARNING: No match for site '{site.site_name}'")

                # Check for unmatched parishes
                matched_ids = {m.parish_id for _, m in matches if m}
                for p in group_parishes:
                    if p.parish_id not in matched_ids:
                        log(f"WARNING: No site matched parish '{p.name}'")
        else:
            log("Dry run - skipping database save")

        return True

    except Exception as e:
        log(f"ERROR: {e}")
        logger.exception(f"Failed to process {parish_id}")
        return False


def parse_arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Parish bulletin processor")

    parser.add_argument(
        "parish_ids",
        nargs="*",
        help="Specific parish IDs to process",
    )
    parser.add_argument(
        "-a",
        "--all",
        action="store_true",
        help="Process all enabled parishes with stale data",
    )
    parser.add_argument(
        "-d",
        "--dry-run",
        action="store_true",
        help="Download and extract but do not save to database",
    )
    parser.add_argument(
        "--method",
        choices=["direct_pdf", "marker_ocr"],
        default="direct_pdf",
        help="Extraction method (default: direct_pdf)",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=7,
        help="Days before data is considered stale (default: 7)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    return parser.parse_args()


async def main():
    args = parse_arguments()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Initialize components
    try:
        openai_client = AsyncOpenAI()
        extractor = BulletinExtractor(
            openai_client, method=args.method  # type: ignore
        )
        db = NotionClient.from_environment()
    except Exception as e:
        logger.error(f"Failed to initialize: {e}")
        sys.exit(1)

    # Get parishes to process
    if args.all:
        parishes = await db.get_parishes_to_process(stale_days=args.stale_days)
        logger.info(f"Found {len(parishes)} parishes to process")
    elif args.parish_ids:
        parishes = []
        for pid in args.parish_ids:
            parish = await db.get_parish(pid)
            if parish:
                parishes.append(parish)
            else:
                logger.warning(f"Parish not found: {pid}")
    else:
        logger.error("Specify parish IDs or use --all")
        sys.exit(1)

    if not parishes:
        logger.error("No parishes to process")
        sys.exit(1)

    # Filter out secondary sites (they're processed with their primary)
    primary_parishes = [p for p in parishes if p.is_primary_site]
    secondary_count = len(parishes) - len(primary_parishes)
    if secondary_count > 0:
        logger.info(f"Skipping {secondary_count} secondary sites (processed with primary)")

    # Process parishes concurrently (max 7 at a time)
    semaphore = asyncio.Semaphore(7)

    async def process_with_limit(parish: ParishRecord):
        async with semaphore:
            return await process_parish(
                parish=parish,
                extractor=extractor,
                db=db,
                dry_run=args.dry_run,
            )

    results_list = await asyncio.gather(
        *[process_with_limit(p) for p in primary_parishes]
    )

    success_count = sum(1 for r in results_list if r)
    failed_count = len(results_list) - success_count
    logger.info(f"Complete: {success_count} succeeded, {failed_count} failed")


if __name__ == "__main__":
    asyncio.run(main())
