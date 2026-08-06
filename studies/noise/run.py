"""Extract the roster's bulletins N times under one condition.

Each bulletin is downloaded once into CACHE and every repeat extracts from
those exact bytes, so the model's own sampling is the only variable. Results
go through main.py's production post-processing (collapse_sites +
sanitize_extraction) — the pilot measured raw extract() and overstated the
noise, because the collapse step resolves most site-count disagreements.

    python -m studies.noise.run baseline --repeats 5
    python -m studies.noise.run baseline --repeats 5 --resume

Output: results/<condition>.json, keyed by parish_id, one entry per repeat.
Resumable: --resume keeps repeats already recorded and tops up to --repeats.
"""
import argparse
import asyncio
import json
import logging
import time
from pathlib import Path

from openai import AsyncOpenAI

from extractor import BulletinExtractor
from main import collapse_sites
from sources import get_source_for_publisher
from utils.sanitize import sanitize_extraction

HERE = Path(__file__).parent
ROSTER = HERE / "roster.json"
CACHE = HERE / "cache"
RESULTS = HERE / "results"

logging.basicConfig(level=logging.ERROR, format="%(levelname)s %(message)s")
log = logging.getLogger("noise")


class UsageRecorder:
    """Wrap the OpenAI parse call to capture token usage per request.

    The extractor discards `response.usage`, and this study should not change
    production code just to see it. Wrapping the client method keeps the
    instrumentation entirely inside the study.
    """

    def __init__(self, client: AsyncOpenAI):
        self.calls: list[dict] = []
        self._inner = client.beta.chat.completions.parse
        client.beta.chat.completions.parse = self._wrapped  # type: ignore[method-assign]

    async def _wrapped(self, *args, **kwargs):
        started = time.monotonic()
        response = await self._inner(*args, **kwargs)
        usage = getattr(response, "usage", None)
        self.calls.append({
            "seconds": round(time.monotonic() - started, 2),
            "prompt_tokens": getattr(usage, "prompt_tokens", None),
            "completion_tokens": getattr(usage, "completion_tokens", None),
        })
        return response

    def drain(self) -> list[dict]:
        calls, self.calls = self.calls, []
        return calls


def cached(pid: str) -> tuple[bytes, str] | None:
    blob, meta = CACHE / f"{pid}.bin", CACHE / f"{pid}.meta"
    if blob.exists() and meta.exists():
        return blob.read_bytes(), meta.read_text().strip()
    return None


async def prefetch(roster: list[dict], delay: float) -> dict[str, str]:
    """Download every uncached bulletin, one at a time.

    Deliberately serial with a delay between downloads. Only Discover Mass
    limits itself (a global lock and a 10s gap); Parishes Online and eCatholic
    would otherwise take a concurrent burst from one host. Because every
    condition extracts from this cache, the parish sites are hit once for the
    whole study — so there is no reason to hurry here.

    Returns {parish_id: error} for the ones that failed.
    """
    todo = [e for e in roster if not cached(e["parish_id"])]
    if not todo:
        print(f"cache complete: {len(roster)} bulletins")
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
            print(f"  [{i+1}/{len(todo)}] {pid:42} "
                  f"{len(result.pdf_bytes)/1e6:5.1f}MB {result.content_type}", flush=True)
        except Exception as e:
            errors[pid] = f"{type(e).__name__}: {e}"
            print(f"  [{i+1}/{len(todo)}] {pid:42} FAILED: {e}", flush=True)
    return errors


async def one_repeat(entry: dict, content: bytes, ctype: str,
                     extractor: BulletinExtractor, usage: UsageRecorder) -> dict:
    """One extraction, carried through production post-processing."""
    started = time.monotonic()
    extraction = await extractor.extract(content, content_type=ctype)

    collapsed = collapse_sites(
        extraction, entry["parish_id"], entry["name"], entry["group_size"]
    )
    report = sanitize_extraction(extraction, entry["parish_id"])

    return {
        "extraction": json.loads(extraction.model_dump_json()),
        "collapsed": collapsed,
        "repairs": report.repairs,
        "flags": report.flags,
        "seconds": round(time.monotonic() - started, 2),
        "usage": usage.drain(),
    }


