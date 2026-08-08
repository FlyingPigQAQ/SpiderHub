from __future__ import annotations

import asyncio
import logging
from types import TracebackType

import httpx

from spiderhub.challenges.detect import ChallengeDetectedError, detect_challenge
from spiderhub.core.settings import Settings
from spiderhub.downloaders.base import FetchedResponse

logger = logging.getLogger(__name__)


class HttpxFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HttpxFetcher:
        self._client = httpx.AsyncClient(
            transport=self._transport,
            timeout=self._settings.http_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "SpiderHub/0.1 (+https://github.com/local/SpiderHub)"
            },
        )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str) -> FetchedResponse:
        if self._client is None:
            raise RuntimeError("HttpxFetcher must be used as async context manager")
        delay = self._settings.request_delay_seconds
        retries = self._settings.http_max_retries
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                response = await self._client.get(url)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "fetch error url=%s attempt=%s err=%s", url, attempt, exc
                )
                continue
            headers = {k: v for k, v in response.headers.items()}
            reason = detect_challenge(
                url=str(response.url),
                status_code=response.status_code,
                text=response.text,
                headers=headers,
            )
            if reason:
                raise ChallengeDetectedError(
                    str(response.url), response.status_code, reason
                )
            if 200 <= response.status_code < 300:
                return FetchedResponse(
                    url=str(response.url),
                    status_code=response.status_code,
                    text=response.text,
                    headers=headers,
                )
            last_exc = httpx.HTTPStatusError(
                f"status {response.status_code}",
                request=response.request,
                response=response,
            )
            logger.warning(
                "bad status url=%s status=%s attempt=%s",
                url,
                response.status_code,
                attempt,
            )
        assert last_exc is not None
        raise last_exc
