"""Parish bulletin processor - async CLI entrypoint."""

import argparse
import asyncio
import logging
import sys
from dataclasses import dataclass, field

from openai import AsyncOpenAI

from database import NotionClient
from definitions import SINGLE_SITE_PARISHES, SITE_MAPPINGS
from extractor import BulletinExtractor, ExtractionMethod
from schemas import BulletinExtraction, ParishRecord, SiteInfo
from sources import get_source_for_publisher
from utils.log_context import set_parish_context

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def match_sites_to_parishes(
    sites: list[SiteInfo],
    parishes: list[ParishRecord],
    bulletin_group_id: str,
) -> list[tuple[SiteInfo, ParishRecord | None]]:
    """Match extracted sites to parish records using explicit mappings.

    Uses SITE_MAPPINGS from definitions.py to match site names to parish IDs.
    Returns list of (site, matched_parish) tuples. matched_parish is None if no match.
    """
    mappings = SITE_MAPPINGS.get(bulletin_group_id, {})
    parish_by_id = {p.parish_id: p for p in parishes}

    results: list[tuple[SiteInfo, ParishRecord | None]] = []
    for site in sites:
        site_lower = site.site_name.lower()
        matched: ParishRecord | None = None
        for pattern, parish_id in mappings.items():
            if pattern in site_lower:
                matched = parish_by_id.get(parish_id)
                break
        results.append((site, matched))
    return results


def merge_sites_into_one(sites: list[SiteInfo], parish_name: str) -> SiteInfo:
    """Merge multiple extracted sites into a single site.

    Used when a parish is in SINGLE_SITE_PARISHES to combine incorrectly
    split data back into one site.
    """
    from schemas import AdorationSchedule

    if not sites:
        return SiteInfo(site_name=parish_name)

    if len(sites) == 1:
        return sites[0]

    # Use first site as base, take its address info
    base = sites[0]

    # Merge all mass times, confessions, and adoration from all sites
    all_masses = []
    all_confessions = []
    all_adoration_times = []
    is_perpetual = False

    for site in sites:
        all_masses.extend(site.mass_times)
        all_confessions.extend(site.confession_times)
        all_adoration_times.extend(site.adoration.times)
        if site.adoration.is_perpetual:
            is_perpetual = True

    return SiteInfo(
        site_name=parish_name,
        address=base.address,
        city=base.city,
        state=base.state,
        zipcode=base.zipcode,
        mass_times=all_masses,
        confession_times=all_confessions,
        adoration=AdorationSchedule(times=all_adoration_times, is_perpetual=is_perpetual),
    )


@dataclass
class ProcessResult:
    """Result of processing a parish."""
    parish_id: str
    parish_name: str
    success: bool
    error: str | None = None
    warnings: list[str] = field(default_factory=list)


async def process_parish(
    parish: ParishRecord,
    extractor: BulletinExtractor,
    db: NotionClient,
    dry_run: bool = False,
) -> ProcessResult:
    """Process a single parish bulletin. Returns ProcessResult with details.

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
    warnings: list[str] = []

    def log(msg: str):
        logger.info(f"[{parish_id}] {parish_name} - {msg}")
        log_entries.append(msg)

    def warn(msg: str):
        logger.warning(f"[{parish_id}] {parish_name} - {msg}")
        log_entries.append(f"WARNING: {msg}")
        warnings.append(msg)

    try:
        # Skip secondary sites - they'll be processed when primary is processed
        if parish.is_secondary_site:
            log(f"Skipping secondary site (primary: {parish.bulletin_group_id})")
            return ProcessResult(parish_id, parish_name, success=True)

        # Skip unsupported publishers
        if publisher in ["Other", ""]:
            return ProcessResult(parish_id, parish_name, success=False, error=f"Unsupported publisher: {publisher}")

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

        result = await source.download(parish_id, bulletin_url=parish.bulletin_url)
        if not result.success:
            return ProcessResult(parish_id, parish_name, success=False, error=f"Download failed: {result.error}")

        log(f"Downloaded bulletin ({len(result.pdf_bytes)} bytes, type={result.content_type})")

        # 2. Extract (single LLM call)
        log("Extracting information...")
        extraction: BulletinExtraction = await extractor.extract(
            result.pdf_bytes, content_type=result.content_type
        )

        # Force single-site if parish is in SINGLE_SITE_PARISHES
        if parish_id in SINGLE_SITE_PARISHES and len(extraction.sites) > 1:
            log(f"Merging {len(extraction.sites)} sites into one (SINGLE_SITE_PARISHES)")
            merged = merge_sites_into_one(extraction.sites, parish_name)
            extraction.sites = [merged]

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
            warn("No mass times found - may indicate extraction issue")

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
                matches = match_sites_to_parishes(
                    extraction.sites, group_parishes, parish.bulletin_group_id
                )

                for site_idx, (site, matched_parish) in enumerate(matches):
                    if matched_parish:
                        await db.save_extraction(
                            parish_id=matched_parish.parish_id,
                            extraction=extraction,
                            bulletin_url=result.url,
                            log=log_entries,
                            site_index=site_idx,
                            skip_name_update=True,
                        )
                        log(f"Saved site '{site.site_name}' → {matched_parish.name}")
                    else:
                        warn(f"No match for site '{site.site_name}'")

                # Check for unmatched parishes
                matched_ids = {m.parish_id for _, m in matches if m}
                for p in group_parishes:
                    if p.parish_id not in matched_ids:
                        warn(f"No site matched parish '{p.name}'")
        else:
            log("Dry run - skipping database save")

        return ProcessResult(parish_id, parish_name, success=True, warnings=warnings)

    except Exception as e:
        logger.exception(f"Failed to process {parish_id}")
        return ProcessResult(parish_id, parish_name, success=False, error=str(e), warnings=warnings)


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

    # Process parishes concurrently (max 5 at a time to avoid Notion rate limits)
    semaphore = asyncio.Semaphore(5)

    async def process_with_limit(parish: ParishRecord):
        async with semaphore:
            return await process_parish(
                parish=parish,
                extractor=extractor,
                db=db,
                dry_run=args.dry_run,
            )

    results: list[ProcessResult] = await asyncio.gather(
        *[process_with_limit(p) for p in primary_parishes]
    )

    # Summarize results
    succeeded = [r for r in results if r.success]
    failed = [r for r in results if not r.success]
    with_warnings = [r for r in succeeded if r.warnings]

    logger.info(f"Complete: {len(succeeded)} succeeded, {len(failed)} failed")

    # Report failures and save issues to Notion
    if failed:
        logger.error("=" * 60)
        logger.error("FAILED PARISHES:")
        for r in failed:
            logger.error(f"  [{r.parish_id}] {r.parish_name}: {r.error}")
            if not args.dry_run:
                await db.save_issue(r.parish_id, error=r.error, warnings=r.warnings)

    # Report warnings and save to Notion
    if with_warnings:
        logger.warning("=" * 60)
        logger.warning("PARISHES WITH WARNINGS:")
        for r in with_warnings:
            logger.warning(f"  [{r.parish_id}] {r.parish_name}:")
            for w in r.warnings:
                logger.warning(f"    - {w}")
            if not args.dry_run:
                await db.save_issue(r.parish_id, warnings=r.warnings)


if __name__ == "__main__":
    asyncio.run(main())
