"""Measure what the verification layer says on a real run.

v2.5.14-16 added three checks that only ever *warn*: the fabrication check
(`verify_times`), and the change verification (`verify_changes`) with its diff,
re-extraction and text-layer steps. The changelog committed to measuring their
alarm rate before letting any of it gate a write, because the rate is the whole
question — a check that fires on a third of the roster is not usable as a
signal no matter how sound each individual warning is.

This runs the production verification path over a sample of parishes and
reports what a human would have to read on Saturday morning.

    python -m studies.verification.run --sample 50
    python -m studies.verification.run --sample 50 --resume

**Nothing is written to Notion.** Stored schedules are read (that is what the
diff compares against); `save_extraction` is never called.

The sample deliberately excludes `studies/noise/roster.json`: those 100
parishes have been read repeatedly and several were hand-repaired, so they are
no longer representative of what an ordinary Saturday looks like.

Note what a diff means here. Stored data was written by the most recent real
run, and PO/eCatholic name a bulletin for the Sunday it covers, so a run a day
or two later usually reads *the same bytes the stored value came from*. Under
that condition a diff is extraction noise, not the parish changing anything —
which is exactly the quantity worth measuring. Where a self-hosted or webpage
source has rolled to a newer bulletin, a diff may be real; the per-parish
output names the bulletin so the two can be told apart.
"""

import argparse
import asyncio
import json
import logging
import random
import time
from collections import Counter
from pathlib import Path

from openai import AsyncOpenAI

from database.notion import NotionClient
from extractor import BulletinExtractor
from main import _pair_sites, collapse_sites
from sources import get_source_for_publisher
from utils.sanitize import sanitize_extraction
from utils.verify_times import verify_times_against_source
import utils.verify_changes as verify_changes

HERE = Path(__file__).parent
CACHE = HERE / "cache"
RESULTS = HERE / "results"
NOISE_ROSTER = HERE.parent / "noise" / "roster.json"

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
log = logging.getLogger("verification")


def noise_roster_ids() -> set[str]:
    if not NOISE_ROSTER.exists():
        return set()
    data = json.loads(NOISE_ROSTER.read_text())
    entries = data if isinstance(data, list) else data.get("parishes", [])
    return {e["parish_id"] for e in entries}


async def build_sample(db: NotionClient, size: int, seed: int) -> list[dict]:
    """Pick `size` primary parishes outside the noise roster, deterministically."""
    excluded = noise_roster_ids()
    everyone = await db._get_all_parishes()
    by_id = {p.parish_id: p for p in everyone}

    pool = [
        p for p in everyone
        if p.enabled
        and p.publisher
        and p.publisher != "Other"
        and p.parish_id
        and p.parish_id not in excluded
        and p.is_primary_site  # secondaries are fed by their primary
    ]
    pool.sort(key=lambda p: p.parish_id)  # stable order before seeding
    random.Random(seed).shuffle(pool)

    sample = []
    for p in pool[:size]:
        group = [
            q for q in everyone
            if q.bulletin_group_id and q.bulletin_group_id == p.bulletin_group_id
        ] if p.bulletin_group_id else []
        sample.append({
            "parish_id": p.parish_id,
            "name": p.name,
            "publisher": p.publisher,
            "bulletin_url": p.bulletin_url,
            "group_ids": [q.parish_id for q in group] or [p.parish_id],
        })
    return sample


def cached(pid: str) -> tuple[bytes, str] | None:
    blob, meta = CACHE / f"{pid}.bin", CACHE / f"{pid}.meta"
    if blob.exists() and meta.exists():
        return blob.read_bytes(), meta.read_text().strip()
    return None