async def run_parish(entry: dict, needed: int, existing: list,
                     extractor: BulletinExtractor, usage: UsageRecorder,
                     sem: asyncio.Semaphore) -> tuple[str, list]:
    pid = entry["parish_id"]
    async with sem:
        content, ctype = cached(pid)  # prefetch guarantees this
        reps = list(existing)
        for i in range(needed):
            try:
                reps.append(await one_repeat(entry, content, ctype, extractor, usage))
            except Exception as e:
                log.error("%s repeat %d failed: %s", pid, len(reps), e)
                reps.append({"error": f"{type(e).__name__}: {e}"})
        ok = sum(1 for r in reps if "error" not in r)
        print(f"  {pid:42} {ok}/{len(reps)} ok", flush=True)
        return pid, reps


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("condition", help="label for this run, e.g. 'baseline'")
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--resume", action="store_true",
                    help="keep repeats already recorded and top up to --repeats")
    ap.add_argument("--concurrency", type=int, default=4,
                    help="concurrent OpenAI extractions (downloads are always serial)")
    ap.add_argument("--only", help="comma-separated parish_ids, for a smoke test")
    ap.add_argument("--delay", type=float, default=3.0,
                    help="seconds between bulletin downloads during prefetch")
    ap.add_argument("--prefetch-only", action="store_true",
                    help="fill the bulletin cache and stop, running no extractions")
    args = ap.parse_args()

    if not ROSTER.exists():
        raise SystemExit("No roster.json — run `python -m studies.noise.sample` first.")
    roster = json.loads(ROSTER.read_text())
    if args.only:
        wanted = {p.strip() for p in args.only.split(",")}
        roster = [e for e in roster if e["parish_id"] in wanted]

    # Phase 1: fill the bulletin cache, serially. Every condition reads from
    # here, so parish sites are touched once for the whole study.
    failed = await prefetch(roster, args.delay)
    roster = [e for e in roster if cached(e["parish_id"])]
    if failed:
        print(f"excluded {len(failed)} parishes with no cached bulletin: "
              f"{', '.join(failed)}")
    if args.prefetch_only:
        return

    # Phase 2: extract from cache. No parish site is contacted below this line.
    RESULTS.mkdir(parents=True, exist_ok=True)
    out_path = RESULTS / f"{args.condition}.json"
    prior: dict[str, list] = {}
    if args.resume and out_path.exists():
        prior = json.loads(out_path.read_text())

    todo = []
    for entry in roster:
        existing = [r for r in prior.get(entry["parish_id"], []) if "error" not in r]
        needed = args.repeats - len(existing)
        if needed > 0:
            todo.append((entry, needed, existing))

    total_calls = sum(n for _, n, _ in todo)
    print(f"[{args.condition}] {len(todo)} parishes, {total_calls} extractions to run")
    if not total_calls:
        print("nothing to do")
        return

    client = AsyncOpenAI()
    usage = UsageRecorder(client)
    extractor = BulletinExtractor(client)
    sem = asyncio.Semaphore(args.concurrency)

    started = time.monotonic()
    results = await asyncio.gather(*(
        run_parish(e, n, x, extractor, usage, sem) for e, n, x in todo
    ))

    merged = dict(prior)
    merged.update(dict(results))
    out_path.write_text(json.dumps(merged, indent=2) + "\n")

    calls = [c for reps in merged.values() for r in reps
             if "error" not in r for c in r.get("usage", [])]
    prompt_tokens = sum(c["prompt_tokens"] or 0 for c in calls)
    completion_tokens = sum(c["completion_tokens"] or 0 for c in calls)
    errored = sum(1 for reps in merged.values() for r in reps if "error" in r)
    print(f"\n[{args.condition}] wrote {out_path}")
    print(f"  {len(merged)} parishes, {errored} errored repeats, "
          f"{time.monotonic() - started:.0f}s wall")
    print(f"  tokens: {prompt_tokens:,} prompt / {completion_tokens:,} completion")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # Assume env vars are already set
    asyncio.run(main())
