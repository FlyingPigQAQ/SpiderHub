from __future__ import annotations

import logging
from types import TracebackType
from urllib.parse import urlparse

import httpx

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.base import FetchedResponse
from spiderhub.downloaders.browser_factory import build_l3_fetcher
from spiderhub.downloaders.browser_protocol import BrowserFetcher
from spiderhub.downloaders.curl_cffi_fetcher import CurlCffiFetcher
from spiderhub.downloaders.external_solver_fetcher import ExternalSolverFetcher
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher

logger = logging.getLogger(__name__)


def _is_robots_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    return path.endswith("/robots.txt") or path == "/robots.txt"


class AutoFetcher:
    """Sticky L1→L2→L3→L4 upgrade on challenge (robots.txt never sticky-upgrades).

    Default: after the first successful L3/L4 solve, stay on that level.

    CDP special-case: after L3 exports cookies, prefer L2 for content so the
    attached Chrome is not navigated on every URL (avoids focus stealing).
    If L2 still hits a challenge (or ConnectError / other HTTP failure), abandon
    HTTP content and sticky L3. With CDP configured, L1 httpx.ConnectError also
    upgrades to L3 (HTTP may be RST while attached Chrome still works).
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        l2: CurlCffiFetcher | None = None,
        l3: BrowserFetcher | None = None,
        l4: ExternalSolverFetcher | None = None,
    ) -> None:
        self._settings = settings
        self._l1 = HttpxFetcher(settings, transport=transport)
        self._l2 = l2 if l2 is not None else CurlCffiFetcher(settings)
        self._l3 = l3 if l3 is not None else build_l3_fetcher(settings)
        self._l4 = l4 if l4 is not None else ExternalSolverFetcher(settings)
        self._level = 1
        self._l2_entered = False
        self._l3_entered = False
        self._l4_entered = False
        self._browser_session_ready = False
        self._solver_session_ready = False
        self._prefer_http_after_browser = bool(settings.browser_cdp_url.strip())
        self._http_content_abandoned = False

    async def __aenter__(self) -> AutoFetcher:
        await self._l1.__aenter__()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._l4_entered:
            await self._l4.__aexit__(exc_type, exc_val, exc_tb)
            self._l4_entered = False
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
        self._level = max(self._level, 3)

    async def _ensure_l4(self) -> None:
        if not self._l4_entered:
            await self._l4.__aenter__()
            self._l4_entered = True
        self._level = 4

    async def _sync_browser_cookies_to_http(self) -> None:
        cookies = await self._l3.export_cookies()
        if not cookies:
            return
        self._l1.set_cookies(cookies)
        await self._ensure_l2()
        self._l2.set_cookies(cookies)

    async def _prepare_browser_content_session(self) -> None:
        if self._browser_session_ready:
            await self._sync_browser_cookies_to_http()
            return
        await self._sync_browser_cookies_to_http()
        self._browser_session_ready = True
        if self._prefer_http_after_browser and not self._http_content_abandoned:
            # Keep L3 connected for re-upgrade, but stop navigating Chrome.
            self._level = 2
            logger.info(
                "browser session ready; prefer L2 for content after CDP "
                "(cookies synced; Chrome idle unless L2 challenges again)"
            )
            return
        await self._l3.prefer_headless_for_content()
        self._level = 3
        logger.info(
            "browser session ready; sticky L3 for content engine=%s "
            "(reuse one browser tab, skip L2 CF bounce)",
            self._settings.browser_engine,
        )

    async def _abandon_http_content_for_browser(self, url: str) -> FetchedResponse:
        """L2 still challenged after CDP cookies — sticky L3 for the rest."""
        self._http_content_abandoned = True
        self._level = 3
        logger.info(
            "L2 still challenged after CDP cookies; sticky L3 for content url=%s",
            url,
        )
        return await self._fetch_l3(url)

    async def _prepare_solver_content_session(self) -> None:
        if self._solver_session_ready:
            return
        cookies = await self._l4.export_cookies()
        if cookies:
            self._l1.set_cookies(cookies)
            await self._ensure_l2()
            self._l2.set_cookies(cookies)
        self._level = 4
        self._solver_session_ready = True
        logger.info(
            "external solver session ready; sticky L4 for content session=%s",
            self._settings.external_solver_session,
        )

    async def _fetch_l3(self, url: str) -> FetchedResponse:
        await self._ensure_l3()
        try:
            response = await self._l3.fetch(url)
        except ChallengeDetectedError:
            if not self._settings.allow_external_solver:
                raise
            logger.info(
                "upgrade fetch L3->L4 engine=%s url=%s",
                self._settings.browser_engine,
                url,
            )
            return await self._fetch_l4(url)
        await self._prepare_browser_content_session()
        return response

    async def _fetch_l4(self, url: str) -> FetchedResponse:
        await self._ensure_l4()
        response = await self._l4.fetch(url)
        await self._prepare_solver_content_session()
        return response

    def _upgrade_after_l2(self, url: str, exc: ChallengeDetectedError) -> str:
        """Return next hop label: l3, l4, or raise."""
        if _is_robots_url(url) or not self._settings.allow_fetcher_upgrade:
            raise exc
        skip_browser = (
            self._settings.allow_external_solver
            and self._settings.external_solver_skip_browser
        )
        if skip_browser:
            return "l4"
        if self._settings.allow_browser:
            return "l3"
        if self._settings.allow_external_solver:
            return "l4"
        raise exc

    def _can_upgrade_connect_error_to_l3(self, url: str) -> bool:
        """CDP-only: HTTP clients may be RST while attached Chrome still works."""
        return (
            self._prefer_http_after_browser
            and self._settings.allow_fetcher_upgrade
            and self._settings.allow_browser
            and not _is_robots_url(url)
        )

    async def _upgrade_connect_error_to_l3(
        self, url: str, *, from_level: str, exc: httpx.ConnectError
    ) -> FetchedResponse:
        logger.info(
            "upgrade fetch %s->L3 reason=connect_error err=%r engine=%s url=%s",
            from_level,
            str(exc) or type(exc).__name__,
            self._settings.browser_engine,
            url,
        )
        return await self._fetch_l3(url)

    async def fetch(self, url: str) -> FetchedResponse:
        if self._level >= 4:
            return await self._l4.fetch(url)
        if self._level >= 3:
            try:
                return await self._l3.fetch(url)
            except ChallengeDetectedError:
                if not self._settings.allow_external_solver:
                    raise
                logger.info(
                    "upgrade fetch L3->L4 sticky path engine=%s url=%s",
                    self._settings.browser_engine,
                    url,
                )
                return await self._fetch_l4(url)
        if self._level >= 2:
            try:
                return await self._l2.fetch(url)
            except ChallengeDetectedError as exc:
                if (
                    self._prefer_http_after_browser
                    and self._browser_session_ready
                    and not self._http_content_abandoned
                ):
                    return await self._abandon_http_content_for_browser(url)
                hop = self._upgrade_after_l2(url, exc)
                if hop == "l4":
                    logger.info(
                        "upgrade fetch L2->L4 reason=%s status=%s url=%s",
                        exc.reason,
                        exc.status_code,
                        url,
                    )
                    return await self._fetch_l4(url)
                logger.info(
                    "upgrade fetch L2->L3 reason=%s status=%s engine=%s url=%s",
                    exc.reason,
                    exc.status_code,
                    self._settings.browser_engine,
                    url,
                )
                return await self._fetch_l3(url)
            except Exception as exc:
                # After CDP cookie sync, L2 may fail with curl errors (not httpx).
                if (
                    self._prefer_http_after_browser
                    and self._browser_session_ready
                    and not self._http_content_abandoned
                    and not _is_robots_url(url)
                ):
                    logger.info(
                        "L2 failed after CDP cookies; sticky L3 url=%s err=%r",
                        url,
                        str(exc) or type(exc).__name__,
                    )
                    return await self._abandon_http_content_for_browser(url)
                if (
                    isinstance(exc, httpx.ConnectError)
                    and self._can_upgrade_connect_error_to_l3(url)
                ):
                    return await self._upgrade_connect_error_to_l3(
                        url, from_level="L2", exc=exc
                    )
                raise

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
                hop = self._upgrade_after_l2(url, exc2)
                if hop == "l4":
                    logger.info(
                        "upgrade fetch L2->L4 reason=%s status=%s url=%s",
                        exc2.reason,
                        exc2.status_code,
                        url,
                    )
                    return await self._fetch_l4(url)
                logger.info(
                    "upgrade fetch L2->L3 reason=%s status=%s engine=%s url=%s",
                    exc2.reason,
                    exc2.status_code,
                    self._settings.browser_engine,
                    url,
                )
                return await self._fetch_l3(url)
            except httpx.ConnectError as exc2:
                if self._can_upgrade_connect_error_to_l3(url):
                    return await self._upgrade_connect_error_to_l3(
                        url, from_level="L2", exc=exc2
                    )
                raise
        except httpx.ConnectError as exc:
            if self._can_upgrade_connect_error_to_l3(url):
                return await self._upgrade_connect_error_to_l3(
                    url, from_level="L1", exc=exc
                )
            raise
