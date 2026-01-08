"""Test bulletin extraction on a local PDF without Notion."""

import asyncio
import json
import os
import sys

from dotenv import load_dotenv
from openai import AsyncOpenAI

from extractor import BulletinExtractor

# Load environment variables from .env file
load_dotenv()


async def main():
    if len(sys.argv) < 2:
        print("Usage: python test_extraction.py <pdf_file>")
        sys.exit(1)

    pdf_path = sys.argv[1]

    # Read PDF file
    print(f"Reading {pdf_path}...")
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()
    print(f"PDF size: {len(pdf_bytes):,} bytes\n")

    # Initialize extractor
    print("Initializing extractor...")
    openai_client = AsyncOpenAI()
    extractor = BulletinExtractor(openai_client, method="direct_pdf")

    # Extract information
    print("Extracting information from bulletin...\n")
    extraction = await extractor.extract(pdf_bytes, content_type="pdf")

    # Print results
    print("=" * 80)
    print("EXTRACTION RESULTS")
    print("=" * 80)

    # Parish contact info
    print("\nPARISH CONTACT INFO:")
    print(f"  Name: {extraction.parish_info.name or 'N/A'}")
    print(f"  Phone: {extraction.parish_info.phone or 'N/A'}")
    print(f"  Website: {extraction.parish_info.website or 'N/A'}")
    print(f"  Email: {extraction.parish_info.email or 'N/A'}")

    # Sites and schedules
    print(f"\nSITES: {len(extraction.sites)}")
    for i, site in enumerate(extraction.sites, 1):
        print(f"\n  --- Site {i}: {site.site_name} ---")
        if site.address:
            print(f"  Address: {site.address}")
            if site.city or site.state or site.zipcode:
                print(f"           {site.city or ''}, {site.state or ''} {site.zipcode or ''}")

        # Mass times
        print(f"\n  MASS TIMES ({len(site.mass_times)}):")
        if site.mass_times:
            for mass in site.mass_times:
                time_str = f"{mass.time:04d}" if mass.time else "TBA"
                date_str = f" [{mass.mass_date}]" if mass.mass_date else ""
                lang = f" ({mass.language})" if mass.language and mass.language != "English" else ""
                notes = f" - {mass.notes}" if mass.notes else ""
                print(f"    {mass.day.value}: {time_str}{date_str}{lang}{notes}")
        else:
            print("    (none)")

        # Confession times
        print(f"\n  CONFESSION TIMES ({len(site.confession_times)}):")
        if site.confession_times:
            for conf in site.confession_times:
                start = f"{conf.start_time:04d}" if conf.start_time else "TBA"
                end = f"{conf.end_time:04d}" if conf.end_time else ""
                time_range = f"{start}-{end}" if end else start
                notes = f" - {conf.notes}" if conf.notes else ""
                print(f"    {conf.day.value}: {time_range}{notes}")
        else:
            print("    (none)")

        # Adoration
        print(f"\n  ADORATION:")
        if site.adoration.is_perpetual:
            print(f"    Perpetual (24/7)")
        elif site.adoration.times:
            for ador in site.adoration.times:
                start = f"{ador.start_time:04d}" if ador.start_time else "TBA"
                end = f"{ador.end_time:04d}" if ador.end_time else ""
                time_range = f"{start}-{end}" if end else start
                notes = f" - {ador.notes}" if ador.notes else ""
                print(f"    {ador.day.value}: {time_range}{notes}")
        else:
            print("    (none)")

    # Events
    print(f"\nEVENTS ({len(extraction.events)}):")
    if extraction.events:
        for event in extraction.events:
            freq = event.frequency.value
            print(f"\n  {event.name} [{freq}]")
            if event.description:
                print(f"    Description: {event.description}")
            if event.frequency.value == "one_time" and event.event_date:
                print(f"    Date: {event.event_date}")
            if event.frequency.value == "recurring" and event.day_of_week:
                print(f"    Day: {event.day_of_week.value}")
            if event.time:
                print(f"    Time: {event.time:04d}")
            if event.location:
                print(f"    Location: {event.location}")
    else:
        print("  (none)")

    # Events summary
    if extraction.events_summary:
        print(f"\nEVENTS SUMMARY:")
        print(f"  {extraction.events_summary}")

    # Extraction notes
    if extraction.extraction_notes:
        print(f"\nEXTRACTION NOTES:")
        print(f"  {extraction.extraction_notes}")

    # JSON output
    print("\n" + "=" * 80)
    print("JSON OUTPUT")
    print("=" * 80)
    print(json.dumps(extraction.model_dump(), indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(main())
