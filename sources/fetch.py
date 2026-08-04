"""HTTP fetching with a browser-TLS fallback for bot-protected parish sites.

Some parish sites sit behind Cloudflare's managed challenge, which fingerprints
the TLS handshake (JA3) rather than the headers. httpx can't get past that at
any User-Agent — the request is refused before a single header is read. When a
fetch comes back with a "refused" status, we retry it through `curl_cffi`,
which replays a real browser's TLS and HTTP/2 fingerprint.

The fallback is deliberately second, not first: it's slower and speaks to a
native curl binding, so the ~190 parishes that answer plain httpx keep doing so.
"""

import logging
from dataclasses import dataclass
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15"

# Browser profile curl_cffi impersonates on the retry. Any recent profile works
# against the sites we've seen; chrome is the one curl_cffi keeps most current.
IMPERSONATE = "chrome"

# Statuses that mean "the server refused us", as opposed to "that page is not
# here". Cloudflare's managed challenge answers 403; other edges use 429/503.
# Only these trigger the fallback — a 404 is a real 404 on any TLS stack.
BLOCKED_STATUSES = frozenset({403, 429, 503})


@dataclass(slots=True)
class Response:
    """The subset of a response both backends agree on."""

    status_code: int
    content: bytes
    text: str
    headers: dict[str, str]
    url: str
    impersonated: bool = False


class Fetcher:
    """Fetches URLs, falling back to browser-TLS impersonation when blocked.

    Use as an async context manager so a multi-request scrape (bulletin page,
    then the PDF it links to) reuses one connection pool — and, on the fallback
    path, one cookie jar, which matters because Cloudflare hands out a clearance
    cookie that the follow-up request needs.

        async with Fetcher() as fetch:
            page = await fetch(bulletin_url)
            pdf = await fetch(pdf_url)
    """

    def __init__(self, timeout: float = 30.0):
        self._timeout = timeout
        self._client: Optional[httpx.AsyncClient] = None
        self._session = None  # curl_cffi AsyncSession, created only if needed

    async def __aenter__(self) -> "Fetcher":
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._client is not None:
            await self._client.aclose()
        if self._session is not None:
            await self._session.close()

    async def __call__(self, url: str) -> Response:
        """Fetch `url`, retrying through browser TLS if the server refuses.

        Raises `httpx.RequestError` on transport failures, matching what the
        callers already catch.
        """
        assert self._client is not None, "Fetcher must be used as a context manager"

        response = await self._client.get(
            url,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        )
        if response.status_code not in BLOCKED_STATUSES:
            return Response(
                status_code=response.status_code,
                content=response.content,
                text=response.text,
                headers=dict(response.headers),
                url=str(response.url),
            )

        logger.info(
            "HTTP %s from %s - retrying with browser TLS fingerprint",
            response.status_code,
            url,
        )
        fallback = await self._fetch_impersonated(url)
        return fallback if fallback is not None else Response(
            status_code=response.status_code,
            content=response.content,
            text=response.text,
            headers=dict(response.headers),
            url=str(response.url),
        )

    async def _fetch_impersonated(self, url: str) -> Optional[Response]:
        """Refetch through curl_cffi. Returns None if it isn't usable."""
        try:
            from curl_cffi.requests import AsyncSession
        except ImportError:
            logger.warning(
                "curl_cffi is not installed - cannot retry %s past bot protection", url
            )
            return None

        if self._session is None:
            self._session = AsyncSession()

        try:
            # No User-Agent of ours here: curl_cffi sends the full header set
            # that matches the fingerprint it impersonates, and overriding one
            # header would reintroduce the mismatch we're trying to avoid.
            response = await self._session.get(
                url,
                impersonate=IMPERSONATE,
                timeout=self._timeout,
                allow_redirects=True,
            )
        except Exception as e:  # curl_cffi raises its own error hierarchy
            logger.warning("Browser-TLS retry of %s failed: %s", url, e)
            return None

        if response.status_code in BLOCKED_STATUSES:
            logger.warning(
                "Browser-TLS retry of %s still returned HTTP %s",
                url,
                response.status_code,
            )

        return Response(
            status_code=response.status_code,
            content=response.content,
            text=response.text,
            headers=dict(response.headers),
            url=str(response.url),
            impersonated=True,
        )
