from __future__ import annotations

from collections.abc import Mapping

# Weak markers: fine to trust when the response is already non-2xx (403/503/...),
# since a normal 200 page can legitimately reference these strings (e.g. an
# inert cdn-cgi/challenge-platform script tag) without actually being a
# challenge page.
_WEAK_MARKERS = (
    "just a moment...",
    "cf-browser-verification",
    "challenge-platform",
    "cdn-cgi/challenge",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
    "请稍候",
    "请启用 javascript",
    "正在验证您是否是真人",
    "检查您的浏览器",
)

# Strong markers: specific enough to trust even on HTTP 200 (some challenge
# flows return 200 while rendering the interstitial page).
_STRONG_MARKERS_200 = (
    "just a moment...",
    "#challenge-running",
    "cf-browser-verification",
    "请稍候",
    "正在验证您是否是真人",
    "检查您的浏览器",
)


class ChallengeDetectedError(Exception):
    def __init__(self, url: str, status_code: int, reason: str) -> None:
        self.url = url
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"challenge detected for {url}: {reason} ({status_code})")


def detect_challenge(
    *,
    url: str,
    status_code: int,
    text: str,
    headers: Mapping[str, str] | None = None,
) -> str | None:
    del url, headers  # reserved for richer heuristics later
    lowered = text.lower()
    if not (200 <= status_code < 300):
        if any(m in lowered for m in _WEAK_MARKERS):
            return "cloudflare_or_bot_challenge"
        return None
    if any(m in lowered for m in _STRONG_MARKERS_200):
        return "cloudflare_or_bot_challenge"
    return None
