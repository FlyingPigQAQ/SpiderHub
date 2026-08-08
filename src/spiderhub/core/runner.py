from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from spiderhub.core.spider import Spider
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.models.items import Actress, Work
from spiderhub.pipelines.base import Pipeline

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunResult:
    items_ok: int = 0
    items_failed: int = 0
    urls_failed: int = 0


def _allowed(url: str, domains: tuple[str, ...]) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == d or host.endswith(f".{d}") for d in domains)


def _is_robots_url(url: str) -> bool:
    path = urlparse(url).path.rstrip("/")
    return path.endswith("/robots.txt") or path == "/robots.txt"


async def _robots_allowed(fetcher: HttpxFetcher, url: str, enabled: bool) -> bool:
    if not enabled:
        return True
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await fetcher.fetch(robots_url)
    except Exception:  # noqa: BLE001
        logger.warning("robots.txt fetch failed; allowing url=%s", url)
        return True
    rp = RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch("SpiderHub", url)


async def run_spider(
    spider: Spider,
    *,
    fetcher: HttpxFetcher,
    pipeline: Pipeline,
    start_urls: list[str] | None = None,
) -> RunResult:
    result = RunResult()
    queue: deque[str] = deque(start_urls or spider.start_urls())
    seen: set[str] = set()
    await pipeline.open()
    try:
        while queue:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            if not _allowed(url, spider.allowed_domains):
                logger.warning("skip disallowed url=%s", url)
                continue
            if spider.obey_robots and not _is_robots_url(url):
                if not await _robots_allowed(fetcher, url, enabled=True):
                    logger.warning("robots disallowed url=%s", url)
                    continue
            try:
                response = await fetcher.fetch(url)
            except Exception as exc:  # noqa: BLE001 — counted failure boundary
                result.urls_failed += 1
                logger.warning("url failed url=%s err=%s", url, exc)
                continue
            async for item in spider.parse(response):
                if isinstance(item, str):
                    if item not in seen and _allowed(item, spider.allowed_domains):
                        queue.append(item)
                    continue
                if isinstance(item, (Actress, Work)):
                    try:
                        await pipeline.process_item(item)
                        result.items_ok += 1
                    except Exception as exc:  # noqa: BLE001
                        result.items_failed += 1
                        logger.warning("item failed err=%s", exc)
    finally:
        await pipeline.close()
    return result
