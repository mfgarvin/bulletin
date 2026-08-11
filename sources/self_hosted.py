"""Self-hosted bulletin source - generic scraper for parish websites."""

import re
from datetime import date, timedelta
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .base import BulletinSource, DownloadResult
from .fetch import Fetcher

# Patterns that indicate a bulletin PDF
BULLETIN_PATTERNS = [
    r"bulletin",
    r"weekly",
    r"parish.*news",
    r"sunday.*mass",
]

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}

# Bulletins predating this are archive material, and a "year" outside it is a
# misread run of digits rather than a date.
_EARLIEST_YEAR = 2000


def _safe_date(year: int, month: int, day: int) -> date:
    """Build a date, or `date.min` if the numbers aren't a plausible one."""
    if not _EARLIEST_YEAR <= year <= date.today().year + 1:
        return date.min
    try:
        return date(year, month, day)
    except ValueError:
        return date.min

# Date patterns in filenames (to identify recent bulletins)
DATE_PATTERNS = [
    r"(\d{4})[-_]?(\d{2})[-_]?(\d{2})",  # 2024-01-15, 20240115
    r"(\d{2})[-_](\d{2})[-_](\d{4})",  # 01-15-2024
    r"(\d{1,2})[-_](\d{1,2})[-_](\d{2,4})",  # 1-15-24
    r"[-_](\d{1,2})[-_](\d{1,2})\.pdf",  # -1-4.pdf (month-day before .pdf)
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

        async with Fetcher(timeout=30.0) as fetch:
            try:
                # Fetch the bulletin page
                response = await fetch(bulletin_url)
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
                pdf_response = await fetch(pdf_url)
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

        # Rank by keyword score plus a recency bonus, then by date. Recency
        # dominates so a fresh dated bulletin beats a stale file that merely has
        # "bulletin" in its name; but it's only a bonus, so undated
        # "CurrentBulletin"-style links (score-driven) still win when nothing is
        # dated. Implausible future dates (typo'd filenames) score 0 recency
        # rather than sorting to the top.
        ranked = [
            (url, kw + self._recency_bonus(self._extract_date(url)), self._extract_date(url))
            for url, kw in candidates
        ]
        ranked.sort(key=lambda x: (x[1], x[2]), reverse=True)
        return ranked[0][0]

    @staticmethod
    def _recency_bonus(d: date) -> int:
        """Bonus favoring recent bulletins. Future-dated parses are distrusted."""
        if d == date.min:
            return 0
        days = (date.today() - d).days
        if days < -14:      # more than two weeks ahead => almost certainly a bad parse
            return 0
        if days < 0:        # dated up to ~2 weeks ahead (next Sunday's bulletin)
            return 90
        if days <= 30:
            return 100
        if days <= 120:
            return 60
        if days <= 400:
            return 25
        return 5            # dated, but old

    def _extract_date(self, url: str) -> date:
        """Best-effort date for a PDF URL, distrusting implausible future parses.

        A bulletin dated more than two weeks ahead of today is almost always a
        mis-parsed filename (a bare "October3" read as this year, a typo'd
        "21021" read as 2102). Those must not win the recency bonus *or* the
        date tiebreak, so they collapse to `date.min`.
        """
        d = self._extract_date_raw(url)
        if d != date.min and d > date.today() + timedelta(days=14):
            return date.min
        return d

    @staticmethod
    def _parse_numeric_triple(fname: str, path_month: Optional[int]) -> date:
        """Read a separated numeric date out of a filename. `date.min` if none.

        Parishes write the same date every which way, and the ambiguous cases
        can only be told apart by which reading yields a real date:

            8-9-26.pdf                -> M-D-YY   -> 2026-08-09
            5-11-25_bulletin.pdf      -> M-D-YY   -> 2025-05-11
            26_08_09_bulletin.pdf     -> YY-MM-DD -> 2026-08-09  (month 26 is not)
            2026-08-09.pdf            -> YYYY-M-D -> 2026-08-09

        The date need not sit flush against `.pdf`, and need not be preceded by
        a separator — `8-9-26.pdf` starts with it. Both were assumptions of the
        older pattern, and both cost us a current bulletin.

        M-D-YY is preferred over YY-MM-DD when a filename parses as both, since
        these are US parish sites — unless the URL path states a month and only
        one reading agrees with it.
        """
        for match in re.finditer(r"(?<!\d)(\d{1,4})[-_](\d{1,2})[-_](\d{1,4})(?!\d)", fname):
            a, b, c = (int(g) for g in match.groups())

            readings: list[date] = []
            if len(match.group(1)) == 4:                    # YYYY-M-D
                readings.append(_safe_date(a, b, c))
            if len(match.group(3)) == 4:                    # M-D-YYYY
                readings.append(_safe_date(c, a, b))
            if len(match.group(3)) == 2:                    # M-D-YY
                readings.append(_safe_date(2000 + c, a, b))
            if len(match.group(1)) == 2:                    # YY-MM-DD
                readings.append(_safe_date(2000 + a, b, c))

            valid = [d for d in readings if d != date.min]
            if not valid:
                continue
            if path_month is not None:
                agreeing = [d for d in valid if d.month == path_month]
                if agreeing:
                    return agreeing[0]
            return valid[0]

        return date.min

    def _extract_date_raw(self, url: str) -> date:
        """Best-effort date for a PDF URL, for recency ranking. `date.min` if none.

        Handles clean filenames (YYYY-MM-DD, MM-DD-YY, YY-MM-DD), textual names
        ("July 19 2026", even when mistyped as "JFuly 19. 2026"), and the
        eCatholic `/documents/YYYY/M/` layout where the only reliable
        year+month lives in the path and the day is buried in a human-typed
        filename.
        """
        url_lower = url.lower()
        fname = url_lower.rsplit("/", 1)[-1]

        # Year+month from an eCatholic-style /YYYY/M/ path segment.
        path_match = re.search(r"/(\d{4})/(\d{1,2})/", url_lower)
        path_year = int(path_match.group(1)) if path_match else None
        path_month = int(path_match.group(2)) if path_match else None

        # 1. A separated numeric triple anywhere in the filename.
        triple = self._parse_numeric_triple(fname, path_month)
        if triple != date.min:
            return triple

        # 2. YYYY-MM-DD or YYYYMMDD in the filename
        match = re.search(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})", fname)
        if match:
            year, month, day = int(match.group(1)), int(match.group(2)), int(match.group(3))
            try:
                return date(year, month, day)
            except ValueError:
                pass

        # 3. Textual "Month DD [YYYY]" (year falls back to the path's)
        # Separators here are whatever the parish secretary typed: a space, a
        # dot, or the underscore a CMS substituted for one ("august_9_2026").
        # The day must not run into a longer number, or "bulletin_JULY-2026"
        # reads as the 20th and a monthly bulletin acquires a fictional day.
        match = re.search(
            r"(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\.?[\s._-]*"
            r"(\d{1,2})(?!\d)(?:st|nd|rd|th)?[.,\s._-]*(\d{4})?",
            fname,
        )
        if match:
            month = _MONTHS[match.group(1)]
            day = int(match.group(2))
            year = int(match.group(3)) if match.group(3) else (path_year or date.today().year)
            try:
                return date(year, month, day)
            except ValueError:
                pass

        # 4. month-day only with year from path (e.g., /2025/12/file-1-4.pdf)
        filename_match = re.search(r"[-_](\d{1,2})[-_](\d{1,2})\.pdf", url_lower)
        if filename_match and path_year:
            month, day = int(filename_match.group(1)), int(filename_match.group(2))
            year = path_year
            if path_month and month < path_month:  # Dec->Jan rollover
                year += 1
            try:
                return date(year, month, day)
            except ValueError:
                pass

        # 5. eCatholic fallback: path gives year+month, scan the filename for the
        #    day (first 1-31 number once the 4-digit year is removed).
        if path_year and path_month:
            stripped = re.sub(r"20\d{2}", "", fname)
            days = [int(n) for n in re.findall(r"\d{1,2}", stripped) if 1 <= int(n) <= 31]
            try:
                return date(path_year, path_month, days[0] if days else 1)
            except ValueError:
                return date(path_year, path_month, 1)

        return date.min

    def _might_be_pdf(self, href: str, text: str) -> bool:
        """Check if a link might lead to a PDF."""
        # Only consider direct .pdf links
        # Previously this also matched bulletin-keyword links, but that caused
        # false positives like navigation links with "Bulletins" text
        return ".pdf" in href.lower()

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
