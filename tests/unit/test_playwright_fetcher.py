from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.browser_challenge import (
    is_closed_target_error,
    is_recoverable_fetch_error,
)
from spiderhub.downloaders.playwright_fetcher import (
    PlaywrightFetcher,
    challenge_wait_cleared,
    is_transient_page_error,
)
from spiderhub.events import ChallengeNeedsHuman


@pytest.mark.asyncio
async def test_playwright_fetch_ok() -> None:
    async def fake_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        return url, 200, "<html>browser-ok</html>", {}

    settings = Settings(request_delay_seconds=0.0)
    async with PlaywrightFetcher(settings, fetch_page=fake_page) as fetcher:
        resp = await fetcher.fetch("https://missav.ws/cn/x")
    assert resp.status_code == 200
    assert "browser-ok" in resp.text


@pytest.mark.asyncio
async def test_playwright_challenge_raises() -> None:
    async def fake_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        return (
            url,
            403,
            "<title>Just a moment...</title>challenge-platform",
            {},
        )

    settings = Settings(request_delay_seconds=0.0)
    async with PlaywrightFetcher(settings, fetch_page=fake_page) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://missav.ws/cn/x")


@pytest.mark.asyncio
async def test_playwright_fetcher_cdp_enabled_uses_launcher_not_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        request_delay_seconds=0.0,
        browser_cdp_enabled=True,
        browser_challenge_wait_seconds=1.0,
    )
    ensure = AsyncMock(return_value="http://127.0.0.1:9222")
    shutdown = AsyncMock()

    class _FakeLauncher:
        async def ensure_ready(self, actual_settings: Settings) -> str:
            return await ensure(actual_settings)

        async def shutdown(self) -> None:
            await shutdown()

    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.ChromeCdpLauncher",
        _FakeLauncher,
    )
    launch_persistent = AsyncMock(
        side_effect=AssertionError("must not launch persistent")
    )
    launch_ephemeral = AsyncMock(
        side_effect=AssertionError("must not launch ephemeral")
    )
    connect = AsyncMock()
    monkeypatch.setattr(PlaywrightFetcher, "_launch_persistent", launch_persistent)
    monkeypatch.setattr(PlaywrightFetcher, "_launch_ephemeral", launch_ephemeral)
    monkeypatch.setattr(PlaywrightFetcher, "_connect_cdp", connect)

    class _FakePlaywright:
        async def stop(self) -> None:
            return None

    async def fake_start() -> _FakePlaywright:
        return _FakePlaywright()

    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: SimpleNamespace(start=fake_start),
    )

    fetcher = PlaywrightFetcher(settings)
    async with fetcher:
        ensure.assert_awaited_once_with(settings)
        connect.assert_awaited_once_with()
    launch_persistent.assert_not_awaited()
    launch_ephemeral.assert_not_awaited()
    shutdown.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_playwright_fetcher_cdp_connect_failure_shuts_down_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        request_delay_seconds=0.0,
        browser_cdp_enabled=True,
    )
    shutdown = AsyncMock()

    class _FakeLauncher:
        async def ensure_ready(self, _settings: Settings) -> str:
            return "http://127.0.0.1:9222"

        async def shutdown(self) -> None:
            await shutdown()

    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.ChromeCdpLauncher",
        _FakeLauncher,
    )
    connect = AsyncMock(side_effect=RuntimeError("CDP connect failed"))
    monkeypatch.setattr(PlaywrightFetcher, "_connect_cdp", connect)
    stop = AsyncMock()

    class _FakePlaywright:
        async def stop(self) -> None:
            await stop()

    async def fake_start() -> _FakePlaywright:
        return _FakePlaywright()

    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: SimpleNamespace(start=fake_start),
    )

    fetcher = PlaywrightFetcher(settings)
    with pytest.raises(RuntimeError, match="CDP connect failed"):
        await fetcher.__aenter__()

    shutdown.assert_awaited_once_with()
    stop.assert_awaited_once_with()
    assert fetcher._cdp_launcher is None
    assert fetcher._playwright is None


def test_challenge_wait_cleared_requires_title_and_clearance_or_clean_body() -> None:
    assert not challenge_wait_cleared(
        title="Just a moment...",
        cookie_names=(),
        body_html="",
    )
    assert not challenge_wait_cleared(
        title="北野未奈",
        cookie_names=(),
        body_html="<title>请稍候…</title>正在验证您是否是真人",
    )
    assert challenge_wait_cleared(
        title="北野未奈",
        cookie_names=("cf_clearance",),
        body_html="<html>ok</html>",
    )
    assert challenge_wait_cleared(
        title="北野未奈",
        cookie_names=(),
        body_html="<html><title>北野未奈</title><body>ok</body></html>",
    )


def test_is_transient_page_error_for_navigation() -> None:
    assert is_transient_page_error(
        RuntimeError(
            "Page.content: Unable to retrieve content because the page "
            "is navigating and changing the content."
        )
    )
    assert is_transient_page_error(
        RuntimeError("Page.title: Execution context was destroyed")
    )
    assert not is_transient_page_error(RuntimeError("boom"))


