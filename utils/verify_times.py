"""Check that extracted Mass times actually appear in the bulletin.

The Cathedral (`1259`) has published a Sunday 10:30 Mass that occurs **nowhere
in its bulletin** — the masthead reads "Sunday: 8:30, 11:00 am; 5:30 pm" — while
the run reported "No Issues" with an empty Issue Log. That is the signature
failure of this parish class: not a misreading of hard typography but the model
supplying a plausible schedule from prior instead of from the page. `8:30/10:30`
is the most common Sunday pattern in the diocese, and that is what came back.

Misreadings need judgement. **Fabrications do not** — a time the document never
prints is checkable by looking. This pass renders each extracted Mass time the
way a bulletin prints it and asks whether that string occurs.

**The check gates itself on the bulletin's own hit rate.** A masthead is very
often an image even when the body has a good text layer, so "time absent from
the text" usually means "the schedule block is a scan", not "fabricated" — an
ungated version of this check flagged 24% of all recurring Masses across the
noise-study corpus, almost all of them real. When the bulletin verifies most of
its *own* times (>= MIN_HIT_RATE with at least MIN_VERIFIED_TIMES found), the
schedule is demonstrably in the text layer and a missing time is a real signal;
below that, the document is unverifiable and the pass stays silent rather than
guessing. Measured over 1,250 parish-runs against cached bulletins
(studies/noise/), the gated check fired on exactly three slots — 1259's Sunday
10:30, 0670's Thursday 11:15, 2452's Sunday 19:00 — and each one is a time its
bulletin never prints. Zero false positives.

The limit of the gate: a *wholly* fabricated schedule looks identical to an
unverifiable document, so this catches isolated fabrication inside an
otherwise-verifiable bulletin — which is what the model actually does.

Deliberately narrow:

- **Masses only.** Confession and adoration starts are frequently *derived*
  rather than quoted ("confessions after the 8:15 Mass" places a start the page
  never states), which is legal under the v2.5.4 rules and would false-positive
  on every parish that does it.
- **Recurring only.** A dated Mass is usually announced in prose elsewhere.
- **Flags, never repairs.** Only the bulletin can settle a disagreement, and a
  wrong drop is worse than a warning. Nothing here changes what is extracted;
  it changes only whether a run may call itself clean.
- **Check the original bytes, not what the LLM saw.** `compress_if_needed`
  rasterizes oversized PDFs, destroying the text layer by design — and `1259`
  is one of the three parishes it compresses. `process_parish` passes the
  downloaded bytes, upstream of compression.
"""

import logging
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from schemas import BulletinExtraction

logger = logging.getLogger(__name__)

# Below this, the "text layer" is page furniture (headers, an embedded ad) and
# not the bulletin's words. Checking against it would clear every time by
# failing to find any of them, which is the opposite of what this pass is for.
MIN_TEXT_CHARS = 500

# The gate: only flag misses when the bulletin verifies at least this fraction
# of the extraction's recurring Mass times, and at least this many of them.
# Below either bar the schedule block is presumed to be an image (or the
# extraction presumed wholesale wrong, which a text search cannot adjudicate).
MIN_HIT_RATE = 0.8
MIN_VERIFIED_TIMES = 5


def _extract_text(source_bytes: bytes, content_type: str) -> str:
    """Best-effort plain text from a downloaded bulletin. '' if unavailable."""
    if content_type != "pdf":
        # Webpage rows are already markdown/text on the way in.
        return source_bytes.decode("utf-8", errors="replace")

    try:
        import fitz  # PyMuPDF, already a dependency for pdf_compress
    except ImportError:  # pragma: no cover - dependency is in requirements.txt
        logger.warning("PyMuPDF unavailable; skipping source verification")
        return ""

    try:
        with fitz.open(stream=source_bytes, filetype="pdf") as doc:
            return "\n".join(page.get_text() for page in doc)
    except Exception as e:  # a corrupt or encrypted PDF is not a schedule bug
        logger.warning("Could not read PDF text layer: %s", e)
        return ""


def _normalize(text: str) -> str:
    """Lowercase and collapse whitespace so line breaks can't split a time.

    PDF text layers wrap wherever the column does, so "11:00" and the "am" that
    qualifies it routinely land on different lines.
    """
    return re.sub(r"\s+", " ", text.lower())


def _renderings(time: int) -> list[str]:
    """Regexes matching how a bulletin would print this 24-hour time."""
    hour, minute = divmod(time, 100)
    h12 = hour % 12 or 12

    patterns = [
        # The strong form: "10:30" anywhere. Also accepts "10.30", which some
        # parishes use. Not anchored to am/pm - plenty of mastheads omit it on
        # every time but the last in a list ("8:30, 11:00 am"). Ends with a
        # lookahead rather than \b because the meridiem is often glued to the
        # minutes ("8:30am"), and a digit-to-letter join is not a boundary.
        # A single space may sit on either side of the separator - PDF text
        # extraction splits "11:00" into "11 :00" often enough to matter.
        rf"\b{h12} ?[:.] ?{minute:02d}(?!\d)",
    ]

    if minute == 0:
        # A bare hour ("Sunday 8:30 & 11") only counts next to a meridiem, or
        # "11" would match a date, a page number, or the year in "2011". The
        # meridiem may sit at the end of a short list instead of on the hour
        # itself: "Sunday Masses: 9 and 11 a.m." states a 9:00 Mass.
        patterns.append(
            rf"\b{h12}\s*(?:(?:,|and|&|or)\s*\d{{1,2}}(?:[:.]\d{{2}})?\s*)*"
            rf"(?:am|pm|a\.m\.|p\.m\.|o'clock)"
        )
        if hour == 12:
            patterns.append(r"\bnoon\b")
        if hour == 0:
            patterns.append(r"\bmidnight\b")

    return patterns


def verify_times_against_source(
    extraction: "BulletinExtraction",
    source_bytes: bytes,
    content_type: str = "pdf",
) -> list[str]:
    """Flag recurring Mass times that don't appear in the bulletin's own text.

    Returns warning strings for `process_parish` to route through `warn()`, so
    they land in `Issue Log` and the end-of-run summary like any other warning.
    Returns [] when the bulletin cannot support the check (no text layer, or
    the hit rate is below the gate) - silently, because "unverifiable" is a
    property of the document that would otherwise re-warn every week.
    """
    text = _normalize(_extract_text(source_bytes, content_type))

    if len(text) < MIN_TEXT_CHARS:
        logger.info(
            "Source verification skipped - no usable text layer (%d chars)",
            len(text),
        )
        return []

    misses: list[str] = []
    checked = 0
    for site in extraction.sites:
        for mass in site.mass_times:
            if mass.mass_date is not None:
                continue
            checked += 1
            if any(re.search(p, text) for p in _renderings(mass.time)):
                continue
            where = f" at '{site.site_name}'" if len(extraction.sites) > 1 else ""
            misses.append(
                f"Mass {mass.day.value} {mass.time:04d}{where} does not appear "
                "anywhere in the bulletin text - possible fabricated time"
            )

    if not misses:
        return []

    found = checked - len(misses)
    if found < MIN_VERIFIED_TIMES or found / checked < MIN_HIT_RATE:
        logger.info(
            "Source verification inconclusive - only %d/%d recurring Mass "
            "times found in the text layer; schedule block is likely an image",
            found,
            checked,
        )
        return []

    return misses
