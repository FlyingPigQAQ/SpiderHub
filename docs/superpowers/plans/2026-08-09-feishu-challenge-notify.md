# 飞书 Cloudflare 人工验证提醒 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 当 L3 进入 Cloudflare 人工验证等待时，通过进程内事件总线发布事件，并由飞书 Open API 订阅者提醒操作者（个人或群）。

**Architecture:** L3 fetcher 在 interactive challenge wait 入口 `publish(ChallengeNeedsHuman)` → 模块级 `EventBus`（带冷却）→ `FeishuNotifier` 换 `tenant_access_token` 后调 `im/v1/messages`。未配齐飞书凭证则不订阅；通知失败不影响爬取。

**Tech Stack:** Python ≥3.11、`httpx`（异步）、现有 `Settings` / CLI、`pytest` + `pytest-asyncio`、`httpx.MockTransport`；不新增第三方飞书 SDK。

## Global Constraints

- 规范来源：`docs/superpowers/specs/2026-08-09-feishu-challenge-notify-design.md` 与根目录 `AGENTS.md`
- 密钥仅环境变量 / 本地配置；禁止提交 `app_secret`、token；日志不得打印 secret / token 全文
- 通知默认关闭（配置缺任一关键项即不订阅）
- 单测禁止打真实飞书或真实 Cloudflare
- 不改变 robots / 限速 / 升维默认策略
- 系统级工具只用 `brew`；Python 包只用 `uv`
- 配置优先级：CLI > 环境变量 > 配置文件 > 默认值

## File Structure

| 路径 | 职责 |
|------|------|
| `src/spiderhub/events/__init__.py` | 导出事件类型与默认总线 |
| `src/spiderhub/events/types.py` | `ChallengeNeedsHuman` |
| `src/spiderhub/events/bus.py` | 进程内 pub-sub + 冷却 |
| `src/spiderhub/notifiers/__init__.py` | 包标记 |
| `src/spiderhub/notifiers/feishu.py` | token 缓存、发消息、订阅装配 |
| `src/spiderhub/core/settings.py` | 飞书相关 Settings 字段与校验 |
| `src/spiderhub/downloaders/playwright_fetcher.py` | interactive wait 时 publish |
| `src/spiderhub/downloaders/camoufox_fetcher.py` | 同上 |
| `src/spiderhub/downloaders/patchright_fetcher.py` | 同上 |
| `src/spiderhub/cli.py` | `run` 前按配置 subscribe |
| `.env.example` / `config.example.toml` | 示例键 |
| `README.md` / `AGENTS.md` | 使用说明 |
| `tests/unit/test_event_bus.py` | 总线与冷却 |
| `tests/unit/test_feishu_notifier.py` | HTTP mock |
| `tests/unit/test_settings.py` | 飞书配置 |
| `tests/unit/test_playwright_fetcher.py` | publish 挂载（交互路径） |

---

### Task 1: 事件类型与 EventBus（含冷却）

**Files:**
- Create: `src/spiderhub/events/__init__.py`
- Create: `src/spiderhub/events/types.py`
- Create: `src/spiderhub/events/bus.py`
- Create: `tests/unit/test_event_bus.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `ChallengeNeedsHuman(url: str, engine: str, wait_seconds: float, at: datetime)`
  - `EventBus(cooldown_seconds: float = 0.0)`
  - `EventBus.subscribe(event_type: type[T], handler: Callable[[T], Awaitable[None]]) -> None`
  - `EventBus.unsubscribe(event_type: type[T], handler: Callable[[T], Awaitable[None]]) -> None`
  - `async EventBus.publish(event: object) -> None`
  - `EventBus.clear() -> None`（测试重置）
  - `get_bus() -> EventBus` / `set_bus(bus: EventBus | None) -> None`（模块单例，测试可替换）
  - `async publish(event: object) -> None`（便捷：转发到 `get_bus()`）

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_event_bus.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_event_bus.py -v`  
Expected: FAIL（`spiderhub.events` 不存在）

- [ ] **Step 3: Write minimal implementation**

