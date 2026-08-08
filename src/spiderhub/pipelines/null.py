from __future__ import annotations

import logging

from spiderhub.models.items import Actress, Work

logger = logging.getLogger(__name__)


class NullPipeline:
    async def open(self) -> None:
        return None

    async def process_item(self, item: Actress | Work) -> None:
        logger.info("dry-run item type=%s", type(item).__name__)

    async def close(self) -> None:
        return None
