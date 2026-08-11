from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.auto_fetcher import AutoFetcher
from spiderhub.downloaders.base import FetchedResponse
from spiderhub.downloaders.curl_cffi_fetcher import CurlCffiFetcher
from spiderhub.downloaders.external_solver_fetcher import ExternalSolverFetcher
from spiderhub.downloaders.playwright_fetcher import PlaywrightFetcher


@pytest.mark.asyncio
async def test_auto_fetcher_upgrades_l1_to_l2() -> None:
    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            url=url,
            status_code=200,
            text="<html>upgraded-l2</html>",
            headers={},
        )

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=False,
    )
    l2 = CurlCffiFetcher(settings, get=l2_get)
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=l2,
    ) as fetcher:
        first = await fetcher.fetch("https://missav.ws/cn/a")
        second = await fetcher.fetch("https://missav.ws/cn/b")
    assert "upgraded-l2" in first.text
    assert "upgraded-l2" in second.text


@pytest.mark.asyncio
async def test_auto_fetcher_upgrades_l2_to_l3() -> None:
    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            url=url,
            status_code=403,
            text="<title>Just a moment...</title>challenge-platform",
            headers={},
        )

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        return url, 200, "<html>upgraded-l3</html>", {}

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
    )
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=PlaywrightFetcher(settings, fetch_page=l3_page),
    ) as fetcher:
        resp = await fetcher.fetch("https://missav.ws/cn/a")
    assert isinstance(resp, FetchedResponse)
    assert "upgraded-l3" in resp.text


@pytest.mark.asyncio
async def test_auto_fetcher_stays_on_l3_after_challenge() -> None:
    """Without CDP, after browser solve later pages stay on L3 (not L2 bounce)."""
    calls = {"l2": 0, "l3": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        calls["l2"] += 1
        return SimpleNamespace(
            url=url,
            status_code=403,
            text="<title>Just a moment...</title>challenge-platform",
            headers={},
        )

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        calls["l3"] += 1
        return url, 200, f"<html>upgraded-l3 {url}</html>", {}

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
    )
    l3 = PlaywrightFetcher(settings, fetch_page=l3_page)

    async def fake_export_cookies() -> list[dict[str, str]]:
        return [
            {
                "name": "cf_clearance",
                "value": "token",
                "domain": ".missav.ws",
                "path": "/",
            }
        ]

    async def noop_prefer_headless() -> None:
        return None

    l3.export_cookies = fake_export_cookies  # type: ignore[method-assign]
    l3.prefer_headless_for_content = noop_prefer_headless  # type: ignore[method-assign]

    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=l3,
    ) as fetcher:
        first = await fetcher.fetch("https://missav.ws/cn/a")
        second = await fetcher.fetch("https://missav.ws/cn/b")

    assert "upgraded-l3" in first.text
    assert "upgraded-l3" in second.text
    assert calls["l3"] == 2
    assert calls["l2"] == 1


@pytest.mark.asyncio
async def test_auto_fetcher_cdp_prefers_l2_after_browser() -> None:
    """CDP: after L3 solve + cookies, later pages prefer L2 (Chrome stays idle)."""
    calls = {"l2": 0, "l3": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        calls["l2"] += 1
        if calls["l2"] == 1:
            return SimpleNamespace(
                url=url,
                status_code=403,
                text="<title>Just a moment...</title>challenge-platform",
                headers={},
            )
        return SimpleNamespace(
            url=url,
            status_code=200,
            text=f"<html>l2-with-cookies {url}</html>",
            headers={},
        )

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        calls["l3"] += 1
        return url, 200, f"<html>upgraded-l3 {url}</html>", {}

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        browser_cdp_url="http://127.0.0.1:9222",
    )
    l3 = PlaywrightFetcher(settings, fetch_page=l3_page)

    async def fake_export_cookies() -> list[dict[str, str]]:
        return [
            {
                "name": "cf_clearance",
                "value": "token",
                "domain": ".missav.ws",
                "path": "/",
            }
        ]

    async def noop_prefer_headless() -> None:
        raise AssertionError("CDP HTTP content path should not switch headless")

    l3.export_cookies = fake_export_cookies  # type: ignore[method-assign]
    l3.prefer_headless_for_content = noop_prefer_headless  # type: ignore[method-assign]

    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=l3,
    ) as fetcher:
        first = await fetcher.fetch("https://missav.ws/cn/a")
        second = await fetcher.fetch("https://missav.ws/cn/b")

    assert "upgraded-l3" in first.text
    assert "l2-with-cookies" in second.text
    assert calls["l3"] == 1
    assert calls["l2"] == 2


