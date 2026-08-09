# 飞书提醒：爬虫运行完成（设计）

日期：2026-08-09  
状态：已落地

## 背景

已有飞书 Open API 订阅 `ChallengeNeedsHuman`（人工验证等待）。爬虫常在无人值守下跑完或中断，操作者需要完成态提醒。

用户选择：

- 成功与失败均推送；失败说明原因
- `--dry-run` 不推送
- 架构：复用进程内事件总线 + 飞书订阅者（与挑战提醒同方案）
- 不新增独立飞书开关；沿用现有凭证四件套

## 目标

1. 非 dry-run 的 `spiderhub run` 正常结束或异常中断时发布 `SpiderRunFinished`。
2. 飞书订阅者发一条短文本（状态、统计、可选错误原因）。
3. 未配置飞书时 no-op；通知失败不影响退出码与主路径。

## 非目标

- dry-run 推送
- 进度中途 / 分页级推送
- 独立「完成提醒」配置开关或第二套凭证
- 邮件、短信、其它 IM

## 架构

```text
cli._run_async
        │ publish SpiderRunFinished  (非 dry-run)
        ▼
  spiderhub.events.bus
        │
        ▼
  FeishuNotifier.on_spider_run_finished
        │ tenant_access_token + im/v1/messages
        ▼
      飞书个人 / 群
```

分层：

| 层级 | 职责 |
|------|------|
| `events/` | `SpiderRunFinished`；总线；无 I/O |
| `notifiers/` | 格式化文案 + 订阅装配 |
| `cli` | 决定是否 publish（dry_run / 成功或异常）；不直接调飞书 HTTP |
| `core/runner` | 不依赖飞书；仅返回 `RunResult` |

## 事件

```python
@dataclass(frozen=True, slots=True)
class SpiderRunFinished:
    spider_name: str
    status: str  # "success" | "partial" | "failed"
    items_ok: int
    items_failed: int
    urls_failed: int
    error: str | None  # 异常中断时；正常结束为 None
    dry_run: bool
    at: datetime  # UTC
```

状态规则：

| status | 条件 |
|--------|------|
| `success` | 无未捕获异常，且 `urls_failed == 0` 且 `items_failed == 0` |
| `partial` | 无未捕获异常，但存在 URL 或 item 失败 |
| `failed` | `_run_async` 中 `run_spider` / fetcher 等抛出未捕获异常 |

异常中断时：`items_*` / `urls_failed` 若无结果则填 `0`；`error` 为 `f"{type(exc).__name__}: {exc}"`（截断至合理长度，如 500 字符，避免刷屏）。

## 冷却

总线对**事件类型**统一冷却（挑战提醒默认 600s）。完成通知**不得**被冷却吞掉：

- 方案：`EventBus.publish` 支持按类型跳过冷却，或对 `SpiderRunFinished` 使用 `cooldown_exempt` 集合；默认仅 `ChallengeNeedsHuman` 受冷却。
- 推荐实现：`EventBus(cooldown_seconds=..., cooldown_types=frozenset({ChallengeNeedsHuman}))`；未指定 `cooldown_types` 时保持现有「全部类型」行为以兼容旧测试，或显式传入挑战类型（CLI setup 路径）。

实现时优先：**setup_feishu_notifier 创建 bus 时指定只对 `ChallengeNeedsHuman` 冷却**；完成事件始终投递。

## 挂载点

`cli._run_async`：

1. `dry_run` 为真：不 `publish`。
2. 正常路径：`run_spider` 返回后，按 `RunResult` 算 `status`，`publish(SpiderRunFinished(...))`，再 `print(done ...)`。
3. 异常路径：现有 `except` 内，在 `logging.exception` / 打印 stderr 之后、`return 2` 之前 `publish`（`status="failed"`，带 `error`）。

`setup_feishu_notifier` 在现有订阅外增加：

```python
event_bus.subscribe(SpiderRunFinished, notifier.on_spider_run_finished)
```

## 消息文案

成功 / 部分失败：

```text
SpiderHub：爬虫完成
Spider：{spider_name}
状态：成功|部分失败
items_ok={n} items_failed={n} urls_failed={n}
```

失败（异常）：

```text
SpiderHub：爬虫完成
Spider：{spider_name}
状态：失败
items_ok={n} items_failed={n} urls_failed={n}
错误：{error}
```

不附带密钥、Cookie、token。

## 配置

无新 Settings 字段。未配齐飞书四件套则不订阅，publish 为廉价 no-op。

文档：`README.md` / `AGENTS.md` 在飞书小节中补一句「运行完成也会提醒（dry-run 除外）」。

## 错误与可观测性

| 场景 | 行为 |
|------|------|
| 未配置飞书 | 不订阅 |
| dry-run | 不 publish |
| 发消息失败 | warning；退出码不变 |
| Handler 抛错 | 总线捕获 |

## 测试

1. `format_run_finished_message`：success / partial / failed 文案。
2. `setup_feishu_notifier`：订阅后 publish `SpiderRunFinished` 会调发消息（MockTransport）；冷却期内挑战被跳过时，完成事件仍发送。
3. CLI 或薄封装：dry-run 不 publish；正常结束 / 异常各 publish 一次（可 mock `publish`）。

禁止单测打真实飞书。

## 验收

- [ ] 配置齐全、真实 run 成功 → 收到成功完成消息
- [ ] 部分 URL/item 失败 → 部分失败消息 + 统计
- [ ] 未捕获异常 → 失败消息 + 原因
- [ ] dry-run / 未配置飞书 → 无完成推送
- [ ] `ruff` / `mypy` / `pytest` 通过
