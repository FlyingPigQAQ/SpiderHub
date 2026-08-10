# SpiderHub

基于 Python 的爬虫中枢（Crawler Hub）骨架。Agent 约定见 [AGENTS.md](AGENTS.md)。

## 要求

- Python 3.11+
- [uv](https://github.com/astral-sh/uv)（本机用 Homebrew：`brew install uv`）
- 写库时需要本机 MySQL（表结构见 `scripts/sql/missav_schema.sql`）

## 安装

```bash
# 基础依赖
uv sync

# 可选：Camoufox / Patchright 作为 L3 引擎
uv sync --extra stealth
uv run python -m camoufox fetch          # 下载 Camoufox 浏览器
uv run patchright install chromium       # 安装 Patchright Chromium
```

## 配置

任选其一（环境变量优先于 toml）：

```bash
cp .env.example .env
# 或
cp config.example.toml config.local.toml
```

在 `.env` / `config.local.toml` 中填写 MySQL 账号等。完整键名见 `[.env.example](.env.example)` 与 `[config.example.toml](config.example.toml)`。

### 飞书提醒（可选）

SpiderHub 可在 L3 有头浏览器或 CDP 进入 Cloudflare 人工验证等待时，以及非 `--dry-run` 的爬虫运行结束（成功 / 部分失败 / 异常中断）时，发送飞书文本提醒。需先创建飞书企业自建应用、启用机器人能力，并开通 `im:message:send_as_bot`（或飞书文档中的等价权限），然后配置：

```bash
SPIDERHUB_FEISHU_APP_ID=cli_xxx
SPIDERHUB_FEISHU_APP_SECRET=xxx
SPIDERHUB_FEISHU_RECEIVE_ID_TYPE=open_id  # open_id | user_id | chat_id
SPIDERHUB_FEISHU_RECEIVE_ID=ou_xxx
SPIDERHUB_FEISHU_NOTIFY_COOLDOWN_SECONDS=600
```

人工验证提醒默认冷却 600 秒；运行完成提醒不受该冷却限制。以上接收方与凭证配置不完整时提醒保持关闭，不改变现有抓取行为；真实密钥只放在本地环境变量或未入库配置中。

## 质量检查

```bash
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest
```



## CLI 用法

```bash
# 列出已注册 Spider
uv run spiderhub list

# 运行指定 Spider
uv run spiderhub run <spider_name>

# 只解析、不写库
uv run spiderhub run <spider_name> --dry-run

# 覆盖起始 URL / 限制列表页数
uv run spiderhub run <spider_name> --start-url 'https://example.com/...' --max-pages 1
```



## MissAV 女优爬虫

仅抓取元数据；需合规授权并遵守 robots。

```bash
# 1. 建表（应用不会自动 CREATE TABLE；含 failed_urls 失败 URL 表）
mysql -u ... < scripts/sql/missav_schema.sql

# 2. 试跑（不写库）
uv run spiderhub run missav_actress --dry-run

# 3. 只爬列表第一页（仍会抓该页作品详情）
uv run spiderhub run missav_actress --dry-run --max-pages 1

# 4. 正式写库
uv run spiderhub run missav_actress

# 5. 指定其它女优列表页
uv run spiderhub run missav_actress \
  --start-url 'https://missav.ws/cn/actresses/...'
```

解析器已按 MissAV 真站 DOM 校准（`div.thumbnail` 作品卡、`div.space-y-2` 详情字段等）；单测仍保留 `data-testid` fixture 作为回归兜底。

---



## Fetcher 升维用法（L1 → L2 → L3 → L4）

默认路径：

1. **L1** `httpx`
2. **L2** `curl_cffi`（TLS 指纹）
3. **L3** 浏览器（`browser_engine`）
4. **L4** FlareSolverr/Solverr 兼容 API（**默认关闭**）

`robots.txt` 永不 sticky 升维。可用开关关闭任一层：


| 环境变量                                     | 默认      | 作用                         |
| ---------------------------------------- | ------- | -------------------------- |
| `SPIDERHUB_ALLOW_FETCHER_UPGRADE`        | `true`  | 关闭后遇挑战直接抛错，不升维             |
| `SPIDERHUB_ALLOW_BROWSER`                | `true`  | 禁止升到 L3                    |
| `SPIDERHUB_ALLOW_EXTERNAL_SOLVER`        | `false` | 启用 L4                      |
| `SPIDERHUB_EXTERNAL_SOLVER_SKIP_BROWSER` | `false` | `true` 时 L2 挑战后跳过 L3 直奔 L4 |




### 关闭升维 / 仅用 HTTP

```bash
# 完全禁止升维（L1 遇挑战即失败）
SPIDERHUB_ALLOW_FETCHER_UPGRADE=false \
uv run spiderhub run missav_actress --dry-run --max-pages 1

# 允许升到 L2，但禁止浏览器 L3（且 L4 仍默认关）
SPIDERHUB_ALLOW_BROWSER=false \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```



### L2：curl_cffi 指纹目标

```bash
SPIDERHUB_IMPERSONATE_TARGET=chrome \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```



### L3：Playwright（默认）

```bash
SPIDERHUB_BROWSER_ENGINE=playwright \
SPIDERHUB_BROWSER_HEADLESS=true \
SPIDERHUB_BROWSER_CHALLENGE_WAIT_SECONDS=15 \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```

有头模式（独立 Chrome profile，不推荐用于人工勾选 CF）：

```bash
SPIDERHUB_BROWSER_ENGINE=playwright \
SPIDERHUB_BROWSER_HEADLESS=false \
SPIDERHUB_BROWSER_USER_DATA_DIR=.spiderhub/chrome-profile \
SPIDERHUB_BROWSER_CHALLENGE_WAIT_SECONDS=180 \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```



### L3：Camoufox

需先 `uv sync --extra stealth && uv run python -m camoufox fetch`。

```bash
SPIDERHUB_BROWSER_ENGINE=camoufox \
SPIDERHUB_BROWSER_HEADLESS=true \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```



### L3：Patchright

需先 `uv sync --extra stealth && uv run patchright install chromium`。

```bash
SPIDERHUB_BROWSER_ENGINE=patchright \
SPIDERHUB_BROWSER_HEADLESS=true \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```

说明：

- `browser_engine` 为配置单选；引擎间**不**自动互切。
- 设置了 `SPIDERHUB_BROWSER_CDP_URL` 时**强制**走 Playwright CDP，忽略 `browser_engine`。
- 会话默认写入 `.spiderhub/storage_state.json`（已 gitignore）。



### L3：本机 Chrome CDP（人工过 Cloudflare，推荐）

**不要用 Playwright 直接弹窗勾选**（常被判定为自动化，勾选后会反复刷新）。推荐手动启动本机 Chrome 开远程调试，再让 SpiderHub 通过 CDP 接入；过验证后继续用同一标签抓内容（不切 headless，避免再次卡 CF）。

```bash
# 终端 1：独立用户数据目录，避免打扰日常 Chrome
mkdir -p .spiderhub/chrome-profile
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/.spiderhub/chrome-profile"

mkdir -p .spiderhub/firefox-profile
firefox --remote-debugging-port=9222 \
        --profile "$PWD/.spiderhub/firefox-profile" \
        --no-first-run \
        --no-default-browser-check \
        --disable-extensions

# 终端 2：连接 CDP 并拉长挑战等待
SPIDERHUB_BROWSER_CDP_URL=http://127.0.0.1:9222 \
SPIDERHUB_BROWSER_CHALLENGE_WAIT_SECONDS=180 \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```

CDP 模式下：人工过验证后会把 Cookie 同步到 L2（`curl_cffi`），**后续内容页优先走 HTTP**，避免反复 `page.goto` 抢占 Chrome 焦点。若 L2 仍被挑战拦截，则回退并 sticky 在 L3（继续用该 Chrome 标签抓取）。

### L4：FlareSolverr / Solverr（默认关闭）

先自行启动兼容 `/v1` 的 solver（例如本机 `http://127.0.0.1:8191/v1`），再启用适配器。成功后 sticky 在 L4；不内置打码服务。

**L3 失败后再升 L4：**

```bash
SPIDERHUB_ALLOW_EXTERNAL_SOLVER=true \
SPIDERHUB_EXTERNAL_SOLVER_URL=http://127.0.0.1:8191/v1 \
SPIDERHUB_EXTERNAL_SOLVER_TIMEOUT_MS=60000 \
SPIDERHUB_EXTERNAL_SOLVER_SESSION=spiderhub \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```

**跳过 L3，L2 挑战后直接 L4：**

```bash
SPIDERHUB_ALLOW_EXTERNAL_SOLVER=true \
SPIDERHUB_EXTERNAL_SOLVER_SKIP_BROWSER=true \
SPIDERHUB_EXTERNAL_SOLVER_URL=http://127.0.0.1:8191/v1 \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```

**禁用浏览器、仅用 L2→L4：**

```bash
SPIDERHUB_ALLOW_BROWSER=false \
SPIDERHUB_ALLOW_EXTERNAL_SOLVER=true \
SPIDERHUB_EXTERNAL_SOLVER_URL=http://127.0.0.1:8191/v1 \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```



### 用 toml 配置同一套能力

`config.local.toml` 示例片段：

```toml
[crawl]
allow_fetcher_upgrade = true
allow_browser = true
impersonate_target = "chrome"
browser_engine = "playwright"   # playwright | camoufox | patchright
browser_challenge_wait_seconds = 15.0
browser_headless = true
browser_storage_state = ".spiderhub/storage_state.json"
# browser_cdp_url = "http://127.0.0.1:9222"
browser_user_data_dir = ".spiderhub/chrome-profile"
allow_external_solver = false
external_solver_url = "http://127.0.0.1:8191/v1"
external_solver_skip_browser = false
external_solver_timeout_ms = 60000
external_solver_session = "spiderhub"
```

等价环境变量见 `[.env.example](.env.example)`。