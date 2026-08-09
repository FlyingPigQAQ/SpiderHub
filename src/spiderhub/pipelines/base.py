from __future__ import annotations

from typing import Protocol

from spiderhub.models.items import Actress, Work


class Pipeline(Protocol):
    async def open(self) -> None: ...
    async def process_item(self, item: Actress | Work) -> None: ...
    async def record_failed_url(
        self,
        *,
        url: str,
        spider_name: str,
        error_type: str,
        error_message: str,
    ) -> None: ...
    async def close(self) -> None: ...
