# AGENTS.md — SpiderHub

面向编码 Agent 的仓库操作契约。人类可读说明见 `README.md`；本文件只写 Agent 执行任务时必须遵守的约定。

## 项目概览

SpiderHub 是基于 Python 的爬虫中枢（Crawler Hub）：统一管理多站点 Spider、调度、抓取运行时、中间件与结果管道，而不是单站点脚本仓库。

目标能力：

- 以插件方式注册 / 发现 / 运行多个 Spider
- 提供可复用的下载、解析、重试、限速、代理、管道能力
- 提供分层反爬兼容能力（Cloudflare / 同类 Bot Management、JS 挑战页、指纹校验等）
- 保证合规抓取（robots、限速、身份标识）与可观测性（日志、指标、失败归因）

## 技术栈（约定）

默认按以下选型实现；若与现有代码冲突，以代码与 `pyproject.toml` 为准，并同步回写本文件。

| 层级 | 选型 |
|------|------|
| 语言 | Python 3.11+ |
| 包管理 | `uv` + `pyproject.toml`（`src/` 布局） |
| 轻量 HTTP | `httpx`（已引入；L1 默认路径；不要与 `aiohttp` 混用两套客户端栈） |
| 浏览器抓取 | `Playwright`（优先系统 Chrome `channel=chrome`，否则 bundled Chromium）；L3 路径 |
| TLS/JA3 兼容客户端 | `curl_cffi`（已引入；L2 路径，`AutoFetcher` 遇挑战可升维） |
| 反爬检测与降级 | 自研 `challenge` 检测 + `AutoFetcher`（L1 httpx → L2 curl_cffi → L3 Playwright；均可关闭） |
| 代理 | 可插拔 proxy pool（住宅/ISP 优先于数据中心，按站点策略配置） |
| 解析 | `selectolax`（已引入；MissAV 切片使用）；`parsel` / `lxml` 可按需引入 |
| 校验 | `pydantic` v2（已引入） |
| 落库 | `PyMySQL`（已引入；MySQL upsert pipeline） |
| 日志 | 标准库 `logging` + 结构化字段；禁止 `print` 作为正式日志 |
| 质量工具 | `ruff`（lint + format）、`mypy`、`pytest`、`pytest-asyncio` |
| 配置 | 环境变量 + `.env.example` / `config.example.toml`；密钥不入库 |

不要默认引入 Scrapy，除非任务明确要求接入 Scrapy 生态。优先保持 Hub 内核轻量、可组合。

反爬相关第三方「绕过服务 / 打码平台」只允许通过适配器接入，且必须可关闭、可审计；不得写死为唯一下载路径。

## 推荐目录结构

```text
SpiderHub/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
├── src/spiderhub/
│   ├── __init__.py
│   ├── cli.py                 # CLI 入口
│   ├── core/                  # 调度、运行时、注册表
│   ├── downloaders/           # HTTP / browser / curl_cffi 统一 Fetcher
│   ├── challenges/            # 挑战页检测、会话保活、fetcher 升级策略
│   ├── middlewares/           # UA、代理、鉴权、缓存、指纹配置等
│   ├── pipelines/             # 清洗、去重、落库/落盘
│   ├── spiders/               # 具体站点 Spider（一站一模块/包）
│   ├── models/                # 共享 pydantic 模型
│   └── utils/
├── tests/
│   ├── unit/
│   ├── integration/
│   └── fixtures/              # HTML/JSON 夹具，禁止打真实站做单测
└── scripts/                   # 运维/一次性脚本，不进核心包
```

新增能力时先落在正确层级，避免把业务解析逻辑塞进 `core/` 或把 Hub 能力塞进某个 Spider。

## 反爬兼容策略

目标是**分层、可降级、可观测**的抓取兼容，而不是把「绕过」逻辑散落在每个 Spider 里。

### 分层 Fetcher（成本从低到高）

1. **L1 `httpx`**：API / 弱防护页面；最快、最省资源。
2. **L2 `curl_cffi`**：需要更接近浏览器的 TLS/HTTP2 指纹时使用。
3. **L3 `Playwright`**：JS 挑战、强 Bot Management、必须执行页面脚本时使用。
4. **L4 外部适配器（可选）**：商业反爬/解锁 API；仅配置启用，默认关闭。

