from __future__ import annotations

from types import TracebackType
from typing import Any, Protocol

from spiderhub.downloaders.base import FetchedResponse


class BrowserFetcher(Protocol):
    """L3 browser fetcher: fetch + cookie export + optional content-mode switch."""

    async def __aenter__(self) -> BrowserFetcher: ...

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None: ...

    async def fetch(self, url: str) -> FetchedResponse: ...

    async def export_cookies(self) -> list[dict[str, Any]]: ...

    async def prefer_headless_for_content(self) -> None: ...