@pytest.mark.asyncio
async def test_auto_fetcher_cdp_enabled_prefers_l2_flag() -> None:
    settings = Settings(
        request_delay_seconds=0.0,
        browser_cdp_enabled=True,
        allow_fetcher_upgrade=True,
        allow_browser=True,
    )
    async with AutoFetcher(settings) as fetcher:
        assert fetcher._prefer_http_after_browser is True


@pytest.mark.asyncio
async def test_auto_fetcher_cdp_falls_back_sticky_l3_on_l2_challenge() -> None:
    calls = {"l2": 0, "l3": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        calls["l2"] += 1
        return SimpleNamespace(
            url=url,
            status_code=403,
            text="<title>Just a moment...</title>challenge-platform",
            headers={},
        )

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        calls["l3"] += 1
        return url, 200, f"<html>upgraded-l3 {url}</html>", {}

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        browser_cdp_url="http://127.0.0.1:9222",
    )
    l3 = PlaywrightFetcher(settings, fetch_page=l3_page)

    async def fake_export_cookies() -> list[dict[str, str]]:
        return [
            {
                "name": "cf_clearance",
                "value": "token",
                "domain": ".missav.ws",
                "path": "/",
            }
        ]

    l3.export_cookies = fake_export_cookies  # type: ignore[method-assign]

    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=l3,
    ) as fetcher:
        first = await fetcher.fetch("https://missav.ws/cn/a")
        second = await fetcher.fetch("https://missav.ws/cn/b")
        third = await fetcher.fetch("https://missav.ws/cn/c")

    assert "upgraded-l3" in first.text
    assert "upgraded-l3" in second.text
    assert "upgraded-l3" in third.text
    # a: L2 fail -> L3; b: L2 fail -> abandon sticky L3; c: sticky L3 only
    assert calls["l3"] == 3
    assert calls["l2"] == 2


@pytest.mark.asyncio
async def test_auto_fetcher_cdp_upgrades_l1_connect_error_to_l3() -> None:
    calls = {"l3": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=request)

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del url, kwargs
        raise AssertionError("CDP ConnectError path should skip L2")

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        calls["l3"] += 1
        return url, 200, "<html>cdp-after-connect-error</html>", {}

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        browser_cdp_url="http://127.0.0.1:9222",
    )
    l3 = PlaywrightFetcher(settings, fetch_page=l3_page)

    async def fake_export_cookies() -> list[dict[str, str]]:
        return []

    l3.export_cookies = fake_export_cookies  # type: ignore[method-assign]

    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=l3,
    ) as fetcher:
        resp = await fetcher.fetch("https://missav.ws/cn/a")

    assert "cdp-after-connect-error" in resp.text
    assert calls["l3"] == 1


@pytest.mark.asyncio
async def test_auto_fetcher_connect_error_without_cdp_reraises() -> None:
    def l1_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=request)

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        browser_cdp_url="",
    )
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
    ) as fetcher:
        with pytest.raises(httpx.ConnectError):
            await fetcher.fetch("https://missav.ws/cn/a")


@pytest.mark.asyncio
async def test_auto_fetcher_cdp_connect_error_robots_does_not_upgrade() -> None:
    calls = {"l3": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=request)

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        calls["l3"] += 1
        return url, 200, "<html>should-not</html>", {}

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        browser_cdp_url="http://127.0.0.1:9222",
    )
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l3=PlaywrightFetcher(settings, fetch_page=l3_page),
    ) as fetcher:
        with pytest.raises(httpx.ConnectError):
            await fetcher.fetch("https://missav.ws/robots.txt")
    assert calls["l3"] == 0


@pytest.mark.asyncio
async def test_auto_fetcher_cdp_l2_connect_error_abandons_to_sticky_l3() -> None:
    """After CDP session prefers L2, L2 ConnectError should sticky L3 (HTTP dead)."""
    calls = {"l2": 0, "l3": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("", request=request)

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        calls["l2"] += 1
        raise httpx.ConnectError("", request=httpx.Request("GET", url))

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        calls["l3"] += 1
        return url, 200, f"<html>sticky-l3 {url}</html>", {}

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        browser_cdp_url="http://127.0.0.1:9222",
    )
    l3 = PlaywrightFetcher(settings, fetch_page=l3_page)

    async def fake_export_cookies() -> list[dict[str, str]]:
        return [
            {
                "name": "cf_clearance",
                "value": "token",
                "domain": ".missav.ws",
                "path": "/",
            }
        ]

    l3.export_cookies = fake_export_cookies  # type: ignore[method-assign]

    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=l3,
    ) as fetcher:
        first = await fetcher.fetch("https://missav.ws/cn/a")
        second = await fetcher.fetch("https://missav.ws/cn/b")
        third = await fetcher.fetch("https://missav.ws/cn/c")

    assert "sticky-l3" in first.text
    assert "sticky-l3" in second.text
    assert "sticky-l3" in third.text
    # a: L1 ConnectError -> L3; b: prefer L2 ConnectError -> sticky L3; c: sticky L3
    assert calls["l3"] == 3
    assert calls["l2"] == 1


