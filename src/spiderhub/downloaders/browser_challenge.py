from __future__ import annotations

from collections.abc import Collection

from spiderhub.challenges.detect import detect_challenge, is_challenge_title


def challenge_wait_cleared(
    *,
    title: str,
    cookie_names: Collection[str],
    body_html: str,
) -> bool:
    """Return True when the interstitial looks resolved enough to scrape."""
    if is_challenge_title(title):
        return False
    if "cf_clearance" in cookie_names:
        return True
    probe = f"{title}\n{body_html}"
    return detect_challenge(url="", status_code=200, text=probe) is None


def is_transient_page_error(exc: BaseException) -> bool:
    msg = str(exc).lower()
    return any(
        needle in msg
        for needle in (
            "navigating and changing the content",
            "execution context was destroyed",
            "most likely because of a navigation",
            "frame was detached",
            "cannot find context with specified id",
        )
    )
