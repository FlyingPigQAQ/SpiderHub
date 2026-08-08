from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from types import TracebackType
from typing import Any

from spiderhub.challenges.detect import ChallengeDetectedError, detect_challenge
from spiderhub.core.settings import Settings
from spiderhub.downloaders.base import FetchedResponse

logger = logging.getLogger(__name__)

GetFn = Callable[..., Awaitable[Any]]


class CurlCffiFetcher:
    """L2 fetcher: browser-like TLS/HTTP2 fingerprint via curl_cffi."""

    def __init__(
        self,
        settings: Settings,
        *,
        get: GetFn | None = None,
    ) -> None:
        self._settings = settings
        self._get_override = get
        self._session: Any | None = None

    async def __aenter__(self) -> CurlCffiFetcher:
        if self._get_override is None:
            from curl_cffi.requests import AsyncSession

            self._session = AsyncSession(
                timeout=self._settings.http_timeout_seconds,
                impersonate=self._settings.impersonate_target,
                allow_redirects=True,
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        if self._session is not None:
            await self._session.close()
            self._session = None

    async def _get(self, url: str) -> Any:
        if self._get_override is not None:
            return await self._get_override(
                url,
                impersonate=self._settings.impersonate_target,
            )
        if self._session is None:
            raise RuntimeError("CurlCffiFetcher must be used as async context manager")
        return await self._session.get(url)

    async def fetch(self, url: str) -> FetchedResponse:
        delay = self._settings.request_delay_seconds
        retries = self._settings.http_max_retries
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                response = await self._get(url)
            except Exception as exc:  # noqa: BLE001 — network boundary
                last_exc = exc
                logger.warning(
                    "curl_cffi fetch error url=%s attempt=%s err=%s",
                    url,
                    attempt,
                    exc,
                )
                continue
            text = response.text
            status = int(response.status_code)
            headers = {str(k): str(v) for k, v in dict(response.headers).items()}
            final_url = str(getattr(response, "url", url))
            reason = detect_challenge(
                url=final_url,
                status_code=status,
                text=text,
                headers=headers,
            )
            if reason:
                raise ChallengeDetectedError(final_url, status, reason)
            if 200 <= status < 300:
                return FetchedResponse(
                    url=final_url,
                    status_code=status,
                    text=text,
                    headers=headers,
                )
            last_exc = RuntimeError(f"status {status}")
            logger.warning(
                "curl_cffi bad status url=%s status=%s attempt=%s",
                url,
                status,
                attempt,
            )
        assert last_exc is not None
        raise last_exc