```python
# src/spiderhub/events/types.py
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class ChallengeNeedsHuman:
    url: str
    engine: str
    wait_seconds: float
    at: datetime
```

```python
# src/spiderhub/events/bus.py
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
    def __init__(self, *, cooldown_seconds: float = 0.0) -> None:
        self._cooldown_seconds = max(0.0, float(cooldown_seconds))
        self._handlers: dict[type[Any], list[Handler]] = defaultdict(list)
        self._last_publish_at: dict[type[Any], float] = {}

    def subscribe(
        self, event_type: type[T], handler: Callable[[T], Awaitable[None]]
    ) -> None:
        self._handlers[event_type].append(handler)  # type: ignore[arg-type]

    def unsubscribe(
        self, event_type: type[T], handler: Callable[[T], Awaitable[None]]
    ) -> None:
        handlers = self._handlers.get(event_type)
        if not handlers:
            return
        try:
            handlers.remove(handler)  # type: ignore[arg-type]
        except ValueError:
            return

    def clear(self) -> None:
        self._handlers.clear()
        self._last_publish_at.clear()

    async def publish(self, event: object) -> None:
        event_type = type(event)
        now = time.monotonic()
        if self._cooldown_seconds > 0:
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
```

```python
# src/spiderhub/events/__init__.py
from spiderhub.events.bus import EventBus, get_bus, publish, set_bus
from spiderhub.events.types import ChallengeNeedsHuman

__all__ = [
    "ChallengeNeedsHuman",
    "EventBus",
    "get_bus",
    "publish",
    "set_bus",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_event_bus.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spiderhub/events tests/unit/test_event_bus.py
git commit -m "feat: add in-process event bus with challenge cooldown"
```

---

### Task 2: Settings 飞书字段与示例配置

**Files:**
- Modify: `src/spiderhub/core/settings.py`
- Modify: `tests/unit/test_settings.py`
- Modify: `.env.example`
- Modify: `config.example.toml`

**Interfaces:**
- Consumes: 现有 `load_settings`
- Produces: `Settings` 新增
  - `feishu_app_id: str = ""`
  - `feishu_app_secret: str = ""`
  - `feishu_receive_id_type: str = ""`  # `open_id` | `user_id` | `chat_id` 或空
  - `feishu_receive_id: str = ""`
  - `feishu_notify_cooldown_seconds: float = 600.0`
  - `_normalize_feishu_receive_id_type(value) -> str`：空串合法；非空必须是三者之一

- [ ] **Step 1: Write the failing test**

在 `tests/unit/test_settings.py` 追加：

```python
def test_feishu_env_and_defaults(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "SPIDERHUB_FEISHU_APP_ID": "cli_xxx",
            "SPIDERHUB_FEISHU_APP_SECRET": "sec",
            "SPIDERHUB_FEISHU_RECEIVE_ID_TYPE": "open_id",
            "SPIDERHUB_FEISHU_RECEIVE_ID": "ou_xxx",
            "SPIDERHUB_FEISHU_NOTIFY_COOLDOWN_SECONDS": "120",
        },
        config_path=tmp_path / "missing.toml",
    )
    assert settings.feishu_app_id == "cli_xxx"
    assert settings.feishu_app_secret == "sec"
    assert settings.feishu_receive_id_type == "open_id"
    assert settings.feishu_receive_id == "ou_xxx"
    assert settings.feishu_notify_cooldown_seconds == 120.0
    defaults = load_settings(env={}, config_path=tmp_path / "missing.toml")
    assert defaults.feishu_app_id == ""
    assert defaults.feishu_notify_cooldown_seconds == 600.0


def test_invalid_feishu_receive_id_type_raises(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="feishu_receive_id_type"):
        load_settings(
            env={"SPIDERHUB_FEISHU_RECEIVE_ID_TYPE": "email"},
            config_path=tmp_path / "missing.toml",
        )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py::test_feishu_env_and_defaults tests/unit/test_settings.py::test_invalid_feishu_receive_id_type_raises -v`  
Expected: FAIL（无属性 / 无校验）

- [ ] **Step 3: Write minimal implementation**

