"""A content fingerprint for sources that have no date of their own.

Every other publisher tells us, one way or another, *when* the thing we
downloaded is for: Parishes Online and eCatholic name the file for its Sunday,
Discover Mass prints the date in the link text, Self-Hosted usually has it in
the filename. **Webpage rows have nothing.** A parish website is a living
document with no edition, so `bulletin_date` is None for them and will stay
None - there is no date on the page to read.

What a webpage *does* have is content that either changed since last week or
didn't, and that answers the question the date was standing in for. Hashing the
extracted text gives three states, each of which means something different:

- **unchanged** - the page is a static source, which is the normal and expected
  condition for these rows. The useful thing to report is not "fetched today"
  but "unchanged since <date>", which this tracks.
- **changed** - somebody edited the page. Any schedule diff this week is worth
  reading, because it *could* be real.
- **collapsed** - the page lost most of its text. That is the Webpage failure
  mode that matters (a site moves to a JS-rendered template and the scraper
  starts seeing navigation chrome), and it is otherwise silent: the run
  succeeds, the row saves, and the schedule quietly degrades.

The unchanged case is worth more than it looks. **If the page is byte-identical
and the extraction still differs, the difference cannot be real** - it is pure
model noise, established without spending the re-extraction budget that
`verify_changes` uses to approximate exactly this test.
"""

import hashlib
import json
import re
from datetime import date
from typing import Optional

# Whitespace is the one thing that varies without meaning between two renderings
# of the same page. Nothing else is normalised - a case change is a real edit.
_WS_RE = re.compile(r"\s+")

# Fraction of the stored character count below which a page counts as collapsed
# rather than edited. Deliberately the same shape (and the same reasoning) as
# PARTIAL_RETRACTION_RATIO: half is a lot to lose by editing, and a template
# change loses far more than half.
COLLAPSE_RATIO = 0.5


def fingerprint(content: bytes) -> tuple[str, int]:
    """(short sha256, normalised character count) for downloaded page text."""
    text = _WS_RE.sub(" ", content.decode("utf-8", "replace")).strip()
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16], len(text)


def _load(stored: Optional[str]) -> Optional[dict]:
    """Parse a stored record, treating anything unreadable as absent.

    A corrupt fingerprint must never block a run: the whole point of it is to
    be an observation about the page, and a bad observation is worth less than
    the run it would stand in the way of.
    """
    if not stored:
        return None
    try:
        value = json.loads(stored)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) and value.get("hash") else None


def compare(
    stored: Optional[str], content: bytes, today: Optional[date] = None
) -> tuple[str, str, Optional[str]]:
    """Fingerprint `content` and compare it to what was stored.

    Returns `(record, note, warning)` - the JSON to store, a line for the run
    log, and a warning string or None. `first_seen` is carried forward while
    the content is unchanged, so it reads as "unchanged since <date>" rather
    than "fetched today".
    """
    today = today or date.today()
    digest, chars = fingerprint(content)
    previous = _load(stored)

    if previous is None:
        record = {"hash": digest, "chars": chars, "first_seen": today.isoformat()}
        return json.dumps(record), f"page content fingerprinted ({chars} chars)", None

    if previous["hash"] == digest:
        # Carry first_seen forward untouched - that date is the answer to "how
        # long has this page said the same thing".
        record = dict(previous, chars=chars)
        return (
            json.dumps(record),
            f"page content unchanged since {previous.get('first_seen', 'unknown')} "
            f"({chars} chars) - any schedule diff this week is extraction noise",
            None,
        )

    record = {"hash": digest, "chars": chars, "first_seen": today.isoformat()}
    was = previous.get("chars") or 0
    note = (
        f"page content changed (was {was} chars on "
        f"{previous.get('first_seen', 'unknown')}, now {chars})"
    )
    warning = None
    if was and chars < was * COLLAPSE_RATIO:
        warning = (
            f"Page content collapsed from {was} to {chars} characters since "
            f"{previous.get('first_seen', 'unknown')}. The run succeeded, so the "
            f"schedule saved this week was read from whatever is left - check "
            f"the page still renders its schedule in static HTML rather than "
            f"having moved to a JS-rendered template."
        )
    return json.dumps(record), note, warning
