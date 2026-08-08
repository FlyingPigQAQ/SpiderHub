from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from spiderhub.core.settings import Settings
from spiderhub.core.spider import Spider
from spiderhub.downloaders.base import SPIDERHUB_USER_AGENT
from spiderhub.downloaders.protocol import Fetcher
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


async def _robots_allowed(
    fetcher: Fetcher,
    url: str,
    enabled: bool,
    cache: dict[str, RobotFileParser],
) -> bool:
    if not enabled:
        return True
    parsed = urlparse(url)
    host_key = f"{parsed.scheme}://{parsed.netloc}"
    rp = cache.get(host_key)
    if rp is None:
        robots_url = f"{host_key}/robots.txt"
        try:
            resp = await fetcher.fetch(robots_url)
        except Exception:  # noqa: BLE001
            logger.warning("robots.txt fetch failed; allowing host=%s", host_key)
            return True
        rp = RobotFileParser()
        rp.parse(resp.text.splitlines())
        cache[host_key] = rp
    return rp.can_fetch(SPIDERHUB_USER_AGENT, url)


async def run_spider(
    spider: Spider,
    *,
    fetcher: Fetcher,
    pipeline: Pipeline,
    start_urls: list[str] | None = None,
    settings: Settings | None = None,
) -> RunResult:
    result = RunResult()
    queue: deque[str] = deque(start_urls or spider.start_urls())
    seen: set[str] = set()
    obey_robots = (settings.obey_robots if settings else True) and spider.obey_robots
    robots_cache: dict[str, RobotFileParser] = {}
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
            if obey_robots and not _is_robots_url(url):
                if not await _robots_allowed(
                    fetcher, url, enabled=True, cache=robots_cache
                ):
                    logger.warning("robots disallowed url=%s", url)
                    continue
            try:
                response = await fetcher.fetch(url)
            except Exception as exc:  # noqa: BLE001 — counted failure boundary
                result.urls_failed += 1
                logger.warning("url failed url=%s err=%s", url, exc)
                continue
            if not _allowed(response.url, spider.allowed_domains):
                result.urls_failed += 1
                logger.warning(
                    "skip disallowed post-redirect url=%s final=%s",
                    url,
                    response.url,
                )
                continue
            try:
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
            except Exception as exc:  # noqa: BLE001 — parse failure boundary
                result.urls_failed += 1
                logger.warning("parse failed url=%s err=%s", url, exc)
    finally:
        await pipeline.close()
    return result