def test_is_closed_target_error() -> None:
    assert is_closed_target_error(
        RuntimeError("Page.goto: Target page, context or browser has been closed")
    )
    assert is_closed_target_error(
        RuntimeError("browser/page closed during challenge wait")
    )
    assert is_closed_target_error(RuntimeError("Page.goto: Page crashed"))
    assert not is_closed_target_error(RuntimeError("timeout 30000ms exceeded"))


def test_is_recoverable_fetch_error() -> None:
    assert is_recoverable_fetch_error(
        RuntimeError(
            "Page.goto: Timeout 30000ms exceeded.\n"
            'Call log:\n  - navigating to "https://missav.ws/x", '
            'waiting until "domcontentloaded"'
        )
    )
    assert is_recoverable_fetch_error(
        RuntimeError("Page.goto: Target page, context or browser has been closed")
    )
    assert is_recoverable_fetch_error(
        RuntimeError("Page.content: Execution context was destroyed")
    )
    assert not is_recoverable_fetch_error(RuntimeError("boom"))
    assert not is_recoverable_fetch_error(
        ChallengeDetectedError("https://x", 403, "cf")
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "goto_error",
    [
        "Page.goto: Target page, context or browser has been closed",
        "Page.goto: Page crashed",
    ],
)
async def test_playwright_recovers_dead_page_and_retries(goto_error: str) -> None:
    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self, *, fail_goto: bool) -> None:
            self._fail_goto = fail_goto
            self.context = self

        async def goto(self, *_args: object, **_kwargs: object) -> object:
            if self._fail_goto:
                raise RuntimeError(goto_error)

            class _Resp:
                status = 200

            return _Resp()

        async def title(self) -> str:
            return "ok"

        async def cookies(self) -> list[dict[str, str]]:
            return [{"name": "cf_clearance", "value": "1"}]

        async def content(self) -> str:
            return "<html><title>ok</title><body>ok</body></html>"

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def close(self) -> None:
            return None

    class _FakeContext:
        def __init__(self) -> None:
            self.pages_created = 0

        async def cookies(self) -> list[dict[str, str]]:
            return []

        async def new_page(self) -> _FakePage:
            self.pages_created += 1
            # Replacement tab after the dead shared page should succeed.
            return _FakePage(fail_goto=False)

        async def storage_state(self, **_kwargs: object) -> None:
            return None

    settings = Settings(request_delay_seconds=0.0, browser_challenge_wait_seconds=1.0)
    fetcher = PlaywrightFetcher(settings)
    # Bypass real Playwright launch; drive recovery against fakes.
    fetcher._context = _FakeContext()
    fetcher._reuse_page = True
    fetcher._shared_page = _FakePage(fail_goto=True)
    fetcher._content_headless = True

    resp = await fetcher.fetch("https://missav.ws/cn/x")
    assert resp.status_code == 200
    assert fetcher._context.pages_created == 1  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_playwright_retries_goto_timeout_then_succeeds() -> None:
    goto_calls = {"n": 0}

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self

        async def goto(self, *_args: object, **_kwargs: object) -> object:
            goto_calls["n"] += 1
            if goto_calls["n"] < 2:
                raise RuntimeError("Page.goto: Timeout 30000ms exceeded.")

            class _Resp:
                status = 200

            return _Resp()

        async def title(self) -> str:
            return "ok"

        async def cookies(self) -> list[dict[str, str]]:
            return [{"name": "cf_clearance", "value": "1"}]

        async def content(self) -> str:
            return "<html><title>ok</title><body>ok</body></html>"

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            return None

        async def close(self) -> None:
            return None

    class _FakeContext:
        async def cookies(self) -> list[dict[str, str]]:
            return []

        async def new_page(self) -> _FakePage:
            return _FakePage()

        async def storage_state(self, **_kwargs: object) -> None:
            return None

    settings = Settings(request_delay_seconds=0.0, browser_challenge_wait_seconds=1.0)
    fetcher = PlaywrightFetcher(settings)
    fetcher._context = _FakeContext()
    fetcher._reuse_page = True
    fetcher._shared_page = _FakePage()
    fetcher._content_headless = True

    response = await fetcher.fetch("https://missav.ws/cn/x")

    assert response.status_code == 200
    assert goto_calls["n"] == 2


@pytest.mark.asyncio
async def test_playwright_goto_timeout_exhausted_after_three_attempts() -> None:
    goto_calls = {"n": 0}

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self

        async def goto(self, *_args: object, **_kwargs: object) -> object:
            goto_calls["n"] += 1
            raise RuntimeError("Page.goto: Timeout 30000ms exceeded.")

        async def close(self) -> None:
            return None

    class _FakeContext:
        async def cookies(self) -> list[dict[str, str]]:
            return []

        async def new_page(self) -> _FakePage:
            return _FakePage()

        async def storage_state(self, **_kwargs: object) -> None:
            return None

    settings = Settings(request_delay_seconds=0.0, browser_challenge_wait_seconds=1.0)
    fetcher = PlaywrightFetcher(settings)
    fetcher._context = _FakeContext()
    fetcher._reuse_page = True
    fetcher._shared_page = _FakePage()
    fetcher._content_headless = True

    with pytest.raises(RuntimeError, match="Timeout"):
        await fetcher.fetch("https://missav.ws/cn/x")

    assert goto_calls["n"] == 3


