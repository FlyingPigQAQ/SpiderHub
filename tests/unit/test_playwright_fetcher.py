from __future__ import annotations

import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.playwright_fetcher import (
    PlaywrightFetcher,
    challenge_wait_cleared,
    is_transient_page_error,
)


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
