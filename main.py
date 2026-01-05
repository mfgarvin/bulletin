"""Parish bulletin processor - async CLI entrypoint."""

import argparse
import asyncio
import logging
import sys

from openai import AsyncOpenAI

from database import NotionClient
from extractor import BulletinExtractor, ExtractionMethod
from schemas import BulletinExtraction
from sources import get_source_for_publisher

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def process_parish(
    parish_id: str,
    parish_name: str,
    publisher: str,
    extractor: BulletinExtractor,
    db: NotionClient,
    dry_run: bool = False,
) -> bool:
    """Process a single parish bulletin. Returns True on success."""
    log_entries: list[str] = []

    def log(msg: str):
        logger.info(f"[{parish_id}] {parish_name} - {msg}")
        log_entries.append(msg)

    try:
        # Skip unsupported publishers
        if publisher in ["Other", ""]:
            log(f"Skipping unsupported publisher: {publisher}")
            return False

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

        log(
            f"Found: {len(extraction.mass_times)} masses, "
            f"{len(extraction.confession_times)} confessions, "
            f"{len(extraction.events)} events"
        )

        if extraction.adoration.is_perpetual:
            log("Perpetual adoration detected")
        elif extraction.adoration.times:
            log(f"Found {len(extraction.adoration.times)} adoration times")

        if extraction.extraction_notes:
            log(f"Notes: {extraction.extraction_notes}")

        # 3. Validate
        if len(extraction.mass_times) == 0:
            log("WARNING: No mass times found - may indicate extraction issue")

        # 4. Save
        if not dry_run:
            await db.save_extraction(
                parish_id=parish_id,
                extraction=extraction,
                bulletin_url=result.url,
                log=log_entries,
            )
            log("Saved to database")
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

    # Process parishes concurrently (max 7 at a time)
    semaphore = asyncio.Semaphore(7)

    async def process_with_limit(parish):
        async with semaphore:
            return await process_parish(
                parish.parish_id,
                parish.name,
                parish.publisher,
                extractor,
                db,
                dry_run=args.dry_run,
            )

    results_list = await asyncio.gather(
        *[process_with_limit(p) for p in parishes]
    )

    success_count = sum(1 for r in results_list if r)
    failed_count = len(results_list) - success_count
    logger.info(f"Complete: {success_count} succeeded, {failed_count} failed")


if __name__ == "__main__":
    asyncio.run(main())
