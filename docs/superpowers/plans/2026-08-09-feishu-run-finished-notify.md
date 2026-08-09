# 飞书爬虫完成提醒 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 非 dry-run 的 `spiderhub run` 正常结束或异常中断时，通过现有飞书 Open API 推送一条完成提示（含状态、统计、可选错误原因）。

**Architecture:** CLI `publish(SpiderRunFinished)` → 进程内 `EventBus` → `FeishuNotifier` 发文本。挑战提醒冷却仅作用于 `ChallengeNeedsHuman`，完成事件不受冷却吞掉。未配置飞书则 no-op。

**Tech Stack:** Python ≥3.11、`httpx`、现有 `Settings` / `EventBus` / `FeishuNotifier`、`pytest` + `pytest-asyncio`。

## Global Constraints

- 复用现有飞书四件套；不新增独立开关
- `--dry-run` 不 publish
- 成功 / 部分失败 / 异常失败均推送；通知失败不影响退出码
- 单测禁止打真实飞书
- 不主动 commit（除非用户要求）

---

## File map

| Path | Change |
|------|--------|
| `src/spiderhub/events/types.py` | 新增 `SpiderRunFinished` |
| `src/spiderhub/events/__init__.py` | 导出 |
| `src/spiderhub/events/bus.py` | `cooldown_types`：仅对指定类型冷却 |
| `src/spiderhub/notifiers/feishu.py` | 文案、handler、setup 订阅；setup 传入 `cooldown_types` |
| `src/spiderhub/cli.py` | 非 dry-run 成功/失败 publish |
| `tests/unit/test_event_bus.py` | 冷却类型隔离 |
| `tests/unit/test_feishu_notifier.py` | 文案 + 完成事件订阅 |
| `tests/unit/test_cli.py` 或新建薄测 | dry-run 不 publish；成功/失败 publish |
| `README.md` / `AGENTS.md` | 一句说明 |

---

### Task 1: `SpiderRunFinished` + 总线 `cooldown_types`

**Files:**
- Modify: `src/spiderhub/events/types.py`
- Modify: `src/spiderhub/events/__init__.py`
- Modify: `src/spiderhub/events/bus.py`
- Modify: `tests/unit/test_event_bus.py`

**Interfaces:**
- Produces: `SpiderRunFinished(spider_name, status, items_ok, items_failed, urls_failed, error, dry_run, at)`
- Produces: `EventBus(cooldown_seconds=..., cooldown_types: frozenset[type] | None = None)`  
  - `None`：保持旧行为（所有类型都冷却）  
  - 非空 frozenset：仅这些类型应用冷却

- [ ] **Step 1: 事件类型与导出**

```python
@dataclass(frozen=True, slots=True)
class SpiderRunFinished:
    spider_name: str
    status: str  # success | partial | failed
    items_ok: int
    items_failed: int
    urls_failed: int
    error: str | None
    dry_run: bool
    at: datetime
```

- [ ] **Step 2: EventBus 冷却按类型**

在 `publish` 冷却判断前：若 `self._cooldown_types is not None` 且 `event_type not in self._cooldown_types`，跳过冷却逻辑。

- [ ] **Step 3: 单测** — 总线对 A 冷却时，publish B 仍调用 handler

- [ ] **Step 4:** `uv run pytest tests/unit/test_event_bus.py -v`

---

### Task 2: FeishuNotifier 完成消息

**Files:**
- Modify: `src/spiderhub/notifiers/feishu.py`
- Modify: `tests/unit/test_feishu_notifier.py`

**Interfaces:**
- Produces: `format_run_finished_message(event) -> str`
- Produces: `FeishuNotifier.on_spider_run_finished`
- `setup_feishu_notifier`：`EventBus(cooldown_seconds=..., cooldown_types=frozenset({ChallengeNeedsHuman}))`；额外 subscribe `SpiderRunFinished`

- [ ] **Step 1: 失败文案测试（TDD）**

```python
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
```

- [ ] **Step 2: 实现 format + handler + setup 订阅**

- [ ] **Step 3: 单测 setup 后 publish 完成事件会发消息；挑战冷却不挡完成事件**

- [ ] **Step 4:** `uv run pytest tests/unit/test_feishu_notifier.py -v`

---

### Task 3: CLI 挂载 publish

**Files:**
- Modify: `src/spiderhub/cli.py`
- Modify: `tests/unit/test_cli.py`（或扩展现有）

**Interfaces:**
- Helper（可放 cli 模块内）:
  - `_run_status(result) -> str`
  - `_format_run_error(exc) -> str`（截断 500）
  - `_publish_run_finished(...)` 仅 `not dry_run` 时 publish

- [ ] **Step 1: 正常结束 / except 路径 publish**
- [ ] **Step 2: dry-run 断言不 publish（mock `spiderhub.events.publish` 或 cli 内辅助）**
- [ ] **Step 3:** `uv run pytest tests/unit/test_cli.py tests/unit/test_cli_run_dry.py -v`

---

### Task 4: 文档 + 全量质量

**Files:**
- Modify: `README.md`、`AGENTS.md`
- Modify: design spec 状态 → 已落地

- [ ] **Step 1: 文档一句**
- [ ] **Step 2:** `uv run ruff check . && uv run ruff format . && uv run mypy src && uv run pytest`
