"""Run a year of bulletins from one parish through the extractor and analyze.

Point this at a directory of PDFs from a single parish. It will:
  1. Run each PDF through extractor.extract() (concurrent, no Notion).
  2. Save one JSON per bulletin to <outdir>/extractions/<pdf-stem>.json.
  3. Write <outdir>/summary.md highlighting holiday Masses, schedule drift,
     and anomalies that suggest extraction noise vs. real change.

Usage:
    python -m utils.year_harness <pdf_dir> [--out <outdir>] [--concurrency N]

Example:
    python -m utils.year_harness ./bulletins/st-sebastian-2025 --out ./harness-out
"""

import argparse
import asyncio
import json
import logging
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from openai import AsyncOpenAI

from extractor import BulletinExtractor
from schemas import BulletinExtraction

logger = logging.getLogger("year_harness")

WEEKDAY_ORDER = {
    "Sunday": 0, "Monday": 1, "Tuesday": 2, "Wednesday": 3,
    "Thursday": 4, "Friday": 5, "Saturday": 6,
}


async def _extract_one(
    pdf_path: Path,
    extractor: BulletinExtractor,
    sem: asyncio.Semaphore,
) -> tuple[Path, BulletinExtraction | None, str | None]:
    """Extract a single bulletin; returns (path, extraction, error)."""
    async with sem:
        try:
            pdf_bytes = pdf_path.read_bytes()
            extraction = await extractor.extract(pdf_bytes, content_type="pdf")
            logger.info("extracted %s (%d sites)", pdf_path.name, len(extraction.sites))
            return pdf_path, extraction, None
        except Exception as e:
            logger.exception("failed %s", pdf_path.name)
            return pdf_path, None, str(e)


def _mass_key(m: dict) -> tuple[str, int, str | None]:
    """Identity tuple for a regular weekly Mass — day, time, language."""
    return (m["day"], m["time"], m.get("language"))


