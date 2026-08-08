from __future__ import annotations

from typing import Protocol

from spiderhub.models.items import Actress, Work


class Pipeline(Protocol):
    async def open(self) -> None: ...
    async def process_item(self, item: Actress | Work) -> None: ...
    async def close(self) -> None: ...
