from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.camoufox_fetcher import CamoufoxFetcher


@pytest.mark.asyncio
async def test_camoufox_fetch_ok_via_override() -> None:
    async def fake_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        return url, 200, "<html>camoufox-ok</html>", {}

    settings = Settings(request_delay_seconds=0.0, browser_engine="camoufox")
    async with CamoufoxFetcher(settings, fetch_page=fake_page) as fetcher:
        resp = await fetcher.fetch("https://example.com/x")
    assert "camoufox-ok" in resp.text


@pytest.mark.asyncio
async def test_camoufox_challenge_raises() -> None:
    async def fake_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        return (
            url,
            403,
            "<title>Just a moment...</title>challenge-platform",
            {},
        )

    settings = Settings(request_delay_seconds=0.0)
    async with CamoufoxFetcher(settings, fetch_page=fake_page) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://example.com/x")


@pytest.mark.asyncio
async def test_camoufox_storage_state_goes_to_new_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = tmp_path / "storage_state.json"
    storage.write_text("{}", encoding="utf-8")
    launch_calls: list[dict[str, Any]] = []
    context_calls: list[dict[str, Any]] = []

    class _FakeContext:
        async def close(self) -> None:
            return None

    class _FakeBrowser:
        async def new_context(self, **kwargs: Any) -> _FakeContext:
            context_calls.append(kwargs)
            return _FakeContext()

    class _FakeAsyncCamoufox:
        def __init__(self, **kwargs: Any) -> None:
            launch_calls.append(kwargs)

        async def __aenter__(self) -> _FakeBrowser:
            return _FakeBrowser()

        async def __aexit__(self, *args: object) -> None:
            del args

    pkg = types.ModuleType("camoufox")
    api = types.ModuleType("camoufox.async_api")
    api.AsyncCamoufox = _FakeAsyncCamoufox  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "camoufox", pkg)
    monkeypatch.setitem(sys.modules, "camoufox.async_api", api)

    settings = Settings(
        request_delay_seconds=0.0,
        browser_engine="camoufox",
        browser_storage_state=str(storage),
    )
    async with CamoufoxFetcher(settings) as fetcher:
        assert fetcher._context is not None

    assert launch_calls
    assert "storage_state" not in launch_calls[0]
    assert "viewport" not in launch_calls[0]
    assert launch_calls[0].get("window") == (1280, 800)
    assert context_calls
    assert context_calls[0]["storage_state"] == str(storage)
    assert context_calls[0].get("no_viewport") is True
    assert "viewport" not in context_calls[0]
