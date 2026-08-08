# SpiderHub

基于 Python 的爬虫中枢（Crawler Hub）骨架。Agent 约定见 [AGENTS.md](AGENTS.md)。

## 要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)（本机用 Homebrew：`brew install uv`）

## 安装

```bash
uv sync
```

## 常用命令

```bash
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest
uv run spiderhub list
uv run spiderhub run <spider_name>
uv run spiderhub run <spider_name> --dry-run
```

## MissAV 女优爬虫（垂直切片）

1. 复制配置：`cp .env.example .env`（或 `cp config.example.toml config.local.toml`）并填写 MySQL
2. 建表：`mysql -u ... < scripts/sql/missav_schema.sql`
3. 试跑：`uv run spiderhub run missav_actress --dry-run`
4. 写库：`uv run spiderhub run missav_actress`
5. 其它女优：`uv run spiderhub run missav_actress --start-url 'https://missav.ws/cn/actresses/...'`

说明：仅元数据；需合规授权与遵守 robots。默认启用自动升维：L1 `httpx` → L2 `curl_cffi` → L3 Playwright（系统 Chrome）。可用 `SPIDERHUB_ALLOW_FETCHER_UPGRADE` / `SPIDERHUB_ALLOW_BROWSER` 关闭。

若 Cloudflare 需人工验证，可有头模式并拉长等待，完成后会保存会话到 `.spiderhub/storage_state.json`（已 gitignore）：

```bash
SPIDERHUB_BROWSER_HEADLESS=false \
SPIDERHUB_BROWSER_CHALLENGE_WAIT_SECONDS=180 \
uv run spiderhub run missav_actress --dry-run
```

解析器当前以本地 fixture 的 `data-testid` 为契约，真站 DOM 不同时需先校准 `src/spiderhub/spiders/missav/parse.py`。
