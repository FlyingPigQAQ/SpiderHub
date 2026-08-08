# MissAV 女优页 + MySQL 落库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 落地最小可跑 Hub 内核与 `missav_actress` Spider，将女优元信息与作品详情（含简介、全部分页）按番号 upsert 写入 MySQL 8.0。

**Architecture:** CLI → Registry → async Runner → L1 httpx Fetcher（挑战检测 stub）→ MissAV 解析 → pydantic 模型 → MySQL pipeline（dry-run 跳过写库）。解析与下载分离；密钥仅来自本地配置。

**Tech Stack:** Python ≥3.11、`uv`、`httpx`、`pydantic` v2、`selectolax`、`PyMySQL`、`python-dotenv`、ruff、mypy、pytest、pytest-asyncio。

## Global Constraints

- 规范来源：`docs/superpowers/specs/2026-08-09-missav-actress-mysql-design.md` 与根目录 `AGENTS.md`
- Python `>=3.11`；包名 `spiderhub`；入口 `spiderhub = "spiderhub.cli:main"`
- 运行时依赖：`httpx`、`pydantic`、`selectolax`、`PyMySQL`、`python-dotenv`；不引入 Scrapy / Typer / Click / Playwright / curl_cffi / aiomysql
- 仅采集公开元数据；禁止视频流 / m3u8 / 下载链字段与逻辑
- 单测禁止打真实站点；用 `tests/fixtures/missav/` HTML
- 应用不自动建表；提供 `scripts/sql/missav_schema.sql`
- 系统级工具只可用 `brew`；Python 包装依赖只用 `uv`
- 不提交 `.env`、`config.local.toml`、密钥、Cookie
- 配置优先级：CLI > 环境变量 > 配置文件 > 默认值

## File Structure

| 路径 | 职责 |
|------|------|
| `pyproject.toml` / `uv.lock` | 增加运行时依赖并锁定 |
| `.gitignore` | 忽略 `config.local.toml` |
| `.env.example` / `config.example.toml` | MySQL 与限速等示例键 |
| `src/spiderhub/core/settings.py` | 配置加载 |
| `src/spiderhub/core/spider.py` | Spider 基类 / 协议 |
| `src/spiderhub/core/registry.py` | Spider 注册与查找 |
| `src/spiderhub/core/runner.py` | 异步抓取循环 |
| `src/spiderhub/downloaders/base.py` | `FetchedResponse` 数据类 |
| `src/spiderhub/downloaders/httpx_fetcher.py` | L1 httpx + 重试 + 延时 |
| `src/spiderhub/challenges/detect.py` | 挑战页检测与 `ChallengeDetectedError` |
| `src/spiderhub/models/items.py` | `Actress` / `Work` pydantic 模型 |
| `src/spiderhub/pipelines/base.py` | Pipeline 协议 |
| `src/spiderhub/pipelines/mysql.py` | MySQL upsert |
| `src/spiderhub/pipelines/null.py` | dry-run 空 pipeline |
| `src/spiderhub/spiders/missav/parse.py` | HTML 解析纯函数 |
| `src/spiderhub/spiders/missav/spider.py` | `MissavActressSpider` |
| `src/spiderhub/spiders/missav/__init__.py` | 导出并触发注册 |
| `src/spiderhub/cli.py` | list/run/--start-url/--dry-run 接线 |
| `scripts/sql/missav_schema.sql` | 建表 DDL |
| `tests/fixtures/missav/*.html` | 解析夹具 |
| `tests/unit/test_*.py` | 各层单测 |
| `README.md` / `AGENTS.md` | 同步命令与依赖约定 |

---

### Task 1: 依赖、gitignore 与 Settings

**Files:**
- Modify: `pyproject.toml`
- Modify: `.gitignore`
- Modify: `.env.example`
- Create: `config.example.toml`
- Create: `src/spiderhub/core/settings.py`
- Create: `tests/unit/test_settings.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `Settings` dataclass with fields: `mysql_host: str`, `mysql_port: int`, `mysql_user: str`, `mysql_password: str`, `mysql_database: str`, `obey_robots: bool`, `request_delay_seconds: float`, `http_timeout_seconds: float`, `http_max_retries: int`
  - `load_settings(*, env: Mapping[str, str] | None = None, config_path: Path | None = None, cli_overrides: Mapping[str, object] | None = None) -> Settings`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_settings.py
from __future__ import annotations

from pathlib import Path

from spiderhub.core.settings import load_settings


def test_defaults_and_env_override(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "SPIDERHUB_MYSQL_HOST": "db.local",
            "SPIDERHUB_MYSQL_PORT": "3307",
            "SPIDERHUB_MYSQL_USER": "u",
            "SPIDERHUB_MYSQL_PASSWORD": "p",
            "SPIDERHUB_MYSQL_DATABASE": "hub",
            "SPIDERHUB_OBEY_ROBOTS": "false",
            "SPIDERHUB_REQUEST_DELAY_SECONDS": "0.5",
        },
        config_path=tmp_path / "missing.toml",
    )
    assert settings.mysql_host == "db.local"
    assert settings.mysql_port == 3307
    assert settings.mysql_user == "u"
    assert settings.mysql_password == "p"
    assert settings.mysql_database == "hub"
    assert settings.obey_robots is False
    assert settings.request_delay_seconds == 0.5


def test_cli_overrides_env(tmp_path: Path) -> None:
    settings = load_settings(
        env={"SPIDERHUB_MYSQL_HOST": "from-env"},
        config_path=tmp_path / "missing.toml",
        cli_overrides={"mysql_host": "from-cli"},
    )
    assert settings.mysql_host == "from-cli"


def test_toml_file_values(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        '[mysql]\nhost = "toml-host"\nport = 3308\nuser = "tu"\n'
        'password = "tp"\ndatabase = "tdb"\n'
        "[crawl]\nobey_robots = false\nrequest_delay_seconds = 2.0\n",
        encoding="utf-8",
    )
    settings = load_settings(env={}, config_path=path)
    assert settings.mysql_host == "toml-host"
    assert settings.mysql_port == 3308
    assert settings.request_delay_seconds == 2.0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py -v`  
Expected: FAIL（`spiderhub.core.settings` 不存在）

- [ ] **Step 3: Add dependencies and ignore local config**

在 `pyproject.toml` 的 `dependencies` 设为：

```toml
dependencies = [
  "httpx>=0.28",
  "pydantic>=2.10",
  "selectolax>=0.3",
  "PyMySQL>=1.1",
  "python-dotenv>=1.0",
]
```

在 `.gitignore` 追加：

```gitignore
config.local.toml
```

更新 `.env.example`：

```bash
SPIDERHUB_LOG_LEVEL=INFO
SPIDERHUB_MYSQL_HOST=127.0.0.1
SPIDERHUB_MYSQL_PORT=3306
SPIDERHUB_MYSQL_USER=
SPIDERHUB_MYSQL_PASSWORD=
SPIDERHUB_MYSQL_DATABASE=spiderhub
SPIDERHUB_OBEY_ROBOTS=true
SPIDERHUB_REQUEST_DELAY_SECONDS=1.0
```

创建 `config.example.toml`：

```toml
[mysql]
host = "127.0.0.1"
port = 3306
user = ""
password = ""
database = "spiderhub"

[crawl]
obey_robots = true
request_delay_seconds = 1.0
```

Run: `uv sync`

- [ ] **Step 4: Implement `settings.py`**