@pytest.mark.asyncio
async def test_auto_fetcher_robots_does_not_sticky_upgrade() -> None:
    calls = {"l1": 0, "l2": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        calls["l1"] += 1
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        calls["l2"] += 1
        return SimpleNamespace(
            url=url,
            status_code=403,
            text="<title>Just a moment...</title>challenge-platform",
            headers={},
        )

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=False,
    )
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
    ) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://missav.ws/robots.txt")
        assert calls["l2"] == 0  # robots must not sticky-upgrade
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://missav.ws/cn/a")
    assert calls["l1"] >= 2
    assert calls["l2"] == 1


@pytest.mark.asyncio
async def test_auto_fetcher_upgrade_disabled_reraises() -> None:
    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=False,
    )
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
    ) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://missav.ws/cn/a")


@pytest.mark.asyncio
async def test_auto_fetcher_l2_to_l4_skip_browser() -> None:
    calls = {"l3": 0, "l4": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            url=url,
            status_code=403,
            text="<title>Just a moment...</title>challenge-platform",
            headers={},
        )

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        calls["l3"] += 1
        return url, 200, "<html>should-not-l3</html>", {}

    def l4_handler(request: httpx.Request) -> httpx.Response:
        calls["l4"] += 1
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "solution": {
                    "url": "https://missav.ws/cn/a",
                    "status": 200,
                    "response": "<html>upgraded-l4</html>",
                    "cookies": [],
                },
            },
            request=request,
        )

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        allow_external_solver=True,
        external_solver_skip_browser=True,
        external_solver_url="http://solver.test/v1",
    )
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=PlaywrightFetcher(settings, fetch_page=l3_page),
        l4=ExternalSolverFetcher(settings, transport=httpx.MockTransport(l4_handler)),
    ) as fetcher:
        first = await fetcher.fetch("https://missav.ws/cn/a")
        second = await fetcher.fetch("https://missav.ws/cn/b")
    assert "upgraded-l4" in first.text
    assert "upgraded-l4" in second.text
    assert calls["l3"] == 0
    assert calls["l4"] == 2


@pytest.mark.asyncio
async def test_auto_fetcher_l3_to_l4_sticky() -> None:
    calls = {"l3": 0, "l4": 0}

    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            url=url,
            status_code=403,
            text="<title>Just a moment...</title>challenge-platform",
            headers={},
        )

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        calls["l3"] += 1
        return (
            url,
            403,
            "<title>Just a moment...</title>challenge-platform",
            {},
        )

    def l4_handler(request: httpx.Request) -> httpx.Response:
        calls["l4"] += 1
        return httpx.Response(
            200,
            json={
                "status": "ok",
                "solution": {
                    "url": str(request.url),
                    "status": 200,
                    "response": f"<html>l4-{calls['l4']}</html>",
                    "cookies": [],
                },
            },
            request=request,
        )

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        allow_external_solver=True,
        external_solver_skip_browser=False,
        external_solver_url="http://solver.test/v1",
    )
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=PlaywrightFetcher(settings, fetch_page=l3_page),
        l4=ExternalSolverFetcher(settings, transport=httpx.MockTransport(l4_handler)),
    ) as fetcher:
        first = await fetcher.fetch("https://missav.ws/cn/a")
        second = await fetcher.fetch("https://missav.ws/cn/b")
    assert "l4-1" in first.text
    assert "l4-2" in second.text
    assert calls["l3"] == 1
    assert calls["l4"] == 2


@pytest.mark.asyncio
async def test_auto_fetcher_l4_disabled_does_not_upgrade_from_l3() -> None:
    def l1_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    async def l2_get(url: str, **kwargs: object) -> SimpleNamespace:
        del kwargs
        return SimpleNamespace(
            url=url,
            status_code=403,
            text="<title>Just a moment...</title>challenge-platform",
            headers={},
        )

    async def l3_page(url: str) -> tuple[str, int, str, dict[str, str]]:
        return (
            url,
            403,
            "<title>Just a moment...</title>challenge-platform",
            {},
        )

    settings = Settings(
        request_delay_seconds=0.0,
        http_max_retries=1,
        allow_fetcher_upgrade=True,
        allow_browser=True,
        allow_external_solver=False,
    )
    async with AutoFetcher(
        settings,
        transport=httpx.MockTransport(l1_handler),
        l2=CurlCffiFetcher(settings, get=l2_get),
        l3=PlaywrightFetcher(settings, fetch_page=l3_page),
    ) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://missav.ws/cn/a")