@pytest.mark.asyncio
async def test_wait_challenge_clear_retries_navigation_errors() -> None:
    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.calls = 0
            self.context = self

        async def title(self) -> str:
            return "北野未奈"

        async def cookies(self) -> list[dict[str, str]]:
            return []

        async def content(self) -> str:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError(
                    "Page.content: Unable to retrieve content because the page "
                    "is navigating and changing the content."
                )
            return "<html><title>北野未奈</title><body>ok</body></html>"

        async def wait_for_load_state(self, *_args: object, **_kwargs: object) -> None:
            return None

    async def unused_fetch(_url: str) -> tuple[str, int, str, dict[str, str]]:
        raise AssertionError("fetch_page override should not run in this test")

    settings = Settings(request_delay_seconds=0.0, browser_challenge_wait_seconds=5.0)
    fetcher = PlaywrightFetcher(settings, fetch_page=unused_fetch)
    page = _FakePage()
    await fetcher._wait_challenge_clear(page, wait_s=3.0)
    assert page.calls >= 2


@pytest.mark.asyncio
async def test_wait_challenge_publishes_needs_human_only_when_detected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[object] = []
    clock = 0.0

    async def fake_publish(event: object) -> None:
        published.append(event)

    async def fake_sleep(seconds: float) -> None:
        nonlocal clock
        clock += seconds

    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.publish",
        fake_publish,
    )
    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.time.monotonic",
        lambda: clock,
    )
    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.asyncio.sleep",
        fake_sleep,
    )

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self
            self.title_calls = 0

        async def title(self) -> str:
            self.title_calls += 1
            return "Just a moment..."

        async def cookies(self) -> list[dict[str, str]]:
            return []

        async def content(self) -> str:
            return "<html><title>Just a moment...</title></html>"

        async def wait_for_load_state(self, *_a: object, **_k: object) -> None:
            return None

    settings = Settings(
        request_delay_seconds=0.0,
        browser_challenge_wait_seconds=5.0,
    )
    fetcher = PlaywrightFetcher(settings)
    fetcher._interactive = True
    fetcher._content_headless = False
    page = _FakePage()
    await fetcher._wait_challenge_clear(page, wait_s=5.0)

    assert len(published) == 1
    assert page.title_calls >= 1
    event = published[0]
    assert isinstance(event, ChallengeNeedsHuman)
    assert event.url == "https://missav.ws/cn/x"
    assert event.engine == "playwright"
    assert event.wait_seconds == 5.0


@pytest.mark.asyncio
async def test_wait_challenge_skips_publish_when_page_already_clear(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[object] = []

    async def fake_publish(event: object) -> None:
        published.append(event)

    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.publish",
        fake_publish,
    )

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self

        async def title(self) -> str:
            return "北野未奈"

        async def cookies(self) -> list[dict[str, str]]:
            return [{"name": "cf_clearance", "value": "1"}]

        async def content(self) -> str:
            return "<html><title>北野未奈</title><body>ok</body></html>"

        async def wait_for_load_state(self, *_a: object, **_k: object) -> None:
            return None

    settings = Settings(
        request_delay_seconds=0.0,
        browser_challenge_wait_seconds=5.0,
    )
    fetcher = PlaywrightFetcher(settings)
    # CDP / headed keep this path for the whole crawl.
    fetcher._interactive = True
    fetcher._content_headless = False
    await fetcher._wait_challenge_clear(_FakePage(), wait_s=5.0)

    assert published == []


@pytest.mark.asyncio
async def test_wait_challenge_skips_publish_when_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[object] = []
    clock = 0.0

    async def fake_publish(event: object) -> None:
        published.append(event)

    async def fake_sleep(seconds: float) -> None:
        nonlocal clock
        clock += seconds

    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.publish",
        fake_publish,
    )
    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.time.monotonic",
        lambda: clock,
    )
    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.asyncio.sleep",
        fake_sleep,
    )

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self

        async def title(self) -> str:
            return "Just a moment..."

        async def cookies(self) -> list[dict[str, str]]:
            return []

        async def content(self) -> str:
            return "<html><title>Just a moment...</title></html>"

        async def wait_for_load_state(self, *_a: object, **_k: object) -> None:
            return None

    settings = Settings(
        request_delay_seconds=0.0,
        browser_challenge_wait_seconds=5.0,
    )
    fetcher = PlaywrightFetcher(settings)
    fetcher._interactive = False
    fetcher._content_headless = True
    await fetcher._wait_challenge_clear(_FakePage(), wait_s=5.0)

    assert published == []
