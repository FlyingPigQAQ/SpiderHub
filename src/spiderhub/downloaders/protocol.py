from __future__ import annotations

from types import TracebackType
from typing import Protocol

from spiderhub.downloaders.base import FetchedResponse


class Fetcher(Protocol):
    async def __aenter__(self) -> Fetcher: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...

    async def fetch(self, url: str) -> FetchedResponse: ...
