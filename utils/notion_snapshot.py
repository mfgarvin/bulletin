"""Raw snapshot of the Notion database, and a restore path back into it.

`export.json` and `parish_data.json` are committed every Saturday and look like
an archive, but neither is one: both are *derived* views. `notion_to_app` drops
`Events`, every log field, the publisher and enable flags, and — the part that
bites — it drops dated Masses already in the past and nulls coordinates outside
Ohio. You cannot restore from a file that discarded the data on the way out.

This writes the properties as Notion holds them, so a bad run is recoverable:

    python -m utils.notion_snapshot                    # write notion_snapshot.json
    python -m utils.notion_snapshot --restore <file>   # show what would change
    python -m utils.notion_snapshot --restore <file> --apply

The file is overwritten rather than dated. Git history is the archive — the
same arrangement `export.json` already uses — which keeps diffs meaningful and
the repo from growing a file per week.

**Restore is deliberately partial.** `OPERATIONAL_FIELDS` are a human's
classification of a parish, not data a run produced: replaying a week-old
`Enable` would silently re-disable a parish enabled since, and a week-old
`Issues` would stamp over a hand-set `Manual`/`Unsupported` — the very statuses
`PROTECTED_STATUSES` exists to defend. Pass `--all-fields` to include them
anyway, when the point of the restore *is* to undo such a change.
"""

import argparse
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Optional

from database.notion import NOTION_BLOCK_LIMIT, NotionClient

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is optional at runtime
    load_dotenv = None

DEFAULT_PATH = "notion_snapshot.json"

# Property types this tool round-trips. Anything else is captured for reading
# but refused on restore rather than guessed at — a formula or rollup is
# derived by Notion and not ours to write back.
SUPPORTED_TYPES = frozenset({"title", "rich_text", "checkbox", "url", "select", "status"})

# A human's classification of the parish rather than data a run produced.
# Excluded from restore by default; see the module docstring.
OPERATIONAL_FIELDS = frozenset({
    "Enable",
    "Issues",
    "Bulletin Publisher",
    "Bulletin Page URL",
    "Bulletin Group ID",
})


def _read_property(prop: dict) -> Optional[Any]:
    """Read one Notion property to a plain value, or None if unsupported.

    Long `rich_text` and `title` values live across several blocks (Notion caps
    each at 2000 chars). Joining every block is what makes the JSON schedule
    fields round-trip; taking `items[0]` is the v2.5.1 truncation bug.
    """
    prop_type = prop.get("type")
    if prop_type in ("rich_text", "title"):
        return "".join(item["plain_text"] for item in prop[prop_type])
    if prop_type == "checkbox":
        return prop["checkbox"]
    if prop_type == "url":
        return prop["url"] or ""
    if prop_type in ("select", "status"):
        value = prop[prop_type]
        return value["name"] if value else ""
    return None


def _write_property(prop_type: str, value: Any) -> dict:
    """Build the Notion payload that sets a property back to `value`."""
    if prop_type in ("rich_text", "title"):
        text = value or ""
        chunks = [
            text[i : i + NOTION_BLOCK_LIMIT]
            for i in range(0, len(text), NOTION_BLOCK_LIMIT)
        ]
        return {prop_type: [{"text": {"content": c}} for c in chunks]}
    if prop_type == "checkbox":
        return {"checkbox": bool(value)}
    if prop_type == "url":
        return {"url": value or None}
    if prop_type in ("select", "status"):
        return {prop_type: {"name": value} if value else None}
    raise ValueError(f"unsupported property type: {prop_type}")


def _row_to_entry(row: dict) -> dict:
    """Flatten one Notion row to `{parish_id, page_id, properties}`."""
    properties: dict[str, dict] = {}
    for name, prop in row["properties"].items():
        prop_type = prop.get("type")
        if prop_type not in SUPPORTED_TYPES:
            continue
        value = _read_property(prop)
        if value is None:
            continue
        properties[name] = {"type": prop_type, "value": value}

    parish_id = properties.get("ParishID", {}).get("value", "")
    return {
        # Sorted so a week-to-week diff shows real changes, not key reordering.
        "parish_id": parish_id,
        "page_id": row["id"],
        "last_edited_time": row.get("last_edited_time", ""),
        "properties": dict(sorted(properties.items())),
    }


