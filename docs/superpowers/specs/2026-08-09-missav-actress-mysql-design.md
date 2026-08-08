# MissAV 女优页爬虫 + MySQL 落库设计

日期：2026-08-09  
状态：已落地  
范围：垂直切片——最小可跑 Hub 内核 + `missav_actress` Spider + MySQL upsert pipeline

## 背景

SpiderHub 目前处于 A 阶段工程骨架：CLI 占位、模块目录齐全，但无 Spider 基类、下载器、注册表或 pipeline。用户需要抓取 MissAV 女优页公开元数据，并写入本地 MySQL 8.0。

示例种子（亦为默认 `start_url`）：

`https://missav.ws/cn/actresses/%E5%8C%97%E9%87%8E%E6%9C%AA%E5%A5%88`

完整能力约定见根目录 `AGENTS.md`。本规格覆盖一次可交付的垂直切片，不为完整 Stage B/C 反爬栈买单。

## 目标

- 提供通用女优页 Spider：`missav_actress`，可通过 CLI 覆盖 `start-url`；默认种子为上述北野未奈页面
- 抓取女优元信息 + 该女优下**全部列表分页** + **每部作品详情页**补字段（含简介）
- 产物经 pydantic 校验后，按番号 upsert 写入 MySQL 8.0
- 提供用户可填写的配置示例（`.env.example` / `config.example.toml`）；密钥不入库
- 提供建表 SQL（`scripts/sql/missav_schema.sql`）；应用不自动 `CREATE TABLE`
- 补齐刚好能跑通的最小内核：Spider 基类、注册表、Runner、L1 httpx、挑战检测 stub、MySQL pipeline、CLI 接线
- Fixture 驱动的解析与 pipeline 单测；`ruff` / `mypy` / `pytest` 可通过

## 非目标

- 不实现 L2 `curl_cffi` / L3 Playwright 真实升维（可留接口或 stub）
- 不抓视频流、m3u8、下载地址，不绕过登录墙/付费墙/权限校验
- 不爬全站搜索、演员索引站、其它站点
- 不做自动建表/迁移框架
- 不引入 Scrapy；不把商业解锁服务写死进下载路径
- 不在 Spider 内私藏 Cloudflare 特例 hack

## 方案选择

采用**垂直切片**（相对「先完整 Stage B 再挂站」或「独立脚本绕过 Hub」）：

| 方案 | 取舍 |
|------|------|
| 垂直切片（选定） | 一次可 `run` 出库；Hub 只做最小集；遇强 CF 时需后续升维 |
| 完整 Stage B 再挂站 | Hub 更完整，但交付周期长 |
| 独立脚本 | 最快，但违反分层，后续难复用 |

## 架构与数据流

```text
CLI: spiderhub run missav_actress [--start-url ...] [--dry-run]
        │
        ▼
   Registry → MissavActressSpider
        │  start: 默认女优页（可覆盖）
        ▼
   Runner + L1 httpx Fetcher（挑战检测 stub；失败记明确错误）
        │
        ├─ 女优页：解析元信息 + 作品卡片 + 下一页
        ├─ 翻页直至结束
        └─ 每部作品详情页：补全字段 + 简介
        │
        ▼
   pydantic 校验
        │
        ├─ dry-run：只打日志/stdout，不写库
        └─ 默认：MySQL pipeline（按番号 upsert）
```

边界：

- 解析只在 `spiders/missav/`；网络 I/O 在 `downloaders/`；挑战识别在 `challenges/`
- 合规默认：遵守 robots、per-host 限速、可识别的项目策略开关保持可配置
- 配置优先级：CLI > 环境变量 > 配置文件 > 默认值

## 模块落点

| 层级 | 路径 | 职责 |
|------|------|------|
| CLI | `src/spiderhub/cli.py` | `list` 接注册表；`run` 执行；`--start-url`、`--dry-run` |
| Core | `src/spiderhub/core/` | Spider 协议/基类、注册表、Runner、配置加载 |
| Downloader | `src/spiderhub/downloaders/` | L1 httpx Fetcher；统一 Response 形态 |
| Challenges | `src/spiderhub/challenges/` | 挑战页检测 stub；明确错误类型 |
| Models | `src/spiderhub/models/` | `Actress`、`Work` 等共享 pydantic 模型 |
| Pipelines | `src/spiderhub/pipelines/` | MySQL upsert；dry-run 空操作/日志 |
| Spider | `src/spiderhub/spiders/missav/` | 女优页/列表/详情解析与 URL 生成 |
| SQL | `scripts/sql/missav_schema.sql` | 建表脚本 |
| Fixtures | `tests/fixtures/missav/` | HTML 夹具 |
| 配置示例 | `.env.example`、`config.example.toml` | 用户本地复制填写 |

