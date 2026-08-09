# 飞书提醒：Cloudflare 人工验证（设计）

日期：2026-08-09  
状态：待实现

## 背景

SpiderHub L3 浏览器抓取在有头 / CDP 模式下，遇到 Cloudflare 挑战会进入人工等待（日志提示「请在浏览器窗口中手动完成验证」）。长时间无人值守时容易错过窗口，需要及时提醒操作者。

用户选择：

- 接入方式：飞书开放平台应用（`app_id` / `app_secret`），不用群自定义 Webhook
- 接收方：配置支持个人（`open_id` / `user_id`）或群（`chat_id`）
- 触发：进入人工验证等待时立刻提醒；同进程内冷却，避免刷屏
- 架构：轻量进程内事件总线 + 飞书订阅者（方案 B）

## 目标

1. L3 进入「需人工完成 Cloudflare」等待时发布领域事件。
2. 飞书订阅者消费事件，通过 Open API 发一条短文本消息。
3. 未配置完整飞书凭证时完全关闭（no-op），不影响抓取主路径。
4. 通知失败只记日志，不中断 / 不重试阻塞爬虫。

## 非目标

- 不实现自动点选 / 打码 / 接码
- 不做邮件、短信、其它 IM 通道
- 不做消息历史库或管理后台
- 不上第三方重型事件框架（Celery / Kafka 等）

## 架构

```text
L3 fetcher (_wait_challenge_clear)
        │ publish ChallengeNeedsHuman
        ▼
  spiderhub.events.bus  (进程内)
        │
        ▼
  FeishuChallengeSubscriber
        │ tenant_access_token + im/v1/messages
        ▼
      飞书个人 / 群
```

分层约定：

| 层级 | 职责 |
|------|------|
| `events/` | 事件类型 + 进程内总线；无 I/O |
| `notifiers/` | 飞书 HTTP 客户端与订阅装配；可读 Settings |
| `downloaders/` | 仅在进入人工等待时 `publish`；不直接调飞书 |
| `cli` | `run` 启动时按配置 `subscribe` / 不订阅 |

## 事件与总线

### 事件

```python
@dataclass(frozen=True, slots=True)
class ChallengeNeedsHuman:
    url: str
    engine: str          # playwright | camoufox | patchright
    wait_seconds: float
    at: datetime         # UTC
```

### 总线 API（`src/spiderhub/events/bus.py`）

- `subscribe(event_type, handler)`：注册异步 handler `Callable[[T], Awaitable[None]]`
- `unsubscribe(event_type, handler)`：测试用
- `async publish(event)`：依次 `await` 所有订阅者；单个 handler 异常被捕获并 `logger.exception`，不影响其它订阅者与发布方
- 默认使用模块级单例总线（可测：测试中可注入 / reset）

冷却（防刷屏）：

- 默认在**总线侧**对 `ChallengeNeedsHuman` 按「事件类型」做冷却（不按 URL 细分，避免同轮多 URL 连发刷屏）
- 冷却秒数：`feishu_notify_cooldown_seconds`，默认 `600`
- 冷却期内的 `publish`：跳过所有 handler，打 debug 日志
- 说明：冷却状态挂在总线上，与是否启用飞书无关；无订阅者时 publish 仍为廉价 no-op

## 挂载点

以下三处 `_wait_challenge_clear`，在现有「请手动完成验证」日志**之后**、进入 wait 循环**之前**，各 `await publish(ChallengeNeedsHuman(...))` 一次：

1. `PlaywrightFetcher`
2. `CamoufoxFetcher`
3. `PatchrightFetcher`

仅当 `self._interactive and not self._content_headless`（与现有人工提示条件一致）时发布。  
Headless 快速失败路径**不**发事件。

`engine` 字段：

- Playwright：`playwright`（含 CDP）
- Camoufox / Patchright：各自引擎名

## 飞书订阅者

模块：`src/spiderhub/notifiers/feishu.py`

流程：

1. 用 `app_id` + `app_secret` 调  
   `POST https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal`  
   缓存 `tenant_access_token`，按返回的 `expire` 提前 60s 刷新。
