# 浏览器可恢复重试 + 失败 URL 落库（设计）

日期：2026-08-09  
状态：待实现

## 背景

浏览器抓取出现 `Page.goto: Timeout 30000ms exceeded` 时，当前逻辑直接记 `urls_failed` 并跳过，不会重试。仅 `page/context/browser closed` 或 crash 会在 fetcher / runner 层做有限恢复。

无人值守跑批时，瞬时超时或半死 tab 很常见；需要有限重试，并把**最终仍无法正确处理**的 URL 记入 MySQL，便于后续补爬。

用户选择：

- 方案 A：浏览器层扩重试 + Runner 最终失败落库
- 可恢复 fetch 错误重试（超时 / closed / transient），非「一律盲重试」
- 最终失败 URL 都写库；重试成功不写
- `--dry-run` 不写库，只打日志
- 按 `url` 唯一键 upsert（更新错误与 `fail_count`）

## 目标

1. Browser fetcher（playwright / camoufox / patchright）对可恢复导航错误最多尝试 **3** 次（含恢复）。
2. Runner 在 URL **最终失败**（fetch 耗尽 / parse 失败 / 跳转出域）时写入 `failed_urls`。
3. 本次处理成功的 URL 不写入 `failed_urls`。
4. dry-run / `NullPipeline` 不落库。
5. 写失败表本身失败时只打 warning，不中断 crawl。

## 非目标

- 从 `failed_urls` 自动补爬的 CLI / 调度
- 成功处理后自动删除旧失败行
- 调整默认 `http_timeout_seconds`
- 给 L1/L2 再套一层与 `http_max_retries` 重复的重试
- 对 `ChallengeDetectedError` 等业务挑战错误做导航重试

## 架构与数据流

```text
run_spider
  └─ fetcher.fetch(url)
       └─ browser._fetch_page  ← 可恢复错误最多 3 次
            ├─ timeout / transient → invalidate 共享 page，开新 tab 再 goto
            └─ closed/crash → 现有 _recover_browser（新 tab / 重连）
  ├─ 成功 → parse → pipeline.process_item（不写 failed_urls）
  └─ 最终失败（fetch 耗尽 / parse 失败 / 跳转出域）
       └─ pipeline.record_failed_url(url, error_type, error_message, spider_name)
            ├─ MySQLPipeline → upsert failed_urls
            └─ NullPipeline（dry-run）→ no-op
```

要点：

- 重试收敛在浏览器 fetcher；L1/L2 继续用现有 `http_max_retries`。
- Runner 不再单独对 closed 做 requeue，避免与 fetcher 3 次叠加。
- Pipeline Protocol 增加 `record_failed_url`；NullPipeline 空实现。

## 可恢复错误与重试策略

在 `browser_challenge.py` 新增：

```python
def is_recoverable_fetch_error(exc: BaseException) -> bool: ...
```

判定（消息子串，大小写不敏感）：

| 类别 | 规则 |
|------|------|
| closed/crash | 复用 `is_closed_target_error` |
| timeout | 消息含 `timeout`（覆盖 `Page.goto: Timeout 30000ms exceeded`） |
| transient | 复用 `is_transient_page_error` |

重试行为（三引擎共用，最多 3 次 attempt）：

1. 失败且 `is_recoverable_fetch_error` 且未达上限 → warning 日志 + 恢复后重试。
2. closed/crash → `_recover_browser`。
3. timeout / transient → invalidate 共享 page 后新 tab 再 `goto`。
4. 第 3 次仍失败，或不可恢复 → 抛出，由 Runner 计失败并落库。
5. `ChallengeDetectedError` 等**不**走此重试。

常量：将 `_FETCH_CLOSED_RETRIES` 语义扩展为可恢复重试上限（仍为 3），命名可改为 `_FETCH_RECOVERABLE_RETRIES`（实现时三引擎一并改）。

## 表结构与落库语义

追加到 `scripts/sql/missav_schema.sql`（应用不自动 `CREATE TABLE`）：

```sql
CREATE TABLE IF NOT EXISTS failed_urls (
  id BIGINT NOT NULL AUTO_INCREMENT,
  url VARCHAR(1024) NOT NULL,
  spider_name VARCHAR(128) NOT NULL,
  error_type VARCHAR(64) NOT NULL,   -- fetch | parse | redirect
  error_message VARCHAR(1024) NOT NULL,
  fail_count INT NOT NULL DEFAULT 1,
  last_failed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_failed_urls_url (url(768))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

| 场景 | 行为 |
|------|------|
| fetch 3 次仍失败 | upsert；`error_type=fetch`；`fail_count = fail_count + 1` |
| parse 异常 | upsert；`error_type=parse` |
| 跳转后出 `allowed_domains` | upsert；`error_type=redirect` |
| 本次重试/处理成功 | 不写；也不主动删旧行 |
| `--dry-run` / NullPipeline | no-op |
| 写 `failed_urls` 失败 | warning，不中断 crawl |

字段约定：

- `error_message`：`str(exc)` 或简短说明，截断至 1024。
- `spider_name`：便于排查；唯一键仍仅 `url`。
- `last_failed_at`：每次 upsert 更新为当前时间。

## 组件改动清单

| 位置 | 改动 |
|------|------|
| `downloaders/browser_challenge.py` | `is_recoverable_fetch_error` |
| `downloaders/playwright_fetcher.py` 等三引擎 | `_fetch_page` 按可恢复错误重试 + timeout/transient 换页 |
| `core/runner.py` | 去掉 closed 专用 requeue；最终失败调 `record_failed_url` |
| `pipelines/base.py` / `null.py` / `mysql.py` | Protocol + 实现 |
| `scripts/sql/missav_schema.sql` | 新表 |
| `AGENTS.md`（及必要时 README） | 约定同步 |
| `tests/unit/...` | 见下节 |

## 测试

- `is_recoverable_fetch_error`：timeout / closed / transient → True；挑战类 / 普通 RuntimeError → False。
- browser fetcher：timeout mock → 第 2 次成功；连续失败 → 恰好 3 次后抛出。
- runner：最终失败调用 `record_failed_url`；中途成功不调用；不再对 closed 额外 requeue（fetcher 已覆盖）。
- MySQL：upsert SQL / `fail_count` 递增路径可单测。
- NullPipeline：`record_failed_url` no-op。

## 文档与运维

- 建表：运维执行 `mysql ... < scripts/sql/missav_schema.sql`（或单独执行新增 DDL）。
- `AGENTS.md`：补充浏览器可恢复重试与 `failed_urls` 落库约定。

## 决策记录

| 决策 | 选择 | 理由 |
|------|------|------|
| 重试位置 | 浏览器 fetcher | 可与 tab/浏览器恢复结合 |
| 落库位置 | Runner 最终失败边界 | parse/redirect 也能覆盖；成功路径天然不写 |
| 唯一键 | `url` | 用户选定；跨 spider 同 URL 合并计数 |
| dry-run | 不写库 | 与现有 NullPipeline / 飞书完成提醒一致 |
| 成功删旧行 | 不做 | YAGNI；补爬清理后续再做 |
