from __future__ import annotations

from collections.abc import Collection

from spiderhub.challenges.detect import (
    ChallengeDetectedError,
    detect_challenge,
    is_challenge_title,
)


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


def is_closed_target_error(exc: BaseException) -> bool:
    """True when Playwright reports page/context/browser closed or crashed.

    Renderer crashes leave a dead shared tab; treat like a closed target so
    fetchers can open a fresh page / reconnect and retry.
    """
    msg = str(exc).lower()
    return any(
        needle in msg
        for needle in (
            "target page, context or browser has been closed",
            "target closed",
            "browser has been closed",
            "context has been closed",
            "page has been closed",
            "browser/page closed",
            "page crashed",
        )
    )


def is_recoverable_fetch_error(exc: BaseException) -> bool:
    """True for closed/crash, navigation timeout, or transient page errors."""
    if isinstance(exc, ChallengeDetectedError):
        return False
    if is_closed_target_error(exc) or is_transient_page_error(exc):
        return True
    return "timeout" in str(exc).lower()