在 `settings.py`：

1. `FEISHU_RECEIVE_ID_TYPES = frozenset({"open_id", "user_id", "chat_id"})`
2. `Settings` 增加五个字段（见 Interfaces）
3. `_normalize_feishu_receive_id_type`：strip；空 → `""`；否则必须 in set
4. `load_settings` 的 `data` / `env_map` / bool-float-int 分支同步：
   - env keys：`SPIDERHUB_FEISHU_APP_ID` 等
   - toml：可读 `[notify]` 段（`app_id` / `app_secret` / `receive_id_type` / `receive_id` / `notify_cooldown_seconds`）；secret 仍建议只用 env
5. `.env.example` 追加注释块；`config.example.toml` 追加：

```toml
[notify]
# 飞书应用提醒（默认关闭；secret 建议只放环境变量）
# app_id = ""
# app_secret = ""
# receive_id_type = "open_id"  # open_id | user_id | chat_id
# receive_id = ""
# notify_cooldown_seconds = 600
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_settings.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spiderhub/core/settings.py tests/unit/test_settings.py .env.example config.example.toml
git commit -m "feat: add Feishu notify settings"
```

---

### Task 3: FeishuNotifier + 订阅装配

**Files:**
- Create: `src/spiderhub/notifiers/__init__.py`
- Create: `src/spiderhub/notifiers/feishu.py`
- Create: `tests/unit/test_feishu_notifier.py`

**Interfaces:**
- Consumes: `Settings`、`ChallengeNeedsHuman`、`EventBus.subscribe`
- Produces:
  - `class FeishuNotifier`
  - `FeishuNotifier.__init__(settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None)`
  - `async FeishuNotifier.send_text(text: str) -> None`
  - `async FeishuNotifier.on_challenge_needs_human(event: ChallengeNeedsHuman) -> None`
  - `def feishu_configured(settings: Settings) -> bool`
  - `def format_challenge_message(event: ChallengeNeedsHuman) -> str`
  - `async def setup_feishu_notifier(settings: Settings, *, bus: EventBus | None = None, transport: ... = None) -> FeishuNotifier | None`  
    未配置返回 `None`；已配置则构造 notifier、按 `settings.feishu_notify_cooldown_seconds` 确保总线冷却（若传入/`get_bus()` 的 cooldown 为 0 则 `set_bus(EventBus(cooldown_seconds=...))` 或在 setup 时新建并 set）、`subscribe(ChallengeNeedsHuman, notifier.on_challenge_needs_human)` 并返回实例

冷却装配规则（写死在实现里，避免歧义）：

- `setup_feishu_notifier` 若启用：`bus = bus or get_bus()`；若 `bus` 仍是默认 cooldown=0，则 `set_bus(EventBus(cooldown_seconds=settings.feishu_notify_cooldown_seconds))` 并用新 bus subscribe。若调用方传入已配置 cooldown 的 bus，则尊重传入值。
- 更简单且推荐：**setup 始终** `set_bus(EventBus(cooldown_seconds=settings.feishu_notify_cooldown_seconds))`（测试可先 `set_bus` 再传入同一实例）。文档化：CLI 在 run 开头调用一次 setup。

推荐最终签名：

```python
async def setup_feishu_notifier(
    settings: Settings,
    *,
    bus: EventBus | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FeishuNotifier | None: ...
```

实现：

- 若未 `feishu_configured` → return None
- `event_bus = bus or EventBus(cooldown_seconds=settings.feishu_notify_cooldown_seconds)`；若 `bus is None`：`set_bus(event_bus)`
- 若 `bus is not None`：不强制改全局，只在该 bus 上 subscribe（测试用）
- CLI 调用时不传 bus，让 setup 创建并 `set_bus`

Token API：`POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal`  
Body：`{"app_id","app_secret"}`；缓存 `expire` 提前 60s。

消息 API：`POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={type}`  
Body：`receive_id` / `msg_type=text` / `content` 为 **JSON 字符串** `{"text":"..."}`。

文案：