## Spider 行为

**元数据**

- `name = "missav_actress"`
- `allowed_domains = ("missav.ws",)`
- `fetch_mode = "auto"`（本阶段实际走 L1；遇挑战抛明确错误，不在 Spider 内绕过）
- `obey_robots = True`（可配置关闭，默认开）
- 默认限速：可配置 `SPIDERHUB_REQUEST_DELAY_SECONDS`（默认建议 `1.0`）

**抓取流程**

1. 请求女优页（默认或 `--start-url`）
2. 解析女优元信息（姓名、slug、封面、简介若有、profile URL）
3. 解析当前页作品卡片，收集详情 URL（及卡片上已有的番号/标题/封面等，详情页再覆盖补全）
4. 若存在下一页，继续翻页直至结束
5. 对每个详情 URL 请求详情页，解析完整作品字段
6. yield 结构化 `Actress` / `Work`（及关联所需的 actress slug、tag 名列表）

**详情页字段（选定 B）**

- 番号、标题、发行日、时长、女优、厂牌/片商/系列、标签、封面、详情 URL
- 简介/描述文案
- 不包含任何播放地址或下载链

**CLI 示例**

```bash
uv run spiderhub list
uv run spiderhub run missav_actress --dry-run
uv run spiderhub run missav_actress
uv run spiderhub run missav_actress --start-url 'https://missav.ws/cn/actresses/...'
```

## 数据模型（pydantic）

逻辑模型（字段名可在实现时微调，但语义固定）：

- `Actress`：`slug`, `name`, `name_ja?`, `name_en?`, `profile_url`, `cover_url?`, `bio?`, `source="missav"`
- `Work`：`code`, `title`, `description?`, `release_date?`, `duration_seconds?`, `maker?`, `label?`, `series?`, `cover_url?`, `detail_url`, `actress_slugs` / `actress_names`, `tags`, `source="missav"`

校验规则：

- `code`、`detail_url`、`title` 为作品必填；缺番号则丢弃该条并打警告
- 日期/时长解析失败时置空并记警告，不导致整次 run 失败

## MySQL 表结构（8.0）

字符集建议：`utf8mb4`，排序规则 `utf8mb4_unicode_ci`。

### `actresses`

| 字段 | 类型/约束 | 说明 |
|------|-----------|------|
| `id` | BIGINT PK AI | |
| `slug` | VARCHAR UNIQUE | 女优 URL slug |
| `name` | VARCHAR | 显示名 |
| `name_ja` | VARCHAR NULL | |
| `name_en` | VARCHAR NULL | |
| `profile_url` | VARCHAR | |
| `cover_url` | VARCHAR NULL | |
| `bio` | TEXT NULL | |
| `source` | VARCHAR | 固定 `missav` |
| `created_at` / `updated_at` | DATETIME | |

### `works`

| 字段 | 类型/约束 | 说明 |
|------|-----------|------|
| `id` | BIGINT PK AI | |
| `code` | VARCHAR UNIQUE | upsert 键 |
| `title` | VARCHAR | |
| `description` | TEXT NULL | |
| `release_date` | DATE NULL | |
| `duration_seconds` | INT NULL | |
| `maker` / `label` / `series` | VARCHAR NULL | |
| `cover_url` | VARCHAR NULL | |
| `detail_url` | VARCHAR(1024)，非唯一索引（前缀 255） | utf8mb4 下 UNIQUE 会超出 InnoDB 索引键长度限制，upsert 键仍用 `code` |
| `source` | VARCHAR | `missav` |
| `created_at` / `updated_at` | DATETIME | |

### `tags`

| 字段 | 说明 |
|------|------|
| `id` | BIGINT PK AI |
| `name` | UNIQUE |
| `source` | `missav` |

### 关联

- `work_actresses (work_id, actress_id)` 复合主键
- `work_tags (work_id, tag_id)` 复合主键

### 写入策略

