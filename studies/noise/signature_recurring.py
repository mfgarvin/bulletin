"""Targeted signature: how stable is one parish's *recurring* Mass list?

The aggregate table in `analyze.py` cannot answer "does this parish keep
inventing Masses" — between-parish variance swamps it, which is the lesson
v2.5.5 recorded the hard way. This reports the one thing instead: for a single
parish, the presence rate of every distinct recurring Mass slot across repeats,
the notes attached to the unstable ones, and what `collapse_sites()` did.

Recurring only — `mass_date is None`. Dated Masses are a separate failure mode.

    python -m studies.noise.signature_recurring baseline --parish 1259
    python -m studies.noise.signature_recurring baseline promptfix --parish 1259

Multiple conditions are printed side by side, oldest first, so a fix can be
read as a change in presence rate rather than a single-run diff.
"""

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

RESULTS = Path(__file__).parent / "results"
TRUTH = Path(__file__).parent / "truth.json"

Slot = tuple[str, int]


def _recurring(rep: dict) -> list[dict]:
    """Every recurring Mass across the repeat's sites, post-collapse."""
    out = []
    for site in rep["extraction"]["sites"]:
        for m in site["mass_times"]:
            if m.get("mass_date") is None:
                out.append(m)
    return out


def load(condition: str, parish_id: str) -> list[dict]:
    path = RESULTS / f"{condition}.json"
    if not path.exists():
        raise SystemExit(f"no results for condition {condition!r} ({path})")
    data = json.loads(path.read_text())
    if parish_id not in data:
        raise SystemExit(f"{parish_id} not in {condition} (has {len(data)} parishes)")
    return [r for r in data[parish_id] if "error" not in r]


def load_truth(parish_id: str) -> dict | None:
    if not TRUTH.exists():
        return None
    entry = json.loads(TRUTH.read_text()).get(parish_id)
    if not entry:
        return None
    entry["_recurring"] = {(d, t) for d, t in entry["recurring"]}
    entry["_excluded"] = {
        (d, t) for d, t in entry.get("excluded", {}).get("slots", [])
    }
    return entry


def score_against_truth(reps: list[dict], truth: dict) -> None:
    """Per-run correctness, not just self-agreement.

    A parish can be perfectly stable and stably wrong; jaccard across repeats
    cannot see that. This is the number that says whether the row is right.
    """
    want, leak = truth["_recurring"], truth["_excluded"]
    exact = 0
    missing_tally: Counter = Counter()
    spurious_tally: Counter = Counter()
    leak_runs = 0

    for rep in reps:
        got = {(m["day"], m["time"]) for m in _recurring(rep)}
        missing = want - got
        spurious = got - want - leak
        if got & leak:
            leak_runs += 1
        if not missing and not spurious:
            exact += 1
        missing_tally.update(missing)
        spurious_tally.update(spurious)

    n = len(reps)
    print(f"    vs truth ({len(want)} slots, {truth['source'].split(' — ')[-1]}):")
    print(f"      {exact}/{n} runs exactly correct")
    if missing_tally:
        print("      missing:")
        for (day, time), c in missing_tally.most_common():
            print(f"        {c}/{n}  {day:<9} {time:04d}")
    if spurious_tally:
        print("      spurious:")
        for (day, time), c in spurious_tally.most_common():
            print(f"        {c}/{n}  {day:<9} {time:04d}")
    if leak:
        slots = ", ".join(f"{d} {t:04d}" for d, t in sorted(leak))
        print(f"      known leak ({slots}) present in {leak_runs}/{n} runs "
              f"— another parish owns it, counted separately")


def report(condition: str, parish_id: str, truth: dict | None = None) -> None:
    reps = load(condition, parish_id)
    n = len(reps)
    print(f"=== {condition}  ({n} good repeats)")
    if not n:
        return

    counts = Counter()
    notes: dict[Slot, Counter] = defaultdict(Counter)
    per_run = []
    for rep in reps:
        masses = _recurring(rep)
        per_run.append(len(masses))
        seen = set()
        for m in masses:
            slot: Slot = (m["day"], m["time"])
            seen.add(slot)
            if m.get("notes"):
                notes[slot][m["notes"]] += 1
        counts.update(seen)

    print(f"    recurring Masses/run: mean {sum(per_run)/n:.1f}  "
          f"min {min(per_run)}  max {max(per_run)}  (per-run {per_run})")

    stable = [s for s, c in counts.items() if c == n]
    unstable = sorted(
        (s for s, c in counts.items() if c < n),
        key=lambda s: (-counts[s], s[0], s[1]),
    )
    print(f"    {len(stable)} always present, {len(unstable)} unstable, "
          f"{len(counts)} distinct")

    for slot in unstable:
        day, time = slot
        pct = counts[slot] / n
        note = notes[slot].most_common(1)
        note_s = f"  — {note[0][0][:88]}" if note else ""
        print(f"      {counts[slot]}/{n} ({pct:5.0%})  {day:<9} {time:04d}{note_s}")

    if truth:
        score_against_truth(reps, truth)

    collapsed = Counter(r.get("collapsed") or "(no collapse)" for r in reps)
    print("    collapse_sites():")
    for line, c in collapsed.most_common():
        print(f"      {c}/{n}  {line[:150]}")
    print()


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("conditions", nargs="+")
    ap.add_argument("--parish", required=True)
    args = ap.parse_args()

    truth = load_truth(args.parish)
    print(f"\n### recurring-Mass stability — parish {args.parish}\n")
    if truth is None:
        print("(no truth.json entry — self-agreement only)\n")
    for condition in args.conditions:
        report(condition, args.parish, truth)


if __name__ == "__main__":
    main()
