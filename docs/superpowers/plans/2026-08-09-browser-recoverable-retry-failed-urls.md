# 浏览器可恢复重试 + 失败 URL 落库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 浏览器可恢复导航错误最多重试 3 次；最终仍无法正确处理的 URL upsert 到 MySQL `failed_urls`（dry-run 不写）。

**Architecture:** `is_recoverable_fetch_error` 驱动三引擎 `_fetch_page` 重试（timeout/transient 与 closed 一样走 `_recover_browser` 换页/重连）。Runner 去掉 closed 专用 requeue；在 fetch/parse/redirect 最终失败时调用 `pipeline.record_failed_url`。`MySQLPipeline` upsert；`NullPipeline` no-op。

**Tech Stack:** Python ≥3.11、PyMySQL、现有 Playwright/Camoufox/Patchright fetcher、`pytest` + `pytest-asyncio`。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-09-browser-recoverable-retry-failed-urls-design.md`
- 可恢复重试上限固定为 **3**（含首次）
- 唯一键：`url`；`fail_count` 递增；成功路径不写、不删旧行
- `--dry-run` / `NullPipeline` 不写库
- 写失败表失败只 warning，不中断 crawl
- 应用不自动 `CREATE TABLE`；DDL 进 `scripts/sql/missav_schema.sql`
- 单测禁止打真实站 / 真实 MySQL
- 不主动 commit（除非用户明确要求）；下列 Commit 步骤默认跳过

---

## File map

| Path | Change |
|------|--------|
| `src/spiderhub/downloaders/browser_challenge.py` | 新增 `is_recoverable_fetch_error` |
| `src/spiderhub/downloaders/playwright_fetcher.py` | `_FETCH_RECOVERABLE_RETRIES`；`_fetch_page` 按可恢复错误重试 |
| `src/spiderhub/downloaders/camoufox_fetcher.py` | 同上 |
| `src/spiderhub/downloaders/patchright_fetcher.py` | 同上 |
| `src/spiderhub/pipelines/base.py` | Protocol 增加 `record_failed_url` |
| `src/spiderhub/pipelines/null.py` | no-op 实现 |
| `src/spiderhub/pipelines/mysql.py` | upsert SQL + `record_failed_url` |
| `src/spiderhub/core/runner.py` | 去掉 closed requeue；最终失败落库 |
| `scripts/sql/missav_schema.sql` | `failed_urls` 表 |
| `AGENTS.md` | 约定一句 |
| `README.md` | 建表说明可提 `failed_urls`（可选一句） |
| `tests/unit/test_playwright_fetcher.py` | recoverable 判定 + timeout 重试 |
| `tests/unit/test_runner.py` | 落库调用；调整/替换 closed requeue 测例 |
| `tests/unit/test_mysql_pipeline.py` | upsert SQL |

---

### Task 1: `is_recoverable_fetch_error`

**Files:**
- Modify: `src/spiderhub/downloaders/browser_challenge.py`
- Modify: `tests/unit/test_playwright_fetcher.py`

**Interfaces:**
- Produces: `is_recoverable_fetch_error(exc: BaseException) -> bool`
- Consumes: `is_closed_target_error`, `is_transient_page_error`

- [ ] **Step 1: Write the failing test**

在 `tests/unit/test_playwright_fetcher.py` 增加：

```python
from spiderhub.downloaders.browser_challenge import (
    is_closed_target_error,
    is_recoverable_fetch_error,
)

def test_is_recoverable_fetch_error() -> None:
    assert is_recoverable_fetch_error(
        RuntimeError(
            "Page.goto: Timeout 30000ms exceeded.\n"
            'Call log:\n  - navigating to "https://missav.ws/x", '
            'waiting until "domcontentloaded"'
        )
    )
    assert is_recoverable_fetch_error(
        RuntimeError("Page.goto: Target page, context or browser has been closed")
    )
    assert is_recoverable_fetch_error(
        RuntimeError("Page.content: Execution context was destroyed")
    )
    assert not is_recoverable_fetch_error(RuntimeError("boom"))
    assert not is_recoverable_fetch_error(
        ChallengeDetectedError("https://x", 403, "cf")
    )
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_playwright_fetcher.py::test_is_recoverable_fetch_error -v`

Expected: FAIL（`is_recoverable_fetch_error` 未定义）

- [ ] **Step 3: Implement**

在 `browser_challenge.py`：

```python
def is_recoverable_fetch_error(exc: BaseException) -> bool:
    """True for closed/crash, navigation timeout, or transient page errors."""
    from spiderhub.challenges.detect import ChallengeDetectedError

    if isinstance(exc, ChallengeDetectedError):
        return False
    if is_closed_target_error(exc) or is_transient_page_error(exc):
        return True
    return "timeout" in str(exc).lower()
