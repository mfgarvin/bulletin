"""Abstract database client protocol."""

from abc import ABC, abstractmethod
from typing import Optional

from schemas import BulletinExtraction, ParishRecord


class DatabaseClient(ABC):
    """Abstract database client - implement for Notion, Supabase, etc."""

    @abstractmethod
    async def get_parishes_to_process(self, stale_days: int = 7) -> list[ParishRecord]:
        """Get enabled parishes that need updating (data older than stale_days)."""
        ...

    @abstractmethod
    async def get_parish(self, parish_id: str) -> Optional[ParishRecord]:
        """Get a single parish by ID."""
        ...

    @abstractmethod
    async def save_extraction(
        self,
        parish_id: str,
        extraction: BulletinExtraction,
        bulletin_url: str,
        log: list[str],
    ) -> None:
        """Save extraction results to database."""
        ...