def _summarize(
    results: list[tuple[Path, BulletinExtraction | None, str | None]],
    outdir: Path,
) -> str:
    """Build the summary.md content."""
    total = len(results)
    succeeded = [(p, e) for p, e, err in results if e is not None]
    failed = [(p, err) for p, _, err in results if err is not None]

    lines: list[str] = []
    lines.append(f"# Year-of-Bulletins Harness Summary\n")
    lines.append(f"- PDFs processed: **{total}**")
    lines.append(f"- Succeeded: **{len(succeeded)}**")
    lines.append(f"- Failed: **{len(failed)}**\n")

    if failed:
        lines.append("## Failures\n")
        for path, err in failed:
            lines.append(f"- `{path.name}`: {err}")
        lines.append("")

    if not succeeded:
        return "\n".join(lines)

    # --- Regular weekly Mass stability ---
    # Count how many bulletins each (day, time, language) Mass appears in.
    weekly_counter: Counter[tuple[str, int, str | None]] = Counter()
    for _, extraction in succeeded:
        seen_this_bulletin: set[tuple[str, int, str | None]] = set()
        for site in extraction.sites:
            for m in site.mass_times:
                if m.mass_date is None:
                    key = (m.day.value, m.time, m.language)
                    if key not in seen_this_bulletin:
                        weekly_counter[key] += 1
                        seen_this_bulletin.add(key)

    n = len(succeeded)
    stable = [k for k, c in weekly_counter.items() if c == n]
    drifting = sorted(
        [(k, c) for k, c in weekly_counter.items() if c < n],
        key=lambda kc: (-kc[1], WEEKDAY_ORDER.get(kc[0][0], 99), kc[0][1]),
    )

    lines.append(f"## Regular Weekly Mass Schedule (across {n} bulletins)\n")
    if stable:
        lines.append(f"**Stable masses** (present in every bulletin):\n")
        for day, time, lang in sorted(stable, key=lambda k: (WEEKDAY_ORDER.get(k[0], 99), k[1])):
            lang_str = f" ({lang})" if lang else ""
            lines.append(f"- {day} {time:04d}{lang_str}")
        lines.append("")

    if drifting:
        lines.append(f"**Drifting masses** (missing from some bulletins — possible extraction noise or real schedule change):\n")
        for (day, time, lang), count in drifting:
            lang_str = f" ({lang})" if lang else ""
            lines.append(f"- {day} {time:04d}{lang_str} — appears in {count}/{n} bulletins")
        lines.append("")

    # --- Holiday Masses ---
    holiday_by_date: dict[str, list[tuple[Path, Any]]] = defaultdict(list)
    for path, extraction in succeeded:
        for site in extraction.sites:
            for m in site.mass_times:
                if m.mass_date is not None:
                    holiday_by_date[m.mass_date.isoformat()].append((path, m))

    lines.append(f"## Holiday / Special Masses Detected\n")
    if not holiday_by_date:
        lines.append("_No holiday Masses (mass_date) found in any bulletin._\n")
    else:
        for date_str in sorted(holiday_by_date.keys()):
            entries = holiday_by_date[date_str]
            lines.append(f"### {date_str} ({len(entries)} entries across bulletins)\n")
            seen: set[tuple[str, int, str | None]] = set()
            for path, m in entries:
                key = (m.day.value, m.time, m.notes)
                if key in seen:
                    continue
                seen.add(key)
                lang_str = f" ({m.language})" if m.language else ""
                note_str = f" — {m.notes}" if m.notes else ""
                lines.append(f"- {m.day.value} {m.time:04d}{lang_str}{note_str} (from `{path.name}`)")
            lines.append("")

    # --- Adoration sanity ---
    perpetual_count = 0
    for _, extraction in succeeded:
        if any(site.adoration.is_perpetual for site in extraction.sites):
            perpetual_count += 1
    lines.append(f"## Adoration\n")
    lines.append(f"- Bulletins marking perpetual adoration: **{perpetual_count}/{n}**")
    if 0 < perpetual_count < n:
        lines.append(f"  - ⚠️ Inconsistent — perpetual flag flips across bulletins, suggests extraction instability.")
    lines.append("")

    # --- Anomalies ---
    lines.append("## Anomalies\n")
    anomalies = []
    for path, extraction in succeeded:
        site_count = len(extraction.sites)
        if site_count == 0:
            anomalies.append(f"- `{path.name}`: zero sites extracted")
            continue
        if site_count > 1:
            anomalies.append(f"- `{path.name}`: {site_count} sites extracted — verify this parish is multi-site")
        if all(len(s.mass_times) == 0 for s in extraction.sites):
            anomalies.append(f"- `{path.name}`: no Mass times extracted")
    if not anomalies:
        lines.append("_None._")
    else:
        lines.extend(anomalies)
    lines.append("")

    return "\n".join(lines)


async def main_async(args: argparse.Namespace) -> int:
    pdf_dir = Path(args.pdf_dir)
    if not pdf_dir.is_dir():
        logger.error("not a directory: %s", pdf_dir)
        return 1

    pdfs = sorted(pdf_dir.glob("*.pdf"))
    if not pdfs:
        logger.error("no PDFs found in %s", pdf_dir)
        return 1

    outdir = Path(args.out)
    extractions_dir = outdir / "extractions"
    extractions_dir.mkdir(parents=True, exist_ok=True)

    logger.info("processing %d PDFs from %s (concurrency=%d)", len(pdfs), pdf_dir, args.concurrency)

    client = AsyncOpenAI()
    extractor = BulletinExtractor(client)
    sem = asyncio.Semaphore(args.concurrency)

    results = await asyncio.gather(*[_extract_one(p, extractor, sem) for p in pdfs])

    # Persist per-bulletin JSON
    for path, extraction, err in results:
        out_path = extractions_dir / f"{path.stem}.json"
        if extraction is not None:
            out_path.write_text(
                json.dumps(extraction.model_dump(mode="json"), indent=2, ensure_ascii=False)
            )
        else:
            out_path.write_text(json.dumps({"error": err}, indent=2))

    # Summary
    summary = _summarize(results, outdir)
    (outdir / "summary.md").write_text(summary)

    logger.info("wrote %d extraction files + summary.md to %s", len(results), outdir)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_dir", help="Directory of PDFs (one parish, ~52 bulletins)")
    parser.add_argument("--out", default="./harness-out", help="Output directory (default: ./harness-out)")
    parser.add_argument("--concurrency", type=int, default=5, help="Concurrent extractions (default: 5)")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