```text
SpiderHub：需要完成 Cloudflare 验证
引擎：{engine}
等待上限：{wait_seconds:.0f}s
URL：{url}
请在浏览器窗口完成验证。
```

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_feishu_notifier.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_feishu_notifier.py -v`  
Expected: FAIL

- [ ] **Step 3: Write minimal implementation**

实现 `feishu.py` 要点：

- 客户端：`httpx.AsyncClient(timeout=..., transport=transport)`，在 notifier 内懒创建或 `__init__` 创建；提供 `aclose` 可选（CLI 短生命周期可不关，但测试里可用 async with 或显式 aclose——若持有 client，在 `setup` 返回的实例上保留 `_client`，测试结束 `await notifier.aclose()`）。
- `_ensure_token`：若缓存有效直接返回；否则 POST internal，校验 `code==0`，存 `token` 与 `monotonic deadline = now + expire - 60`。
- `send_text`：try/except 全捕获，失败 `logger.warning`；成功路径检查响应 `code==0`。
- `on_challenge_needs_human`：`await self.send_text(format_challenge_message(event))`。
- 日志：禁止输出 `app_secret` / token。

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_feishu_notifier.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spiderhub/notifiers tests/unit/test_feishu_notifier.py
git commit -m "feat: add Feishu Open API challenge notifier"
```

---

### Task 4: L3 fetcher 挂载 publish + CLI 装配

**Files:**
- Modify: `src/spiderhub/downloaders/playwright_fetcher.py`
- Modify: `src/spiderhub/downloaders/camoufox_fetcher.py`
- Modify: `src/spiderhub/downloaders/patchright_fetcher.py`
- Modify: `src/spiderhub/cli.py`
- Modify: `tests/unit/test_playwright_fetcher.py`
- Modify: `tests/unit/test_cli_run_dry.py`（若需 mock setup；否则仅保证 dry-run 不因未配置飞书失败）

**Interfaces:**
- Consumes: `spiderhub.events.publish`、`ChallengeNeedsHuman`、`setup_feishu_notifier`
- Produces: interactive wait 路径会 publish；CLI run 开头 await setup

- [ ] **Step 1: Write the failing test**

在 `tests/unit/test_playwright_fetcher.py` 追加：

```python
@pytest.mark.asyncio
async def test_wait_challenge_publishes_needs_human(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import UTC, datetime

    published: list[object] = []

    async def fake_publish(event: object) -> None:
        published.append(event)

    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.publish",
        fake_publish,
    )

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self

        async def title(self) -> str:
            return "ok"

        async def cookies(self) -> list[dict[str, str]]:
            return [{"name": "cf_clearance", "value": "1"}]

        async def content(self) -> str:
            return "<html>ok</html>"

        async def wait_for_load_state(self, *_a: object, **_k: object) -> None:
            return None

    settings = Settings(request_delay_seconds=0.0, browser_challenge_wait_seconds=5.0)
    fetcher = PlaywrightFetcher(settings)
    fetcher._interactive = True
    fetcher._content_headless = False
    await fetcher._wait_challenge_clear(_FakePage(), wait_s=5.0)
    assert len(published) == 1
    event = published[0]
    assert getattr(event, "url") == "https://missav.ws/cn/x"
    assert getattr(event, "engine") == "playwright"
    assert getattr(event, "wait_seconds") == 5.0
```

再追加 headless 不发布：

```python
@pytest.mark.asyncio
async def test_wait_challenge_skips_publish_when_headless(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[object] = []

    async def fake_publish(event: object) -> None:
        published.append(event)

    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.publish",
        fake_publish,
    )

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self

        async def title(self) -> str:
            return "ok"

        async def cookies(self) -> list[dict[str, str]]:
            return [{"name": "cf_clearance", "value": "1"}]

        async def content(self) -> str:
            return "<html>ok</html>"

        async def wait_for_load_state(self, *_a: object, **_k: object) -> None:
            return None

    settings = Settings(request_delay_seconds=0.0, browser_challenge_wait_seconds=5.0)
    fetcher = PlaywrightFetcher(settings)
    fetcher._interactive = False
    fetcher._content_headless = True
    await fetcher._wait_challenge_clear(_FakePage(), wait_s=5.0)
    assert published == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_playwright_fetcher.py::test_wait_challenge_publishes_needs_human -v`  
