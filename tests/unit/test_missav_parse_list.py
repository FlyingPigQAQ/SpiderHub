from __future__ import annotations

from pathlib import Path

from spiderhub.spiders.missav.parse import parse_actress_list

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "missav"
BASE = "https://missav.ws/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88"


def test_parse_list_page_extracts_actress_works_and_next() -> None:
    html = (FIX / "actress_list_page1.html").read_text(encoding="utf-8")
    page = parse_actress_list(html, BASE)
    assert page.actress.name == "北野未奈"
    assert page.actress.slug == "北野未奈" or "北野未奈" in page.actress.profile_url
    assert len(page.detail_urls) == 2
    assert page.detail_urls[0].endswith("/cn/abc-001")
    assert page.next_page_url == f"{BASE}?page=2"


def test_parse_empty_list() -> None:
    html = (FIX / "actress_list_empty.html").read_text(encoding="utf-8")
    page = parse_actress_list(html, BASE)
    assert page.detail_urls == []
    assert page.next_page_url is None


def test_parse_real_dom_list_extracts_thumbnail_works() -> None:
    html = (FIX / "actress_list_real_dom.html").read_text(encoding="utf-8")
    page = parse_actress_list(html, BASE)
    assert page.actress.name == "北野未奈"
    assert page.actress.cover_url and "actress/" in page.actress.cover_url
    assert page.detail_urls == [
        "https://missav.ws/cn/dsod-013",
        "https://missav.ws/cn/ngod-352",
    ]
    assert page.next_page_url is not None
    assert page.next_page_url.endswith("?page=2")


def test_parse_numbered_pagination_without_next_link() -> None:
    html = (FIX / "actress_list_numbered_pages.html").read_text(encoding="utf-8")
    page = parse_actress_list(html, f"{BASE}?page=20")
    assert page.next_page_url == f"{BASE}?page=21"