```

（若顶部已可 import `ChallengeDetectedError` 且无环依赖，用顶部 import；当前 `browser_challenge` 已从 `detect` import，可直接加。）

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/unit/test_playwright_fetcher.py::test_is_recoverable_fetch_error -v`

Expected: PASS

- [ ] **Step 5: Commit**（仅用户要求时）

```bash
git add src/spiderhub/downloaders/browser_challenge.py tests/unit/test_playwright_fetcher.py
git commit -m "$(cat <<'EOF'
feat: classify recoverable browser fetch errors

EOF
)"
```

---

### Task 2: 三引擎 `_fetch_page` 可恢复重试

**Files:**
- Modify: `src/spiderhub/downloaders/playwright_fetcher.py`
- Modify: `src/spiderhub/downloaders/camoufox_fetcher.py`
- Modify: `src/spiderhub/downloaders/patchright_fetcher.py`
- Modify: `tests/unit/test_playwright_fetcher.py`

**Interfaces:**
- Consumes: `is_recoverable_fetch_error`
- Produces: `_FETCH_RECOVERABLE_RETRIES = 3`；`_fetch_page` 对可恢复错误最多 3 次，每次失败后 `_recover_browser`

- [ ] **Step 1: Write failing tests**（playwright 为代表；camoufox/patchright 逻辑同构，本任务改代码即可，不强制各写一套 fake）

```python
@pytest.mark.asyncio
async def test_playwright_retries_goto_timeout_then_succeeds() -> None:
    goto_calls = {"n": 0}

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self

        async def goto(self, *_a: object, **_k: object) -> object:
            goto_calls["n"] += 1
            if goto_calls["n"] < 2:
                raise RuntimeError("Page.goto: Timeout 30000ms exceeded.")

            class _Resp:
                status = 200

            return _Resp()

        async def title(self) -> str:
            return "ok"

        async def cookies(self) -> list[dict[str, str]]:
            return [{"name": "cf_clearance", "value": "1"}]

        async def content(self) -> str:
            return "<html><title>ok</title><body>ok</body></html>"

        async def wait_for_load_state(self, *_a: object, **_k: object) -> None:
            return None

        async def close(self) -> None:
            return None

    class _FakeContext:
        async def cookies(self) -> list[dict[str, str]]:
            return []

        async def new_page(self) -> _FakePage:
            return _FakePage()

        async def storage_state(self, **_k: object) -> None:
            return None

    settings = Settings(request_delay_seconds=0.0, browser_challenge_wait_seconds=1.0)
    fetcher = PlaywrightFetcher(settings)
    fetcher._context = _FakeContext()
    fetcher._reuse_page = True
    fetcher._shared_page = _FakePage()
    fetcher._content_headless = True

    resp = await fetcher.fetch("https://missav.ws/cn/x")
    assert resp.status_code == 200
    assert goto_calls["n"] == 2


@pytest.mark.asyncio
async def test_playwright_goto_timeout_exhausted_after_three_attempts() -> None:
    goto_calls = {"n": 0}

    class _FakePage:
        url = "https://missav.ws/cn/x"

        def __init__(self) -> None:
            self.context = self

        async def goto(self, *_a: object, **_k: object) -> object:
            goto_calls["n"] += 1
            raise RuntimeError("Page.goto: Timeout 30000ms exceeded.")

        async def close(self) -> None:
            return None

    class _FakeContext:
        async def cookies(self) -> list[dict[str, str]]:
            return []

        async def new_page(self) -> _FakePage:
            return _FakePage()

        async def storage_state(self, **_k: object) -> None:
            return None

    settings = Settings(request_delay_seconds=0.0, browser_challenge_wait_seconds=1.0)
    fetcher = PlaywrightFetcher(settings)
    fetcher._context = _FakeContext()
    fetcher._reuse_page = True
    fetcher._shared_page = _FakePage()
    fetcher._content_headless = True

    with pytest.raises(RuntimeError, match="Timeout"):
        await fetcher.fetch("https://missav.ws/cn/x")
    assert goto_calls["n"] == 3
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_playwright_fetcher.py::test_playwright_retries_goto_timeout_then_succeeds tests/unit/test_playwright_fetcher.py::test_playwright_goto_timeout_exhausted_after_three_attempts -v`

