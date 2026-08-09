from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from spiderhub.core.settings import Settings
from spiderhub.events.bus import EventBus, set_bus
from spiderhub.events.types import ChallengeNeedsHuman
from spiderhub.notifiers.feishu import (
    FeishuNotifier,
    feishu_configured,
    format_challenge_message,
    setup_feishu_notifier,
)


@pytest.fixture(autouse=True)
def _isolate_bus() -> None:
    set_bus(None)
    yield
    set_bus(None)


def test_feishu_configured_requires_all_fields() -> None:
    assert not feishu_configured(Settings())
    assert feishu_configured(
        Settings(
            feishu_app_id="a",
            feishu_app_secret="b",
            feishu_receive_id_type="chat_id",
            feishu_receive_id="oc_x",
        )
    )


def test_format_challenge_message_contains_url_and_engine() -> None:
    text = format_challenge_message(
        ChallengeNeedsHuman(
            url="https://missav.ws/cn/x",
            engine="playwright",
            wait_seconds=180.0,
            at=datetime.now(UTC),
        )
    )
    assert "https://missav.ws/cn/x" in text
    assert "playwright" in text
    assert "180" in text


@pytest.mark.asyncio
async def test_send_text_fetches_token_and_posts_message() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "t-test", "expire": 7200},
                request=request,
            )
        if "/im/v1/messages" in request.url.path:
            return httpx.Response(200, json={"code": 0, "data": {}}, request=request)
        return httpx.Response(404, request=request)

    settings = Settings(
        feishu_app_id="cli_x",
        feishu_app_secret="sec",
        feishu_receive_id_type="open_id",
        feishu_receive_id="ou_x",
        http_timeout_seconds=5.0,
    )
    transport = httpx.MockTransport(handler)
    notifier = FeishuNotifier(settings, transport=transport)
    await notifier.send_text("hello")
    assert len(calls) == 2
    assert calls[0].url.path.endswith("/tenant_access_token/internal")
    assert "receive_id_type=open_id" in str(calls[1].url)
    body = json.loads(calls[1].content.decode())
    assert body["receive_id"] == "ou_x"
    assert body["msg_type"] == "text"
    assert json.loads(body["content"])["text"] == "hello"

    # token cached — second send only hits messages
    await notifier.send_text("again")
    assert len(calls) == 3


@pytest.mark.asyncio
async def test_setup_subscribes_and_handles_event() -> None:
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200,
                json={"code": 0, "tenant_access_token": "t-test", "expire": 7200},
                request=request,
            )
        return httpx.Response(200, json={"code": 0, "data": {}}, request=request)

    settings = Settings(
        feishu_app_id="cli_x",
        feishu_app_secret="sec",
        feishu_receive_id_type="chat_id",
        feishu_receive_id="oc_x",
        feishu_notify_cooldown_seconds=600.0,
    )
    bus = EventBus(cooldown_seconds=0.0)
    notifier = await setup_feishu_notifier(
        settings, bus=bus, transport=httpx.MockTransport(handler)
    )
    assert notifier is not None
    await bus.publish(
        ChallengeNeedsHuman(
            url="https://example.com/cf",
            engine="playwright",
            wait_seconds=15.0,
            at=datetime.now(UTC),
        )
    )
    assert any("/im/v1/messages" in str(r.url) for r in calls)


@pytest.mark.asyncio
async def test_setup_returns_none_when_unconfigured() -> None:
    assert await setup_feishu_notifier(Settings()) is None
