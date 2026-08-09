from __future__ import annotations

from datetime import UTC, datetime

import pytest

from spiderhub.events.bus import EventBus, get_bus, set_bus
from spiderhub.events.types import ChallengeNeedsHuman


@pytest.fixture(autouse=True)
def _isolate_bus() -> None:
    set_bus(None)
    yield
    set_bus(None)


@pytest.mark.asyncio
async def test_publish_invokes_subscriber() -> None:
    bus = EventBus(cooldown_seconds=0.0)
    seen: list[ChallengeNeedsHuman] = []

    async def handler(event: ChallengeNeedsHuman) -> None:
        seen.append(event)

    bus.subscribe(ChallengeNeedsHuman, handler)
    event = ChallengeNeedsHuman(
        url="https://example.com/x",
        engine="playwright",
        wait_seconds=180.0,
        at=datetime.now(UTC),
    )
    await bus.publish(event)
    assert seen == [event]


@pytest.mark.asyncio
async def test_cooldown_skips_second_publish() -> None:
    bus = EventBus(cooldown_seconds=600.0)
    calls = 0

    async def handler(_event: ChallengeNeedsHuman) -> None:
        nonlocal calls
        calls += 1

    bus.subscribe(ChallengeNeedsHuman, handler)
    e1 = ChallengeNeedsHuman(
        url="https://example.com/a",
        engine="playwright",
        wait_seconds=10.0,
        at=datetime.now(UTC),
    )
    e2 = ChallengeNeedsHuman(
        url="https://example.com/b",
        engine="playwright",
        wait_seconds=10.0,
        at=datetime.now(UTC),
    )
    await bus.publish(e1)
    await bus.publish(e2)
    assert calls == 1


@pytest.mark.asyncio
async def test_handler_error_does_not_block_others() -> None:
    bus = EventBus(cooldown_seconds=0.0)
    ok: list[str] = []

    async def bad(_event: ChallengeNeedsHuman) -> None:
        raise RuntimeError("boom")

    async def good(event: ChallengeNeedsHuman) -> None:
        ok.append(event.url)

    bus.subscribe(ChallengeNeedsHuman, bad)
    bus.subscribe(ChallengeNeedsHuman, good)
    await bus.publish(
        ChallengeNeedsHuman(
            url="https://example.com/ok",
            engine="playwright",
            wait_seconds=1.0,
            at=datetime.now(UTC),
        )
    )
    assert ok == ["https://example.com/ok"]


@pytest.mark.asyncio
async def test_module_publish_uses_get_bus() -> None:
    from spiderhub.events import publish

    bus = EventBus(cooldown_seconds=0.0)
    set_bus(bus)
    assert get_bus() is bus
    seen: list[str] = []

    async def handler(event: ChallengeNeedsHuman) -> None:
        seen.append(event.engine)

    bus.subscribe(ChallengeNeedsHuman, handler)
    await publish(
        ChallengeNeedsHuman(
            url="https://example.com/z",
            engine="camoufox",
            wait_seconds=5.0,
            at=datetime.now(UTC),
        )
    )
    assert seen == ["camoufox"]