async def prefetch(sample: list[dict], delay: float) -> dict[str, str]:
    """Serial, delayed downloads — the noise study's rule, for the same reason.

    Only Discover Mass limits itself; the other hosts would otherwise take a
    concurrent burst. Caching also means a re-run of this study costs nothing
    at the parish sites.
    """
    todo = [e for e in sample if not cached(e["parish_id"])]
    if not todo:
        print(f"cache complete: {len(sample)} bulletins")
        return {}
    CACHE.mkdir(parents=True, exist_ok=True)
    print(f"prefetching {len(todo)} bulletins serially ({delay}s apart)")
    errors: dict[str, str] = {}
    for i, entry in enumerate(todo):
        pid = entry["parish_id"]
        if i:
            await asyncio.sleep(delay)
        try:
            source = get_source_for_publisher(entry["publisher"])
            result = await source.download(pid, entry["bulletin_url"])
            if not result.success or not result.pdf_bytes:
                raise RuntimeError(result.error or "no content")
            (CACHE / f"{pid}.bin").write_bytes(result.pdf_bytes)
            (CACHE / f"{pid}.meta").write_text(result.content_type)
            (CACHE / f"{pid}.url").write_text(result.url or "")
            print(f"  [{i+1}/{len(todo)}] {pid:40} "
                  f"{len(result.pdf_bytes)/1e6:5.1f}MB {result.content_type}", flush=True)
        except Exception as e:
            errors[pid] = f"{type(e).__name__}: {e}"
            print(f"  [{i+1}/{len(todo)}] {pid:40} FAILED: {e}", flush=True)
    return errors


async def verify_one(entry: dict, db: NotionClient, extractor: BulletinExtractor,
                     sem: asyncio.Semaphore) -> dict:
    """One parish through the production verification path. No writes."""
    pid = entry["parish_id"]
    async with sem:
        content, ctype = cached(pid)
        started = time.monotonic()
        extraction = await extractor.extract(content, content_type=ctype)
        group_size = len(entry["group_ids"])
        collapsed = collapse_sites(extraction, pid, entry["name"], group_size)
        report = sanitize_extraction(extraction, pid)

        fabrication = verify_times_against_source(extraction, content, ctype)

        # Rebuild what the save step would pair, then diff against Notion.
        class _P:  # the two attributes _pair_sites reads
            parish_id = pid
            bulletin_group_id = entry["group_ids"][0] if group_size > 1 else None
        group_records = []
        if group_size > 1:
            group_records = await db.get_bulletin_group(_P.bulletin_group_id)
        parish_record = next(
            (r for r in group_records if r.parish_id == pid), None
        )
        if parish_record is None:
            parish_record = await db.get_parish(pid)
            group_records = [parish_record]

        pairings = _pair_sites(extraction, parish_record, group_records)
        stored = {}
        for target in pairings:
            got = await db.get_stored_schedules(target)
            if got is not None:
                stored[target] = got

        async def _reextract():
            second = await extractor.extract(content, content_type=ctype)
            collapse_sites(second, pid, entry["name"], group_size)
            sanitize_extraction(second, pid)
            return _pair_sites(second, parish_record, group_records)

        changes = await verify_changes.verify_schedule_changes(
            pairings, stored, _reextract, content, ctype
        )

        return {
            "parish_id": pid,
            "name": entry["name"],
            "publisher": entry["publisher"],
            "bulletin": (CACHE / f"{pid}.url").read_text() if (CACHE / f"{pid}.url").exists() else "",
            "collapsed": collapsed,
            "repairs": report.repairs,
            "flags": report.flags,
            "fabrication_warnings": fabrication,
            "change_warnings": changes,
            "masses": sum(len(s.mass_times) for s in extraction.sites),
            "seconds": round(time.monotonic() - started, 2),
        }


