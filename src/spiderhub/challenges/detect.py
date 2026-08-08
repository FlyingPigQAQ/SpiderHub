from __future__ import annotations

from collections.abc import Mapping

_MARKERS = (
    "just a moment...",
    "cf-browser-verification",
    "challenge-platform",
    "cdn-cgi/challenge",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
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
    if status_code in {403, 503} and any(m in lowered for m in _MARKERS):
        return "cloudflare_or_bot_challenge"
    if any(m in lowered for m in _MARKERS):
        return "challenge_markers_in_body"
    return None
