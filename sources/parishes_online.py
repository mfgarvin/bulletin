"""Parishes Online bulletin source."""

from datetime import datetime, timedelta

import httpx

from .base import BulletinSource, DownloadResult

ROOT_URL = "https://container.parishesonline.com/bulletins/14"
DATE_FORMAT = "%Y%m%dB.pdf"
LOOKBACK_DAYS = 30


class ParishesOnlineSource(BulletinSource):
    """Download bulletins from Parishes Online."""

    @property
    def name(self) -> str:
        return "Parishes Online"

    async def download(self, parish_id: str) -> DownloadResult:
        """Download the latest bulletin, searching back up to 30 days."""
        async with httpx.AsyncClient() as client:
            current_date = datetime.now()

            for days_back in range(LOOKBACK_DAYS):
                check_date = current_date - timedelta(days=days_back)
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
                error=f"No bulletin found in the last {LOOKBACK_DAYS} days",
            )