2. 发消息：  
   `POST https://open.feishu.cn/open-apis/im/v1/messages?receive_id_type={type}`  
   Header：`Authorization: Bearer {token}`  
   Body：
   ```json
   {
     "receive_id": "<id>",
     "msg_type": "text",
     "content": "{\"text\":\"...\"}"
   }
   ```
3. HTTP 客户端：复用项目已有 `httpx`（异步），超时与 `http_timeout_seconds` 对齐或固定 10s。
4. 任意飞书 API 非成功：`logger.warning`，不抛到爬虫主路径。

消息文案（中文，单行/短多行均可）：

```text
SpiderHub：需要完成 Cloudflare 验证
引擎：{engine}
等待上限：{wait_seconds:.0f}s
URL：{url}
请在浏览器窗口完成验证。
```

不在消息中附带 Cookie、`cf_clearance`、密钥。

### 装配

在 `cli.py` 的 `run` 路径、构造 fetcher **之前**：

- 若 `feishu_app_id`、`feishu_app_secret`、`feishu_receive_id_type`、`feishu_receive_id` 均非空 → 创建订阅者并 `subscribe`
- 否则跳过（保持关闭）
- 进程结束无需强制 unsubscribe（短生命周期 CLI）

## 配置

`Settings` 新增字段（默认关闭语义：空字符串）：

| 字段 | 默认 | Env |
|------|------|-----|
| `feishu_app_id` | `""` | `SPIDERHUB_FEISHU_APP_ID` |
| `feishu_app_secret` | `""` | `SPIDERHUB_FEISHU_APP_SECRET` |
| `feishu_receive_id_type` | `""` | `SPIDERHUB_FEISHU_RECEIVE_ID_TYPE` |
| `feishu_receive_id` | `""` | `SPIDERHUB_FEISHU_RECEIVE_ID` |
| `feishu_notify_cooldown_seconds` | `600` | `SPIDERHUB_FEISHU_NOTIFY_COOLDOWN_SECONDS` |

`feishu_receive_id_type` 合法值：`open_id` | `user_id` | `chat_id`。非空但不合法时：`load_settings` 抛 `ValueError`（与 `browser_engine` 一致）。

同步更新：

- `.env.example`
- `config.example.toml`（可放 `[notify]` 段；secret 仍以 env 为准，toml 可留空注释）
- `README.md` / `AGENTS.md`：简短说明「可选飞书人工验证提醒」

密钥纪律：禁止提交真实 `app_secret`；日志不得打印 secret / token 全文。

## 错误与可观测性

| 场景 | 行为 |
|------|------|
| 未配置飞书 | 不订阅；fetcher 仍可 publish（无 handler） |
| Token 获取失败 | warning；本次通知跳过 |
| 发消息失败 | warning；爬取继续 |
| Handler 抛错 | 总线捕获；不影响 fetcher wait 循环 |
| 冷却中 | debug；不调用 handler |

## 测试

1. **总线**：subscribe → publish 调用 handler；冷却期内第二次 publish 不调用；handler 抛错不阻断其它 handler。
2. **飞书客户端**：`httpx.MockTransport` 覆盖 token 缓存与发消息 query/body；缺配置不装配。
3. **fetcher 挂载**：对 Playwright（或共享 helper）mock `publish`，interactive wait 路径断言调用一次；headless 路径不调用。
4. **settings**：合法 type / 非法 type / env 覆盖。

禁止单测打真实飞书或真实 Cloudflare。

## 合规

- 提醒能力可关闭（默认关闭）
- 仅服务已授权/合法公开采集场景下的操作者通知
- 不改变 robots / 限速 / 升维默认策略

## 实现顺序建议

1. `events` 总线 + 单测  
2. Settings / 示例配置  
3. `FeishuNotifier` + 订阅装配 + 单测  
4. 三个 L3 fetcher 挂载 publish  
5. CLI 装配 + 文档  

## 验收

- [ ] 配置齐全时，进入人工 CF 等待会收到一条飞书消息
- [ ] 冷却期内重复挑战不重复打扰
- [ ] 未配置时行为与现网一致
- [ ] `ruff` / `mypy` / `pytest` 通过
)
