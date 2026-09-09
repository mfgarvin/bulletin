"""Discover Mass bulletin source."""

import asyncio
import re
from datetime import date, datetime
from typing import Optional

import httpx
from bs4 import BeautifulSoup

from .base import BulletinSource, DownloadResult

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"

# Global lock to serialize all Discover Mass requests (they lock out scrapers)
_dm_lock = asyncio.Lock()

# Discover Mass hands out an encrypted, opaque download token - 80 bytes of
# high-entropy binary once un-base64'd - so the URL will never carry a date.
# The *page* does: the anchor the scraper already looks up prints the covered
# Sunday as its own link text, with the whole archive listed beneath it.
#
#     <a href="...download.php?bulletin=<token>" id="bulletin-current">Sep 6, 2026</a>
#
# So freshness IS checkable for these ~28 parishes, and by reading the page
# rather than by diffing tokens across runs.
_DM_DATE_FORMATS = ("%b %d, %Y", "%B %d, %Y")
_DM_DATE_RE = re.compile(r"[A-Za-z]{3,9}\.?\s+\d{1,2},\s*\d{4}")


def _parse_link_date(text: Optional[str]) -> Optional[date]:
    """Read 'Sep 6, 2026' out of the current-bulletin link's own text.

    Returns None on anything unrecognised rather than guessing - the freshness
    rules say a date that fails to parse costs a check, while a date parsed
    wrong can pass one.
    """
    if not text:
        return None
    match = _DM_DATE_RE.search(text)
    if not match:
        return None
    # "Sept" is neither %b nor %B; every other abbreviation already is one.
    cleaned = re.sub(r"\bSept\b", "Sep", match.group(0).replace(".", ""))
    for fmt in _DM_DATE_FORMATS:
        try:
            return datetime.strptime(cleaned, fmt).date()
        except ValueError:
            continue
    return None


class DiscoverMassSource(BulletinSource):
    """Download bulletins from Discover Mass by scraping the parish page."""

    @property
    def name(self) -> str:
        return "Discover Mass"

    @property
    def rate_limit_delay(self) -> float:
        # Delay is enforced inside download() via global lock
        return 0.0

    async def download(
        self, parish_id: str, bulletin_url: Optional[str] = None
    ) -> DownloadResult:
        """Scrape the parish page to find the current bulletin URL, then download it."""
        page_url = f"https://discovermass.com/church/{parish_id}/#bulletins"

        async with _dm_lock:
            # 10 second delay before each request to avoid lockout
            await asyncio.sleep(10.0)

            async with httpx.AsyncClient() as client:
                try:
                    # First, fetch the parish page to find the bulletin URL
                    response = await client.get(
                        page_url,
                        headers={"User-Agent": USER_AGENT},
                        follow_redirects=True,
                    )
                    if response.status_code != 200:
                        return DownloadResult(
                            success=False,
                            error=f"Failed to load parish page: HTTP {response.status_code}",
                        )

                    # Parse HTML to find bulletin link
                    soup = BeautifulSoup(response.text, "html.parser")
                    bulletin_element = soup.find(id="bulletin-current")

                    if not bulletin_element:
                        return DownloadResult(
                            success=False,
                            error="Could not find bulletin link on parish page",
                        )

                    bulletin_url = bulletin_element.get("href")
                    if not bulletin_url:
                        return DownloadResult(
                            success=False,
                            error="Bulletin element has no href",
                        )

                    # Download the PDF
                    pdf_response = await client.get(
                        bulletin_url,
                        headers={"User-Agent": USER_AGENT},
                        follow_redirects=True,
                    )
                    if pdf_response.status_code != 200:
                        return DownloadResult(
                            success=False,
                            error=f"Failed to download bulletin: HTTP {pdf_response.status_code}",
                        )

                    return DownloadResult(
                        success=True,
                        pdf_bytes=pdf_response.content,
                        url=bulletin_url,
                        bulletin_date=_parse_link_date(
                            bulletin_element.get_text()
                        ),
                    )

                except httpx.RequestError as e:
                    return DownloadResult(
                        success=False,
                        error=f"Request error: {e}",
                    )