```python
# src/spiderhub/core/settings.py
from __future__ import annotations

import os
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Settings:
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = ""
    mysql_password: str = ""
    mysql_database: str = "spiderhub"
    obey_robots: bool = True
    request_delay_seconds: float = 1.0
    http_timeout_seconds: float = 30.0
    http_max_retries: int = 3


def _as_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("rb") as fh:
        return tomllib.load(fh)


def load_settings(
    *,
    env: Mapping[str, str] | None = None,
    config_path: Path | None = None,
    cli_overrides: Mapping[str, object] | None = None,
) -> Settings:
    environ = env if env is not None else os.environ
    path = config_path if config_path is not None else Path("config.local.toml")
    raw = _load_toml(path)
    mysql = raw.get("mysql", {}) if isinstance(raw.get("mysql"), dict) else {}
    crawl = raw.get("crawl", {}) if isinstance(raw.get("crawl"), dict) else {}

    data: dict[str, object] = {
        "mysql_host": mysql.get("host", "127.0.0.1"),
        "mysql_port": int(mysql.get("port", 3306)),
        "mysql_user": str(mysql.get("user", "")),
        "mysql_password": str(mysql.get("password", "")),
        "mysql_database": str(mysql.get("database", "spiderhub")),
        "obey_robots": _as_bool(crawl.get("obey_robots"), True),
        "request_delay_seconds": float(crawl.get("request_delay_seconds", 1.0)),
        "http_timeout_seconds": 30.0,
        "http_max_retries": 3,
    }

    env_map = {
        "mysql_host": "SPIDERHUB_MYSQL_HOST",
        "mysql_port": "SPIDERHUB_MYSQL_PORT",
        "mysql_user": "SPIDERHUB_MYSQL_USER",
        "mysql_password": "SPIDERHUB_MYSQL_PASSWORD",
        "mysql_database": "SPIDERHUB_MYSQL_DATABASE",
        "obey_robots": "SPIDERHUB_OBEY_ROBOTS",
        "request_delay_seconds": "SPIDERHUB_REQUEST_DELAY_SECONDS",
    }
    for field, key in env_map.items():
        if key in environ and environ[key] != "":
            if field in {"mysql_port", "http_max_retries"}:
                data[field] = int(environ[key])
            elif field in {"request_delay_seconds", "http_timeout_seconds"}:
                data[field] = float(environ[key])
            elif field == "obey_robots":
                data[field] = _as_bool(environ[key], True)
            else:
                data[field] = environ[key]

    if cli_overrides:
        for key, value in cli_overrides.items():
            if value is not None and key in data:
                data[key] = value

    return Settings(**data)  # type: ignore[arg-type]
```

在 `src/spiderhub/core/__init__.py` 可保持简短 docstring，不必再导出全部符号。

- [ ] **Step 5: Run tests**

Run: `uv run pytest tests/unit/test_settings.py -v`  
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml uv.lock .gitignore .env.example config.example.toml \
  src/spiderhub/core/settings.py tests/unit/test_settings.py
git commit -m "$(cat <<'EOF'
feat: add settings loading and runtime dependencies

Enable MySQL/crawl config from toml, env, and CLI overrides for the missav slice.
EOF
)"
```

---

### Task 2: pydantic 模型 `Actress` / `Work`

**Files:**
- Create: `src/spiderhub/models/items.py`
- Modify: `src/spiderhub/models/__init__.py`
- Create: `tests/unit/test_models.py`

**Interfaces:**
- Consumes: 无
- Produces:
  - `class Actress(BaseModel)` fields: `slug: str`, `name: str`, `profile_url: HttpUrl | str`, `name_ja: str | None = None`, `name_en: str | None = None`, `cover_url: str | None = None`, `bio: str | None = None`, `source: Literal["missav"] = "missav"`
  - `class Work(BaseModel)` fields: `code: str`, `title: str`, `detail_url: str`, `description: str | None = None`, `release_date: date | None = None`, `duration_seconds: int | None = None`, `maker: str | None = None`, `label: str | None = None`, `series: str | None = None`, `cover_url: str | None = None`, `actress_slugs: list[str] = []`, `actress_names: list[str] = []`, `tags: list[str] = []`, `source: Literal["missav"] = "missav"`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_models.py
from __future__ import annotations

import pytest
from pydantic import ValidationError

from spiderhub.models.items import Actress, Work


def test_actress_and_work_minimal() -> None:
    actress = Actress(
        slug="kitano-mina",
        name="北野未奈",
        profile_url="https://missav.ws/cn/actresses/kitano-mina",
    )
    work = Work(
        code="ABC-123",
        title="Sample",
        detail_url="https://missav.ws/cn/abc-123",
        actress_slugs=["kitano-mina"],
        tags=["solo"],
    )
    assert actress.source == "missav"
    assert work.code == "ABC-123"
    assert work.tags == ["solo"]


def test_work_requires_code() -> None:
    with pytest.raises(ValidationError):
        Work(code="", title="t", detail_url="https://missav.ws/cn/x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_models.py -v`  
Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement models**

```python
# src/spiderhub/models/items.py
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Actress(BaseModel):
    slug: str
    name: str
    profile_url: str
    name_ja: str | None = None
    name_en: str | None = None
    cover_url: str | None = None
    bio: str | None = None
    source: Literal["missav"] = "missav"

    @field_validator("slug", "name", "profile_url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class Work(BaseModel):
    code: str
    title: str
    detail_url: str
    description: str | None = None
    release_date: date | None = None
    duration_seconds: int | None = None
    maker: str | None = None
    label: str | None = None
    series: str | None = None
    cover_url: str | None = None
    actress_slugs: list[str] = Field(default_factory=list)
    actress_names: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: Literal["missav"] = "missav"

    @field_validator("code", "title", "detail_url")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value
```

`src/spiderhub/models/__init__.py`：

```python
"""Shared pydantic models."""

from spiderhub.models.items import Actress, Work

__all__ = ["Actress", "Work"]
```

- [ ] **Step 4: Run tests**

Run: `uv run pytest tests/unit/test_models.py -v`  
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/spiderhub/models/items.py src/spiderhub/models/__init__.py tests/unit/test_models.py
git commit -m "$(cat <<'EOF'
feat: add Actress and Work pydantic models

Define validated missav metadata shapes before parse and MySQL pipelines.
EOF
)"
```

---

### Task 3: 挑战页检测 stub

**Files:**
- Create: `src/spiderhub/challenges/detect.py`
- Create: `tests/unit/test_challenge_detect.py`
- Create: `tests/fixtures/challenges/cloudflare_challenge.html`

**Interfaces:**
- Consumes: 无
- Produces:
  - `class ChallengeDetectedError(Exception)` with `url: str`, `status_code: int`, `reason: str`
  - `detect_challenge(*, url: str, status_code: int, text: str, headers: Mapping[str, str] | None = None) -> str | None`  
    返回挑战原因字符串；无挑战返回 `None`

- [ ] **Step 1: Write fixture + failing test**

`tests/fixtures/challenges/cloudflare_challenge.html`：

```html
<!doctype html>
<html><head><title>Just a moment...</title></head>
<body><div id="challenge-running">Enable JavaScript and cookies to continue</div></body>
</html>
```

```python
# tests/unit/test_challenge_detect.py
from __future__ import annotations

from pathlib import Path

from spiderhub.challenges.detect import detect_challenge

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "challenges"


def test_detects_cloudflare_challenge_page() -> None:
    html = (FIXTURES / "cloudflare_challenge.html").read_text(encoding="utf-8")
    reason = detect_challenge(url="https://missav.ws/x", status_code=403, text=html)
    assert reason is not None
    assert "cloudflare" in reason.lower() or "challenge" in reason.lower()


def test_normal_page_is_clean() -> None:
    reason = detect_challenge(
        url="https://missav.ws/x",
        status_code=200,
        text="<html><title>北野未奈</title><body>ok</body></html>",
    )
    assert reason is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_challenge_detect.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement detector**

```python
# src/spiderhub/challenges/detect.py
from __future__ import annotations

from collections.abc import Mapping

_MARKERS = (
    "just a moment...",
    "cf-browser-verification",
    "challenge-platform",
    "cdn-cgi/challenge",
    "enable javascript and cookies to continue",
    "attention required! | cloudflare",
)


class ChallengeDetectedError(Exception):
    def __init__(self, url: str, status_code: int, reason: str) -> None:
        self.url = url
        self.status_code = status_code
        self.reason = reason
        super().__init__(f"challenge detected for {url}: {reason} ({status_code})")


def detect_challenge(
    *,
    url: str,
    status_code: int,
    text: str,
    headers: Mapping[str, str] | None = None,
) -> str | None:
    del url, headers  # reserved for richer heuristics later
    lowered = text.lower()
    if status_code in {403, 503} and any(m in lowered for m in _MARKERS):
        return "cloudflare_or_bot_challenge"
    if any(m in lowered for m in _MARKERS):
        return "challenge_markers_in_body"
    return None
```

