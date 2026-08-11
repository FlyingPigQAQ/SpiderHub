from __future__ import annotations

from spiderhub.core.settings import BROWSER_ENGINES, Settings, cdp_mode_active
from spiderhub.downloaders.browser_protocol import BrowserFetcher
from spiderhub.downloaders.camoufox_fetcher import CamoufoxFetcher
from spiderhub.downloaders.patchright_fetcher import PatchrightFetcher
from spiderhub.downloaders.playwright_fetcher import FetchPageFn, PlaywrightFetcher


def build_l3_fetcher(
    settings: Settings,
    *,
    fetch_page: FetchPageFn | None = None,
) -> BrowserFetcher:
    """Build L3 browser fetcher. CDP always forces PlaywrightFetcher."""
    if cdp_mode_active(settings):
        return PlaywrightFetcher(settings, fetch_page=fetch_page)

    engine = settings.browser_engine.strip().lower() or "playwright"
    if engine not in BROWSER_ENGINES:
        allowed = ", ".join(sorted(BROWSER_ENGINES))
        raise ValueError(f"browser_engine must be one of: {allowed}; got {engine!r}")

    if engine == "camoufox":
        return CamoufoxFetcher(settings, fetch_page=fetch_page)
    if engine == "patchright":
        return PatchrightFetcher(settings, fetch_page=fetch_page)
    return PlaywrightFetcher(settings, fetch_page=fetch_page)
