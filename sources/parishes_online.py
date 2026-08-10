"""Parishes Online bulletin source."""

from datetime import datetime, timedelta
from typing import Optional

import httpx

from .base import BulletinSource, DownloadResult

ROOT_URL = "https://container.parishesonline.com/bulletins/14"
DATE_FORMAT = "%Y%m%dB.pdf"
LOOKBACK_DAYS = 30

# See the note in sources/ecatholic.py — same filename convention, same fix.
# The Sunday-dated bulletin is posted days early, so a Saturday run has to look
# forward to see it. Three days reaches the coming Sunday without being able to
# skip past a week's events.
LOOKAHEAD_DAYS = 3


class ParishesOnlineSource(BulletinSource):
    """Download bulletins from Parishes Online."""

    @property
    def name(self) -> str:
        return "Parishes Online"

    async def download(
        self, parish_id: str, bulletin_url: Optional[str] = None
    ) -> DownloadResult:
        """Download the latest bulletin, newest date first.

        Searches from `LOOKAHEAD_DAYS` in the future back to `LOOKBACK_DAYS` ago.
        """
        async with httpx.AsyncClient() as client:
            current_date = datetime.now()

            for offset in range(LOOKAHEAD_DAYS, -LOOKBACK_DAYS, -1):
                check_date = current_date + timedelta(days=offset)
                filename = check_date.strftime(DATE_FORMAT)
                url = f"{ROOT_URL}/{parish_id}/{filename}"

                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        return DownloadResult(
                            success=True,
                            pdf_bytes=response.content,
                            url=url,
                        )
                except httpx.RequestError as e:
                    continue

            return DownloadResult(
                success=False,
                error=f"No bulletin found within {LOOKBACK_DAYS} days",
            )
