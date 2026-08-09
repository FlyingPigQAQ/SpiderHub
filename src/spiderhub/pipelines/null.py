from __future__ import annotations

import logging

from spiderhub.models.items import Actress, Work

logger = logging.getLogger(__name__)


class NullPipeline:
    async def open(self) -> None:
        return None

    async def process_item(self, item: Actress | Work) -> None:
        logger.info("dry-run item type=%s", type(item).__name__)

    async def record_failed_url(
        self,
        *,
        url: str,
        spider_name: str,
        error_type: str,
        error_message: str,
    ) -> None:
        return None

    async def close(self) -> None:
        return None
