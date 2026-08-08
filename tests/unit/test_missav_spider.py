from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from spiderhub.core.registry import discover_builtin_spiders, get_spider
from spiderhub.core.runner import run_spider
from spiderhub.core.settings import Settings
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.pipelines.null import NullPipeline

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "missav"
DEFAULT = "https://missav.ws/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88"


@pytest.mark.asyncio
async def test_spider_registered_and_dry_run_crawl() -> None:
    discover_builtin_spiders()
    spider_cls = get_spider("missav_actress")
    list_html = (FIX / "actress_list_page2.html").read_text(encoding="utf-8")
    detail_html = (FIX / "work_detail.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/actresses/" in url:
            return httpx.Response(200, text=list_html, request=request)
        if url.endswith("/robots.txt"):
            return httpx.Response(
                200, text="User-agent: *\nAllow: /\n", request=request
            )
        return httpx.Response(200, text=detail_html, request=request)

    settings = Settings(
        request_delay_seconds=0.0, http_max_retries=1, obey_robots=False
    )
    spider = spider_cls(start_url=DEFAULT)
    async with HttpxFetcher(
        settings, transport=httpx.MockTransport(handler)
    ) as fetcher:
        result = await run_spider(
            spider, fetcher=fetcher, pipeline=NullPipeline(), settings=settings
        )
    assert result.items_ok >= 1
    assert result.urls_failed == 0
