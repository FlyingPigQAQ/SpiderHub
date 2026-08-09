from __future__ import annotations

import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.patchright_fetcher import PatchrightFetcher


@pytest.mark.asyncio
async def test_patchright_fetch_ok_via_override() -> None:
    async def fake_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        return url, 200, "<html>patchright-ok</html>", {}

    settings = Settings(request_delay_seconds=0.0, browser_engine="patchright")
    async with PatchrightFetcher(settings, fetch_page=fake_page) as fetcher:
        resp = await fetcher.fetch("https://example.com/x")
    assert "patchright-ok" in resp.text


@pytest.mark.asyncio
async def test_patchright_challenge_raises() -> None:
    async def fake_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        return (
            url,
            403,
            "<title>Just a moment...</title>challenge-platform",
            {},
        )

    settings = Settings(request_delay_seconds=0.0)
    async with PatchrightFetcher(settings, fetch_page=fake_page) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://example.com/x")