- [ ] **Step 4: Run tests + Commit**

Run: `uv run pytest tests/unit/test_challenge_detect.py -v`  
Expected: PASS

```bash
git add src/spiderhub/challenges/detect.py \
  tests/unit/test_challenge_detect.py \
  tests/fixtures/challenges/cloudflare_challenge.html
git commit -m "$(cat <<'EOF'
feat: add challenge page detection stub

Classify Cloudflare-like challenge HTML so fetchers do not treat it as content.
EOF
)"
```

---

### Task 4: L1 httpx Fetcher

**Files:**
- Create: `src/spiderhub/downloaders/base.py`
- Create: `src/spiderhub/downloaders/httpx_fetcher.py`
- Create: `tests/unit/test_httpx_fetcher.py`

**Interfaces:**
- Consumes: `Settings.http_timeout_seconds`, `Settings.http_max_retries`, `Settings.request_delay_seconds`, `detect_challenge`, `ChallengeDetectedError`
- Produces:
  - `@dataclass class FetchedResponse`: `url: str`, `status_code: int`, `text: str`, `headers: dict[str, str]`
  - `class HttpxFetcher`:  
    - `async def __aenter__/__aexit__`  
    - `async def fetch(self, url: str) -> FetchedResponse`  
    行为：延时 → GET → 挑战检测 → 非 2xx/网络错误有限重试 → 耗尽抛异常

- [ ] **Step 1: Write the failing test（MockTransport）**

```python
# tests/unit/test_httpx_fetcher.py
from __future__ import annotations

import httpx
import pytest

from spiderhub.challenges.detect import ChallengeDetectedError
from spiderhub.core.settings import Settings
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher


@pytest.mark.asyncio
async def test_fetch_ok() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>ok</html>", request=request)

    transport = httpx.MockTransport(handler)
    settings = Settings(request_delay_seconds=0.0, http_max_retries=1)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        resp = await fetcher.fetch("https://missav.ws/cn/x")
    assert resp.status_code == 200
    assert "ok" in resp.text


@pytest.mark.asyncio
async def test_fetch_challenge_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            text="<title>Just a moment...</title>challenge-platform",
            request=request,
        )

    transport = httpx.MockTransport(handler)
    settings = Settings(request_delay_seconds=0.0, http_max_retries=1)
    async with HttpxFetcher(settings, transport=transport) as fetcher:
        with pytest.raises(ChallengeDetectedError):
            await fetcher.fetch("https://missav.ws/cn/x")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_httpx_fetcher.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement fetcher**

```python
# src/spiderhub/downloaders/base.py
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class FetchedResponse:
    url: str
    status_code: int
    text: str
    headers: dict[str, str] = field(default_factory=dict)
```

```python
# src/spiderhub/downloaders/httpx_fetcher.py
from __future__ import annotations

import asyncio
import logging

import httpx

from spiderhub.challenges.detect import ChallengeDetectedError, detect_challenge
from spiderhub.core.settings import Settings
from spiderhub.downloaders.base import FetchedResponse

logger = logging.getLogger(__name__)


class HttpxFetcher:
    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._settings = settings
        self._transport = transport
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> HttpxFetcher:
        self._client = httpx.AsyncClient(
            transport=self._transport,
            timeout=self._settings.http_timeout_seconds,
            follow_redirects=True,
            headers={
                "User-Agent": "SpiderHub/0.1 (+https://github.com/local/SpiderHub)"
            },
        )
        return self

    async def __aexit__(self, *args: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def fetch(self, url: str) -> FetchedResponse:
        if self._client is None:
            raise RuntimeError("HttpxFetcher must be used as async context manager")
        delay = self._settings.request_delay_seconds
        retries = self._settings.http_max_retries
        last_exc: Exception | None = None
        for attempt in range(1, retries + 1):
            if delay > 0:
                await asyncio.sleep(delay)
            try:
                response = await self._client.get(url)
            except httpx.HTTPError as exc:
                last_exc = exc
                logger.warning(
                    "fetch error url=%s attempt=%s err=%s", url, attempt, exc
                )
                continue
            headers = {k: v for k, v in response.headers.items()}
            reason = detect_challenge(
                url=str(response.url),
                status_code=response.status_code,
                text=response.text,
                headers=headers,
            )
            if reason:
                raise ChallengeDetectedError(
                    str(response.url), response.status_code, reason
                )
            if 200 <= response.status_code < 300:
                return FetchedResponse(
                    url=str(response.url),
                    status_code=response.status_code,
                    text=response.text,
                    headers=headers,
                )
            last_exc = httpx.HTTPStatusError(
                f"status {response.status_code}",
                request=response.request,
                response=response,
            )
            logger.warning(
                "bad status url=%s status=%s attempt=%s",
                url,
                response.status_code,
                attempt,
            )
        assert last_exc is not None
        raise last_exc
```

- [ ] **Step 4: Run tests + Commit**

Run: `uv run pytest tests/unit/test_httpx_fetcher.py -v`  
Expected: PASS

```bash
git add src/spiderhub/downloaders/base.py \
  src/spiderhub/downloaders/httpx_fetcher.py \
  tests/unit/test_httpx_fetcher.py
git commit -m "$(cat <<'EOF'
feat: add L1 httpx fetcher with challenge checks

Provide delayed, retried GETs that fail fast on bot-challenge pages.
EOF
)"
```

---

### Task 5: Spider 基类与注册表

**Files:**
- Create: `src/spiderhub/core/spider.py`
- Create: `src/spiderhub/core/registry.py`
- Create: `tests/unit/test_registry.py`

**Interfaces:**
- Consumes: `FetchedResponse`, `Actress | Work`
- Produces:
  - `class Spider(ABC)` class attrs: `name: str`, `allowed_domains: tuple[str, ...]`, `fetch_mode: str = "auto"`, `obey_robots: bool = True`；方法：`start_urls(self) -> list[str]`；`async def parse(self, response: FetchedResponse) -> AsyncIterator[Actress | Work | str]`（可 yield 模型或后续 URL）
  - `register_spider(spider_cls: type[Spider]) -> type[Spider]`
  - `get_spider(name: str) -> type[Spider]`
  - `list_spiders() -> list[str]`
  - `discover_builtin_spiders() -> None`（import `spiderhub.spiders.missav` 以触发注册；Task 10 前可空实现只 import 包）

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_registry.py
from __future__ import annotations

import pytest

from spiderhub.core.registry import get_spider, list_spiders, register_spider
from spiderhub.core.spider import Spider
from spiderhub.downloaders.base import FetchedResponse


class _DemoSpider(Spider):
    name = "demo"
    allowed_domains = ("example.com",)

    def start_urls(self) -> list[str]:
        return ["https://example.com/"]

    async def parse(self, response: FetchedResponse):
        if False:
            yield response.url


def test_register_and_list() -> None:
    register_spider(_DemoSpider)
    assert "demo" in list_spiders()
    assert get_spider("demo") is _DemoSpider


def test_unknown_spider() -> None:
    with pytest.raises(KeyError):
        get_spider("missing-spider-xyz")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_registry.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement spider + registry**

```python
# src/spiderhub/core/spider.py
from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

from spiderhub.downloaders.base import FetchedResponse
from spiderhub.models.items import Actress, Work

ParseItem = Actress | Work | str


class Spider(ABC):
    name: str
    allowed_domains: tuple[str, ...]
    fetch_mode: str = "auto"
    obey_robots: bool = True

    @abstractmethod
    def start_urls(self) -> list[str]:
        raise NotImplementedError

    @abstractmethod
    async def parse(self, response: FetchedResponse) -> AsyncIterator[ParseItem]:
        raise NotImplementedError
        yield  # pragma: no cover
