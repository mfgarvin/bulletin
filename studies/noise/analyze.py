"""Score a condition's repeats for stability.

    python -m studies.noise.analyze baseline
    python -m studies.noise.analyze baseline --worst 15
    python -m studies.noise.analyze baseline --parish 0036

Metrics, all computed per parish then aggregated:

  identical    fraction of run *pairs* whose slot multiset matches exactly.
               Comparable to the pilot's "79% agreement", and the harshest
               measure — one flapping slot condemns the whole parish.
  jaccard      mean pairwise Jaccard over slots. Graded, so a parish that
               agrees on 19 of 20 Masses scores far above one that agrees
               on none. This is the number to track.
  core         slots present in every repeat, as a fraction of all distinct
               slots seen. 1.0 means the model never disagreed with itself.
  vote_delta   slots a majority vote keeps that a single average run would
               have got wrong. What self-consistency would buy, computed
               from data already collected — no extra API calls.

Aggregate numbers hide the structure, so everything is segmented by category
(recurring vs dated Masses, confession, adoration), by publisher, and by the
roster's `reasons` tags.
"""
import argparse
import json
from collections import Counter, defaultdict
from itertools import combinations
from pathlib import Path
from statistics import mean

HERE = Path(__file__).parent
ROSTER = HERE / "roster.json"
RESULTS = HERE / "results"

CATEGORIES = ("mass_recurring", "mass_dated", "confession", "adoration")


def norm_end(start, end, end_next_day):
    """Canonical end. None means open-ended under either encoding.

    Rows written before v2.5.4 repeated the start instead of emitting null;
    `end_next_day` is what distinguishes that from a real 24-hour span.
    """
    if end is None:
        return None
    if end == start and not end_next_day:
        return None
    return end


def slots(extraction: dict) -> dict[str, Counter]:
    """Canonical slot multisets, unioned across sites.

    Sites are unioned because a slot moving between a parish's own sites is a
    labelling difference, not a data change; site-count churn is tracked
    separately.
    """
    out = {c: Counter() for c in CATEGORIES}
    for site in extraction.get("sites", []):
        for m in site.get("mass_times", []):
            key = (m.get("day"), m.get("time"), (m.get("language") or "").lower())
            if m.get("mass_date"):
                out["mass_dated"][key + (m["mass_date"],)] += 1
            else:
                out["mass_recurring"][key] += 1
        for c in site.get("confession_times", []):
            out["confession"][(
                c.get("day"), c.get("start_time"),
                norm_end(c.get("start_time"), c.get("end_time"),
                         c.get("end_next_day", False)),
            )] += 1
        adoration = site.get("adoration") or {}
        if adoration.get("is_perpetual"):
            out["adoration"][("perpetual",)] += 1
        for a in adoration.get("times", []):
            out["adoration"][(
                a.get("day"), a.get("start_time"),
                norm_end(a.get("start_time"), a.get("end_time"),
                         a.get("end_next_day", False)),
            )] += 1
    return out


def jaccard(a: Counter, b: Counter) -> float:
    """Multiset Jaccard. Two empty runs agree perfectly."""
    inter = sum((a & b).values())
    union = sum((a | b).values())
    return 1.0 if union == 0 else inter / union


def score_category(runs: list[Counter]) -> dict:
    """Stability of one category across N repeats."""
    n = len(runs)
    pairs = list(combinations(range(n), 2))
    identical = mean(1.0 if runs[i] == runs[j] else 0.0 for i, j in pairs) if pairs else 1.0
    jac = mean(jaccard(runs[i], runs[j]) for i, j in pairs) if pairs else 1.0

    # How many repeats each distinct slot appears in
    appearances = Counter()
    for run in runs:
        for key in run:
            appearances[key] += 1
    distinct = len(appearances)
    core = sum(1 for c in appearances.values() if c == n)
    majority = {k for k, c in appearances.items() if c * 2 > n}

    # What a majority vote fixes: slots an average single run gets wrong
    # (present but sub-majority, or absent but majority) that the vote resolves.
    if runs:
        wrong_per_run = mean(
            len({k for k in run if k not in majority} | {k for k in majority if k not in run})
            for run in runs
        )
    else:
        wrong_per_run = 0.0

    return {
        "n_runs": n,
        "identical": identical,
        "jaccard": jac,
        "distinct": distinct,
        "core": core,
        "core_frac": 1.0 if distinct == 0 else core / distinct,
        "unstable": distinct - core,
        "mean_size": mean(sum(r.values()) for r in runs) if runs else 0.0,
        "vote_delta": wrong_per_run,
        "flappers": sorted(
            ((k, c) for k, c in appearances.items() if c < n),
            key=lambda kv: (-kv[1], str(kv[0])),
        ),
    }


def score_parish(reps: list[dict]) -> dict | None:
    good = [r for r in reps if "error" not in r]
    if len(good) < 2:
        return None
    per_run = [slots(r["extraction"]) for r in good]
    scores = {
        cat: score_category([run[cat] for run in per_run])
        for cat in CATEGORIES
    }
    site_counts = Counter(len(r["extraction"].get("sites", [])) for r in good)
    return {
        "scores": scores,
        "n_good": len(good),
        "n_error": len(reps) - len(good),
        "site_counts": dict(site_counts),
        "site_stable": len(site_counts) == 1,
        "collapsed": sum(1 for r in good if r.get("collapsed")),
        "repairs": Counter(m.split(":")[0] for r in good for m in r["repairs"]),
        "flags": Counter(m.split(":")[0] for r in good for m in r["flags"]),
    }


