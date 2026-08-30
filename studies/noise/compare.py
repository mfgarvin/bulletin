"""Paired A/B between two conditions over the parishes they share.

Reports three things, in increasing order of how much they should move you:

1. **Aggregate jaccard per category**, with a bootstrap CI over parishes. Kept
   because it is what people ask for, and labelled `noise` when the CI spans
   zero — which, at n=50, it usually will. v2.5.5 saw confession core move +22%
   with a CI of -1% to +47%.
2. **Per-parish movers**, so a change that is really one parish cannot hide
   inside a mean.
3. **Targeted signatures** — counts of specific failure shapes. These are the
   ones that resolve. Decide them before running the condition.

    python -m studies.noise.compare promptfix promptv3-wide
"""

import argparse
import itertools
import json
import random
from collections import Counter
from pathlib import Path

RESULTS = Path(__file__).parent / "results"
CATS = ("mass", "confession", "adoration")


def _cats(rep: dict) -> tuple[Counter, Counter, Counter]:
    m, c, a = Counter(), Counter(), Counter()
    for s in rep["extraction"]["sites"]:
        for x in s["mass_times"]:
            m[(x["day"], x["time"], x.get("mass_date"))] += 1
        for x in s["confession_times"]:
            c[(x["day"], x["start_time"], x.get("end_time"))] += 1
        for x in s["adoration"]["times"]:
            a[(x["day"], x["start_time"], x.get("end_time"))] += 1
    return m, c, a


def _jac(a: Counter, b: Counter) -> float:
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return inter / union if union else 1.0


def _stability(reps: list[dict], idx: int) -> float | None:
    good = [r for r in reps if "error" not in r]
    if len(good) < 2:
        return None
    sets = [_cats(r)[idx] for r in good]
    pairs = list(itertools.combinations(sets, 2))
    return sum(_jac(a, b) for a, b in pairs) / len(pairs)


def _signatures(reps: list[dict]) -> Counter:
    """Failure shapes the v3 rules were written to remove."""
    sig = Counter()
    good = [r for r in reps if "error" not in r]
    sig["runs"] = len(good)
    for r in good:
        sites = r["extraction"]["sites"]
        sig["sites_total"] += len(sites)
        if r.get("collapsed"):
            sig["runs_collapsed"] += 1
        sig["repairs"] += len(r.get("repairs", []))
        sig["flags"] += len(r.get("flags", []))
        for s in sites:
            for m in s["mass_times"]:
                note = (m.get("notes") or "").lower()
                # rule 3: a vigil is an evening Mass; before noon is an AM/PM flip
                if "vigil" in note and m["time"] < 1200:
                    sig["vigil_before_noon"] += 1
                # rule 2: a confession time recorded as a Mass
                if "confession" in note:
                    sig["mass_noted_confession"] += 1
            for c in s["confession_times"]:
                if c.get("end_time") is None:
                    sig["confession_open_ended"] += 1
    return sig


def bootstrap(pairs: list[tuple[float, float]], n: int = 4000) -> tuple[float, float]:
    if not pairs:
        return (0.0, 0.0)
    rng = random.Random(7)
    deltas = []
    for _ in range(n):
        sample = [pairs[rng.randrange(len(pairs))] for _ in pairs]
        deltas.append(sum(b - a for a, b in sample) / len(sample))
    deltas.sort()
    return deltas[int(0.025 * n)], deltas[int(0.975 * n)]


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--movers", type=float, default=0.10,
                    help="report parishes moving more than this (default 0.10)")
    args = ap.parse_args()

    old = json.loads((RESULTS / f"{args.old}.json").read_text())
    new = json.loads((RESULTS / f"{args.new}.json").read_text())
    shared = sorted(set(old) & set(new))
    print(f"\n### {args.old} -> {args.new}   ({len(shared)} shared parishes)\n")

    print("1. AGGREGATE (bootstrap CI over parishes)")
    for i, cat in enumerate(CATS):
        pairs = []
        for pid in shared:
            o, n = _stability(old[pid], i), _stability(new[pid], i)
            if o is not None and n is not None:
                pairs.append((o, n))
        if not pairs:
            continue
        mo = sum(a for a, _ in pairs) / len(pairs)
        mn = sum(b for _, b in pairs) / len(pairs)
        lo, hi = bootstrap(pairs)
        verdict = "noise" if lo <= 0 <= hi else "RESOLVES"
        print(f"   {cat:<11} {mo:5.0%} -> {mn:5.0%}  "
              f"delta {mn-mo:+5.1%}  CI [{lo:+.1%}, {hi:+.1%}]  {verdict}")

    print(f"\n2. PER-PARISH MOVERS (>{args.movers:.0%})")
    any_mover = False
    for i, cat in enumerate(CATS):
        rows = []
        for pid in shared:
            o, n = _stability(old[pid], i), _stability(new[pid], i)
            if o is None or n is None:
                continue
            if abs(n - o) > args.movers:
                rows.append((n - o, pid, o, n))
        if rows:
            any_mover = True
            rows.sort()
            print(f"   {cat}:")
            for d, pid, o, n in rows:
                print(f"     {d:+6.0%}  {pid:<44} {o:4.0%} -> {n:4.0%}")
    if not any_mover:
        print("   (none)")

    print("\n3. TARGETED SIGNATURES (totals over all shared parishes)")
    so = Counter()
    sn = Counter()
    for pid in shared:
        so.update(_signatures(old[pid]))
        sn.update(_signatures(new[pid]))
    keys = ["runs", "vigil_before_noon", "mass_noted_confession",
            "sites_total", "runs_collapsed", "confession_open_ended",
            "repairs", "flags"]
    for k in keys:
        arrow = "" if so[k] == sn[k] else ("  <-- changed")
        print(f"   {k:<24} {so[k]:>6} -> {sn[k]:>6}{arrow}")
    print()


if __name__ == "__main__":
    main()
