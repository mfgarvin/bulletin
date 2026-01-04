"""Discover Mass bulletin source."""

import httpx
from bs4 import BeautifulSoup

from .base import BulletinSource, DownloadResult

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"


class DiscoverMassSource(BulletinSource):
    """Download bulletins from Discover Mass by scraping the parish page."""

    @property
    def name(self) -> str:
        return "Discover Mass"

    @property
    def rate_limit_delay(self) -> float:
        # Discover Mass may rate limit, add a small delay
        return 1.0

    async def download(self, parish_id: str) -> DownloadResult:
        """Scrape the parish page to find the current bulletin URL, then download it."""
        page_url = f"https://discovermass.com/church/{parish_id}/#bulletins"

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
                )

            except httpx.RequestError as e:
                return DownloadResult(
                    success=False,
                    error=f"Request error: {e}",
                )