Expected: FAIL（timeout 当前不重试，第一次即抛出；`goto_calls["n"] == 1`）

- [ ] **Step 3: Implement in all three fetchers**

对每个文件：

1. Import `is_recoverable_fetch_error`（可保留 `is_closed_target_error` 若别处仍用）。
2. 将 `_FETCH_CLOSED_RETRIES` 重命名为 `_FETCH_RECOVERABLE_RETRIES = 3`。
3. 改 `_fetch_page`：

```python
async def _fetch_page(self, url: str) -> tuple[str, int, str, dict[str, str]]:
    if self._fetch_page_override is not None:
        return await self._fetch_page_override(url)
    last_exc: BaseException | None = None
    for attempt in range(1, _FETCH_RECOVERABLE_RETRIES + 1):
        try:
            return await self._navigate_once(url)
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if (
                not is_recoverable_fetch_error(exc)
                or attempt >= _FETCH_RECOVERABLE_RETRIES
            ):
                raise
            logger.warning(
                "recoverable fetch error; retrying attempt=%s/%s url=%s err=%s",
                attempt,
                _FETCH_RECOVERABLE_RETRIES,
                url,
                exc,
            )
            await self._recover_browser(url=url, attempt=attempt)
    assert last_exc is not None
    raise last_exc
```

说明：timeout/transient/closed 统一走现有 `_recover_browser`（invalidate 共享 page；浏览器仍活则下次 `_ensure_page` 开新 tab）。无需再分叉两套恢复路径。

- [ ] **Step 4: Run related tests**

Run: `uv run pytest tests/unit/test_playwright_fetcher.py -v`

Expected: PASS（含原有 closed/crash 恢复测例）

- [ ] **Step 5: Commit**（仅用户要求时）

```bash
git add src/spiderhub/downloaders/playwright_fetcher.py \
  src/spiderhub/downloaders/camoufox_fetcher.py \
  src/spiderhub/downloaders/patchright_fetcher.py \
  tests/unit/test_playwright_fetcher.py
git commit -m "$(cat <<'EOF'
feat: retry recoverable browser navigation errors up to 3 times

EOF
)"
```

---

### Task 3: `failed_urls` DDL + Pipeline API

**Files:**
- Modify: `scripts/sql/missav_schema.sql`
- Modify: `src/spiderhub/pipelines/base.py`
- Modify: `src/spiderhub/pipelines/null.py`
- Modify: `src/spiderhub/pipelines/mysql.py`
- Modify: `tests/unit/test_mysql_pipeline.py`

**Interfaces:**
- Produces:

```python
async def record_failed_url(
    self,
    *,
    url: str,
    spider_name: str,
    error_type: str,  # fetch | parse | redirect
    error_message: str,
) -> None: ...
```

- Produces: `upsert_failed_url_sql(...) -> tuple[str, tuple[object, ...]]`

- [ ] **Step 1: Append DDL**

在 `scripts/sql/missav_schema.sql` 末尾追加：

```sql
CREATE TABLE IF NOT EXISTS failed_urls (
  id BIGINT NOT NULL AUTO_INCREMENT,
  url VARCHAR(1024) NOT NULL,
  spider_name VARCHAR(128) NOT NULL,
  error_type VARCHAR(64) NOT NULL,
  error_message VARCHAR(1024) NOT NULL,
  fail_count INT NOT NULL DEFAULT 1,
  last_failed_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_failed_urls_url (url(768))
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 2: Write failing SQL test**

```python
from spiderhub.pipelines.mysql import upsert_failed_url_sql

def test_upsert_failed_url_sql() -> None:
    sql, params = upsert_failed_url_sql(
        url="https://missav.ws/dm91/cn/mdon-044",
        spider_name="missav_actress",
        error_type="fetch",
        error_message="Page.goto: Timeout 30000ms exceeded.",
    )
    assert "INSERT INTO failed_urls" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "fail_count" in sql.lower()
    assert params[0] == "https://missav.ws/dm91/cn/mdon-044"
    assert params[1] == "missav_actress"
    assert params[2] == "fetch"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mysql_pipeline.py::test_upsert_failed_url_sql -v`

Expected: FAIL（函数未定义）

- [ ] **Step 4: Implement SQL helper + pipeline methods**

`mysql.py`：

```python
_ERR_MSG_MAX = 1024

