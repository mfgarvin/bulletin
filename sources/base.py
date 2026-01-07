"""Abstract bulletin source protocol."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


@dataclass
class DownloadResult:
    """Result of a bulletin download attempt."""

    success: bool
    pdf_bytes: Optional[bytes] = None
    url: Optional[str] = None
    error: Optional[str] = None
    content_type: str = "pdf"  # "pdf", "html", or "text"


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
