from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from spiderhub.downloaders.base import FetchedResponse
from spiderhub.models.items import Actress, Work

ParseItem = Actress | Work | str


class Spider(ABC):
    name: str
    allowed_domains: tuple[str, ...]
    fetch_mode: str = "auto"
    obey_robots: bool = True

    def __init__(self, *, start_url: str | None = None) -> None:
        """Optional seed URL; subclasses read ``start_url`` if supported."""
        _ = start_url

    @abstractmethod
    def start_urls(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def parse(self, response: FetchedResponse) -> AsyncIterator[ParseItem]:
        raise NotImplementedError
        yield  # pragma: no cover
