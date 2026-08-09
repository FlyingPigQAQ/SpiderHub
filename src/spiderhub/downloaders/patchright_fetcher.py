from __future__ import annotations

import asyncio
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from types import TracebackType
from typing import Any

from spiderhub.challenges.detect import (
    ChallengeDetectedError,
    detect_challenge,
    is_challenge_title,
)
from spiderhub.core.settings import Settings
from spiderhub.downloaders.base import FetchedResponse
from spiderhub.downloaders.browser_challenge import (
    challenge_wait_cleared,
    is_closed_target_error,
    is_transient_page_error,
)
from spiderhub.downloaders.playwright_fetcher import FetchPageFn
from spiderhub.events import ChallengeNeedsHuman, publish

logger = logging.getLogger(__name__)

_FETCH_CLOSED_RETRIES = 3


class PatchrightFetcher:
    """L3 fetcher using Patchright (stealth Chromium, Playwright-compatible API)."""

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
        self._user_data_dir = Path(settings.browser_user_data_dir)
        self._owns_browser = False
        self._owns_context = False
        self._interactive = not settings.browser_headless
        self._content_headless = settings.browser_headless
        self._reuse_page = not settings.browser_headless
        self._shared_page: Any | None = None

    async def __aenter__(self) -> PatchrightFetcher:
        if self._fetch_page_override is None:
            try:
                from patchright.async_api import async_playwright
            except ImportError as exc:  # pragma: no cover - env dependent
                raise ImportError(
                    "browser_engine=patchright requires optional dependency; "
                    "install with: uv sync --extra stealth && "
                    "uv run patchright install chromium"
                ) from exc
            self._playwright = await async_playwright().start()
            if not self._settings.browser_headless:
                await self._launch_persistent()
            else:
                await self._launch_ephemeral()
                self._content_headless = True
            logger.info(
                "patchright L3 ready headless=%s",
                self._settings.browser_headless,
            )
        return self

    async def _launch_persistent(self) -> None:
        assert self._playwright is not None
        self._user_data_dir.mkdir(parents=True, exist_ok=True)
        launch_kwargs: dict[str, Any] = {
            "user_data_dir": str(self._user_data_dir),
            "headless": False,
            "locale": "zh-CN",
            "viewport": {"width": 1280, "height": 800},
        }
        if self._storage_path.is_file():
            launch_kwargs["storage_state"] = str(self._storage_path)
        self._context = await self._playwright.chromium.launch_persistent_context(
            **launch_kwargs,
        )
        self._owns_context = True
        self._reuse_page = True

    async def _launch_ephemeral(self) -> None:
        assert self._playwright is not None
        self._browser = await self._playwright.chromium.launch(headless=True)
        self._owns_browser = True
        context_kwargs: dict[str, Any] = {
            "locale": "zh-CN",
            "viewport": {"width": 1280, "height": 800},
        }
        if self._storage_path.is_file():
            context_kwargs["storage_state"] = str(self._storage_path)
        self._context = await self._browser.new_context(**context_kwargs)
        self._owns_context = True

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        del exc_type, exc_val, exc_tb
        await self._close_browser_stack()
        if self._playwright is not None:
            await self._playwright.stop()
            self._playwright = None

    async def _persist_storage(self) -> None:
        if self._context is None or self._fetch_page_override is not None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        await self._context.storage_state(path=str(self._storage_path))
        logger.info("saved patchright storage_state path=%s", self._storage_path)

    async def export_cookies(self) -> list[dict[str, Any]]:
        if self._context is None or self._fetch_page_override is not None:
            return []
        cookies = await self._context.cookies()
        return [dict(cookie) for cookie in cookies]

    async def prefer_headless_for_content(self) -> None:
        await self._persist_storage()

    async def _stable_page_content(self, page: Any, *, attempts: int = 10) -> str:
        last_exc: BaseException | None = None
        for _ in range(attempts):
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=5_000)
                return str(await page.content())
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if is_transient_page_error(exc):
                    await asyncio.sleep(0.4)
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def _wait_challenge_clear(self, page: Any, *, wait_s: float) -> None:
        if self._interactive and not self._content_headless:
            logger.warning(
                "Patchright: 若出现 Cloudflare 验证页，请在窗口中完成 (最长 %.0fs)",
                wait_s,
            )
            await publish(
                ChallengeNeedsHuman(
                    url=str(page.url),
                    engine="patchright",
                    wait_seconds=wait_s,
                    at=datetime.now(UTC),
                )
            )
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            try:
                title = await page.title()
                cookies = await page.context.cookies()
                cookie_names = {c.get("name", "") for c in cookies}
                if is_challenge_title(title):
                    await asyncio.sleep(0.5)
                    continue
                if "cf_clearance" in cookie_names:
                    return
                body_html = await self._stable_page_content(page, attempts=3)
                if challenge_wait_cleared(
                    title=title,
                    cookie_names=cookie_names,
                    body_html=body_html,
                ):
                    return
            except Exception as exc:  # noqa: BLE001
                if is_transient_page_error(exc):
                    await asyncio.sleep(0.5)
                    continue
                raise
            await asyncio.sleep(0.5)
        logger.warning("patchright challenge wait timed out url=%s", page.url)

    async def _ensure_page(self) -> Any:
        if self._context is None:
            raise RuntimeError(
                "PatchrightFetcher must be used as async context manager"
            )
        if self._reuse_page:
            if self._shared_page is None:
                self._shared_page = await self._context.new_page()
            return self._shared_page
        return await self._context.new_page()

    async def _invalidate_shared_page(self) -> None:
        if self._shared_page is None:
            return
        try:
            await self._shared_page.close()
        except Exception:  # noqa: BLE001
            pass
        self._shared_page = None

    async def _browser_stack_alive(self) -> bool:
        try:
            if self._browser is not None and hasattr(self._browser, "is_connected"):
                if not self._browser.is_connected():
                    return False
            if self._context is None:
                return False
            await self._context.cookies()
            return True
        except Exception:  # noqa: BLE001
            return False

    async def _close_browser_stack(self) -> None:
        await self._invalidate_shared_page()
        if self._owns_context and self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
        self._context = None
        self._owns_context = False
        if self._owns_browser and self._browser is not None:
            try:
                await self._browser.close()
            except Exception:  # noqa: BLE001
                pass
        self._browser = None
        self._owns_browser = False

    async def _relaunch_browser(self) -> None:
        await self._close_browser_stack()
        if self._playwright is None:
            raise RuntimeError("Patchright driver is not running; cannot recover")
        if not self._settings.browser_headless:
            await self._launch_persistent()
        else:
            await self._launch_ephemeral()
            self._content_headless = True

    async def _recover_browser(self, *, url: str, attempt: int) -> None:
        await self._invalidate_shared_page()
        if await self._browser_stack_alive():
            logger.warning(
                "patchright page closed; opening a new tab attempt=%s url=%s",
                attempt,
                url,
            )
            return
        logger.warning(
            "patchright browser closed; relaunching attempt=%s url=%s",
            attempt,
            url,
        )
        await self._relaunch_browser()

    async def _navigate_once(self, url: str) -> tuple[str, int, str, dict[str, str]]:
        page = await self._ensure_page()
        close_page = not self._reuse_page
        try:
            response = await page.goto(
                url,
                wait_until="domcontentloaded",
                timeout=int(self._settings.http_timeout_seconds * 1000),
            )
            status = int(response.status) if response is not None else 200
            wait_s = max(5.0, self._settings.browser_challenge_wait_seconds)
            if self._content_headless:
                wait_s = min(wait_s, 15.0)
            await self._wait_challenge_clear(page, wait_s=wait_s)
            text = await self._stable_page_content(page)
            title = await page.title()
            probe = f"{title}\n{text}"
            reason = detect_challenge(
                url=str(page.url),
                status_code=status if is_challenge_title(title) else 200,
                text=probe,
            )
            if reason:
                text = probe
            else:
                status = 200
            return str(page.url), status, text, {"content-type": "text/html"}
        finally:
            if close_page:
                try:
                    await page.close()
                except Exception:  # noqa: BLE001
                    pass

    async def _fetch_page(self, url: str) -> tuple[str, int, str, dict[str, str]]:
        if self._fetch_page_override is not None:
            return await self._fetch_page_override(url)
        last_exc: BaseException | None = None
        for attempt in range(1, _FETCH_CLOSED_RETRIES + 1):
            try:
                return await self._navigate_once(url)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                if not is_closed_target_error(exc) or attempt >= _FETCH_CLOSED_RETRIES:
                    raise
                await self._recover_browser(url=url, attempt=attempt)
        assert last_exc is not None
        raise last_exc

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
            raise RuntimeError(f"patchright status {status} for {final_url}")
        await self._persist_storage()
        return FetchedResponse(
            url=final_url,
            status_code=status,
            text=text,
            headers=headers,
        )
