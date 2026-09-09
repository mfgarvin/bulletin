"""Abstract bulletin source protocol."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class DownloadResult:
    """Result of a bulletin download attempt."""

    success: bool
    pdf_bytes: Optional[bytes] = None
    url: Optional[str] = None
    error: Optional[str] = None
    content_type: str = "pdf"  # "pdf", "html", or "text"
    # The date the bulletin *covers*, when the source can know it from the URL:
    # the probed filename for Parishes Online and eCatholic (which name each
    # file for its Sunday), the parsed link or subpage slug for Self-Hosted.
    #
    # `None` means unknown, and is deliberately NOT filled in with a guess.
    # Discover Mass URLs are opaque tokens and Webpage rows have no file at all,
    # so any date for them would be inferred from the run's own calendar - and a
    # caller cannot tell an inferred date from a read one. Callers that need a
    # week to work with (the holiday calendar, freshness checks) substitute the
    # run's Sunday themselves, knowing they are doing it.
    bulletin_date: Optional[date] = None


class BulletinSource(ABC):
    """Abstract bulletin source."""

    @abstractmethod
    async def download(
        self, parish_id: str, bulletin_url: Optional[str] = None
    ) -> DownloadResult:
        """Download the latest bulletin for a parish.

        Args:
            parish_id: The parish identifier used by this source.
            bulletin_url: Optional URL for self-hosted bulletins (page containing PDF link).
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name of this source."""
        ...

    @property
    def rate_limit_delay(self) -> float:
        """Seconds to wait between requests (for rate limiting)."""
        return 0.0