def summarize(rows: list[dict]) -> None:
    n = len(rows)
    print("\n" + "=" * 72)
    print(f"VERIFICATION ALARM RATE over {n} parishes")
    print("=" * 72)

    fab = [r for r in rows if r["fabrication_warnings"]]
    chg = [r for r in rows if r["change_warnings"]]
    flagged = [r for r in rows if r["flags"]]

    def pct(k):
        return f"{k} ({k/n:.0%})" if n else str(k)

    print(f"  fabrication warnings (verify_times) : {pct(len(fab))} parishes")
    print(f"  change warnings      (verify_changes): {pct(len(chg))} parishes")
    print(f"  sanitizer flags                      : {pct(len(flagged))} parishes")
    print(f"  parishes with NO warning of any kind : "
          f"{pct(n - len({r['parish_id'] for r in fab+chg+flagged}))}")

    verdicts = Counter()
    for r in chg:
        for w in r["change_warnings"]:
            if "NOT reproduced" in w:
                verdicts["noise (not reproduced)"] += 1
            elif "reproduced on a second" in w:
                verdicts["reproduced"] += 1
            else:
                verdicts["unverified"] += 1
    print("\n  change warnings by verdict:")
    for k, v in verdicts.most_common():
        print(f"    {k:26} {v}")

    if fab:
        print("\n  FABRICATION (a time the bulletin never prints):")
        for r in fab:
            for w in r["fabrication_warnings"]:
                print(f"    [{r['parish_id']}] {w}")

    repro = [(r, w) for r in chg for w in r["change_warnings"]
             if "reproduced on a second" in w and "NOT reproduced" not in w]
    if repro:
        print(f"\n  REPRODUCED changes — the ones worth a human's time ({len(repro)}):")
        for r, w in repro:
            print(f"    [{r['parish_id']}] {r['name'][:34]}")
            print(f"        {w}")

    noise = [(r, w) for r in chg for w in r["change_warnings"] if "NOT reproduced" in w]
    if noise:
        print(f"\n  SELF-LABELLED NOISE ({len(noise)}) — suppressible by gating the write:")
        for r, w in noise[:12]:
            print(f"    [{r['parish_id']}] {w.split(' [')[0]}")
        if len(noise) > 12:
            print(f"    ... and {len(noise)-12} more")


async def main_async(args) -> None:
    db = NotionClient.from_environment()
    RESULTS.mkdir(parents=True, exist_ok=True)
    sample_path = RESULTS / "sample.json"

    if sample_path.exists() and args.resume:
        sample = json.loads(sample_path.read_text())
        print(f"resuming with {len(sample)} sampled parishes")
    else:
        sample = await build_sample(db, args.sample, args.seed)
        sample_path.write_text(json.dumps(sample, indent=1))
        print(f"sampled {len(sample)} parishes (seed {args.seed})")

    errors = await prefetch(sample, args.delay)
    usable = [e for e in sample if cached(e["parish_id"])]
    if errors:
        print(f"\n{len(errors)} download failures (excluded): {list(errors)}")

    # Every diff should get its re-extraction; the production budget is a cost
    # guard for a 189-parish run, not a measurement limit.
    verify_changes._budget = len(usable) + 10

    client = AsyncOpenAI()
    extractor = BulletinExtractor(client)
    sem = asyncio.Semaphore(args.concurrency)
    print(f"\nextracting {len(usable)} parishes (concurrency {args.concurrency})")

    started = time.monotonic()
    results = await asyncio.gather(
        *(verify_one(e, db, extractor, sem) for e in usable),
        return_exceptions=True,
    )
    rows, failures = [], []
    for entry, r in zip(usable, results):
        if isinstance(r, Exception):
            failures.append((entry["parish_id"], f"{type(r).__name__}: {r}"))
        else:
            rows.append(r)
            marks = []
            if r["fabrication_warnings"]:
                marks.append("FAB")
            if r["change_warnings"]:
                marks.append("CHG")
            if r["flags"]:
                marks.append("flag")
            print(f"  {r['parish_id']:40} {r['masses']:2d} masses "
                  f"{' '.join(marks)}", flush=True)

    (RESULTS / "run.json").write_text(json.dumps(rows, indent=1))
    print(f"\ndone in {(time.monotonic()-started)/60:.1f} min; "
          f"{len(failures)} extraction failures")
    for pid, err in failures:
        print(f"  FAILED {pid}: {err}")
    summarize(rows)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--sample", type=int, default=50)
    p.add_argument("--seed", type=int, default=20260830)
    p.add_argument("--delay", type=float, default=2.0)
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("--resume", action="store_true")
    args = p.parse_args()
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    asyncio.run(main_async(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
