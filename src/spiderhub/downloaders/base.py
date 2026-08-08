from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FetchedResponse:
    url: str
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
