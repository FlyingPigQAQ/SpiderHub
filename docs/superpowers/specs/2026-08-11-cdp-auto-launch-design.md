# 本机 CDP 自动启动（设计）

日期：2026-08-11  
状态：待实现

## 背景

L3 人工过 Cloudflare 时，推荐通过本机 Chrome 远程调试（CDP）接入，而不是用 Playwright 直接弹窗（易被判定为自动化）。现状要求用户手动执行：

```bash
mkdir -p .spiderhub/chrome-profile
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome \
  --remote-debugging-port=9222 \
  --user-data-dir="$PWD/.spiderhub/chrome-profile"
```

并自行设置 `SPIDERHUB_BROWSER_CDP_URL`。这增加了上手成本，也容易和日常 Chrome 配置混淆。

用户选择：

- 方案 1：独立 `ChromeCdpLauncher` + 现有 Playwright 仅 `connect_over_cdp`
- 浏览器探测顺序：Chrome → Chromium → Edge（都没有则提示安装 Chrome）
- 生命周期可配：默认结束时关掉自启浏览器；`browser_cdp_keep_alive=true` 时保留
- 配置：`browser_cdp_enabled`；未提供 `browser_cdp_url` 时自动探测并启动

## 目标

1. 配置开启 CDP 后，代码自动创建 profile 目录、探测并启动本机 Chromium 系浏览器、等待 CDP 就绪，再由 Playwright 连接。
2. CDP 开启时**不**使用 Playwright / Camoufox / Patchright 的 browser launch 路径；仅 `connect_over_cdp`。
3. 找不到支持 CDP 的本机浏览器时，给出明确中文错误与 Homebrew 安装建议（不代装）。
4. 兼容旧用法：已提供 `browser_cdp_url` 时只连接、不自动启动。
5. 自启进程默认可在 run 结束时关闭；可选 keep-alive。

## 非目标

- 支持 Firefox CDP 自动启动（文档可保留手工示例，不在本迭代实现）
- 用 `curl | sh` / 官方 pkg 代装浏览器（违背 AGENTS.md brew 约定）
- 去掉 Playwright 依赖（仍需其 CDP 客户端）
- 多开并行 CDP 端口池 / 远程机器上的浏览器编排
- 改变 L2 Cookie 同步与 sticky L3 语义（沿用现有 AutoFetcher CDP 行为）

## 配置

| 字段 | 环境变量 | 默认 | 含义 |
|------|----------|------|------|
| `browser_cdp_enabled` | `SPIDERHUB_BROWSER_CDP_ENABLED` | `false` | 开启本机 CDP 路径 |
| `browser_cdp_url` | `SPIDERHUB_BROWSER_CDP_URL` | `""` | 非空：只连接；空且 enabled：自动探测并启动，默认 `http://127.0.0.1:9222` |
| `browser_cdp_keep_alive` | `SPIDERHUB_BROWSER_CDP_KEEP_ALIVE` | `false` | `true`：不杀本次自启进程 |
| `browser_user_data_dir` | `SPIDERHUB_BROWSER_USER_DATA_DIR` | `.spiderhub/chrome-profile` | 独立 profile（启动时 `mkdir -p`） |

兼容规则：

- `browser_cdp_enabled=true` **或** 非空 `browser_cdp_url` → 视为 CDP 模式（后者兼容旧配置）。
- CDP 模式下 `build_l3_fetcher` 强制返回 `PlaywrightFetcher`（忽略 `browser_engine` 的 launch 选型）。
- 关闭 CDP 时行为与现在一致。

`config.example.toml` / `.env.example` / `README.md` / `AGENTS.md` 同步上述字段；README 中「手动起 Chrome」改为可选高级用法，默认推荐 `browser_cdp_enabled=true`。

## 架构与数据流

```text
AutoFetcher._ensure_l3
  └─ PlaywrightFetcher.__aenter__
       ├─ ChromeCdpLauncher.ensure_ready(settings)
       │    ├─ URL 已提供 → 只校验/等待可连（不 Popen）
       │    ├─ URL 为空 → 探测 Chrome→Chromium→Edge
       │    │    ├─ 9222 已是 CDP → 复用（不记自启）
       │    │    └─ 否则 mkdir profile + Popen 原生浏览器
       │    └─ 轮询 /json/version 直至就绪或超时
       ├─ connect_over_cdp(final_url)   ← 不 launch Playwright Chromium
       └─ …
PlaywrightFetcher.__aexit__
  ├─ disconnect CDP
  └─ launcher.shutdown()  # 仅自启且 keep_alive=false 时 terminate
```

