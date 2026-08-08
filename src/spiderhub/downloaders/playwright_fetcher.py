from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from pathlib import Path
from types import TracebackType
from typing import Any

from spiderhub.challenges.detect import ChallengeDetectedError, detect_challenge
from spiderhub.core.settings import Settings
from spiderhub.downloaders.base import FetchedResponse

logger = logging.getLogger(__name__)

FetchPageFn = Callable[[str], Awaitable[tuple[str, int, str, dict[str, str]]]]


class PlaywrightFetcher:
    """L3 fetcher: real browser for JS / bot-management challenges."""

    def __init__(
        self,
        settings: Settings,
        *,
        fetch_page: FetchPageFn | None = None,
    ) -> None:
        self._settings = settings
        self._fetch_page_override = fetch_page
        self._playwright: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._storage_path = Path(settings.browser_storage_state)

    async def __aenter__(self) -> PlaywrightFetcher:
        if self._fetch_page_override is None:
            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            launch_kwargs: dict[str, Any] = {
                "headless": self._settings.browser_headless,
                "args": ["--disable-blink-features=AutomationControlled"],
                "ignore_default_args": ["--enable-automation"],
            }
            try:
                self._browser = await self._playwright.chromium.launch(
                    channel="chrome",
                    **launch_kwargs,
                )
            except Exception:
                logger.warning("launch channel=chrome failed; trying bundled chromium")
                self._browser = await self._playwright.chromium.launch(**launch_kwargs)
            context_kwargs: dict[str, Any] = {
                "locale": "zh-CN",
                "viewport": {"width": 1280, "height": 800},
            }
            if self._storage_path.is_file():
                context_kwargs["storage_state"] = str(self._storage_path)
                logger.info("loaded browser storage_state path=%s", self._storage_path)
            self._context = await self._browser.new_context(**context_kwargs)
            await self._context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        if self._context is not None:
            await self._context.close()
            self._context = None
        if self._browser is not None:
            await self._browser.close()
            self._browser = None
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _persist_storage(self) -> None:
        if self._context is None or self._fetch_page_override is not None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(self._storage_path))
        logger.info("saved browser storage_state path=%s", self._storage_path)

    async def _fetch_page(self, url: str) -> tuple[str, int, str, dict[str, str]]:
        if self._fetch_page_override is not None:
            return await self._fetch_page_override(url)
        if self._context is None:
            raise RuntimeError(
                "PlaywrightFetcher must be used as async context manager"
            )
        page = await self._context.new_page()
        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(self._settings.http_timeout_seconds * 1000),
            )
            status = int(response.status) if response is not None else 200
            wait_s = max(5.0, self._settings.browser_challenge_wait_seconds)
            wait_ms = int(wait_s * 1000)
            if not self._settings.browser_headless:
                logger.warning(
                    "若出现 Cloudflare 验证页，请在打开的浏览器窗口中手动完成验证"
                )
            try:
                await page.wait_for_function(
                    """() => {
                        const t = (document.title || '').toLowerCase();
                        return !t.includes('just a moment')
                            && !t.includes('请稍候')
                            && !t.includes('attention required');
                    }""",
                    timeout=wait_ms,
                )
                status = 200
            except Exception:  # noqa: BLE001 — timeout means challenge unresolved
                logger.warning("browser challenge wait timed out url=%s", url)
            text = await page.content()
            title = await page.title()
            probe = f"{title}\n{text}"
            reason = detect_challenge(
                url=str(page.url),
                status_code=status,
                text=probe,
            )
            if reason:
                text = probe
            headers = {"content-type": "text/html"}
            return str(page.url), status, text, headers
        finally:
            await page.close()

    async def fetch(self, url: str) -> FetchedResponse:
        delay = self._settings.request_delay_seconds
        if delay > 0:
            await asyncio.sleep(delay)
        final_url, status, text, headers = await self._fetch_page(url)
        reason = detect_challenge(
            url=final_url,
            status_code=status,
            text=text,
            headers=headers,
        )
        if reason:
            raise ChallengeDetectedError(final_url, status, reason)
        if not (200 <= status < 300):
            raise RuntimeError(f"browser status {status} for {final_url}")
        await self._persist_storage()
        return FetchedResponse(
            url=final_url,
            status_code=status,
            text=text,
            headers=headers,
        )
