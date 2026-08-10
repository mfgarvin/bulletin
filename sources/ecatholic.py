"""eCatholic bulletin source."""

from datetime import datetime, timedelta
from typing import Optional

import httpx

from .base import BulletinSource, DownloadResult

ROOT_URL = "https://files.ecatholic.com"
DATE_FORMAT = "%Y%m%d.pdf"
LOOKBACK_DAYS = 30

# The filename is the Sunday the bulletin covers, and parishes upload it days
# early — St. Charles' Sunday file is typically up by Friday night. The weekly
# job runs Saturday, so searching only backwards from today can never see it,
# and every PO/eCatholic parish served last week's bulletin. Look a few days
# ahead, newest published wins.
#
# Three days reaches the coming Sunday from a Saturday run without being able to
# skip a whole week: a parish that posts unusually early can't pull a mid-week
# run onto the *following* Sunday's bulletin, which would drop the current
# week's events.
LOOKAHEAD_DAYS = 3


class ECatholicSource(BulletinSource):
    """Download bulletins from eCatholic."""

    @property
    def name(self) -> str:
        return "eCatholic"

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
                url = f"{ROOT_URL}/{parish_id}/bulletins/{filename}"

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
