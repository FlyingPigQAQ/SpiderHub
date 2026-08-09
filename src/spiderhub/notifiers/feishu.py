from __future__ import annotations

import json
import logging
import time

import httpx

from spiderhub.core.settings import Settings
from spiderhub.events.bus import EventBus, set_bus
from spiderhub.events.types import ChallengeNeedsHuman

logger = logging.getLogger(__name__)

TOKEN_URL = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
MESSAGE_URL = "https://open.feishu.cn/open-apis/im/v1/messages"


class FeishuNotifier:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._client = httpx.AsyncClient(
            timeout=settings.http_timeout_seconds,
            transport=transport,
        )
        self._tenant_access_token: str | None = None
        self._token_deadline = 0.0

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _ensure_token(self) -> str:
        now = time.monotonic()
        if self._tenant_access_token is not None and now < self._token_deadline:
            return self._tenant_access_token

        response = await self._client.post(
            TOKEN_URL,
            json={
                "app_id": self._settings.feishu_app_id,
                "app_secret": self._settings.feishu_app_secret,
            },
        )
        response.raise_for_status()
        payload = response.json()
        if payload.get("code") != 0:
            raise RuntimeError(f"Feishu token API failed code={payload.get('code')}")

        token = payload.get("tenant_access_token")
        if not isinstance(token, str) or not token:
            raise RuntimeError("Feishu token API returned no tenant access token")
        expire = float(payload.get("expire", 0))
        self._tenant_access_token = token
        self._token_deadline = now + expire - 60.0
        return token

    async def send_text(self, text: str) -> None:
        try:
            token = await self._ensure_token()
            response = await self._client.post(
                MESSAGE_URL,
                params={
                    "receive_id_type": self._settings.feishu_receive_id_type,
                },
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "receive_id": self._settings.feishu_receive_id,
                    "msg_type": "text",
                    "content": json.dumps({"text": text}, ensure_ascii=False),
                },
            )
            response.raise_for_status()
            payload = response.json()
            if payload.get("code") != 0:
                raise RuntimeError(
                    f"Feishu message API failed code={payload.get('code')}"
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning("failed to send Feishu notification: %s", exc)

    async def on_challenge_needs_human(self, event: ChallengeNeedsHuman) -> None:
        await self.send_text(format_challenge_message(event))


def feishu_configured(settings: Settings) -> bool:
    return all(
        value.strip()
        for value in (
            settings.feishu_app_id,
            settings.feishu_app_secret,
            settings.feishu_receive_id_type,
            settings.feishu_receive_id,
        )
    )


def format_challenge_message(event: ChallengeNeedsHuman) -> str:
    return (
        "SpiderHub：需要完成 Cloudflare 验证\n"
        f"引擎：{event.engine}\n"
        f"等待上限：{event.wait_seconds:.0f}s\n"
        f"URL：{event.url}\n"
        "请在浏览器窗口完成验证。"
    )


async def setup_feishu_notifier(
    settings: Settings,
    *,
    bus: EventBus | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FeishuNotifier | None:
    if not feishu_configured(settings):
        return None

    event_bus = bus or EventBus(
        cooldown_seconds=settings.feishu_notify_cooldown_seconds
    )
    if bus is None:
        set_bus(event_bus)

    notifier = FeishuNotifier(settings, transport=transport)
    event_bus.subscribe(
        ChallengeNeedsHuman,
        notifier.on_challenge_needs_human,
    )
    return notifier
