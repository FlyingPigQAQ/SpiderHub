from __future__ import annotations

from pathlib import Path

from spiderhub.challenges.detect import detect_challenge

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "challenges"


def test_detects_cloudflare_challenge_page() -> None:
    html = (FIXTURES / "cloudflare_challenge.html").read_text(encoding="utf-8")
    reason = detect_challenge(url="https://missav.ws/x", status_code=403, text=html)
    assert reason is not None
    assert "cloudflare" in reason.lower() or "challenge" in reason.lower()


def test_normal_page_is_clean() -> None:
    reason = detect_challenge(
        url="https://missav.ws/x",
        status_code=200,
        text="<html><title>北野未奈</title><body>ok</body></html>",
    )
    assert reason is None