### 必须具备的能力

- **挑战检测**：识别 Cloudflare / 同类挑战页、403/503 + 特征正文、等待室等，并输出明确错误类型（勿当普通空页）。
- **自动升级**：同一请求可按策略从 L1 升到 L2/L3；升级原因写入日志与指标。
- **会话复用**：挑战通过后的 Cookie / 存储状态可复用到后续 L1/L2 请求，避免每页都开浏览器。
- **代理策略**：按 `allowed_domains` / Spider 配置绑定代理池；失败时区分「代理差」与「挑战未通过」。
- **站点策略声明**：Spider 元数据可声明 `fetch_mode`（`http` / `impersonate` / `browser` / `auto`）与是否允许升维。
- **可关闭**：全局与 per-spider 都能禁用浏览器/外部解锁路径，便于合规与排障。

### 明确不做 / 不内置

- 不内置验证码打码、接码、人工打码流水线作为核心依赖。
- 不实现漏洞利用、WAF 0-day、绕过登录墙 / 付费墙 / 权限校验。
- 不在仓库中提交可运行的「专用破解脚本」或未审计的二进制绕过工具。
- Spider 内禁止私藏一套 Cloudflare 特例 hack；一律走 `challenges/` + `downloaders/` 扩展点。

## 系统依赖安装（强制）

安装**系统级**工具 / 运行时（如浏览器依赖、系统库、CLI）时：

1. **只允许通过 Homebrew（`brew`）安装**。
2. 若 `brew` 无对应 formula/cask，或安装失败：**不要**改用 `curl | sh`、官方 pkg、手动下载、`apt`/`yum` 等其它方式自行安装。
3. 此时应**明确通知用户**缺少什么、为什么 `brew` 不可用，并给出建议由用户自行安装的步骤；等待用户完成后再继续。
4. Python 包仍用 `uv` / `pyproject.toml` 管理，不适用本条；本条仅约束系统级依赖。

Agent 不得在未获用户明确授权时绕过上述规则。

## 常用命令

```bash
# 环境
uv sync

# 质量
uv run ruff check .
uv run ruff format .
uv run mypy src
uv run pytest

# 运行
uv run spiderhub list
uv run spiderhub run <spider_name>
uv run spiderhub run <spider_name> --dry-run
uv run spiderhub run missav_actress --start-url 'https://missav.ws/cn/actresses/...'

# 建表（应用不自动 CREATE TABLE）
mysql -u ... < scripts/sql/missav_schema.sql
```

改动涉及依赖、命令或目录约定时，同一 PR 更新本文件与 `README.md`。

## Spider 编写规范

1. **一 Spider 一职责**：一个模块/包只服务一个目标站点或明确边界的数据源。
2. **声明式元数据**：每个 Spider 必须声明 `name`、`allowed_domains`（或等价白名单）、默认限速、是否遵守 robots、`fetch_mode`（默认 `auto`）。
3. **解析与下载分离**：网络 I/O 走 downloader/middleware/challenges；Spider 专注 URL 生成与解析，不手写反爬绕过。
4. **输出结构化**：产物用 pydantic 模型校验后再进 pipeline；禁止裸 `dict` 贯穿全链路。
5. **可测**：核心解析必须可用本地 fixture 单测；集成测试才允许受控外网（默认关闭）。
6. **失败可诊断**：保留 URL、状态码、重试次数、最终错误类型；不要吞掉异常后静默成功。

## 架构边界

| 可以做 | 不要做 |
|--------|--------|
| 在 `spiders/` 增加站点实现 | 在 Spider 内硬编码全局代理/密钥 |
| 扩展 middleware / pipeline / challenges | 把限速、重试、CF 处理复制到每个 Spider |
| 增加 L2/L3 fetcher 或外部解锁适配器 | 把商业解锁服务写死进核心下载路径 |
| 通过注册表发现 Spider | 用动态 `exec` / 任意远程代码加载 Spider |
| 写 fixture 驱动的解析与挑战检测测试 | 单测直接打生产站点 / 依赖真实 CF 挑战 |
| 为 Hub 增加 CLI / 调度钩子 | 为了省事把脚本逻辑堆进 `core/` |

