from __future__ import annotations

from types import SimpleNamespace

import pytest

from spiderhub.core.settings import Settings
from spiderhub.downloaders.browser_factory import build_l3_fetcher
from spiderhub.downloaders.camoufox_fetcher import CamoufoxFetcher
from spiderhub.downloaders.patchright_fetcher import PatchrightFetcher
from spiderhub.downloaders.playwright_fetcher import PlaywrightFetcher


def test_cdp_forces_playwright_even_when_camoufox_configured() -> None:
    settings = Settings(
        browser_engine="camoufox",
        browser_cdp_url="http://127.0.0.1:9222",
    )
    fetcher = build_l3_fetcher(settings)
    assert isinstance(fetcher, PlaywrightFetcher)


def test_build_camoufox_engine() -> None:
    settings = Settings(browser_engine="camoufox")
    assert isinstance(build_l3_fetcher(settings), CamoufoxFetcher)


def test_build_patchright_engine() -> None:
    settings = Settings(browser_engine="patchright")
    assert isinstance(build_l3_fetcher(settings), PatchrightFetcher)


def test_build_playwright_default() -> None:
    settings = Settings(browser_engine="playwright")
    assert isinstance(build_l3_fetcher(settings), PlaywrightFetcher)


def test_unknown_engine_raises() -> None:
    fake = SimpleNamespace(browser_cdp_url="", browser_engine="nope")
    with pytest.raises(ValueError, match="browser_engine"):
        build_l3_fetcher(fake)  # type: ignore[arg-type]
