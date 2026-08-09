from __future__ import annotations

import logging
from types import TracebackType
from urllib.parse import urlparse

import httpx

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.base import FetchedResponse
from spiderhub.downloaders.curl_cffi_fetcher import CurlCffiFetcher
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.downloaders.playwright_fetcher import PlaywrightFetcher

logger = logging.getLogger(__name__)


def _is_robots_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    return path.endswith("/robots.txt") or path == "/robots.txt"


class AutoFetcher:
    """Sticky L1→L2→L3 upgrade on challenge (robots.txt never sticky-upgrades).

    After the first successful L3 solve, stay on L3 for content. MissAV-style CF
    often still blocks L2 even with cookies; leaving CDP/browser avoids a hang on
    headless re-challenge waits.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        l2: CurlCffiFetcher | None = None,
        l3: PlaywrightFetcher | None = None,
    ) -> None:
        self._settings = settings
        self._l1 = HttpxFetcher(settings, transport=transport)
        self._l2 = l2 if l2 is not None else CurlCffiFetcher(settings)
        self._l3 = l3 if l3 is not None else PlaywrightFetcher(settings)
        self._level = 1
        self._l2_entered = False
        self._l3_entered = False
        self._browser_session_ready = False

    async def __aenter__(self) -> AutoFetcher:
        await self._l1.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._l3_entered:
            await self._l3.__aexit__(exc_type, exc_val, exc_tb)
            self._l3_entered = False
        if self._l2_entered:
            await self._l2.__aexit__(exc_type, exc_val, exc_tb)
            self._l2_entered = False
        await self._l1.__aexit__(exc_type, exc_val, exc_tb)

    async def _ensure_l2(self) -> None:
        if not self._l2_entered:
            await self._l2.__aenter__()
            self._l2_entered = True
        self._level = max(self._level, 2)

    async def _ensure_l3(self) -> None:
        if not self._l3_entered:
            await self._l3.__aenter__()
            self._l3_entered = True
        self._level = 3

    async def _prepare_browser_content_session(self) -> None:
        if self._browser_session_ready:
            return
        cookies = await self._l3.export_cookies()
        if cookies:
            self._l1.set_cookies(cookies)
            await self._ensure_l2()
            self._l2.set_cookies(cookies)
        await self._l3.prefer_headless_for_content()
        self._level = 3
        self._browser_session_ready = True
        logger.info(
            "browser session ready; sticky L3 for content "
            "(reuse one browser tab, skip L2 CF bounce)"
        )

    async def _fetch_l3(self, url: str) -> FetchedResponse:
        await self._ensure_l3()
        response = await self._l3.fetch(url)
        await self._prepare_browser_content_session()
        return response

    async def fetch(self, url: str) -> FetchedResponse:
        if self._level >= 3:
            return await self._l3.fetch(url)
        if self._level >= 2:
            try:
                return await self._l2.fetch(url)
            except ChallengeDetectedError as exc:
                if _is_robots_url(url) or not self._settings.allow_fetcher_upgrade:
                    raise
                if not self._settings.allow_browser:
                    raise
                logger.info(
                    "upgrade fetch L2->L3 reason=%s status=%s url=%s",
                    exc.reason,
                    exc.status_code,
                    url,
                )
                return await self._fetch_l3(url)

        try:
            return await self._l1.fetch(url)
        except ChallengeDetectedError as exc:
            if _is_robots_url(url) or not self._settings.allow_fetcher_upgrade:
                raise
            logger.info(
                "upgrade fetch L1->L2 reason=%s status=%s url=%s",
                exc.reason,
                exc.status_code,
                url,
            )
            await self._ensure_l2()
            try:
                return await self._l2.fetch(url)
            except ChallengeDetectedError as exc2:
                if not self._settings.allow_browser:
                    raise
                logger.info(
                    "upgrade fetch L2->L3 reason=%s status=%s url=%s",
                    exc2.reason,
                    exc2.status_code,
                    url,
                )
                return await self._fetch_l3(url)