## 代码风格

- 遵循 Ruff 默认 + 项目 `pyproject.toml`；不手写与 Ruff 冲突的格式。
- 公共 API 与核心模块补类型注解；`mypy` 能过再合并。
- 命名：模块/函数 `snake_case`，类 `PascalCase`，常量 `UPPER_SNAKE_CASE`。
- 异步函数名不必加 `_async` 后缀，除非与同步孪生 API 并存。
- 优先小函数与显式依赖注入；避免隐蔽全局单例（配置/客户端工厂除外且需可测试）。
- 注释只解释非显而易见的约束（反爬、分页语义、站点特例），不写复述代码的废话。

## 合规与安全（强制）

爬虫仓库的默认姿态是**克制、可追溯、可关闭**。

- **遵守 robots.txt**（除非用户/任务明确要求且有合法授权说明）；默认开启。
- **限速与并发**：默认全局与 per-host 限速；浏览器模式尤其要限并发；禁止无上限扫站。
- **授权边界**：反爬兼容能力仅用于已获授权或合法可公开采集的目标；不用于入侵、盗号、刷量或规避访问控制。
- **身份与指纹**：L2/L3 可使用浏览器级指纹/客户端配置，但必须可配置、可审计；默认仍保留可识别的项目标识策略开关。
- **密钥**：代理、Cookie、Token、解锁服务 API Key 只来自环境变量或本地密钥管理；永不提交 `.env`、Cookie 导出、账号文件。
- **不生成攻击载荷**：不做漏洞利用、撞库、打码接码流水线、绕过付费墙/登录墙/权限校验。
- **数据最小化**：只抓任务需要的字段；日志默认脱敏（Cookie、Authorization、cf_clearance、手机号等）。
- **尊重站点条款与法律**：对明显违规抓取需求，拒绝实现并说明原因。

## 测试要求

- 新解析逻辑：至少一个 fixture 单测覆盖主路径与 1 个边界（空结果/缺字段/编码异常）。
- 改 downloader / 重试 / 限速：补充单元测试，必要时用 `respx` / `httpx.MockTransport` 模拟网络。
- 改挑战检测 / fetcher 升级：用本地 HTML/状态码 fixture 覆盖「识别 → 升级 → 会话复用」；不要用真实 Cloudflare 站点当单测。
- 不删除失败测试来“修绿”；先修产品代码或明确修正过时断言。
- PR 级改动至少保证：`ruff check`、`mypy`、`pytest` 通过（工具链就绪后）。

## Git 与变更纪律

- 只改任务相关文件；不顺手重构无关模块。
- 不主动创建 commit / PR，除非用户明确要求。
- 提交信息写清「为什么」，避免纯文件清单式描述。
- 约定变更（目录、命令、依赖、合规默认值）必须更新 `AGENTS.md`。

## Definition of Done

完成任务前自检：

- [ ] 改动落在正确层级（core / spider / middleware / pipeline / challenges）
- [ ] 无密钥、Cookie、`cf_clearance`、真实账号数据入库
- [ ] 新 Spider/解析有 fixture 测试或说明为何不可测
- [ ] 反爬处理走统一 fetcher/challenge 扩展点，未在 Spider 内私藏 hack
- [ ] 默认限速 / robots / 升维开关未被静默削弱
- [ ] `ruff` / `mypy` / `pytest`（若已配置）已通过
- [ ] 若改变 Agent 约定，已更新本文件

## 明确禁止

- 提交 `__pycache__/`、`.venv/`、抓取原始大文件、未脱敏日志
- 在库内加入后门式 remote loader 或未审计插件执行
- 用同步阻塞请求污染 asyncio 事件循环（必要时 `asyncio.to_thread`；Playwright 调用需隔离）
- 为通过测试而 mock 掉全部合规检查
- 实现或指导：漏洞利用、打码接码、绕过登录墙/付费墙/权限校验
- 将外部解锁服务或浏览器模式设为不可关闭的全局唯一路径

---

本文件是 Agent 的默认指令源。用户在对话中的明确指示优先于本文件；更近路径的嵌套 `AGENTS.md`（若存在）优先于根文件。