```

```python
# src/spiderhub/core/registry.py
from __future__ import annotations

from spiderhub.core.spider import Spider

_REGISTRY: dict[str, type[Spider]] = {}


def register_spider(spider_cls: type[Spider]) -> type[Spider]:
    if not getattr(spider_cls, "name", None):
        raise ValueError("spider class must define name")
    _REGISTRY[spider_cls.name] = spider_cls
    return spider_cls


def get_spider(name: str) -> type[Spider]:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown spider: {name}") from exc


def list_spiders() -> list[str]:
    return sorted(_REGISTRY)


def discover_builtin_spiders() -> None:
    # Imported for side-effect registration; expand as spiders are added.
    from spiderhub.spiders import missav as _missav  # noqa: F401
```

注意：Task 5 提交前若 `spiderhub.spiders.missav` 尚不存在，先让 `discover_builtin_spiders` 为空实现：

```python
def discover_builtin_spiders() -> None:
    return None
```

并在 Task 10 改为真实 import。本 Task 的测试只覆盖手动 `register_spider`。

- [ ] **Step 4: Run tests + Commit**

Run: `uv run pytest tests/unit/test_registry.py -v`  
Expected: PASS

```bash
git add src/spiderhub/core/spider.py src/spiderhub/core/registry.py tests/unit/test_registry.py
git commit -m "$(cat <<'EOF'
feat: add spider base class and in-memory registry

Establish registration APIs used by CLI list/run for site spiders.
EOF
)"
```

---

### Task 6: Runner（抓取循环 + pipeline 钩子）

**Files:**
- Create: `src/spiderhub/pipelines/base.py`
- Create: `src/spiderhub/pipelines/null.py`
- Create: `src/spiderhub/core/runner.py`
- Create: `tests/unit/test_runner.py`

**Interfaces:**
- Consumes: `Spider`, `HttpxFetcher`, `Settings`, `Actress`, `Work`
- Produces:
  - `class Pipeline(Protocol)`: `async def open(self) -> None`, `async def process_item(self, item: Actress | Work) -> None`, `async def close(self) -> None`
  - `class NullPipeline`（no-op，用于 dry-run）
  - `class RunResult` dataclass: `items_ok: int`, `items_failed: int`, `urls_failed: int`
  - `async def run_spider(spider: Spider, *, fetcher: HttpxFetcher, pipeline: Pipeline, start_urls: list[str] | None = None) -> RunResult`  
    行为：BFS/队列处理 URL；`parse` yield `str` 则入队（同域）；yield 模型则交 pipeline；详情失败计数并继续；挑战/耗尽失败计 `urls_failed`

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_runner.py
from __future__ import annotations

import httpx
import pytest

from spiderhub.core.runner import run_spider
from spiderhub.core.settings import Settings
from spiderhub.core.spider import Spider
from spiderhub.downloaders.base import FetchedResponse
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.models.items import Work
from spiderhub.pipelines.null import NullPipeline


class _ListSpider(Spider):
    name = "list_demo"
    allowed_domains = ("example.com",)

    def start_urls(self) -> list[str]:
        return ["https://example.com/list"]

    async def parse(self, response: FetchedResponse):
        if response.url.endswith("/list"):
            yield "https://example.com/a"
            yield Work(
                code="A-1",
                title="One",
                detail_url="https://example.com/a",
            )
        else:
            yield Work(
                code="A-1",
                title="One detailed",
                detail_url=response.url,
                description="bio",
            )


@pytest.mark.asyncio
async def test_runner_follows_url_and_items() -> None:
    pages = {
        "https://example.com/list": "<html>list</html>",
        "https://example.com/a": "<html>detail</html>",
    }

    def handler(request: httpx.Request) -> httpx.Response:
        body = pages[str(request.url)]
        return httpx.Response(200, text=body, request=request)

    settings = Settings(request_delay_seconds=0.0, http_max_retries=1)
    async with HttpxFetcher(
        settings, transport=httpx.MockTransport(handler)
    ) as fetcher:
        result = await run_spider(
            _ListSpider(), fetcher=fetcher, pipeline=NullPipeline()
        )
    assert result.items_ok >= 1
    assert result.urls_failed == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_runner.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement pipelines + runner**

```python
# src/spiderhub/pipelines/base.py
from __future__ import annotations

from typing import Protocol

from spiderhub.models.items import Actress, Work


class Pipeline(Protocol):
    async def open(self) -> None: ...
    async def process_item(self, item: Actress | Work) -> None: ...
    async def close(self) -> None: ...
```

```python
# src/spiderhub/pipelines/null.py
from __future__ import annotations

import logging

from spiderhub.models.items import Actress, Work

logger = logging.getLogger(__name__)


class NullPipeline:
    async def open(self) -> None:
        return None

    async def process_item(self, item: Actress | Work) -> None:
        logger.info("dry-run item type=%s", type(item).__name__)

    async def close(self) -> None:
        return None
```

```python
# src/spiderhub/core/runner.py
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from urllib.parse import urlparse

from spiderhub.core.spider import Spider
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.models.items import Actress, Work
from spiderhub.pipelines.base import Pipeline

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class RunResult:
    items_ok: int = 0
    items_failed: int = 0
    urls_failed: int = 0


def _allowed(url: str, domains: tuple[str, ...]) -> bool:
    host = urlparse(url).hostname or ""
    return any(host == d or host.endswith(f".{d}") for d in domains)


async def run_spider(
    spider: Spider,
    *,
    fetcher: HttpxFetcher,
    pipeline: Pipeline,
    start_urls: list[str] | None = None,
) -> RunResult:
    result = RunResult()
    queue: deque[str] = deque(start_urls or spider.start_urls())
    seen: set[str] = set()
    await pipeline.open()
    try:
        while queue:
            url = queue.popleft()
            if url in seen:
                continue
            seen.add(url)
            if not _allowed(url, spider.allowed_domains):
                logger.warning("skip disallowed url=%s", url)
                continue
            try:
                response = await fetcher.fetch(url)
            except Exception as exc:  # noqa: BLE001 — counted failure boundary
                result.urls_failed += 1
                logger.warning("url failed url=%s err=%s", url, exc)
                continue
            async for item in spider.parse(response):
                if isinstance(item, str):
                    if item not in seen and _allowed(item, spider.allowed_domains):
                        queue.append(item)
                    continue
                if isinstance(item, (Actress, Work)):
                    try:
                        await pipeline.process_item(item)
                        result.items_ok += 1
                    except Exception as exc:  # noqa: BLE001
                        result.items_failed += 1
                        logger.warning("item failed err=%s", exc)
    finally:
        await pipeline.close()
    return result
```

本切片 robots：若 `spider.obey_robots` 且 settings 开启，可在 `run_spider` 开头用 `urllib.robotparser` 过滤 start URL；为控范围，**最小实现**为记录日志占位函数 `_robots_allowed(url) -> True`，并在代码注释标明后续可接真解析。若实现成本低（标准库），可在同 Task 内用 `RobotFileParser` 拉 `https://domain/robots.txt`（仍经 fetcher）；拉失败则默认允许并打警告。推荐最小：先恒 True + warning once「robots check not fully wired」，但规格要求默认遵守——因此实现如下辅助：

```python
async def _robots_allowed(fetcher: HttpxFetcher, url: str, enabled: bool) -> bool:
    if not enabled:
        return True
    parsed = urlparse(url)
    robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
    try:
        resp = await fetcher.fetch(robots_url)
    except Exception:  # noqa: BLE001
        logger.warning("robots.txt fetch failed; allowing url=%s", url)
        return True
    from urllib.robotparser import RobotFileParser

    rp = RobotFileParser()
    rp.parse(resp.text.splitlines())
    return rp.can_fetch("SpiderHub", url)
```

在处理每个业务 URL 前调用；`robots.txt` 自身 URL 不要再递归检查。

- [ ] **Step 4: Run tests + Commit**

Run: `uv run pytest tests/unit/test_runner.py -v`  
Expected: PASS

