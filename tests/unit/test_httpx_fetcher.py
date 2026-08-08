from __future__ import annotations

import httpx
import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher


@pytest.mark.asyncio
async def test_fetch_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>ok</html>", request=request)

    transport = httpx.MockTransport(handler)
    settings = Settings(request_delay_seconds=0.0, http_max_retries=1)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        resp = await fetcher.fetch("https://missav.ws/cn/x")
    assert resp.status_code == 200
    assert "ok" in resp.text


@pytest.mark.asyncio
async def test_fetch_challenge_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    transport = httpx.MockTransport(handler)
    settings = Settings(request_delay_seconds=0.0, http_max_retries=1)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://missav.ws/cn/x")
