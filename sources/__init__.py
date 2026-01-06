"""Bulletin source abstraction layer."""

from .base import BulletinSource, DownloadResult
from .parishes_online import ParishesOnlineSource
from .discover_mass import DiscoverMassSource
from .ecatholic import ECatholicSource
from .self_hosted import SelfHostedSource


def get_source_for_publisher(publisher: str) -> BulletinSource:
    """Factory function to get the appropriate source for a publisher."""
    sources = {
        "Parishes Online": ParishesOnlineSource(),
        "PO": ParishesOnlineSource(),
        "Discover Mass": DiscoverMassSource(),
        "DM": DiscoverMassSource(),
        "eCatholic": ECatholicSource(),
        "EC": ECatholicSource(),
        "Self-Hosted": SelfHostedSource(),
        "SH": SelfHostedSource(),
    }
    source = sources.get(publisher)
    if not source:
        raise ValueError(f"Unknown publisher: {publisher}")
    return source


__all__ = [
    "BulletinSource",
    "DownloadResult",
    "get_source_for_publisher",
    "ParishesOnlineSource",
    "DiscoverMassSource",
    "ECatholicSource",
    "SelfHostedSource",
]