Expected: FAIL（未 publish）

- [ ] **Step 3: Implement fetcher hooks + CLI**

三个 fetcher 在人工提示日志后插入（以 Playwright 为例）：

```python
from datetime import UTC, datetime

from spiderhub.events import ChallengeNeedsHuman, publish

# inside _wait_challenge_clear, after the warning log:
await publish(
    ChallengeNeedsHuman(
        url=str(page.url),
        engine="playwright",  # camoufox / patchright 各写各的
        wait_seconds=wait_s,
        at=datetime.now(UTC),
    )
)
```

Camoufox → `engine="camoufox"`；Patchright → `engine="patchright"`。

`cli.py` `_run_async`：

```python
from spiderhub.notifiers.feishu import setup_feishu_notifier

settings = load_settings()
await setup_feishu_notifier(settings)
```

放在构造 `AutoFetcher` 之前即可。

- [ ] **Step 4: Run tests**

Run:

```bash
uv run pytest tests/unit/test_playwright_fetcher.py tests/unit/test_cli_run_dry.py tests/unit/test_event_bus.py tests/unit/test_feishu_notifier.py -v
```

Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spiderhub/downloaders/playwright_fetcher.py \
  src/spiderhub/downloaders/camoufox_fetcher.py \
  src/spiderhub/downloaders/patchright_fetcher.py \
  src/spiderhub/cli.py \
  tests/unit/test_playwright_fetcher.py
git commit -m "feat: emit challenge-needs-human and wire Feishu on run"
```

---

### Task 5: 文档与质量门禁

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`
- Modify: `docs/superpowers/specs/2026-08-09-feishu-challenge-notify-design.md`（状态改为「已落地」仅在全部完成后）

- [ ] **Step 1: Update README**

在「配置」或反爬章节增加小节小节「飞书人工验证提醒（可选）」：

- 需自建飞书企业自建应用，开通机器人能力与 `im:message:send_as_bot`（或文档等价权限）
- 配置 `SPIDERHUB_FEISHU_APP_ID` / `SECRET` / `RECEIVE_ID_TYPE` / `RECEIVE_ID`
- 冷却默认 600s
- 进入 L3 有头/CDP 人工等待时发送一条文本
- 未配置则无行为变化

- [ ] **Step 2: Update AGENTS.md**

在技术栈或可观测性附近加一行：可选飞书 Open API 订阅 `ChallengeNeedsHuman`；默认关闭；密钥不入库。

- [ ] **Step 3: Full quality gate**

```bash
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest
```

Expected: 全部通过

- [ ] **Step 4: Mark spec landed + commit**

将 design spec 状态改为「已落地」。

```bash
git add README.md AGENTS.md docs/superpowers/specs/2026-08-09-feishu-challenge-notify-design.md
git commit -m "docs: document optional Feishu Cloudflare challenge alerts"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| 进程内事件总线 + `ChallengeNeedsHuman` | Task 1 |
| 冷却默认 600s、按事件类型 | Task 1 + Task 2/3 装配 |
| 飞书 Open API token + im/v1/messages | Task 3 |
| `open_id` / `user_id` / `chat_id` | Task 2 + 3 |
| L3 三引擎 interactive wait 挂载 | Task 4 |
| CLI 按配置 subscribe | Task 4 |
| 未配置关闭 / 失败不阻断 | Task 3–4 |
| Settings / .env / toml 示例 | Task 2 |
| README / AGENTS | Task 5 |
| 单测（总线 / 飞书 mock / fetcher publish） | Task 1/3/4 |

## Placeholder / consistency self-review

- 无 TBD；冷却装配规则已在 Task 3 写死
- `engine` 字符串与 `browser_engine` 取值一致：`playwright` / `camoufox` / `patchright`
- 消息 API `content` 必须是序列化后的 JSON 字符串（测试已断言）
)
