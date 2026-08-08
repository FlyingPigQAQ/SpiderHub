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

    settings = Settings(request_delay_seconds=0.0, http_max_retries=1)
    transport = httpx.MockTransport(handler)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        result = await run_spider(
            _ListSpider(), fetcher=fetcher, pipeline=NullPipeline()
        )
    assert result.items_ok >= 1
    assert result.urls_failed == 0
