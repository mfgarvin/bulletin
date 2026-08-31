# Verification alarm-rate study

What does the verification layer actually *say* on an ordinary run?

v2.5.14-16 added three checks that only ever warn — the fabrication check
(`utils/verify_times.py`) and the change verification (`utils/verify_changes.py`)
with its diff, re-extraction and text-layer steps. Each was validated for
correctness when it shipped. This measures the other half of the question:
**how much does a human have to read on Saturday morning, and how much of it
is worth reading?** A check that fires on a third of the roster is not a usable
signal however sound each individual warning is.

```bash
python -m studies.verification.run --sample 50
python -m studies.verification.run --sample 50 --resume   # reuse sample + cache
```

Nothing is written to Notion. Stored schedules are read — that is what the
diff compares against — and `save_extraction` is never called.

## Design

**The sample excludes `studies/noise/roster.json`.** Those 100 parishes have
been extracted hundreds of times and several were hand-repaired off the back of
it, so they are no longer representative of an ordinary Saturday. The sample is
seeded and written to `results/sample.json`, so a re-run measures the same
parishes.

**Downloads are serial and delayed**, the same rule as the noise study: only
Discover Mass limits itself, and the other hosts would otherwise take a
concurrent burst. Bulletins are cached, so re-running costs nothing at the
parish sites.

**What a diff means here.** Stored data was written by the last real run, and
PO/eCatholic name a bulletin for the Sunday it covers — so a study run a day or
two later usually reads *the same bytes the stored value came from*. A diff is
then extraction noise rather than the parish changing anything, which is the
quantity worth measuring. Where a self-hosted or webpage source has rolled to a
newer bulletin a diff may be real, so the per-parish output names the bulletin.

**The re-extraction budget is lifted.** `REEXTRACT_BUDGET` is a cost guard for
a 189-parish production run, not a measurement limit, so the study gives every
diff its second extraction.

## Reading the output

Change warnings carry their own verdict, and the split is the point:

- `[NOT reproduced on a second extraction]` — the second run of the same bytes
  disagreed with the first, so the change is the model flapping. This is the
  population that a write gate would suppress entirely.
- `[reproduced on a second extraction]` — both extractions agree the stored
  value is wrong. These are the ones worth a human's time.
- `(not printed in bulletin - suspicious)` / `(still printed in bulletin)` —
  the text-layer check, added only when the bulletin verifies enough of its own
  times to support the claim.

A fabrication warning is always worth reading: it means a recurring Mass time
appears nowhere in a bulletin that otherwise checks out.
