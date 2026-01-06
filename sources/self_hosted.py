"""Self-hosted bulletin source - generic scraper for parish websites."""

import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .base import BulletinSource, DownloadResult

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"

# Patterns that indicate a bulletin PDF
BULLETIN_PATTERNS = [
    r"bulletin",
    r"weekly",
    r"parish.*news",
    r"sunday.*mass",
]

# Date patterns in filenames (to identify recent bulletins)
DATE_PATTERNS = [
    r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})",  # 2024-01-15, 20240115
    r"(\d{2})[-_](\d{2})[-_](\d{4})",  # 01-15-2024
    r"(\d{1,2})[-_](\d{1,2})[-_](\d{2,4})",  # 1-15-24
]


class SelfHostedSource(BulletinSource):
    """Download bulletins from self-hosted parish websites.

    Scrapes the provided bulletin page URL to find PDF links,
    prioritizing links that contain 'bulletin' and recent dates.
    """

    @property
    def name(self) -> str:
        return "Self-Hosted"

    async def download(
        self, parish_id: str, bulletin_url: Optional[str] = None
    ) -> DownloadResult:
        """Scrape the bulletin page to find and download the latest PDF.

        Args:
            parish_id: Parish identifier (used for logging).
            bulletin_url: URL of the page containing the bulletin PDF link.
        """
        if not bulletin_url:
            return DownloadResult(
                success=False,
                error="No bulletin_url provided for self-hosted source",
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                # Fetch the bulletin page
                response = await client.get(
                    bulletin_url,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
                if response.status_code != 200:
                    return DownloadResult(
                        success=False,
                        error=f"Failed to load bulletin page: HTTP {response.status_code}",
                    )

                # Find the best PDF link
                pdf_url = self._find_best_pdf_link(response.text, bulletin_url)
                if not pdf_url:
                    return DownloadResult(
                        success=False,
                        error="No PDF links found on bulletin page",
                    )

                # Download the PDF
                pdf_response = await client.get(
                    pdf_url,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
                if pdf_response.status_code != 200:
                    return DownloadResult(
                        success=False,
                        error=f"Failed to download PDF: HTTP {pdf_response.status_code}",
                    )

                # Verify it's actually a PDF
                content_type = pdf_response.headers.get("content-type", "")
                if "pdf" not in content_type.lower() and not pdf_url.lower().endswith(
                    ".pdf"
                ):
                    return DownloadResult(
                        success=False,
                        error=f"Downloaded file is not a PDF: {content_type}",
                    )

                return DownloadResult(
                    success=True,
                    pdf_bytes=pdf_response.content,
                    url=pdf_url,
                )

            except httpx.RequestError as e:
                return DownloadResult(
                    success=False,
                    error=f"Request error: {e}",
                )

    def _find_best_pdf_link(self, html: str, base_url: str) -> Optional[str]:
        """Find the best PDF link on the page.

        Scores links based on:
        - Contains 'bulletin' or related terms
        - Has a recent date in the filename
        - Is a direct .pdf link
        """
        soup = BeautifulSoup(html, "html.parser")
        candidates: list[tuple[str, int]] = []  # (url, score)

        # Find all links
        for link in soup.find_all("a", href=True):
            href = link.get("href", "")
            text = link.get_text().lower()

            # Skip non-PDF links (unless they might redirect to PDF)
            if not self._might_be_pdf(href, text):
                continue

            # Make URL absolute
            full_url = urljoin(base_url, href)

            # Score this link
            score = self._score_link(href, text)
            candidates.append((full_url, score))

        # Also check for embedded PDFs (iframes, embeds)
        for embed in soup.find_all(["iframe", "embed", "object"]):
            src = embed.get("src") or embed.get("data")
            if src and ".pdf" in src.lower():
                full_url = urljoin(base_url, src)
                score = self._score_link(src, "")
                candidates.append((full_url, score))

        if not candidates:
            return None

        # Return the highest-scoring link
        candidates.sort(key=lambda x: x[1], reverse=True)
        return candidates[0][0]

    def _might_be_pdf(self, href: str, text: str) -> bool:
        """Check if a link might lead to a PDF."""
        href_lower = href.lower()
        text_lower = text.lower()

        # Direct PDF link
        if ".pdf" in href_lower:
            return True

        # Link text suggests bulletin
        for pattern in BULLETIN_PATTERNS:
            if re.search(pattern, text_lower) or re.search(pattern, href_lower):
                return True

        return False

    def _score_link(self, href: str, text: str) -> int:
        """Score a link based on likelihood of being the current bulletin."""
        score = 0
        href_lower = href.lower()
        text_lower = text.lower()

        # Direct .pdf link
        if ".pdf" in href_lower:
            score += 10

        # Contains bulletin-related terms
        for pattern in BULLETIN_PATTERNS:
            if re.search(pattern, href_lower):
                score += 20
            if re.search(pattern, text_lower):
                score += 15

        # Contains a date (likely current bulletin)
        for pattern in DATE_PATTERNS:
            if re.search(pattern, href_lower):
                score += 25
                break

        # Link text suggests "current" or "latest"
        if any(word in text_lower for word in ["current", "latest", "this week"]):
            score += 30

        # Penalize archive links
        if any(word in href_lower for word in ["archive", "past", "old"]):
            score -= 20

        return score