def upsert_failed_url_sql(
    *,
    url: str,
    spider_name: str,
    error_type: str,
    error_message: str,
) -> tuple[str, tuple[object, ...]]:
    msg = error_message[:_ERR_MSG_MAX]
    sql = """
    INSERT INTO failed_urls (
      url, spider_name, error_type, error_message, fail_count, last_failed_at
    ) VALUES (%s,%s,%s,%s,1,CURRENT_TIMESTAMP)
    ON DUPLICATE KEY UPDATE
      spider_name=VALUES(spider_name),
      error_type=VALUES(error_type),
      error_message=VALUES(error_message),
      fail_count=fail_count+1,
      last_failed_at=CURRENT_TIMESTAMP
    """
    return sql, (url, spider_name, error_type, msg)
```

`MySQLPipeline.record_failed_url`：`asyncio.to_thread` 执行 upsert + commit；异常 rollback 后 **re-raise**（由 Runner 捕获打 warning——见 Task 4）。或在 pipeline 内吞掉并 warning——**本计划选 Runner 捕获**，pipeline 正常抛出，便于单测。

`base.py` Protocol 增加同签名方法。

`NullPipeline.record_failed_url`：`return None`（可打 debug，勿强制）。

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_mysql_pipeline.py -v`

Expected: PASS

- [ ] **Step 6: Commit**（仅用户要求时）

```bash
git add scripts/sql/missav_schema.sql \
  src/spiderhub/pipelines/base.py \
  src/spiderhub/pipelines/null.py \
  src/spiderhub/pipelines/mysql.py \
  tests/unit/test_mysql_pipeline.py
git commit -m "$(cat <<'EOF'
feat: add failed_urls upsert pipeline API

EOF
)"
```

---

### Task 4: Runner 最终失败落库 + 去掉 closed requeue

**Files:**
- Modify: `src/spiderhub/core/runner.py`
- Modify: `tests/unit/test_runner.py`

**Interfaces:**
- Consumes: `pipeline.record_failed_url(...)`
- Removes: `browser_closed_requeued` / `is_closed_target_error` requeue 分支

- [ ] **Step 1: Replace closed-requeue test with recording tests**

删除或改写 `test_runner_requeues_once_after_browser_closed`（fetcher 已负责重试；Runner 不应再 requeue）。

新增：

```python
class _RecordingPipeline(NullPipeline):
    def __init__(self) -> None:
        self.failed: list[dict[str, str]] = []

    async def record_failed_url(
        self,
        *,
        url: str,
        spider_name: str,
        error_type: str,
        error_message: str,
    ) -> None:
        self.failed.append(
            {
                "url": url,
                "spider_name": spider_name,
                "error_type": error_type,
                "error_message": error_message,
            }
        )


class _AlwaysFailFetcher:
    async def fetch(self, url: str) -> FetchedResponse:
        raise RuntimeError("Page.goto: Timeout 30000ms exceeded.")


@pytest.mark.asyncio
async def test_runner_records_failed_url_on_fetch_error() -> None:
    pipeline = _RecordingPipeline()
    result = await run_spider(
        _ClosedOnceSpider(),  # start_urls list; rename/reuse or tiny spider
        fetcher=_AlwaysFailFetcher(),  # type: ignore[arg-type]
        pipeline=pipeline,
        settings=Settings(obey_robots=False, request_delay_seconds=0.0),
    )
    assert result.urls_failed == 1
    assert result.items_ok == 0
    assert len(pipeline.failed) == 1
    assert pipeline.failed[0]["error_type"] == "fetch"
    assert pipeline.failed[0]["url"] == "https://example.com/list"


@pytest.mark.asyncio
async def test_runner_records_failed_url_on_parse_error() -> None:
    # Use existing _ParseFailSpider + httpx mock; assert bad URL recorded as parse
    ...


@pytest.mark.asyncio
async def test_runner_does_not_record_on_success() -> None:
    # Existing list spider happy path + _RecordingPipeline; assert pipeline.failed == []
    ...
```

