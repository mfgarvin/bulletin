"""Capture extracted adoration schedules to a file instead of discarding them.

`UPDATE_ADORATION = False` in `database/notion.py` means a normal run extracts
adoration and then throws it away, by design — adoration is near-static and
should not churn weekly. This module keeps a copy of what *would* have been
written, so a run can be reviewed afterwards against what Notion already holds.

TEMPORARY (added 2026-08-07 for the 2026-08-08 run). Remove the `record()` /
`write()` calls in `main.py` and the commit step in `.github/workflows/
gh-actions.yml` once the captured data has been reviewed.
"""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from schemas import SiteInfo

logger = logging.getLogger(__name__)

CAPTURE_PATH = Path(__file__).resolve().parent.parent / "adoration_capture.json"

# parish_id -> record. One entry per destination row; a later save for the same
# row (multi-site merges) overwrites the earlier one, matching what Notion would
# have ended up with.
_records: dict[str, dict] = {}


def record(
    parish_id: str,
    parish_name: str,
    site: SiteInfo,
    bulletin_url: str | None = None,
    key: str | None = None,
) -> None:
    """Stash the adoration this run extracted for one destination parish row.

    `key` overrides the dedup key (defaults to `parish_id`), for the dry-run
    path where several sites share one parish id and would otherwise collide.
    """
    _records[key or parish_id] = {
        "parish_id": parish_id,
        "parish_name": parish_name,
        "site_name": site.site_name,
        "bulletin_url": bulletin_url,
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "adoration": site.adoration.model_dump(mode="json"),
    }


def write(path: Path = CAPTURE_PATH) -> int:
    """Merge this run's records into the capture file. Returns records written.

    Merging (rather than overwriting) means a partial run — a single parish, or
    the local worker's subset — adds to the file instead of replacing it.
    """
    if not _records:
        return 0

    existing: dict[str, dict] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text())
            existing = {r["parish_id"]: r for r in loaded.get("parishes", [])}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            logger.warning(f"Could not read existing {path.name}, starting fresh: {e}")

    existing.update(_records)
    payload = {
        "note": (
            "Adoration extracted per run and NOT written to Notion "
            "(UPDATE_ADORATION = False). Temporary capture for manual review."
        ),
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "parishes": [existing[k] for k in sorted(existing)],
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    logger.info(f"Wrote {len(_records)} adoration records to {path.name} ({len(existing)} total)")
    return len(_records)