- `actresses`：按 `slug` upsert
- `works`：按 `code` 做 `INSERT ... ON DUPLICATE KEY UPDATE`（更新标题、简介、日期等可变字段）
- `tags`：按 `name` upsert
- 关联表：按该 `work` **覆盖对齐**（删除该 work 旧关联后写入新关联，或等价的 diff 同步），避免脏标签残留
- 应用**不**自动建表；用户先执行 `scripts/sql/missav_schema.sql`

## 配置

仓库提交示例，不提交真实密钥：

**环境变量（`.env.example`）**

```bash
SPIDERHUB_MYSQL_HOST=127.0.0.1
SPIDERHUB_MYSQL_PORT=3306
SPIDERHUB_MYSQL_USER=
SPIDERHUB_MYSQL_PASSWORD=
SPIDERHUB_MYSQL_DATABASE=spiderhub

SPIDERHUB_OBEY_ROBOTS=true
SPIDERHUB_REQUEST_DELAY_SECONDS=1.0
```

**可选 TOML（`config.example.toml`）** 镜像上述键；本地 `config.local.toml` / `.env` 均 gitignore。

优先级：CLI > 环境变量 > 配置文件 > 默认值。

MySQL 驱动锁定为 `PyMySQL`（同步）。Runner 若为 asyncio，写库经 `asyncio.to_thread`，避免阻塞事件循环。不在本切片引入 `aiomysql`。

## 错误处理

| 场景 | 行为 |
|------|------|
| 网络/超时/非 2xx | 记录 URL、状态码、重试次数；有限重试；耗尽则该 URL 失败 |
| 挑战页 | 检测后抛明确错误类型；不当正常 HTML 解析 |
| 详情页失败 | 跳过该作品并计数，不中断整次 run |
| 缺番号 | 丢弃并警告 |
| MySQL 连接失败 | CLI 非 0 退出 |
| 单条 upsert 失败 | 记日志并继续；结束汇总失败数 |
| `--dry-run` | 解析+校验，不连库、不写库 |

## 测试计划

- Fixture：女优列表页（含下一页链）、详情页、空列表、缺字段各至少 1 份（真实 HTML 脱敏后入库，禁止单测打真站）
- 单测：列表解析、翻页 URL、详情解析主路径与边界
- Pipeline 单测：mock 连接或断言 upsert SQL/参数拼装，不连真实 MySQL
- CLI：`list` 能看到 `missav_actress`；`run --dry-run` 在 fixture/mock 传输下可走通（若集成成本过高，至少保证单元层覆盖 Runner 接线）
- 质量：`uv run ruff check .`、`uv run mypy src`、`uv run pytest`

## 依赖增量（相对 A 阶段空依赖）

运行时（预期）：

- `httpx`
- `pydantic` v2
- `selectolax`
- `PyMySQL`
- `python-dotenv`（加载本地 `.env`）

开发依赖保持现有 `ruff` / `mypy` / `pytest` / `pytest-asyncio`；网络 mock 可用 `respx` 或 `httpx.MockTransport`。

## 合规与安全

- 仅采集公开页面元数据；不做视频下载或访问控制绕过
- 默认 robots + 限速；浏览器/外部解锁路径本阶段未启用且保持可关闭原则
- MySQL 密码、Cookie 等只来自本地配置/环境变量
- 日志脱敏：不打印密码、完整 Cookie、Authorization

## 实现顺序（供后续 plan 拆解）

1. 配置加载 + 依赖写入 `pyproject.toml`
2. 核心：Spider 协议、注册表、Runner、L1 fetcher、挑战 stub
3. Models + MySQL schema SQL + MySQL pipeline（upsert）
4. `missav_actress` 解析（fixture 先行）
5. CLI 接线与 dry-run
6. 文档：`README.md` / `AGENTS.md` 同步命令与依赖约定

## 验收标准

- [ ] `uv run spiderhub list` 显示 `missav_actress`
- [ ] `uv run spiderhub run missav_actress --dry-run` 不写库且退出码 0（在可测环境下）
- [ ] 配置正确时，真实 run 可将女优与作品 upsert 进 MySQL 8.0
- [ ] 重复 run 同一女优不产生重复 `works.code`，字段可更新
- [ ] 无密钥入库；无视频流相关字段或逻辑
- [ ] `ruff` / `mypy` / `pytest` 通过
