"""Freeze the parish roster the noise study runs against.

The roster is written once and committed, so every later condition runs over
the same parishes. Re-running this would reshuffle the sample and make runs
incomparable — it refuses to overwrite unless you pass --force.

    python -m studies.noise.sample
"""
import argparse
import asyncio
import json
from pathlib import Path

from database import NotionClient
from definitions import SINGLE_SITE_PARISHES, SITE_MAPPINGS, VERIFIED_PERPETUAL_PARISHES

ROSTER = Path(__file__).parent / "roster.json"

# Parishes with a known structural reason to be unstable. These are the cases
# the study most needs to see, so they are included regardless of the quota.
FORCED = [
    # shared bulletin where the sites name each other (the pilot's worst cases)
    "0036", "0138",
    # confession-encoding fixes from v2.5.4
    "1259", "0141", "sc-p", "0290", "st-vincent-de-paul-elyria-oh", "13722", "0054",
    # multi-site matcher via SITE_MAPPINGS
    "our-lady-help-of-christians-litchfield-oh",
    # long-window confession false positive
    "1285",
]

# Publisher quota for the remainder, proportional to the enabled corpus
# (93 PO / 28 DM / 16 EC / 14 SH / 5 WP over 156).
QUOTA = {
    "Parishes Online": 22,
    "Discover Mass": 7,
    "eCatholic": 4,
    "Self-Hosted": 4,
    "Webpage": 2,
}


def reasons(p, single_site: set[str], mapped: set[str], perpetual: set[str]) -> list[str]:
    """Why this parish is interesting, for later segmentation of the results."""
    out = []
    if p.parish_id in single_site:
        out.append("single_site_override")
    if p.parish_id in mapped:
        out.append("site_mappings")
    if p.parish_id in perpetual:
        out.append("verified_perpetual")
    if not p.is_primary_site:
        out.append("secondary_site")
    return out


async def build() -> list[dict]:
    db = NotionClient.from_environment()
    # stale_days=-9999 makes every enabled parish "stale", i.e. return them all
    parishes = await db.get_parishes_to_process(stale_days=-9999)
    by_id = {p.parish_id: p for p in parishes}

    mapped = {pid for m in SITE_MAPPINGS.values() for pid in m.values()}
    mapped |= set(SITE_MAPPINGS)

    picked = [by_id[f] for f in FORCED if f in by_id]
    missing = [f for f in FORCED if f not in by_id]
    if missing:
        print(f"warning: forced parishes not found/enabled: {missing}")

    have = {p.parish_id for p in picked}
    for publisher, n in QUOTA.items():
        count = 0
        for p in sorted(parishes, key=lambda x: x.parish_id):
            if count >= n:
                break
            if p.parish_id in have or p.publisher != publisher:
                continue
            picked.append(p)
            have.add(p.parish_id)
            count += 1

    # group_size drives collapse_sites(), so resolve it exactly the way
    # process_parish() does rather than inferring it from the roster.
    out = []
    for p in picked:
        group_size = 1
        if p.bulletin_group_id:
            group = await db.get_bulletin_group(p.bulletin_group_id)
            group_size = max(len(group), 1)
        out.append({
            "parish_id": p.parish_id,
            "name": p.name,
            "publisher": p.publisher,
            "bulletin_url": p.bulletin_url,
            "bulletin_group_id": p.bulletin_group_id,
            "group_size": group_size,
            "reasons": reasons(p, SINGLE_SITE_PARISHES, mapped, VERIFIED_PERPETUAL_PARISHES),
        })
    return out


async def expand(n: int, existing: list[dict]) -> list[dict]:
    """Append n parishes not already on the roster, keeping it proportional.

    Existing entries are never touched, so runs already recorded against them
    stay valid. New entries are marked with a batch number — they have no
    baseline, so they measure stability rather than change.
    """
    db = NotionClient.from_environment()
    parishes = await db.get_parishes_to_process(stale_days=-9999)
    have = {e["parish_id"] for e in existing}
    pool = [p for p in parishes if p.parish_id not in have]

    mapped = {pid for m in SITE_MAPPINGS.values() for pid in m.values()}
    mapped |= set(SITE_MAPPINGS)

    # Proportional to what is left, so the roster as a whole stays representative
    by_pub: dict[str, list] = {}
    for p in sorted(pool, key=lambda x: x.parish_id):
        by_pub.setdefault(p.publisher, []).append(p)
    total = len(pool)
    picked: list = []
    for pub, ps in sorted(by_pub.items(), key=lambda kv: -len(kv[1])):
        take = round(n * len(ps) / total)
        picked.extend(ps[:take])
    picked = picked[:n]

    batch = max((e.get("batch", 1) for e in existing), default=1) + 1
    out = []
    for p in picked:
        group_size = 1
        if p.bulletin_group_id:
            group = await db.get_bulletin_group(p.bulletin_group_id)
            group_size = max(len(group), 1)
        out.append({
            "parish_id": p.parish_id,
            "name": p.name,
            "publisher": p.publisher,
            "bulletin_url": p.bulletin_url,
            "bulletin_group_id": p.bulletin_group_id,
            "group_size": group_size,
            "batch": batch,
            "reasons": reasons(p, SINGLE_SITE_PARISHES, mapped, VERIFIED_PERPETUAL_PARISHES),
        })
    return out


async def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true",
                    help="overwrite an existing roster (invalidates prior runs)")
    ap.add_argument("--expand", type=int, metavar="N",
                    help="append N more parishes, leaving existing entries untouched")
    args = ap.parse_args()

    if args.expand:
        existing = json.loads(ROSTER.read_text()) if ROSTER.exists() else []
        for e in existing:
            e.setdefault("batch", 1)
        added = await expand(args.expand, existing)
        ROSTER.write_text(json.dumps(existing + added, indent=2) + "\n")
        by_pub: dict[str, int] = {}
        for r in added:
            by_pub[r["publisher"]] = by_pub.get(r["publisher"], 0) + 1
        print(f"Added {len(added)} parishes (batch {added[0]['batch'] if added else '-'}); "
              f"roster now {len(existing) + len(added)}")
        for pub, n in sorted(by_pub.items()):
            print(f"  {pub:20} +{n}")
        return

    if ROSTER.exists() and not args.force:
        print(f"{ROSTER} already exists; refusing to reshuffle. Use --force to overwrite.")
        return

    roster = await build()
    ROSTER.write_text(json.dumps(roster, indent=2) + "\n")

    by_pub: dict[str, int] = {}
    for r in roster:
        by_pub[r["publisher"]] = by_pub.get(r["publisher"], 0) + 1
    print(f"Wrote {len(roster)} parishes to {ROSTER}")
    for pub, n in sorted(by_pub.items()):
        print(f"  {pub:20} {n}")
    flagged = sum(1 for r in roster if r["reasons"])
    print(f"  {'(with reasons)':20} {flagged}")


if __name__ == "__main__":
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass  # Assume env vars are already set
    asyncio.run(main())
