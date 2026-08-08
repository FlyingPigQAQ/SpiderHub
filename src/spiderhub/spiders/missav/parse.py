from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote, urljoin, urlparse

from selectolax.parser import HTMLParser

from spiderhub.models.items import Actress, Work

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class ActressListPage:
    actress: Actress
    detail_urls: list[str]
    next_page_url: str | None


def _slug_from_actress_url(page_url: str) -> str:
    path = urlparse(page_url).path.rstrip("/")
    return unquote(path.split("/")[-1])


def parse_actress_list(html: str, page_url: str) -> ActressListPage:
    tree = HTMLParser(html)
    name_node = tree.css_first("[data-testid=actress-name]") or tree.css_first("h1")
    name = (name_node.text(strip=True) if name_node else "") or _slug_from_actress_url(
        page_url
    )
    cover_node = tree.css_first("[data-testid=actress-cover]")
    cover = cover_node.attributes.get("src") if cover_node else None
    bio_node = tree.css_first("[data-testid=actress-bio]")
    bio = bio_node.text(strip=True) if bio_node else None
    actress = Actress(
        slug=_slug_from_actress_url(page_url),
        name=name,
        profile_url=page_url.split("?")[0],
        cover_url=cover,
        bio=bio,
    )
    detail_urls: list[str] = []
    for anchor in tree.css("[data-testid=work-link]"):
        href = anchor.attributes.get("href")
        if href:
            detail_urls.append(urljoin(page_url, href))
    next_node = tree.css_first("[data-testid=next-page]")
    next_page_url = None
    if next_node and (next_href := next_node.attributes.get("href")):
        next_page_url = urljoin(page_url, next_href)
    return ActressListPage(
        actress=actress, detail_urls=detail_urls, next_page_url=next_page_url
    )


def _text(tree: HTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    value = node.text(strip=True)
    return value or None


def _attr(tree: HTMLParser, selector: str, name: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    return node.attributes.get(name)


def _parse_duration_seconds(text: str) -> int | None:
    text = text.strip()
    m = re.search(r"(\d+)\s*分", text)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+):(\d+):(\d+)", text)
    if m:
        h, mi, s = map(int, m.groups())
        return h * 3600 + mi * 60 + s
    m = re.search(r"(\d+):(\d+)", text)
    if m:
        mi, s = map(int, m.groups())
        return mi * 60 + s
    return None


def _parse_release_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        logger.warning("invalid release_date=%s", raw)
        return None


def parse_work_detail(html: str, page_url: str) -> Work | None:
    tree = HTMLParser(html)
    code = _text(tree, "[data-testid=work-code]") or ""
    if not code:
        logger.warning("drop work missing code url=%s", page_url)
        return None
    title_node = tree.css_first("h1")
    title = _text(tree, "[data-testid=work-title]") or (
        title_node.text(strip=True) if title_node else code
    )
    duration_raw = _text(tree, "[data-testid=duration]")
    duration_seconds = _parse_duration_seconds(duration_raw) if duration_raw else None
    release_raw = _attr(tree, "[data-testid=release-date]", "datetime") or _text(
        tree, "[data-testid=release-date]"
    )
    actress_names: list[str] = []
    actress_slugs: list[str] = []
    for node in tree.css("[data-testid=actress]"):
        name = node.text(strip=True)
        if name:
            actress_names.append(name)
        href = node.attributes.get("href")
        if href:
            slug = unquote(urlparse(href).path.rstrip("/").split("/")[-1])
            if slug:
                actress_slugs.append(slug)
    tags = [
        n.text(strip=True)
        for n in tree.css("[data-testid=tag]")
        if n.text(strip=True)
    ]
    return Work(
        code=code,
        title=title,
        detail_url=page_url,
        description=_text(tree, "[data-testid=description]"),
        release_date=_parse_release_date(release_raw),
        duration_seconds=duration_seconds,
        maker=_text(tree, "[data-testid=maker]"),
        label=_text(tree, "[data-testid=label]"),
        series=_text(tree, "[data-testid=series]"),
        cover_url=_attr(tree, "[data-testid=cover]", "src"),
        actress_slugs=actress_slugs,
        actress_names=actress_names,
        tags=tags,
    )
