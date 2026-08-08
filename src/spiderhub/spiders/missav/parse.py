from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import unquote, urljoin, urlparse

from selectolax.parser import HTMLParser

from spiderhub.models.items import Actress


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
