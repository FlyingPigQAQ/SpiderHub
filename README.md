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
4. 只爬列表第一页（仍会抓该页作品详情）：`uv run spiderhub run missav_actress --dry-run --max-pages 1`
5. 写库：`uv run spiderhub run missav_actress`
6. 其它女优：`uv run spiderhub run missav_actress --start-url 'https://missav.ws/cn/actresses/...'`

说明：仅元数据；需合规授权与遵守 robots。默认启用自动升维：L1 `httpx` → L2 `curl_cffi` → L3 Playwright（系统 Chrome）。可用 `SPIDERHUB_ALLOW_FETCHER_UPGRADE` / `SPIDERHUB_ALLOW_BROWSER` 关闭。

若 Cloudflare 需人工验证：**不要用 Playwright 直接弹窗勾选**（常被判定为自动化，勾选后会反复刷新）。推荐先手动启动本机 Chrome 并开远程调试，再让 SpiderHub 通过 CDP 接入：

```bash
# 终端 1：独立用户数据目录，避免打扰日常 Chrome
mkdir -p .spiderhub/chrome-profile
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/.spiderhub/chrome-profile"

# 终端 2：CDP 过验证后继续用同一 Chrome 标签抓内容（不切 headless，避免再次卡 CF）
SPIDERHUB_BROWSER_CDP_URL=http://127.0.0.1:9222 \
SPIDERHUB_BROWSER_CHALLENGE_WAIT_SECONDS=180 \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```

会话保存到 `.spiderhub/storage_state.json`（已 gitignore）。CDP 模式下会复用同一标签翻页，而不是每页新建窗口。

解析器已按 MissAV 真站 DOM 校准（`div.thumbnail` 作品卡、`div.space-y-2` 详情字段等）；单测仍保留 `data-testid` fixture 作为回归兜底。
