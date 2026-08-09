from __future__ import annotations

import asyncio
import logging
import time
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

logger = logging.getLogger(__name__)

_FETCH_CLOSED_RETRIES = 3


class CamoufoxFetcher:
    """L3 fetcher using Camoufox (anti-detect Firefox via Playwright-compatible API)."""

    def __init__(
        self,
        settings: Settings,
        *,
        fetch_page: FetchPageFn | None = None,
    ) -> None:
        self._settings = settings
        self._fetch_page_override = fetch_page
        self._browser_cm: Any | None = None
        self._browser: Any | None = None
        self._context: Any | None = None
        self._owns_context = False
        self._storage_path = Path(settings.browser_storage_state)
        self._interactive = not settings.browser_headless
        self._content_headless = settings.browser_headless
        self._reuse_page = True
        self._shared_page: Any | None = None

    async def __aenter__(self) -> CamoufoxFetcher:
        if self._fetch_page_override is None:
            try:
                from camoufox.async_api import AsyncCamoufox
            except ImportError as exc:  # pragma: no cover - env dependent
                raise ImportError(
                    "browser_engine=camoufox requires optional dependency; "
                    "install with: uv sync --extra stealth && python -m camoufox fetch"
                ) from exc
            # storage_state is a BrowserContext option, not BrowserType.launch().
            # Prefer Camoufox `window=` over Playwright viewport: Firefox/Camoufox
            # rejects setDefaultViewport(isMobile=...) from new_context(viewport=...).
            launch_kwargs: dict[str, Any] = {
                "headless": self._settings.browser_headless,
                "locale": "zh-CN",
                "window": (1280, 800),
            }
            self._browser_cm = AsyncCamoufox(**launch_kwargs)
            launched = await self._browser_cm.__aenter__()
            self._browser = launched
            await self._bind_context(launched)
            logger.info(
                "camoufox L3 ready headless=%s",
                self._settings.browser_headless,
            )
        return self

    async def _bind_context(self, launched: Any) -> None:
        """Attach a context; load storage_state via new_context when possible."""
        # Playwright defaults new_context() to 1280x720 viewport, which calls
        # Browser.setDefaultViewport(isMobile=...). Camoufox Firefox rejects that.
        context_kwargs: dict[str, Any] = {"no_viewport": True}
        if self._storage_path.is_file():
            context_kwargs["storage_state"] = str(self._storage_path)
            logger.info("loaded camoufox storage_state path=%s", self._storage_path)

        # AsyncCamoufox normally returns Browser; persistent_context returns Context.
        if hasattr(launched, "new_context"):
            self._context = await launched.new_context(**context_kwargs)
            self._owns_context = True
            return
        self._context = launched
        self._owns_context = False
        if "storage_state" in context_kwargs:
            logger.warning(
                "camoufox returned a context without new_context; "
                "storage_state was not applied path=%s",
                self._storage_path,
            )

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._shared_page is not None:
            try:
                await self._shared_page.close()
            except Exception:  # noqa: BLE001
                pass
            self._shared_page = None
        if self._owns_context and self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
        self._context = None
        self._owns_context = False
        if self._browser_cm is not None:
            await self._browser_cm.__aexit__(exc_type, exc_val, exc_tb)
            self._browser_cm = None
            self._browser = None

    async def _persist_storage(self) -> None:
        if self._context is None or self._fetch_page_override is not None:
            return
        storage_state = getattr(self._context, "storage_state", None)
        if storage_state is None:
            return
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        await storage_state(path=str(self._storage_path))
        logger.info("saved camoufox storage_state path=%s", self._storage_path)

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
        deadline = time.monotonic() + wait_s
        if self._interactive and not self._content_headless:
            logger.warning(
                "Camoufox: 若出现 Cloudflare 验证页，请在窗口中完成 (最长 %.0fs)",
                wait_s,
            )
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
        logger.warning("camoufox challenge wait timed out url=%s", page.url)

    async def _ensure_page(self) -> Any:
        if self._context is None:
            raise RuntimeError("CamoufoxFetcher must be used as async context manager")
        if self._reuse_page:
            if self._shared_page is None:
                if hasattr(self._context, "new_page"):
                    self._shared_page = await self._context.new_page()
                else:
                    assert self._browser is not None
                    self._shared_page = await self._browser.new_page()
            return self._shared_page
        if hasattr(self._context, "new_page"):
            return await self._context.new_page()
        assert self._browser is not None
        return await self._browser.new_page()

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

    async def _relaunch_browser(self) -> None:
        await self._invalidate_shared_page()
        if self._owns_context and self._context is not None:
            try:
                await self._context.close()
            except Exception:  # noqa: BLE001
                pass
        self._context = None
        self._owns_context = False
        if self._browser_cm is not None:
            try:
                await self._browser_cm.__aexit__(None, None, None)
            except Exception:  # noqa: BLE001
                pass
            self._browser_cm = None
            self._browser = None
        from camoufox.async_api import AsyncCamoufox

        launch_kwargs: dict[str, Any] = {
            "headless": self._settings.browser_headless,
            "locale": "zh-CN",
            "window": (1280, 800),
        }
        self._browser_cm = AsyncCamoufox(**launch_kwargs)
        launched = await self._browser_cm.__aenter__()
        self._browser = launched
        await self._bind_context(launched)

    async def _recover_browser(self, *, url: str, attempt: int) -> None:
        await self._invalidate_shared_page()
        if await self._browser_stack_alive():
            logger.warning(
                "camoufox page closed; opening a new tab attempt=%s url=%s",
                attempt,
                url,
            )
            return
        logger.warning(
            "camoufox browser closed; relaunching attempt=%s url=%s",
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
            raise RuntimeError(f"camoufox status {status} for {final_url}")
        await self._persist_storage()
        return FetchedResponse(
            url=final_url,
            status_code=status,
            text=text,
            headers=headers,
        )
