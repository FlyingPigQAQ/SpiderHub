from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date
from urllib.parse import unquote, urljoin, urlparse

from selectolax.parser import HTMLParser, Node

from spiderhub.models.items import Actress, Work

logger = logging.getLogger(__name__)

_WORK_HREF_RE = re.compile(
    r"/(?:dm\d+/)?cn/([a-z0-9]+(?:-[a-z0-9]+)+)/?(?:$|\?)",
    re.IGNORECASE,
)
_ACTRESS_H1_SUFFIXES = (
    "出演的 AV 在线看",
    "出演的AV在线看",
    "出演的 AV 線上看",
    "出演的AV線上看",
)


@dataclass(slots=True)
class ActressListPage:
    actress: Actress
    detail_urls: list[str]
    next_page_url: str | None


def _slug_from_actress_url(page_url: str) -> str:
    path = urlparse(page_url).path.rstrip("/")
    return unquote(path.split("/")[-1])


def _normalize_actress_name(raw: str, *, slug: str) -> str:
    name = raw.strip()
    for suffix in _ACTRESS_H1_SUFFIXES:
        if name.endswith(suffix):
            name = name[: -len(suffix)].strip()
    if "出演" in name:
        name = name.split("出演", 1)[0].strip()
    return name or slug


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


def _meta_row(tree: HTMLParser, label: str) -> Node | None:
    want = label if label.endswith(":") else f"{label}:"
    for div in tree.css("div.space-y-2 div.text-secondary"):
        span = div.css_first("span")
        if span is not None and span.text(strip=True) == want:
            return div
    return None


def _meta_text(tree: HTMLParser, label: str) -> str | None:
    row = _meta_row(tree, label)
    if row is None:
        return None
    node = row.css_first(".font-medium") or row.css_first("time")
    if node is None:
        return None
    value = node.text(strip=True)
    return value or None


def _meta_links(tree: HTMLParser, label: str, href_substr: str) -> list[Node]:
    row = _meta_row(tree, label)
    if row is None:
        return []
    return [
        a
        for a in row.css("a")
        if href_substr in (a.attributes.get("href") or "") and a.text(strip=True)
    ]


def _is_work_detail_href(href: str) -> bool:
    path = urlparse(urljoin("https://missav.ws/", href)).path
    return _WORK_HREF_RE.search(path) is not None


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
    if text.isdigit():
        return int(text)
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


def _code_from_url(page_url: str) -> str | None:
    path = urlparse(page_url).path.rstrip("/")
    m = _WORK_HREF_RE.search(path + "/")
    if not m:
        return None
    return m.group(1).upper()


def parse_actress_list(html: str, page_url: str) -> ActressListPage:
    tree = HTMLParser(html)
    slug = _slug_from_actress_url(page_url)

    name_node = tree.css_first("[data-testid=actress-name]") or tree.css_first("h1")
    raw_name = name_node.text(strip=True) if name_node else ""
    name = _normalize_actress_name(raw_name, slug=slug)

    cover_node = tree.css_first("[data-testid=actress-cover]") or tree.css_first(
        'img[src*="/actress/"]'
    )
    cover = cover_node.attributes.get("src") if cover_node else None
    if cover_node is not None:
        alt = (cover_node.attributes.get("alt") or "").strip()
        if alt and ("出演" in raw_name or not raw_name):
            name = _normalize_actress_name(alt, slug=slug)

    bio_node = tree.css_first("[data-testid=actress-bio]")
    bio = bio_node.text(strip=True) if bio_node else None
    actress = Actress(
        slug=slug,
        name=name,
        profile_url=page_url.split("?")[0],
        cover_url=cover,
        bio=bio,
    )

    detail_urls: list[str] = []
    seen: set[str] = set()
    testid_anchors = tree.css("[data-testid=work-link]")
    if testid_anchors:
        anchors = testid_anchors
        require_work_shape = False
    else:
        anchors = tree.css("div.thumbnail a[href]")
        require_work_shape = True
    for anchor in anchors:
        href = anchor.attributes.get("href")
        if not href:
            continue
        if require_work_shape and not _is_work_detail_href(href):
            continue
        absolute = urljoin(page_url, href).split("#")[0]
        if absolute in seen:
            continue
        seen.add(absolute)
        detail_urls.append(absolute)

    next_page_url = None
    next_node = tree.css_first("[data-testid=next-page]") or tree.css_first(
        "a[rel=next]"
    )
    if next_node is None:
        for anchor in tree.css("a[href]"):
            if anchor.text(strip=True) in {"下一页", "下一頁", "Next", "next"}:
                next_node = anchor
                break
    if next_node and (next_href := next_node.attributes.get("href")):
        next_page_url = urljoin(page_url, next_href)

    return ActressListPage(
        actress=actress, detail_urls=detail_urls, next_page_url=next_page_url
    )


