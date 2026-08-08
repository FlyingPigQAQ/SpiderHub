from __future__ import annotations

from pathlib import Path

from spiderhub.spiders.missav.parse import parse_work_detail

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "missav"


def test_parse_detail_fields() -> None:
    html = (FIX / "work_detail.html").read_text(encoding="utf-8")
    work = parse_work_detail(html, "https://missav.ws/cn/abc-001")
    assert work is not None
    assert work.code == "ABC-001"
    assert work.title.startswith("ABC-001")
    assert work.description == "作品简介"
    assert work.release_date is not None
    assert work.duration_seconds == 120 * 60
    assert work.maker == "DemoMaker"
    assert "北野未奈" in work.actress_names
    assert "solo" in work.tags


def test_missing_code_returns_none() -> None:
    html = (FIX / "work_detail_missing_code.html").read_text(encoding="utf-8")
    assert parse_work_detail(html, "https://missav.ws/cn/x") is None
