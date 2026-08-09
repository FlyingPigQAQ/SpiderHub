from __future__ import annotations

import json
from datetime import UTC, datetime

import httpx
import pytest

from spiderhub.core.settings import Settings
from spiderhub.events.bus import EventBus, set_bus
from spiderhub.events.types import ChallengeNeedsHuman, SpiderRunFinished
from spiderhub.notifiers.feishu import (
    FeishuNotifier,
    feishu_configured,
    format_challenge_message,
    format_run_finished_message,
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
async def test_notifier_uses_short_dedicated_http_timeout() -> None:
    settings = Settings(http_timeout_seconds=30.0)
    notifier = FeishuNotifier(settings, transport=httpx.MockTransport(lambda _: None))

    assert notifier._client.timeout.connect == 5.0
    assert notifier._client.timeout.read == 5.0
    assert notifier._client.timeout.write == 5.0
    assert notifier._client.timeout.pool == 5.0
    await notifier.aclose()


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


def test_format_run_finished_message_failed_includes_error() -> None:
    text = format_run_finished_message(
        SpiderRunFinished(
            spider_name="demo",
            status="failed",
            items_ok=0,
            items_failed=0,
            urls_failed=0,
            error="RuntimeError: boom",
            dry_run=False,
            at=datetime.now(UTC),
        )
    )
    assert "失败" in text
    assert "demo" in text
    assert "RuntimeError: boom" in text


def test_format_run_finished_message_success() -> None:
    text = format_run_finished_message(
        SpiderRunFinished(
            spider_name="demo",
            status="success",
            items_ok=3,
            items_failed=0,
            urls_failed=0,
            error=None,
            dry_run=False,
            at=datetime.now(UTC),
        )
    )
    assert "成功" in text
    assert "items_ok=3" in text
    assert "错误：" not in text


@pytest.mark.asyncio
async def test_setup_handles_run_finished_despite_challenge_cooldown() -> None:
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
    bus = EventBus(
        cooldown_seconds=600.0,
        cooldown_types=frozenset({ChallengeNeedsHuman}),
    )
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
    await bus.publish(
        ChallengeNeedsHuman(
            url="https://example.com/cf2",
            engine="playwright",
            wait_seconds=15.0,
            at=datetime.now(UTC),
        )
    )
    message_calls_after_challenge = sum(
        1 for r in calls if "/im/v1/messages" in str(r.url)
    )
    assert message_calls_after_challenge == 1

    await bus.publish(
        SpiderRunFinished(
            spider_name="demo",
            status="success",
            items_ok=1,
            items_failed=0,
            urls_failed=0,
            error=None,
            dry_run=False,
            at=datetime.now(UTC),
        )
    )
    message_calls = [r for r in calls if "/im/v1/messages" in str(r.url)]
    assert len(message_calls) == 2
    body = json.loads(message_calls[-1].content.decode())
    assert "爬虫完成" in json.loads(body["content"])["text"]
