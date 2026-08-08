from __future__ import annotations

import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.playwright_fetcher import PlaywrightFetcher


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
