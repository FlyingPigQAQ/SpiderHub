from __future__ import annotations

from types import SimpleNamespace

import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.curl_cffi_fetcher import CurlCffiFetcher


@pytest.mark.asyncio
async def test_curl_cffi_fetch_ok() -> None:
    async def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            url=url,
            status_code=200,
            text="<html>ok</html>",
            headers={"content-type": "text/html"},
        )

    settings = Settings(request_delay_seconds=0.0, http_max_retries=1)
    async with CurlCffiFetcher(settings, get=fake_get) as fetcher:
        resp = await fetcher.fetch("https://missav.ws/cn/x")
    assert resp.status_code == 200
    assert "ok" in resp.text


@pytest.mark.asyncio
async def test_curl_cffi_challenge_raises() -> None:
    async def fake_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            url=url,
            status_code=403,
            text="<title>Just a moment...</title>challenge-platform",
            headers={},
        )

    settings = Settings(request_delay_seconds=0.0, http_max_retries=1)
    async with CurlCffiFetcher(settings, get=fake_get) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://missav.ws/cn/x")
