from __future__ import annotations

import logging
import time
from collections import defaultdict
from collections.abc import Awaitable, Callable
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
Handler = Callable[[Any], Awaitable[None]]

_bus: EventBus | None = None


class EventBus:
    def __init__(
        self,
        *,
        cooldown_seconds: float = 0.0,
        cooldown_types: frozenset[type[Any]] | None = None,
    ) -> None:
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._cooldown_types = cooldown_types
        self._handlers: dict[type[Any], list[Handler]] = defaultdict(list)
        self._last_publish_at: dict[type[Any], float] = {}

    def subscribe(
        self, event_type: type[T], handler: Callable[[T], Awaitable[None]]
    ) -> None:
        self._handlers[event_type].append(handler)

    def unsubscribe(
        self, event_type: type[T], handler: Callable[[T], Awaitable[None]]
    ) -> None:
        handlers = self._handlers.get(event_type)
        if not handlers:
            return
        try:
            handlers.remove(handler)
        except ValueError:
            return

    def clear(self) -> None:
        self._handlers.clear()
        self._last_publish_at.clear()

    async def publish(self, event: object) -> None:
        event_type = type(event)
        now = time.monotonic()
        apply_cooldown = self._cooldown_seconds > 0 and (
            self._cooldown_types is None or event_type in self._cooldown_types
        )
        if apply_cooldown:
            last = self._last_publish_at.get(event_type)
            if last is not None and (now - last) < self._cooldown_seconds:
                logger.debug(
                    "event cooldown skip type=%s remaining=%.1fs",
                    event_type.__name__,
                    self._cooldown_seconds - (now - last),
                )
                return
        handlers = list(self._handlers.get(event_type, ()))
        if not handlers:
            return
        if apply_cooldown:
            self._last_publish_at[event_type] = now
        for handler in handlers:
            try:
                await handler(event)
            except Exception:  # noqa: BLE001
                logger.exception(
                    "event handler failed type=%s handler=%s",
                    event_type.__name__,
                    getattr(handler, "__name__", repr(handler)),
                )


def get_bus() -> EventBus:
    global _bus
    if _bus is None:
        _bus = EventBus(cooldown_seconds=0.0)
    return _bus


def set_bus(bus: EventBus | None) -> None:
    global _bus
    _bus = bus


async def publish(event: object) -> None:
    await get_bus().publish(event)