```bash
git add src/spiderhub/pipelines/base.py src/spiderhub/pipelines/null.py \
  src/spiderhub/core/runner.py tests/unit/test_runner.py
git commit -m "$(cat <<'EOF'
feat: add async spider runner and null pipeline

Drive URL queue parsing into pipelines with per-URL failure isolation.
EOF
)"
```

---

### Task 7: MySQL schema SQL + upsert pipeline

**Files:**
- Create: `scripts/sql/missav_schema.sql`
- Create: `src/spiderhub/pipelines/mysql.py`
- Create: `tests/unit/test_mysql_pipeline.py`

**Interfaces:**
- Consumes: `Settings` MySQL 字段, `Actress`, `Work`
- Produces:
  - `class MySQLPipeline`:  
    - `__init__(self, settings: Settings, *, connect=None)` — `connect` 可注入用于测试  
    - `async def open/close/process_item`  
    - 内部同步写库经 `asyncio.to_thread`  
    - `upsert_actress(conn, actress)`, `upsert_work(conn, work)` 纯函数便于单测

- [ ] **Step 1: Write schema file**

```sql
-- scripts/sql/missav_schema.sql
CREATE DATABASE IF NOT EXISTS spiderhub
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;

USE spiderhub;

CREATE TABLE IF NOT EXISTS actresses (
  id BIGINT NOT NULL AUTO_INCREMENT,
  slug VARCHAR(255) NOT NULL,
  name VARCHAR(255) NOT NULL,
  name_ja VARCHAR(255) NULL,
  name_en VARCHAR(255) NULL,
  profile_url VARCHAR(1024) NOT NULL,
  cover_url VARCHAR(1024) NULL,
  bio TEXT NULL,
  source VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_actresses_slug (slug)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS works (
  id BIGINT NOT NULL AUTO_INCREMENT,
  code VARCHAR(128) NOT NULL,
  title VARCHAR(512) NOT NULL,
  description TEXT NULL,
  release_date DATE NULL,
  duration_seconds INT NULL,
  maker VARCHAR(255) NULL,
  label VARCHAR(255) NULL,
  series VARCHAR(255) NULL,
  cover_url VARCHAR(1024) NULL,
  detail_url VARCHAR(1024) NOT NULL,
  source VARCHAR(64) NOT NULL,
  created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uq_works_code (code),
  UNIQUE KEY uq_works_detail_url (detail_url)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS tags (
  id BIGINT NOT NULL AUTO_INCREMENT,
  name VARCHAR(255) NOT NULL,
  source VARCHAR(64) NOT NULL,
  PRIMARY KEY (id),
  UNIQUE KEY uq_tags_name (name)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS work_actresses (
  work_id BIGINT NOT NULL,
  actress_id BIGINT NOT NULL,
  PRIMARY KEY (work_id, actress_id),
  CONSTRAINT fk_wa_work FOREIGN KEY (work_id) REFERENCES works (id),
  CONSTRAINT fk_wa_actress FOREIGN KEY (actress_id) REFERENCES actresses (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

CREATE TABLE IF NOT EXISTS work_tags (
  work_id BIGINT NOT NULL,
  tag_id BIGINT NOT NULL,
  PRIMARY KEY (work_id, tag_id),
  CONSTRAINT fk_wt_work FOREIGN KEY (work_id) REFERENCES works (id),
  CONSTRAINT fk_wt_tag FOREIGN KEY (tag_id) REFERENCES tags (id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;
```

- [ ] **Step 2: Write failing pipeline unit test（假连接记录 SQL）**

```python
# tests/unit/test_mysql_pipeline.py
from __future__ import annotations

from spiderhub.models.items import Actress, Work
from spiderhub.pipelines.mysql import upsert_actress_sql, upsert_work_sql


def test_upsert_actress_sql_uses_slug_key() -> None:
    sql, params = upsert_actress_sql(
        Actress(slug="a", name="A", profile_url="https://missav.ws/cn/actresses/a")
    )
    assert "INSERT INTO actresses" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params[0] == "a"


def test_upsert_work_sql_uses_code_key() -> None:
    sql, params = upsert_work_sql(
        Work(code="ABC-123", title="T", detail_url="https://missav.ws/cn/abc-123")
    )
    assert "INSERT INTO works" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params[0] == "ABC-123"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_mysql_pipeline.py -v`  
Expected: FAIL

- [ ] **Step 4: Implement MySQL pipeline helpers + class**

写入完整 `src/spiderhub/pipelines/mysql.py`：

```python
from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pymysql
from pymysql.connections import Connection

from spiderhub.core.settings import Settings
from spiderhub.models.items import Actress, Work

logger = logging.getLogger(__name__)
ConnectFn = Callable[..., Connection]


def upsert_actress_sql(actress: Actress) -> tuple[str, tuple[object, ...]]:
    sql = """
    INSERT INTO actresses (
      slug, name, name_ja, name_en, profile_url, cover_url, bio, source
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      name=VALUES(name),
      name_ja=VALUES(name_ja),
      name_en=VALUES(name_en),
      profile_url=VALUES(profile_url),
      cover_url=VALUES(cover_url),
      bio=VALUES(bio),
      source=VALUES(source)
    """
    params = (
        actress.slug,
        actress.name,
        actress.name_ja,
        actress.name_en,
        actress.profile_url,
        actress.cover_url,
        actress.bio,
        actress.source,
    )
    return sql, params


def upsert_work_sql(work: Work) -> tuple[str, tuple[object, ...]]:
    sql = """
    INSERT INTO works (
      code, title, description, release_date, duration_seconds,
      maker, label, series, cover_url, detail_url, source
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      title=VALUES(title),
      description=VALUES(description),
      release_date=VALUES(release_date),
      duration_seconds=VALUES(duration_seconds),
      maker=VALUES(maker),
      label=VALUES(label),
      series=VALUES(series),
      cover_url=VALUES(cover_url),
      detail_url=VALUES(detail_url),
      source=VALUES(source)
    """
    params = (
        work.code,
        work.title,
        work.description,
        work.release_date,
        work.duration_seconds,
        work.maker,
        work.label,
        work.series,
        work.cover_url,
        work.detail_url,
        work.source,
    )
    return sql, params


def _upsert_tag(cursor: Any, name: str, source: str) -> int:
    cursor.execute(
        """
        INSERT INTO tags (name, source) VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE source=VALUES(source)
        """,
        (name, source),
    )
    cursor.execute("SELECT id FROM tags WHERE name=%s", (name,))
    row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _actress_id_by_slug(cursor: Any, slug: str) -> int | None:
    cursor.execute("SELECT id FROM actresses WHERE slug=%s", (slug,))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def sync_work_relations(
    cursor: Any,
    work_id: int,
    *,
    actress_slugs: list[str],
    tags: list[str],
    source: str,
) -> None:
    cursor.execute("DELETE FROM work_tags WHERE work_id=%s", (work_id,))
    cursor.execute("DELETE FROM work_actresses WHERE work_id=%s", (work_id,))
    for tag in tags:
        tag_id = _upsert_tag(cursor, tag, source)
        cursor.execute(
            "INSERT INTO work_tags (work_id, tag_id) VALUES (%s, %s)",
            (work_id, tag_id),
        )
    for slug in actress_slugs:
        actress_id = _actress_id_by_slug(cursor, slug)
        if actress_id is None:
            logger.warning("skip work_actress missing slug=%s", slug)
            continue
        cursor.execute(
            "INSERT INTO work_actresses (work_id, actress_id) VALUES (%s, %s)",
            (work_id, actress_id),
        )


class MySQLPipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        connect: ConnectFn | None = None,
    ) -> None:
        self._settings = settings
        self._connect = connect or pymysql.connect
        self._conn: Connection | None = None

    def _connect_sync(self) -> Connection:
        return self._connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password,
            database=self._settings.mysql_database,
            charset="utf8mb4",
            autocommit=False,
        )

    async def open(self) -> None:
        self._conn = await asyncio.to_thread(self._connect_sync)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    def _process_sync(self, item: Actress | Work) -> None:
        assert self._conn is not None
        try:
            with self._conn.cursor() as cursor:
                if isinstance(item, Actress):
                    sql, params = upsert_actress_sql(item)
                    cursor.execute(sql, params)
                else:
                    sql, params = upsert_work_sql(item)
                    cursor.execute(sql, params)
                    cursor.execute("SELECT id FROM works WHERE code=%s", (item.code,))
                    row = cursor.fetchone()
                    assert row is not None
                    sync_work_relations(
                        cursor,
                        int(row[0]),
                        actress_slugs=item.actress_slugs,
                        tags=item.tags,
                        source=item.source,
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    async def process_item(self, item: Actress | Work) -> None:
        await asyncio.to_thread(self._process_sync, item)
```

