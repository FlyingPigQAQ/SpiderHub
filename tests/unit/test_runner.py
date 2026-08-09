from __future__ import annotations

import httpx
import pytest

from spiderhub.core.runner import run_spider
from spiderhub.core.settings import Settings
from spiderhub.core.spider import Spider
from spiderhub.downloaders.base import FetchedResponse
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.models.items import Work
from spiderhub.pipelines.null import NullPipeline


class _ListSpider(Spider):
    name = "list_demo"
    allowed_domains = ("example.com",)
    obey_robots = False

    def start_urls(self) -> list[str]:
        return ["https://example.com/list"]

    async def parse(self, response: FetchedResponse):
        if response.url.endswith("/list"):
            yield "https://example.com/a"
            yield Work(
                code="A-1",
                title="One",
                detail_url="https://example.com/a",
            )
        else:
            yield Work(
                code="A-1",
                title="One detailed",
                detail_url=response.url,
                description="bio",
            )


@pytest.mark.asyncio
async def test_runner_follows_url_and_items() -> None:
    pages = {
        "https://example.com/list": "<html>list</html>",
        "https://example.com/a": "<html>detail</html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages[str(request.url)]
        return httpx.Response(200, text=body, request=request)

    settings = Settings(
        obey_robots=False,
        request_delay_seconds=0.0,
        http_max_retries=1,
    )
    transport = httpx.MockTransport(handler)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        result = await run_spider(
            _ListSpider(),
            fetcher=fetcher,
            pipeline=NullPipeline(),
            settings=settings,
        )
    assert result.items_ok >= 1
    assert result.urls_failed == 0


class _ParseFailSpider(Spider):
    name = "parse_fail_demo"
    allowed_domains = ("example.com",)
    obey_robots = False

    def start_urls(self) -> list[str]:
        return [
            "https://example.com/bad",
            "https://example.com/ok",
        ]

    async def parse(self, response: FetchedResponse):
        if response.url.endswith("/bad"):
            raise ValueError("parse boom")
        yield Work(
            code="OK-1",
            title="Recovered",
            detail_url=response.url,
        )


class _FlakyClosedFetcher:
    def __init__(self) -> None:
        self.calls = 0

    async def fetch(self, url: str) -> FetchedResponse:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError(
                "Page.goto: Target page, context or browser has been closed"
            )
        return FetchedResponse(url=url, status_code=200, text="<html>ok</html>")


class _ClosedOnceSpider(Spider):
    name = "closed_once_demo"
    allowed_domains = ("example.com",)
    obey_robots = False

    def start_urls(self) -> list[str]:
        return ["https://example.com/list"]

    async def parse(self, response: FetchedResponse):
        yield Work(
            code="OK-1",
            title="Recovered",
            detail_url=response.url,
        )


@pytest.mark.asyncio
async def test_runner_requeues_once_after_browser_closed() -> None:
    fetcher = _FlakyClosedFetcher()
    result = await run_spider(
        _ClosedOnceSpider(),
        fetcher=fetcher,  # type: ignore[arg-type]
        pipeline=NullPipeline(),
        settings=Settings(obey_robots=False, request_delay_seconds=0.0),
    )
    assert fetcher.calls == 2
    assert result.items_ok == 1
    assert result.urls_failed == 0


@pytest.mark.asyncio
async def test_runner_parse_failure_isolated() -> None:
    pages = {
        "https://example.com/bad": "<html>bad</html>",
        "https://example.com/ok": "<html>ok</html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages[str(request.url)]
        return httpx.Response(200, text=body, request=request)

    settings = Settings(
        obey_robots=False,
        request_delay_seconds=0.0,
        http_max_retries=1,
    )
    transport = httpx.MockTransport(handler)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        result = await run_spider(
            _ParseFailSpider(),
            fetcher=fetcher,
            pipeline=NullPipeline(),
            settings=settings,
        )
    assert result.urls_failed == 1
    assert result.items_ok == 1
