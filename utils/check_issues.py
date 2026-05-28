"""Notify on Notion `Issues` field after a bulletin processor run.

Queries the parish database for any row where `Issues` is "Error" or "Warning"
and posts a digest to a Discord or Slack webhook. The webhook URL is detected
from its host so the same script targets either platform.

Required env vars:
  NOTION_API_KEY     — Notion integration token
  PARISH_DB_ID       — Parish database id
  NOTIFY_WEBHOOK_URL — Discord or Slack incoming webhook URL

Exits 0 in all normal cases (including "issues found and reported") so the
GitHub Actions workflow doesn't mark itself as failed when issues exist.
Non-zero only on misconfiguration or unreachable webhook.
"""

import asyncio
import logging
import os
import sys
from dataclasses import dataclass
from typing import Optional

import httpx
from notion_client import AsyncClient

logger = logging.getLogger(__name__)


@dataclass
class IssueRow:
    parish_id: str
    name: str
    status: str  # "Error" | "Warning"
    log: str


def _plain_text(row: dict, prop: str) -> str:
    p = row["properties"].get(prop) or {}
    items = p.get(p.get("type", ""), [])
    if isinstance(items, list) and items:
        return items[0].get("plain_text", "")
    return ""


def _status(row: dict, prop: str) -> str:
    p = row["properties"].get(prop) or {}
    s = p.get("status")
    return s.get("name", "") if s else ""


async def fetch_issues(client: AsyncClient, database_id: str) -> list[IssueRow]:
    """Return all parishes whose Issues status is Error or Warning."""
    issues: list[IssueRow] = []
    cursor: Optional[str] = None

    while True:
        kwargs = {
            "database_id": database_id,
            "filter": {
                "or": [
                    {"property": "Issues", "status": {"equals": "Error"}},
                    {"property": "Issues", "status": {"equals": "Warning"}},
                ]
            },
        }
        if cursor:
            kwargs["start_cursor"] = cursor
        response = await client.databases.query(**kwargs)

        for row in response["results"]:
            issues.append(IssueRow(
                parish_id=_plain_text(row, "ParishID"),
                name=_plain_text(row, "Name"),
                status=_status(row, "Issues"),
                log=_plain_text(row, "Issue Log"),
            ))

        if not response.get("has_more"):
            break
        cursor = response["next_cursor"]

    issues.sort(key=lambda i: (0 if i.status == "Error" else 1, i.parish_id))
    return issues


def build_digest(issues: list[IssueRow]) -> str:
    """Plain-text digest. Works for either Discord or Slack."""
    if not issues:
        return "Bulletin processor: all parishes clean — no errors or warnings."

    errors = [i for i in issues if i.status == "Error"]
    warnings = [i for i in issues if i.status == "Warning"]

    lines = [f"**Bulletin processor digest** — {len(errors)} error(s), {len(warnings)} warning(s)"]
    lines.append("")

    if errors:
        lines.append(f"__Errors ({len(errors)})__")
        for i in errors:
            lines.append(f"• `{i.parish_id}` {i.name}")
            if i.log:
                first_line = i.log.split("\n")[0].strip()
                lines.append(f"   {first_line}")
        lines.append("")

    if warnings:
        lines.append(f"__Warnings ({len(warnings)})__")
        for i in warnings:
            lines.append(f"• `{i.parish_id}` {i.name}")
            if i.log:
                first_line = i.log.split("\n")[0].strip()
                lines.append(f"   {first_line}")

    return "\n".join(lines)


async def post_webhook(webhook_url: str, message: str) -> None:
    """Post the digest to Discord or Slack, detecting platform from URL.

    Both platforms accept a JSON POST with a body field carrying the message
    text; only the field name differs. Discord caps content at 2000 chars,
    Slack at ~40K — truncate to the lower bound to be safe.
    """
    if len(message) > 1900:
        message = message[:1900] + "\n…(truncated)"

    if "discord.com" in webhook_url or "discordapp.com" in webhook_url:
        payload = {"content": message}
    elif "slack.com" in webhook_url:
        payload = {"text": message}
    else:
        # Unknown host — try Slack format as the more common generic webhook
        payload = {"text": message}

    async with httpx.AsyncClient(timeout=15.0) as http:
        response = await http.post(webhook_url, json=payload)
        response.raise_for_status()
        logger.info("webhook posted (%d bytes, status %d)", len(message), response.status_code)


async def main_async() -> int:
    api_key = os.environ.get("NOTION_API_KEY")
    database_id = os.environ.get("PARISH_DB_ID")
    webhook_url = os.environ.get("NOTIFY_WEBHOOK_URL")
    if not (api_key and database_id and webhook_url):
        logger.error("missing required env: NOTION_API_KEY, PARISH_DB_ID, NOTIFY_WEBHOOK_URL")
        return 2

    client = AsyncClient(auth=api_key)
    issues = await fetch_issues(client, database_id)
    logger.info("found %d parishes with Error/Warning", len(issues))

    message = build_digest(issues)
    await post_webhook(webhook_url, message)
    return 0


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s - %(message)s")
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    return asyncio.run(main_async())


if __name__ == "__main__":
    sys.exit(main())