def weighted(rows, cat, field, weight="distinct"):
    """Aggregate a per-parish metric, weighting by slot count.

    Unweighted means let a parish with two confession slots count as much as
    one with twenty, which flatters the aggregate.
    """
    num = den = 0.0
    for r in rows:
        s = r["scores"][cat]
        w = s[weight] or 0
        if w:
            num += s[field] * w
            den += w
    return num / den if den else float("nan")


def table(title, groups: dict[str, list], args):
    print(f"\n{title}")
    print(f"  {'group':28} {'n':>3}  " + "  ".join(f"{c:>22}" for c in CATEGORIES))
    print(f"  {'':28} {'':>3}  " + "  ".join(f"{'ident/jacc/core':>22}" for _ in CATEGORIES))
    for name, rows in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        if not rows:
            continue
        cells = []
        for cat in CATEGORIES:
            # A category no parish in the group emitted has nothing to score;
            # printing 100% there would read as perfect stability.
            present = [r for r in rows if r["scores"][cat]["distinct"]]
            if not present:
                cells.append(f"{'—':>6}{'—':>8}{'—':>8}")
                continue
            ident = mean(r["scores"][cat]["identical"] for r in present)
            jac = weighted(present, cat, "jaccard")
            core = weighted(present, cat, "core_frac")
            cells.append(f"{ident:6.0%}{jac:8.0%}{core:8.0%}")
        print(f"  {name:28} {len(rows):>3}  " + "  ".join(f"{c:>22}" for c in cells))


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("condition")
    ap.add_argument("--worst", type=int, default=10,
                    help="how many least-stable parishes to list")
    ap.add_argument("--parish", help="show every flapping slot for one parish")
    args = ap.parse_args()

    data = json.loads((RESULTS / f"{args.condition}.json").read_text())
    roster = {e["parish_id"]: e for e in json.loads(ROSTER.read_text())}

    rows = []
    skipped = []
    for pid, reps in data.items():
        scored = score_parish(reps)
        if scored is None:
            skipped.append(pid)
            continue
        scored["parish_id"] = pid
        scored["entry"] = roster.get(pid, {"name": pid, "publisher": "?", "reasons": []})
        rows.append(scored)

    if args.parish:
        show_parish(rows, args.parish)
        return

    n_runs = max((r["scores"]["mass_recurring"]["n_runs"] for r in rows), default=0)
    print(f"=== {args.condition}: {len(rows)} parishes x {n_runs} repeats ===")
    if skipped:
        print(f"  skipped (fewer than 2 good repeats): {', '.join(skipped)}")

    table("Overall", {"all": rows}, args)
    table("By publisher", group_by(rows, lambda r: r["entry"]["publisher"]), args)

    tagged = defaultdict(list)
    for r in rows:
        for tag in r["entry"].get("reasons") or ["(none)"]:
            tagged[tag].append(r)
    table("By roster tag", tagged, args)

    unstable_sites = [r for r in rows if not r["site_stable"]]
    print(f"\nSite-count churn: {len(unstable_sites)}/{len(rows)} parishes")
    for r in unstable_sites:
        print(f"  {r['parish_id']:42} {r['site_counts']}")

    print(f"\nMajority-vote delta (slots per run a vote would fix)")
    for cat in CATEGORIES:
        total = sum(r["scores"][cat]["vote_delta"] for r in rows)
        worst = max(rows, key=lambda r: r["scores"][cat]["vote_delta"])
        print(f"  {cat:16} {total:6.1f} across corpus   worst: "
              f"{worst['parish_id']} ({worst['scores'][cat]['vote_delta']:.1f})")

    print(f"\nLeast stable parishes (by slot-weighted mean jaccard)")
    def overall(r):
        num = den = 0.0
        for cat in CATEGORIES:
            s = r["scores"][cat]
            num += s["jaccard"] * s["distinct"]
            den += s["distinct"]
        return num / den if den else 1.0
    for r in sorted(rows, key=overall)[:args.worst]:
        tags = ",".join(r["entry"].get("reasons", [])) or "-"
        detail = " ".join(
            f"{cat.split('_')[-1][:4]}={r['scores'][cat]['jaccard']:.0%}"
            for cat in CATEGORIES if r["scores"][cat]["distinct"]
        )
        print(f"  {overall(r):5.0%}  {r['parish_id']:40} {detail:44} [{tags}]")
    print(f"\n  drill in with: python -m studies.noise.analyze {args.condition} "
          f"--parish <id>")


def group_by(rows, key):
    out = defaultdict(list)
    for r in rows:
        out[key(r)].append(r)
    return out


def show_parish(rows, pid):
    match = next((r for r in rows if r["parish_id"] == pid), None)
    if not match:
        raise SystemExit(f"no scored results for {pid}")
    print(f"=== {pid} — {match['entry'].get('name')} ===")
    print(f"  repeats: {match['n_good']} good, {match['n_error']} errored")
    print(f"  sites: {match['site_counts']}  collapsed in {match['collapsed']} runs")
    for cat in CATEGORIES:
        s = match["scores"][cat]
        if not s["distinct"]:
            continue
        print(f"\n  {cat}: {s['mean_size']:.1f} slots/run, "
              f"{s['core']}/{s['distinct']} stable, jaccard {s['jaccard']:.0%}")
        for key, count in s["flappers"]:
            print(f"    {count}/{s['n_runs']} runs: {key}")
    if match["repairs"]:
        print(f"\n  sanitizer repairs: {dict(match['repairs'])}")
    if match["flags"]:
        print(f"  sanitizer flags:   {dict(match['flags'])}")


if __name__ == "__main__":
    main()
