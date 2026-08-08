from __future__ import annotations

from dataclasses import dataclass, field

SPIDERHUB_USER_AGENT = "SpiderHub/0.1 (+https://github.com/local/SpiderHub)"


@dataclass(slots=True)
class FetchedResponse:
    url: str
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
