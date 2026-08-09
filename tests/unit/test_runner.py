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


class _RecordingPipeline(NullPipeline):
    def __init__(self) -> None:
        self.failed: list[dict[str, str]] = []

    async def record_failed_url(
        self,
        *,
        url: str,
        spider_name: str,
        error_type: str,
        error_message: str,
    ) -> None:
        self.failed.append(
            {
                "url": url,
                "spider_name": spider_name,
                "error_type": error_type,
                "error_message": error_message,
            }
        )


class _AlwaysFailFetcher:
    async def fetch(self, url: str) -> FetchedResponse:
        raise RuntimeError("Page.goto: Timeout 30000ms exceeded.")


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
async def test_runner_records_failed_url_on_fetch_error() -> None:
    pipeline = _RecordingPipeline()
    result = await run_spider(
        _ClosedOnceSpider(),
        fetcher=_AlwaysFailFetcher(),  # type: ignore[arg-type]
        pipeline=pipeline,
        settings=Settings(obey_robots=False, request_delay_seconds=0.0),
    )
    assert result.urls_failed == 1
    assert result.items_ok == 0
    assert len(pipeline.failed) == 1
    assert pipeline.failed[0]["error_type"] == "fetch"
    assert pipeline.failed[0]["url"] == "https://example.com/list"


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


@pytest.mark.asyncio
async def test_runner_records_failed_url_on_parse_error() -> None:
    pages = {
        "https://example.com/bad": "<html>bad</html>",
        "https://example.com/ok": "<html>ok</html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[str(request.url)], request=request)

    settings = Settings(
        obey_robots=False,
        request_delay_seconds=0.0,
        http_max_retries=1,
    )
    pipeline = _RecordingPipeline()
    transport = httpx.MockTransport(handler)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        result = await run_spider(
            _ParseFailSpider(),
            fetcher=fetcher,
            pipeline=pipeline,
            settings=settings,
        )
    assert result.items_ok == 1
    assert result.urls_failed == 1
    assert len(pipeline.failed) == 1
    assert pipeline.failed[0]["error_type"] == "parse"
    assert pipeline.failed[0]["url"] == "https://example.com/bad"


@pytest.mark.asyncio
async def test_runner_does_not_record_on_success() -> None:
    pages = {
        "https://example.com/list": "<html>list</html>",
        "https://example.com/a": "<html>detail</html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[str(request.url)], request=request)

    settings = Settings(
        obey_robots=False,
        request_delay_seconds=0.0,
        http_max_retries=1,
    )
    pipeline = _RecordingPipeline()
    transport = httpx.MockTransport(handler)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        result = await run_spider(
            _ListSpider(),
            fetcher=fetcher,
            pipeline=pipeline,
            settings=settings,
        )
    assert result.urls_failed == 0
    assert pipeline.failed == []
