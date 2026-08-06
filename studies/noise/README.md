# Extraction noise study

How repeatable is extraction? Re-run the same bulletins through the same
pipeline N times and measure how much the model disagrees with itself.

This exists because of a finding from the 2026-08-05 A/B pilot: **two runs of
the identical prompt over identical PDF bytes agreed on only ~79% of slots**
(11 of 20 parishes byte-identical). Run-to-run variance was as large as the
difference between the two prompts being compared, which means a prompt change
cannot be judged by diffing one run against one run. You need a same-prompt
control and systematic metrics. That is what this measures.

## Running it

```bash
# 1. Freeze the roster (once — re-running reshuffles the sample)
python -m studies.noise.sample

# 2. Fill the bulletin cache. Serial and slow on purpose; see below.
python -m studies.noise.run baseline --prefetch-only

# 3. Extract, 5 repeats per parish
python -m studies.noise.run baseline --repeats 5

# ...interrupted? top up what's missing without redoing finished repeats
python -m studies.noise.run baseline --repeats 5 --resume

# 4. Score it
python -m studies.noise.analyze baseline
python -m studies.noise.analyze baseline --parish 0036   # every flapping slot
```

## Design, and the three traps it avoids

**Measure the production layer, not raw `extract()`.** The pilot measured raw
extraction output and *overstated* the noise. `0036` Holy Spirit and `0138`
St. Joseph — both Avon Lake, sharing one bulletin — scored 30% and 41% on
recurring Masses because the model couldn't decide between one site and three.
But both are in `SINGLE_SITE_PARISHES`, and the collapse step in `main.py`
resolves exactly that; through it they score 82% and 100%. So `run.py` calls
`main.collapse_sites()` and then `sanitize_extraction()`, the same two steps in
the same order as `process_parish`. `collapse_sites()` was lifted out of
`process_parish` for this, and `process_parish` calls it too, so the study and
production cannot drift apart.

**Hold the bytes fixed.** Bulletins change weekly, so a study that re-downloads
would measure the parish, not the model. Every bulletin is downloaded once into
`cache/` (gitignored, ~1GB for 100 parishes) and every repeat of every condition
extracts from those exact bytes. The only variable is the model's own sampling.

**Be gentle with the parish sites.** Downloads are a separate phase, strictly
serial, with `--delay` (default 3s) between them — only Discover Mass limits
itself, so Parishes Online and eCatholic would otherwise take a concurrent
burst on one host. This runs once for the whole study; every later condition is
cache-only and contacts no parish site at all. `--concurrency` applies to the
OpenAI calls, never to downloads.

## What the metrics mean

Slots are compared as **multisets**, unioned across a parish's sites, split into
four categories — recurring Masses, dated/holiday Masses, confession, adoration.
Aggregate numbers hide the structure: in the pilot, recurring Masses were ~99%
reproducible once site-count disagreement was excluded, while adoration sat at
~69% and dated Masses at ~45%.

| metric | meaning |
|---|---|
| `identical` | fraction of run *pairs* whose multiset matches exactly. Harshest — one flapping slot condemns the parish. Comparable to the pilot's 79%. |
| `jaccard` | mean pairwise multiset Jaccard. Graded, slot-weighted. **The number to track.** |
| `core` | slots present in *every* repeat, over all distinct slots seen. 1.0 = the model never contradicted itself. |
| `vote_delta` | slots per run that a majority vote would fix. What self-consistency would buy — computed from data already collected, no extra API calls. |

Ends are normalized before comparison (`end == start` with `end_next_day` false
means open-ended, same as `null`), so the v2.5.4 encoding change doesn't read as
a data change. Results are segmented by publisher and by the roster's `reasons`
tags (`single_site_override`, `site_mappings`, `verified_perpetual`,
`secondary_site`) — shared-bulletin parishes that name each other are the known
structural trouble spot.

## Roster

100 parishes, publisher-proportional to the 156 enabled, plus every parish with
a known structural reason to be unstable forced in. `roster.json` is committed
and frozen — every condition must run over the same parishes to be comparable,
so `sample.py` refuses to overwrite it without `--force`.

Entries carry a `batch`. **Batch 1 (50) is the only one with a `baseline`**, so
it is the only set that supports a paired A/B; batch 2 measures absolute
stability under whatever condition ran, and can surface new failure modes, but
cannot tell you what a change *did*. Grow the roster with `--expand`, which
appends without touching existing entries, so runs already recorded stay valid:

```bash
python -m studies.noise.sample --expand 50
```

`results/` is **gitignored** — each condition is ~5MB of raw extraction dumps.
Keep the ones you want to compare against locally; re-running a condition
regenerates it. `--resume` tops up an existing file rather than redoing repeats,
so an interrupted run costs nothing.

## Adding a condition

The baseline is the current production config. To test an intervention, change
the thing, then run it under a new label and compare:

```bash
python -m studies.noise.run reasoning-high --repeats 5
python -m studies.noise.analyze reasoning-high
```

Note that gpt-5.2 has no `temperature` knob — the gpt-5 family is fixed at the
default — so the available sampling levers are `reasoning_effort`, `verbosity`,
and majority-vote self-consistency over N calls.

**Do not judge a change by the aggregate table.** The v2.5.5 A/B moved
confession jaccard +9% and core +22% on batch 1, and a bootstrap over parishes
put *every* category delta inside the noise band — that gain included (core CI
-1% to +47%). Between-parish variance swamps aggregates at n=50, so the table
will happily show you a plausible number that means nothing.

What resolves is a **targeted failure signature**: a count of the specific thing
you set out to fix, over the same cached bulletins. In v2.5.5 those were
unambiguous — perpetual chapels enumerating hours 8 runs -> 0, adoration slots
at perpetual chapels 49 -> 0 — and they also proved the change was *surgical*,
since 9 of the 10 open-ended confession slots that disappeared belonged to the
one parish being fixed. Decide the signature before running the condition.