- [ ] **Step 5: Run tests + Commit**

Run: `uv run pytest tests/unit/test_mysql_pipeline.py -v`  
Expected: PASS

```bash
git add scripts/sql/missav_schema.sql src/spiderhub/pipelines/mysql.py \
  tests/unit/test_mysql_pipeline.py
git commit -m "$(cat <<'EOF'
feat: add MySQL schema and upsert pipeline helpers

Persist actress/work metadata by slug and code without auto-migrations.
EOF
)"
```

---

### Task 8: MissAV 女优列表页解析（fixture 先行）

**Files:**
- Create: `tests/fixtures/missav/actress_list_page1.html`
- Create: `tests/fixtures/missav/actress_list_page2.html`
- Create: `tests/fixtures/missav/actress_list_empty.html`
- Create: `src/spiderhub/spiders/missav/parse.py`
- Create: `tests/unit/test_missav_parse_list.py`

**Interfaces:**
- Consumes: HTML 字符串 + 当前页 URL
- Produces（纯函数）：
  - `parse_actress_list(html: str, page_url: str) -> ActressListPage`  
    其中 `@dataclass ActressListPage`: `actress: Actress`, `detail_urls: list[str]`, `next_page_url: str | None`

**Fixture 约定（解析器只认这些结构；若真站 DOM 不同，同步改 fixture+选择器）：**

```html
<!-- actress_list_page1.html -->
<!doctype html>
<html lang="zh">
<head><title>北野未奈 | MissAV</title></head>
<body>
  <h1 data-testid="actress-name">北野未奈</h1>
  <img data-testid="actress-cover" src="https://cdn.example/cover.jpg" alt="北野未奈"/>
  <p data-testid="actress-bio">简介文本</p>
  <div data-testid="work-card">
    <a data-testid="work-link" href="/cn/abc-001">
      <img src="https://cdn.example/t1.jpg" alt="ABC-001 标题一"/>
    </a>
  </div>
  <div data-testid="work-card">
    <a data-testid="work-link" href="/cn/abc-002">
      <img src="https://cdn.example/t2.jpg" alt="ABC-002 标题二"/>
    </a>
  </div>
  <a data-testid="next-page" href="?page=2">下一页</a>
</body>
</html>
```

```html
<!-- actress_list_page2.html：无下一页，一张卡片 -->
<!doctype html>
<html><body>
  <h1 data-testid="actress-name">北野未奈</h1>
  <div data-testid="work-card">
    <a data-testid="work-link" href="/cn/abc-003"><img alt="ABC-003 标题三"/></a>
  </div>
</body></html>
```

```html
<!-- actress_list_empty.html -->
<!doctype html>
<html><body>
  <h1 data-testid="actress-name">空空</h1>
</body></html>
```

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_missav_parse_list.py
from __future__ import annotations

from pathlib import Path

from spiderhub.spiders.missav.parse import parse_actress_list

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "missav"
BASE = "https://missav.ws/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88"


def test_parse_list_page_extracts_actress_works_and_next() -> None:
    html = (FIX / "actress_list_page1.html").read_text(encoding="utf-8")
    page = parse_actress_list(html, BASE)
    assert page.actress.name == "北野未奈"
    assert page.actress.slug == "北野未奈" or "北野未奈" in page.actress.profile_url
    assert len(page.detail_urls) == 2
    assert page.detail_urls[0].endswith("/cn/abc-001")
    assert page.next_page_url == f"{BASE}?page=2"


def test_parse_empty_list() -> None:
    html = (FIX / "actress_list_empty.html").read_text(encoding="utf-8")
    page = parse_actress_list(html, BASE)
    assert page.detail_urls == []
    assert page.next_page_url is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_missav_parse_list.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement `parse_actress_list` with selectolax**

```python
# src/spiderhub/spiders/missav/parse.py（本 Task 先实现列表部分）
from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urljoin, urlparse, unquote

from selectolax.parser import HTMLParser

from spiderhub.models.items import Actress, Work


@dataclass(slots=True)
class ActressListPage:
    actress: Actress
    detail_urls: list[str]
    next_page_url: str | None


def _slug_from_actress_url(page_url: str) -> str:
    path = urlparse(page_url).path.rstrip("/")
    return unquote(path.split("/")[-1])


def parse_actress_list(html: str, page_url: str) -> ActressListPage:
    tree = HTMLParser(html)
    name_node = tree.css_first("[data-testid=actress-name]") or tree.css_first("h1")
    name = (name_node.text(strip=True) if name_node else "") or _slug_from_actress_url(
        page_url
    )
    cover_node = tree.css_first("[data-testid=actress-cover]")
    cover = cover_node.attributes.get("src") if cover_node else None
    bio_node = tree.css_first("[data-testid=actress-bio]")
    bio = bio_node.text(strip=True) if bio_node else None
    actress = Actress(
        slug=_slug_from_actress_url(page_url),
        name=name,
        profile_url=page_url.split("?")[0],
        cover_url=cover,
        bio=bio,
    )
    detail_urls: list[str] = []
    for a in tree.css("[data-testid=work-link]"):
        href = a.attributes.get("href")
        if href:
            detail_urls.append(urljoin(page_url, href))
    next_node = tree.css_first("[data-testid=next-page]")
    next_page_url = (
        urljoin(page_url, next_node.attributes["href"])
        if next_node and next_node.attributes.get("href")
        else None
    )
    return ActressListPage(
        actress=actress, detail_urls=detail_urls, next_page_url=next_page_url
    )
```

- [ ] **Step 4: Run tests + Commit**

Run: `uv run pytest tests/unit/test_missav_parse_list.py -v`  
Expected: PASS

```bash
git add tests/fixtures/missav src/spiderhub/spiders/missav/parse.py \
  tests/unit/test_missav_parse_list.py
git commit -m "$(cat <<'EOF'
feat: parse missav actress list pages from fixtures

Extract actress metadata, work detail URLs, and pagination next links.
EOF
)"
```

---

### Task 9: MissAV 详情页解析

**Files:**
- Create: `tests/fixtures/missav/work_detail.html`
- Create: `tests/fixtures/missav/work_detail_missing_code.html`
- Modify: `src/spiderhub/spiders/missav/parse.py`
- Create: `tests/unit/test_missav_parse_detail.py`

**Interfaces:**
- Consumes: 详情 HTML + URL
- Produces: `parse_work_detail(html: str, page_url: str) -> Work | None`（缺番号返回 `None`）

**Fixture：**

```html
<!-- work_detail.html -->
<!doctype html>
<html><body>
  <h1 data-testid="work-title">ABC-001 标题一</h1>
  <div data-testid="work-code">ABC-001</div>
  <time data-testid="release-date" datetime="2024-01-02">2024-01-02</time>
  <div data-testid="duration">120分</div>
  <div data-testid="maker">DemoMaker</div>
  <div data-testid="label">DemoLabel</div>
  <div data-testid="series">DemoSeries</div>
  <img data-testid="cover" src="https://cdn.example/c.jpg"/>
  <p data-testid="description">作品简介</p>
  <a data-testid="actress" href="/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88">北野未奈</a>
  <a data-testid="tag" href="/cn/genres/solo">solo</a>
  <a data-testid="tag" href="/cn/genres/hd">hd</a>
</body></html>
```

```html
<!-- work_detail_missing_code.html -->
<!doctype html>
<html><body><h1 data-testid="work-title">无番号</h1></body></html>
```

