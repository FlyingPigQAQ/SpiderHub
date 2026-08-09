from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ChallengeNeedsHuman:
    url: str
    engine: str
    wait_seconds: float
    at: datetime


@dataclass(frozen=True, slots=True)
class SpiderRunFinished:
    spider_name: str
    status: str  # success | partial | failed
    items_ok: int
    items_failed: int
    urls_failed: int
    error: str | None
    dry_run: bool
    at: datetime
