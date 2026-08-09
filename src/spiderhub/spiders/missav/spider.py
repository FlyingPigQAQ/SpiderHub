from __future__ import annotations

from collections.abc import AsyncIterator

from spiderhub.core.registry import register_spider
from spiderhub.core.spider import ParseItem, Spider
from spiderhub.downloaders.base import FetchedResponse
from spiderhub.spiders.missav.parse import parse_actress_list, parse_work_detail

DEFAULT_START = "https://missav.ws/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88"


@register_spider
class MissavActressSpider(Spider):
    name = "missav_actress"
    allowed_domains = ("missav.ws",)
    fetch_mode = "auto"

    def __init__(
        self,
        *,
        start_url: str | None = None,
        max_pages: int | None = None,
    ) -> None:
        self._start_url = start_url or DEFAULT_START
        self._max_pages = max_pages
        self._list_pages_seen = 0

    def start_urls(self) -> list[str]:
        return [self._start_url]

    async def parse(self, response: FetchedResponse) -> AsyncIterator[ParseItem]:
        if "/actresses/" in response.url:
            page = parse_actress_list(response.text, response.url)
            self._list_pages_seen += 1
            yield page.actress
            for detail in page.detail_urls:
                yield detail
            if page.next_page_url and (
                self._max_pages is None or self._list_pages_seen < self._max_pages
            ):
                yield page.next_page_url
            return
        work = parse_work_detail(response.text, response.url)
        if work is not None:
            yield work