- [ ] **Step 1: Write failing tests**

```python
# tests/unit/test_missav_parse_detail.py
from __future__ import annotations

from pathlib import Path

from spiderhub.spiders.missav.parse import parse_work_detail

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "missav"


def test_parse_detail_fields() -> None:
    html = (FIX / "work_detail.html").read_text(encoding="utf-8")
    work = parse_work_detail(html, "https://missav.ws/cn/abc-001")
    assert work is not None
    assert work.code == "ABC-001"
    assert work.title.startswith("ABC-001")
    assert work.description == "作品简介"
    assert work.release_date is not None
    assert work.duration_seconds == 120 * 60
    assert work.maker == "DemoMaker"
    assert "北野未奈" in work.actress_names
    assert "solo" in work.tags


def test_missing_code_returns_none() -> None:
    html = (FIX / "work_detail_missing_code.html").read_text(encoding="utf-8")
    assert parse_work_detail(html, "https://missav.ws/cn/x") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_missav_parse_detail.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement `parse_work_detail` + duration/date helpers**

在 `parse.py` 增加（与 Task 8 已有 import 合并为一份完整模块）：

```python
import logging
import re
from datetime import date
from urllib.parse import unquote, urlparse

logger = logging.getLogger(__name__)


def _text(tree: HTMLParser, selector: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    value = node.text(strip=True)
    return value or None


def _attr(tree: HTMLParser, selector: str, name: str) -> str | None:
    node = tree.css_first(selector)
    if node is None:
        return None
    return node.attributes.get(name)


def _parse_duration_seconds(text: str) -> int | None:
    text = text.strip()
    m = re.search(r"(\d+)\s*分", text)
    if m:
        return int(m.group(1)) * 60
    m = re.search(r"(\d+):(\d+):(\d+)", text)
    if m:
        h, mi, s = map(int, m.groups())
        return h * 3600 + mi * 60 + s
    m = re.search(r"(\d+):(\d+)", text)
    if m:
        mi, s = map(int, m.groups())
        return mi * 60 + s
    return None


def _parse_release_date(raw: str | None) -> date | None:
    if not raw:
        return None
    raw = raw.strip()
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        logger.warning("invalid release_date=%s", raw)
        return None


def parse_work_detail(html: str, page_url: str) -> Work | None:
    tree = HTMLParser(html)
    code = _text(tree, "[data-testid=work-code]") or ""
    if not code:
        logger.warning("drop work missing code url=%s", page_url)
        return None
    title = _text(tree, "[data-testid=work-title]") or (
        tree.css_first("h1").text(strip=True) if tree.css_first("h1") else code
    )
    duration_raw = _text(tree, "[data-testid=duration]")
    duration_seconds = _parse_duration_seconds(duration_raw) if duration_raw else None
    release_raw = _attr(tree, "[data-testid=release-date]", "datetime") or _text(
        tree, "[data-testid=release-date]"
    )
    actress_names: list[str] = []
    actress_slugs: list[str] = []
    for node in tree.css("[data-testid=actress]"):
        name = node.text(strip=True)
        if name:
            actress_names.append(name)
        href = node.attributes.get("href")
        if href:
            slug = unquote(urlparse(href).path.rstrip("/").split("/")[-1])
            if slug:
                actress_slugs.append(slug)
    tags = [
        n.text(strip=True) for n in tree.css("[data-testid=tag]") if n.text(strip=True)
    ]
    return Work(
        code=code,
        title=title,
        detail_url=page_url,
        description=_text(tree, "[data-testid=description]"),
        release_date=_parse_release_date(release_raw),
        duration_seconds=duration_seconds,
        maker=_text(tree, "[data-testid=maker]"),
        label=_text(tree, "[data-testid=label]"),
        series=_text(tree, "[data-testid=series]"),
        cover_url=_attr(tree, "[data-testid=cover]", "src"),
        actress_slugs=actress_slugs,
        actress_names=actress_names,
        tags=tags,
    )
```

- [ ] **Step 4: Run tests + Commit**

Run: `uv run pytest tests/unit/test_missav_parse_detail.py -v`  
Expected: PASS

```bash
git add tests/fixtures/missav/work_detail.html \
  tests/fixtures/missav/work_detail_missing_code.html \
  src/spiderhub/spiders/missav/parse.py \
  tests/unit/test_missav_parse_detail.py
git commit -m "$(cat <<'EOF'
feat: parse missav work detail metadata

Fill code, title, description, and related fields from detail fixtures.
EOF
)"
```

---

### Task 10: `MissavActressSpider` 注册与 crawl 编排

**Files:**
- Create: `src/spiderhub/spiders/missav/spider.py`
- Create: `src/spiderhub/spiders/missav/__init__.py`
- Modify: `src/spiderhub/core/registry.py`（`discover_builtin_spiders` import missav）
- Create: `tests/unit/test_missav_spider.py`

**Interfaces:**
- Consumes: `parse_actress_list`, `parse_work_detail`, `register_spider`
- Produces:
  - `@register_spider class MissavActressSpider(Spider)`  
    - `name = "missav_actress"`  
    - `allowed_domains = ("missav.ws",)`  
    - `__init__(self, *, start_url: str | None = None)`  
    - 默认 start：`https://missav.ws/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88`  
    - `parse`：若 URL 含 `/actresses/` → 列表解析：yield `Actress`、yield 各 `detail_url`、yield `next_page_url`；否则当详情：yield `Work` 或跳过

- [ ] **Step 1: Write failing integration-style unit test with MockTransport**

```python
# tests/unit/test_missav_spider.py
from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from spiderhub.core.registry import discover_builtin_spiders, get_spider
from spiderhub.core.runner import run_spider
from spiderhub.core.settings import Settings
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.pipelines.null import NullPipeline

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "missav"
DEFAULT = "https://missav.ws/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88"


@pytest.mark.asyncio
async def test_spider_registered_and_dry_run_crawl() -> None:
    discover_builtin_spiders()
    spider_cls = get_spider("missav_actress")
    list_html = (FIX / "actress_list_page2.html").read_text(encoding="utf-8")
    detail_html = (FIX / "work_detail.html").read_text(encoding="utf-8")

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "/actresses/" in url:
            return httpx.Response(200, text=list_html, request=request)
        if url.endswith("/robots.txt"):
            return httpx.Response(
                200, text="User-agent: *\nAllow: /\n", request=request
            )
        return httpx.Response(200, text=detail_html, request=request)

    settings = Settings(
        request_delay_seconds=0.0, http_max_retries=1, obey_robots=False
    )
    spider = spider_cls(start_url=DEFAULT)
    async with HttpxFetcher(
        settings, transport=httpx.MockTransport(handler)
    ) as fetcher:
        result = await run_spider(spider, fetcher=fetcher, pipeline=NullPipeline())
    assert result.items_ok >= 1
    assert result.urls_failed == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_missav_spider.py -v`  
Expected: FAIL

- [ ] **Step 3: Implement spider package**

```python
# src/spiderhub/spiders/missav/spider.py
from __future__ import annotations

from collections.abc import AsyncIterator

from spiderhub.core.registry import register_spider
from spiderhub.core.spider import ParseItem, Spider
from spiderhub.downloaders.base import FetchedResponse
from spiderhub.spiders.missav.parse import parse_actress_list, parse_work_detail

DEFAULT_START = "https://missav.ws/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88"


@register_spider
class MissavActressSpider(Spider):
    name = "missav_actress"
    allowed_domains = ("missav.ws",)
    fetch_mode = "auto"

    def __init__(self, *, start_url: str | None = None) -> None:
        self._start_url = start_url or DEFAULT_START

    def start_urls(self) -> list[str]:
        return [self._start_url]

    async def parse(self, response: FetchedResponse) -> AsyncIterator[ParseItem]:
        if "/actresses/" in response.url:
            page = parse_actress_list(response.text, response.url)
            yield page.actress
            for detail in page.detail_urls:
                yield detail
            if page.next_page_url:
                yield page.next_page_url
            return
        work = parse_work_detail(response.text, response.url)
        if work is not None:
            yield work
```