新模块：`src/spiderhub/downloaders/cdp_launcher.py`

职责边界：

- Launcher：探测可执行文件、起停进程、端口就绪、回填 URL。
- PlaywrightFetcher：只负责 CDP 连接与页面抓取。
- Camoufox / Patchright：CDP 模式下不参与。

## 探测与启动细节

**可执行文件探测（macOS 优先固定路径，再 `shutil.which`）：**

1. Google Chrome（如 `/Applications/Google Chrome.app/Contents/MacOS/Google Chrome`）
2. Chromium
3. Microsoft Edge

都找不到 → 抛 `RuntimeError`（或专用异常），文案提示安装 Google Chrome，并建议：

```bash
brew install --cask google-chrome
```

（符合 AGENTS.md：系统依赖只允许建议 brew，Agent/代码不代装。）

**启动参数（自启时）：**

- `--remote-debugging-port=<port>`（默认 `9222`）
- `--user-data-dir=<browser_user_data_dir>`
- `--no-first-run` / `--no-default-browser-check`（减少打扰）
- 不向日常默认 profile 写数据

**就绪探测：**

- 仅使用 HTTP GET `http://127.0.0.1:<port>/json/version`；响应需可解析为 JSON 且含浏览器标识字段（如 `Browser` / `webSocketDebuggerUrl`）
- 超时则 terminate 自启进程并报错

**端口占用：**

- 默认端口已是合法 CDP 端点 → 复用，不重复 Popen，不记为自启（退出时不杀）
- 默认端口被占用但**不是** CDP（探测失败）→ 不换端口、不覆盖；报错提示释放 9222 或显式设置 `browser_cdp_url`
- 用户显式提供 URL → 永不自动 Popen；连不上按现有连接错误处理

**平台路径：**

- 本迭代明确覆盖：macOS 固定 App 路径 + 全平台 `PATH`（`google-chrome` / `chromium` / `microsoft-edge` 等常见名）
- Windows 固定路径可作为后续增强；有 `PATH` 即可工作

## 接入点变更

1. `Settings` / `load_settings`：新增 `browser_cdp_enabled`、`browser_cdp_keep_alive`（`_as_bool`）。
2. `build_l3_fetcher`：`enabled or url` → `PlaywrightFetcher`。
3. `PlaywrightFetcher`：CDP 分支前调用 launcher；`__aexit__` 调用 `shutdown`。
4. `AutoFetcher`：`_prefer_http_after_browser = enabled or bool(url)`。
5. 文档四处同步（见上）。

辅助：可在 Settings 或小函数中提供 `cdp_mode_active(settings) -> bool`，避免三处重复判断。

## 错误处理

| 场景 | 行为 |
|------|------|
| 无可用浏览器 | 明确错误 + brew 安装 Chrome 建议；**不**静默降级到 Playwright bundled Chromium |
| 自启后 CDP 超时 | 终止自启进程并报错 |
| 用户自带 URL 连不上 | 不擅自再起浏览器；抛出连接错误 |
| keep_alive=true | 断开 CDP 后保留浏览器进程 |

## 测试计划

全部用 mock / 本地假路径，不依赖真实浏览器进程。

1. **settings**：`browser_cdp_enabled` / `keep_alive` 布尔解析；toml + env。
2. **launcher**：
   - 探测顺序（patch 路径存在性）
   - 全无 → 错误文案含 brew / Chrome
   - 端口已就绪 → 不 Popen
   - 自启成功后 `shutdown` 在 keep_alive false/true 下的行为
3. **browser_factory**：enabled 强制 PlaywrightFetcher（即使 `browser_engine=camoufox`）。
4. **AutoFetcher**：enabled 时 `_prefer_http_after_browser` 为 true。
5. **PlaywrightFetcher**：mock launcher，CDP 路径不调用 `_launch_persistent` / `_launch_ephemeral`。

## 验收标准

- [ ] `browser_cdp_enabled=true` 且无 URL 时，无需用户手动 mkdir / 起 Chrome，即可进入 CDP 抓取路径（本机已装 Chrome/Chromium/Edge）
- [ ] 未安装上述浏览器时，错误信息可指导安装
- [ ] 提供 `browser_cdp_url` 时行为与现网兼容（只连接）
- [ ] 默认 run 结束关闭自启浏览器；`keep_alive=true` 时保留
- [ ] CDP 开启时不 launch Playwright/Camoufox/Patchright 自带浏览器
- [ ] `ruff` / `mypy` / `pytest` 通过；文档已更新