`test_runner_records_failed_url_on_parse_error` 完整骨架：

```python
@pytest.mark.asyncio
async def test_runner_records_failed_url_on_parse_error() -> None:
    pages = {
        "https://example.com/bad": "<html>bad</html>",
        "https://example.com/ok": "<html>ok</html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=pages[str(request.url)], request=request)

    settings = Settings(
        obey_robots=False,
        request_delay_seconds=0.0,
        http_max_retries=1,
    )
    pipeline = _RecordingPipeline()
    transport = httpx.MockTransport(handler)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        result = await run_spider(
            _ParseFailSpider(),
            fetcher=fetcher,
            pipeline=pipeline,
            settings=settings,
        )
    assert result.items_ok == 1
    assert result.urls_failed == 1
    assert len(pipeline.failed) == 1
    assert pipeline.failed[0]["error_type"] == "parse"
    assert pipeline.failed[0]["url"] == "https://example.com/bad"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_runner.py -v`

Expected: FAIL（`record_failed_url` 未被调用 / Protocol 或旧 requeue 行为不符）

- [ ] **Step 3: Implement runner**

核心改动要点：

1. 删除 `browser_closed_requeued` 与 `is_closed_target_error` import/分支。
2. 增加辅助：

```python
async def _record_failed(
    pipeline: Pipeline,
    *,
    url: str,
    spider_name: str,
    error_type: str,
    error_message: str,
) -> None:
    try:
        await pipeline.record_failed_url(
            url=url,
            spider_name=spider_name,
            error_type=error_type,
            error_message=error_message[:1024],
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "record failed_url failed url=%s err=%s",
            url,
            exc,
        )
```

3. fetch 异常 → `urls_failed += 1` → `_record_failed(..., error_type="fetch", error_message=str(exc))`。
4. parse 异常 → 同上，`error_type="parse"`。
5. post-redirect disallowed → `error_type="redirect"`，`error_message` 可用 `f"disallowed redirect final={response.url}"`。
6. **不要**在成功路径调用。

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_runner.py -v`

Expected: PASS

- [ ] **Step 5: Commit**（仅用户要求时）

```bash
git add src/spiderhub/core/runner.py tests/unit/test_runner.py
git commit -m "$(cat <<'EOF'
feat: persist finally-failed crawl URLs via pipeline

EOF
)"
```

---

### Task 5: 文档同步 + 全量质量门禁

**Files:**
- Modify: `AGENTS.md`
- Modify: `README.md`（可选一句）

- [ ] **Step 1: Update AGENTS.md**

在技术栈「飞书提醒」附近或落库行补充，例如：

- 落库行：`PyMySQL`（MySQL upsert；含 `failed_urls` 最终失败 URL）
- 反爬/下载约定一句：浏览器可恢复导航错误（timeout / closed / transient）最多 3 次；耗尽后由 Runner 写入 `failed_urls`；dry-run 不写

- [ ] **Step 2: README**（若「建表」小节只提 schema 文件，加半句：`failed_urls` 亦在该脚本中）

- [ ] **Step 3: Full gate**

```bash
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest
```

Expected: 全部通过

- [ ] **Step 4: Commit**（仅用户要求时）

```bash
git add AGENTS.md README.md
git commit -m "$(cat <<'EOF'
docs: document browser recoverable retry and failed_urls

EOF
)"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| `is_recoverable_fetch_error`（timeout/closed/transient） | Task 1 |
| Challenge 错误不重试 | Task 1 |
| 三引擎最多 3 次 + `_recover_browser` | Task 2 |
| `failed_urls` DDL | Task 3 |
| Pipeline `record_failed_url` + upsert/`fail_count` | Task 3 |
| NullPipeline / dry-run no-op | Task 3–4 |
| Runner fetch/parse/redirect 落库 | Task 4 |
| 去掉 Runner closed requeue | Task 4 |
| 成功不写 | Task 4 |
| 写库失败不中断 | Task 4 `_record_failed` |
| AGENTS/README | Task 5 |

## Self-review notes

- 无 TBD/TODO 占位。
- `record_failed_url` 签名在 Task 3/4 一致。
- timeout 恢复不另造 API，复用 `_recover_browser`，与 design「换页」一致。
- 现有 `test_is_closed_target_error` 仍断言 timeout **不是** closed；recoverable 另测覆盖。