def parse_work_detail(html: str, page_url: str) -> Work | None:
    tree = HTMLParser(html)

    code = _text(tree, "[data-testid=work-code]") or _meta_text(tree, "番号") or ""
    if not code:
        code = _code_from_url(page_url) or ""
    if not code:
        logger.warning("drop work missing code url=%s", page_url)
        return None

    title_node = tree.css_first("h1")
    title = (
        _text(tree, "[data-testid=work-title]")
        or _meta_text(tree, "标题")
        or _meta_text(tree, "標題")
        or (title_node.text(strip=True) if title_node else code)
    )

    duration_raw = _text(tree, "[data-testid=duration]")
    duration_seconds = _parse_duration_seconds(duration_raw) if duration_raw else None
    if duration_seconds is None:
        og_duration = _attr(tree, 'meta[property="og:video:duration"]', "content")
        if og_duration and og_duration.isdigit():
            duration_seconds = int(og_duration)

    release_raw = _attr(tree, "[data-testid=release-date]", "datetime") or _text(
        tree, "[data-testid=release-date]"
    )
    if not release_raw:
        row = _meta_row(tree, "发行日期") or _meta_row(tree, "發行日期")
        if row is not None:
            time_node = row.css_first("time")
            release_raw = (
                time_node.attributes.get("datetime") if time_node is not None else None
            ) or (time_node.text(strip=True) if time_node is not None else None)
        if not release_raw:
            release_raw = _attr(
                tree, 'meta[property="og:video:release_date"]', "content"
            )

    actress_names: list[str] = []
    actress_slugs: list[str] = []
    actress_nodes = tree.css("[data-testid=actress]")
    if not actress_nodes:
        actress_nodes = [
            a
            for a in _meta_links(tree, "女优", "/actresses/")
            + _meta_links(tree, "女優", "/actresses/")
            if "ranking" not in (a.attributes.get("href") or "")
        ]
    for node in actress_nodes:
        name = node.text(strip=True)
        if name:
            actress_names.append(name)
        href = node.attributes.get("href")
        if href:
            slug = unquote(urlparse(href).path.rstrip("/").split("/")[-1])
            if slug and slug != "ranking":
                actress_slugs.append(slug)

    tag_nodes = tree.css("[data-testid=tag]")
    if tag_nodes:
        tags = [n.text(strip=True) for n in tag_nodes if n.text(strip=True)]
    else:
        tags = [
            n.text(strip=True)
            for n in _meta_links(tree, "类型", "/genres/")
            + _meta_links(tree, "類型", "/genres/")
            if n.text(strip=True)
        ]

    maker = _text(tree, "[data-testid=maker]")
    if not maker:
        makers = _meta_links(tree, "发行商", "/makers/") or _meta_links(
            tree, "發行商", "/makers/"
        )
        maker = makers[0].text(strip=True) if makers else None

    label = _text(tree, "[data-testid=label]")
    if not label:
        labels = _meta_links(tree, "标籤", "/labels/") or _meta_links(
            tree, "標籤", "/labels/"
        )
        label = labels[0].text(strip=True) if labels else None

    series = _text(tree, "[data-testid=series]")
    if not series:
        series_links = _meta_links(tree, "系列", "/series/")
        series = series_links[0].text(strip=True) if series_links else None

    cover_url = _attr(tree, "[data-testid=cover]", "src") or _attr(
        tree, 'meta[property="og:image"]', "content"
    )
    description = _text(tree, "[data-testid=description]") or _attr(
        tree, 'meta[property="og:description"]', "content"
    )
    if description is not None and not description.strip():
        description = None

    return Work(
        code=code,
        title=title,
        detail_url=page_url,
        description=description,
        release_date=_parse_release_date(release_raw),
        duration_seconds=duration_seconds,
        maker=maker,
        label=label,
        series=series,
        cover_url=cover_url,
        actress_slugs=actress_slugs,
        actress_names=actress_names,
        tags=tags,
    )
