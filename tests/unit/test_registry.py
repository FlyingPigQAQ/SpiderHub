from __future__ import annotations

import pytest

from spiderhub.core.registry import get_spider, list_spiders, register_spider
from spiderhub.core.spider import Spider
from spiderhub.downloaders.base import FetchedResponse


class _DemoSpider(Spider):
    name = "demo"
    allowed_domains = ("example.com",)

    def start_urls(self) -> list[str]:
        return ["https://example.com/"]

    async def parse(self, response: FetchedResponse):
        if False:
            yield response.url


def test_register_and_list() -> None:
    register_spider(_DemoSpider)
    assert "demo" in list_spiders()
    assert get_spider("demo") is _DemoSpider


def test_unknown_spider() -> None:
    with pytest.raises(KeyError):
        get_spider("missing-spider-xyz")