async def _fetch_all_rows(db: NotionClient) -> list[dict]:
    """Every row in the database, following pagination."""
    rows: list[dict] = []
    cursor: Optional[str] = None
    while True:
        response = await (
            db._query_database(start_cursor=cursor) if cursor else db._query_database()
        )
        rows.extend(response["results"])
        if not response.get("has_more"):
            return rows
        cursor = response["next_cursor"]


async def take_snapshot(path: str) -> str:
    db = NotionClient.from_environment()
    rows = await _fetch_all_rows(db)
    entries = sorted(
        (_row_to_entry(row) for row in rows),
        # Page id breaks ties so rows with no ParishID still order stably.
        key=lambda e: (e["parish_id"], e["page_id"]),
    )
    snapshot = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "database_id": os.environ["PARISH_DB_ID"],
        "parish_count": len(entries),
        "parishes": entries,
    }
    with open(path, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, indent=2, ensure_ascii=False)
        f.write("\n")
    return f"Wrote {len(entries)} parishes to {path}"


def _abbrev(value: Any, width: int = 70) -> str:
    text = json.dumps(value) if not isinstance(value, str) else value
    text = text.replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


async def restore(path: str, apply: bool, parish: Optional[str], all_fields: bool) -> str:
    """Replay a snapshot back into Notion, reporting every field it changes."""
    with open(path, encoding="utf-8") as f:
        snapshot = json.load(f)

    db = NotionClient.from_environment()
    # Keyed by page id, which is stable even for rows whose ParishID is blank.
    current = {row["id"]: row for row in await _fetch_all_rows(db)}

    changed_parishes = 0
    changed_fields = 0
    missing: list[str] = []

    for entry in snapshot["parishes"]:
        if parish and entry["parish_id"] != parish:
            continue

        row = current.get(entry["page_id"])
        if row is None:
            missing.append(entry["parish_id"] or entry["page_id"])
            continue

        updates: dict[str, dict] = {}
        lines: list[str] = []
        for name, saved in entry["properties"].items():
            if not all_fields and name in OPERATIONAL_FIELDS:
                continue
            prop = row["properties"].get(name)
            if prop is None or prop.get("type") != saved["type"]:
                continue  # schema moved under us; refuse rather than guess
            live_value = _read_property(prop)
            if live_value == saved["value"]:
                continue
            updates[name] = _write_property(saved["type"], saved["value"])
            lines.append(
                f"    {name}: {_abbrev(live_value)}\n"
                f"      -> {_abbrev(saved['value'])}"
            )

        if not updates:
            continue

        changed_parishes += 1
        changed_fields += len(updates)
        name = entry["properties"].get("Name", {}).get("value", "")
        print(f"\n--- [{entry['parish_id']}] {name}")
        print("\n".join(lines))

        if apply:
            await db._update_page(entry["page_id"], updates)

    if missing:
        print(f"\nNot found in the database (skipped): {', '.join(missing)}")

    print("\n" + "=" * 60)
    verb = "Restored" if apply else "DRY RUN: would restore"
    tail = "" if apply else "\nRe-run with --apply to write."
    scope = "" if all_fields else f" (operational fields held: {', '.join(sorted(OPERATIONAL_FIELDS))})"
    return f"{verb} {changed_fields} field(s) across {changed_parishes} parish(es).{scope}{tail}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument(
        "--restore", metavar="FILE",
        help="replay this snapshot back into Notion instead of taking a new one",
    )
    parser.add_argument("--apply", action="store_true", help="write (restore is dry-run by default)")
    parser.add_argument("--parish", help="limit a restore to one ParishID")
    parser.add_argument(
        "--all-fields", action="store_true",
        help=f"also restore {', '.join(sorted(OPERATIONAL_FIELDS))} - these are "
             "hand-set classifications and are held back by default",
    )
    parser.add_argument("--out", default=DEFAULT_PATH, help=f"snapshot path (default {DEFAULT_PATH})")
    args = parser.parse_args()

    if load_dotenv is not None:
        load_dotenv()

    if args.restore:
        print(asyncio.run(restore(args.restore, args.apply, args.parish, args.all_fields)))
    else:
        print(asyncio.run(take_snapshot(args.out)))


if __name__ == "__main__":
    main()
