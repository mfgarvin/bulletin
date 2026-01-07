"""Webpage bulletin source - extracts content directly from HTML pages."""

import re
from typing import Optional
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Comment
from markdownify import markdownify as md

from .base import BulletinSource, DownloadResult

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"

# Elements to remove (navigation, headers, footers, sidebars)
REMOVE_SELECTORS = [
    "nav",
    "header",
    "footer",
    "aside",
    ".sidebar",
    ".navigation",
    ".menu",
    ".nav",
    ".header",
    ".footer",
    ".widget",
    ".ad",
    ".advertisement",
    ".social",
    ".share",
    ".comment",
    ".comments",
    "#sidebar",
    "#nav",
    "#navigation",
    "#header",
    "#footer",
    "#menu",
    "script",
    "style",
    "noscript",
    "iframe",
]


class WebpageSource(BulletinSource):
    """Extract bulletin content directly from HTML pages.

    For parishes that publish bulletin information on their website
    rather than in a PDF file.
    """

    @property
    def name(self) -> str:
        return "Webpage"

    async def download(
        self, parish_id: str, bulletin_url: Optional[str] = None
    ) -> DownloadResult:
        """Fetch the webpage and extract content as markdown.

        Args:
            parish_id: Parish identifier (used for logging).
            bulletin_url: URL of the webpage containing bulletin content.
        """
        if not bulletin_url:
            return DownloadResult(
                success=False,
                error="No bulletin_url provided for webpage source",
            )

        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                response = await client.get(
                    bulletin_url,
                    headers={"User-Agent": USER_AGENT},
                    follow_redirects=True,
                )
                if response.status_code != 200:
                    return DownloadResult(
                        success=False,
                        error=f"Failed to load webpage: HTTP {response.status_code}",
                    )

                # Extract and clean HTML content
                markdown_content = self._extract_content(response.text, bulletin_url)

                if not markdown_content or len(markdown_content.strip()) < 100:
                    return DownloadResult(
                        success=False,
                        error="Extracted content is too short - page may not contain bulletin info",
                    )

                return DownloadResult(
                    success=True,
                    pdf_bytes=markdown_content.encode("utf-8"),
                    url=bulletin_url,
                    content_type="text",
                )

            except httpx.RequestError as e:
                return DownloadResult(
                    success=False,
                    error=f"Request error: {e}",
                )

    def _extract_content(self, html: str, base_url: str) -> str:
        """Extract main content from HTML and convert to markdown."""
        soup = BeautifulSoup(html, "html.parser")

        # Remove comments
        for comment in soup.find_all(string=lambda text: isinstance(text, Comment)):
            comment.extract()

        # Remove unwanted elements
        for selector in REMOVE_SELECTORS:
            for element in soup.select(selector):
                element.decompose()

        # Try to find main content area
        main_content = self._find_main_content(soup)

        # Convert to markdown
        markdown = md(
            str(main_content),
            heading_style="ATX",
            bullets="-",
            strip=["img", "a"],  # Strip images and links, keep text
        )

        # Clean up excessive whitespace
        markdown = self._clean_markdown(markdown)

        return markdown

    def _find_main_content(self, soup: BeautifulSoup) -> BeautifulSoup:
        """Find the main content area of the page."""
        # Try common content containers
        content_selectors = [
            "main",
            "article",
            '[role="main"]',
            ".content",
            ".main-content",
            ".post-content",
            ".entry-content",
            ".page-content",
            "#content",
            "#main",
            "#main-content",
        ]

        for selector in content_selectors:
            content = soup.select_one(selector)
            if content and len(content.get_text(strip=True)) > 200:
                return content

        # Fallback to body
        body = soup.find("body")
        return body if body else soup

    def _clean_markdown(self, text: str) -> str:
        """Clean up markdown output."""
        # Collapse multiple blank lines to max 2
        text = re.sub(r"\n{3,}", "\n\n", text)

        # Remove leading/trailing whitespace from lines
        lines = [line.strip() for line in text.split("\n")]
        text = "\n".join(lines)

        # Remove empty list items
        text = re.sub(r"^-\s*$", "", text, flags=re.MULTILINE)

        # Collapse multiple spaces
        text = re.sub(r"  +", " ", text)

        return text.strip()