```python
# src/spiderhub/spiders/missav/__init__.py
"""MissAV site spiders."""

from spiderhub.spiders.missav.spider import MissavActressSpider

__all__ = ["MissavActressSpider"]
```

更新 `discover_builtin_spiders`：

```python
def discover_builtin_spiders() -> None:
    from spiderhub.spiders import missav as _missav  # noqa: F401
```

- [ ] **Step 4: Run tests + Commit**

Run: `uv run pytest tests/unit/test_missav_spider.py -v`  
Expected: PASS

```bash
git add src/spiderhub/spiders/missav src/spiderhub/core/registry.py \
  tests/unit/test_missav_spider.py
git commit -m "$(cat <<'EOF'
feat: register missav_actress spider end-to-end

Wire list/detail parsing into the runner with a default actress seed URL.
EOF
)"
```

---

### Task 11: CLI 接线（list / run / --start-url / --dry-run）

**Files:**
- Modify: `src/spiderhub/cli.py`
- Modify: `tests/unit/test_cli.py`
- Create: `tests/unit/test_cli_run_dry.py`（可选，或扩写 `test_cli.py`）

**Interfaces:**
- Consumes: `discover_builtin_spiders`, `list_spiders`, `get_spider`, `load_settings`, `HttpxFetcher`, `run_spider`, `NullPipeline`, `MySQLPipeline`
- Produces: `main` 行为变更：
  - `list` → 打印已注册 spider 名（每行一个）；无则提示 empty
  - `run NAME [--dry-run] [--start-url URL]` → asyncio 跑 spider；dry-run 用 `NullPipeline`；否则 `MySQLPipeline`；成功 0；未知 spider 2；MySQL open 失败 2

- [ ] **Step 1: Rewrite CLI tests to new behavior**

```python
# tests/unit/test_cli.py
from __future__ import annotations

import pytest

from spiderhub.cli import main


def test_list_shows_missav(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["list"])
    out = capsys.readouterr().out
    assert code == 0
    assert "missav_actress" in out


def test_run_unknown_spider(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "no-such-spider"])
    err = capsys.readouterr().err
    assert code == 2
    assert "unknown" in err.lower() or "no-such-spider" in err
```

另增 dry-run 测试：monkeypatch `HttpxFetcher.fetch` 或整个 `run_spider` 返回 `RunResult(items_ok=1)`，断言 exit 0 且不构造 MySQL（可通过 monkeypatch `MySQLPipeline.open` 若被调用则 fail）。

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cli.py -v`  
Expected: FAIL（仍打印 No spiders / not implemented）

- [ ] **Step 3: Implement CLI**

```python
# src/spiderhub/cli.py（关键结构）
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from spiderhub.core.registry import discover_builtin_spiders, get_spider, list_spiders
from spiderhub.core.runner import run_spider
from spiderhub.core.settings import load_settings
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.pipelines.mysql import MySQLPipeline
from spiderhub.pipelines.null import NullPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spiderhub")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("list", help="List registered spiders")
    run = sub.add_parser("run", help="Run a spider")
    run.add_argument("name", help="Spider name")
    run.add_argument("--dry-run", action="store_true")
    run.add_argument("--start-url", default=None)
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    discover_builtin_spiders()
    try:
        spider_cls = get_spider(args.name)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    settings = load_settings()
    spider = spider_cls(start_url=args.start_url) if args.start_url else spider_cls()
    # If spider __init__ does not accept start_url for other spiders, use:
    # spider = spider_cls(start_url=args.start_url) only for missav; or kwargs pattern.
    pipeline: NullPipeline | MySQLPipeline
    if args.dry_run:
        pipeline = NullPipeline()
    else:
        pipeline = MySQLPipeline(settings)
    try:
        async with HttpxFetcher(settings) as fetcher:
            result = await run_spider(
                spider,
                fetcher=fetcher,
                pipeline=pipeline,
                start_urls=[args.start_url] if args.start_url else None,
            )
    except Exception as exc:  # noqa: BLE001
        logging.exception("run failed: %s", exc)
        print(f"run failed: {exc}", file=sys.stderr)
        return 2
    print(
        f"done items_ok={result.items_ok} items_failed={result.items_failed} "
        f"urls_failed={result.urls_failed}"
    )
    return 0 if result.urls_failed == 0 and result.items_failed == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    discover_builtin_spiders()
    if args.command == "list":
        names = list_spiders()
        if not names:
            print("No spiders registered yet.")
        else:
            print("\n".join(names))
        return 0
    return asyncio.run(_run_async(args))
```

统一 `MissavActressSpider.__init__(start_url: str | None = None)`；`run_spider(..., start_urls=...)` 在 CLI 传入时覆盖。其它未实现 spider 不在本切片出现。

- [ ] **Step 4: Run full unit suite subset + Commit**

Run: `uv run pytest tests/unit/test_cli.py tests/unit/test_missav_spider.py -v`  
Expected: PASS

```bash
git add src/spiderhub/cli.py tests/unit/test_cli.py tests/unit/test_cli_run_dry.py
git commit -m "$(cat <<'EOF'
feat: wire CLI list/run to registry and pipelines

Support --dry-run and --start-url for missav_actress execution.
EOF
)"
```

---

### Task 12: 文档同步与质量门禁

**Files:**
- Modify: `README.md`
- Modify: `AGENTS.md`（技术栈表若需反映已引入依赖；常用命令补充 `--start-url` 与建表 SQL）
- Modify: `docs/superpowers/specs/2026-08-09-missav-actress-mysql-design.md` 状态改为「已落地计划」或保持审阅完成即可

- [ ] **Step 1: Update README**

在 README 增加：

```markdown
## MissAV 女优爬虫（垂直切片）

1. 复制配置：`cp .env.example .env`（或 `cp config.example.toml config.local.toml`）并填写 MySQL
2. 建表：`mysql -u ... < scripts/sql/missav_schema.sql`
3. 试跑：`uv run spiderhub run missav_actress --dry-run`
4. 写库：`uv run spiderhub run missav_actress`
5. 其它女优：`uv run spiderhub run missav_actress --start-url 'https://missav.ws/cn/actresses/...'`
```

说明：仅元数据；需合规授权与遵守 robots。

- [ ] **Step 2: Update AGENTS.md 技术栈表**

将「轻量 HTTP / 校验 / 解析」与真实依赖对齐（httpx、pydantic、selectolax、PyMySQL 已引入）。注明 L2/L3 仍未实现。

- [ ] **Step 3: Run full quality gate**

```bash
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest
```

Expected: 全部通过。

- [ ] **Step 4: Commit**

```bash
git add README.md AGENTS.md docs/superpowers/specs/2026-08-09-missav-actress-mysql-design.md
git commit -m "$(cat <<'EOF'
docs: document missav actress MySQL workflow

Align README and agent guide with the vertical-slice commands and deps.
EOF
)"
```

---

## Spec Coverage Checklist（自检）

| 规格要求 | Task |
|----------|------|
| 通用女优 Spider + 默认北野未奈种子 | 10, 11 |
| 全部分页 + 详情补字段（含简介） | 8, 9, 10 |
| pydantic 校验 | 2 |
| MySQL upsert by code / slug | 7 |
| 配置示例与本地密钥 | 1 |
| 建表 SQL、不自动建表 | 7 |
| 最小内核：基类/注册表/Runner/L1/挑战 stub/pipeline/CLI | 3–6, 11 |
| Fixture 单测、不打真站 | 8–10 |
| dry-run 不写库 | 6, 11 |
| 无视频流逻辑 | 全局约束 + parse 字段集 |
| README/AGENTS 同步 | 12 |

## 真站 DOM 校准说明

单元测试以 `data-testid` fixture 为契约。首次对真站 `--dry-run` 时，若选择器不匹配：用浏览器另存 HTML 到本地（勿提交含个人 Cookie 的文件），脱敏后更新 `tests/fixtures/missav/*` 与 `parse.py` 选择器，并保持测试绿色。不要在单测里请求 `missav.ws`。
