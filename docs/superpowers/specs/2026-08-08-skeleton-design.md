# SpiderHub 工程骨架（A）设计

日期：2026-08-08  
状态：待用户审阅  
范围：仅工程骨架，不含抓取实现

## 背景

SpiderHub 是基于 Python 的爬虫中枢。完整能力见根目录 `AGENTS.md`。本规格只覆盖第一刀 **A：工程骨架**，使仓库可安装、可静态检查、可运行占位 CLI，并为后续 B/C（Hub 内核与反爬分层）预留目录。

## 目标

- 建立 `uv` + `pyproject.toml` + `src/` 布局的可安装包 `spiderhub`
- 按 `AGENTS.md` 建齐模块目录（无业务实现）
- 提供 CLI 占位：`list`、`run`、`--dry-run`
- 配置 `ruff` / `mypy` / `pytest`，并有一条 CLI 冒烟测试
- 提供最小 `README.md`、`.env.example`、`.gitignore`

## 非目标

- 不实现 httpx / curl_cffi / Playwright Fetcher
- 不实现挑战检测、升维、代理池、pipeline 业务逻辑
- 不实现真实 Spider 或注册发现机制（可留空包）
- 不引入 Scrapy、Typer、Click
- 不配置完整 pre-commit 钩子（可后续加；A 阶段以 `uv run` 直接跑检查为准）

## 方案选择

采用**手写最小骨架**（相对 `uv init` 后改造、或上完整 CLI 框架）：

- 目录与 `AGENTS.md` 一一对应，后续 B/C 不返工
- 运行时依赖保持为空，避免空壳阶段背负浏览器等重依赖
- CLI 使用标准库 `argparse`

## 包与工具配置

### `pyproject.toml`

| 项 | 约定 |
|----|------|
| 包名 | `spiderhub` |
| Python | `>=3.11` |
| 布局 | `src/spiderhub` |
| 构建后端 | `hatchling` |
| 脚本入口 | `spiderhub = "spiderhub.cli:main"` |
| 运行时依赖 | 无（A 阶段） |
| 可选/开发依赖 | `ruff`、`mypy`、`pytest`、`pytest-asyncio` |
| Ruff | lint + format，覆盖 `src` 与 `tests` |
| mypy | 检查 `src`；`strict` 或接近 strict 的实用配置 |
| pytest | `testpaths = ["tests"]`，asyncio 模式按需 |

### 系统依赖

遵循 `AGENTS.md`「系统依赖安装」：

1. Python 包用 `uv`，不用系统 pip 乱装
2. 若本机无 `uv`，仅尝试 `brew install uv`
3. brew 不可用或失败则停止并通知用户自行安装，不得改用其它安装方式

## 目录结构

```text
SpiderHub/
├── AGENTS.md
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── src/spiderhub/
│   ├── __init__.py          # 版本号等最小导出
│   ├── cli.py               # argparse：list / run
│   ├── core/
│   ├── downloaders/
│   ├── challenges/
│   ├── middlewares/
│   ├── pipelines/
│   ├── spiders/
│   ├── models/
│   └── utils/
├── tests/
│   ├── unit/
│   │   └── test_cli.py
│   ├── integration/         # 占位（可仅 .gitkeep 或空 __init__ 说明）
│   └── fixtures/
└── docs/superpowers/specs/  # 本文件所在
```

各子包仅含 `__init__.py`（可含一句话模块说明 docstring），**不写业务代码**。

## CLI 行为

入口：`spiderhub.cli:main`

| 命令 | 行为 | 退出码 |
|------|------|--------|
| `spiderhub list` | 打印提示：暂无已注册 spider（或空列表说明） | 0 |
| `spiderhub run <name>` | 打印「尚未实现」类明确错误信息 | 非 0（建议 2） |
| `spiderhub run <name> --dry-run` | 同样未实现，但信息中体现 dry-run 占位 | 非 0 |

不在 A 阶段连接注册表或网络。

## 测试

- `tests/unit/test_cli.py`：通过 `subprocess` 或直接调用 `main` 的参数解析路径，断言 `list` 退出码为 0、stdout 含预期提示
- 不访问外网
- `integration/` 与 `fixtures/` 预留，A 可不写集成测

## 文档与忽略规则

- `README.md`：项目一句话、`uv sync`、常用检查命令、`spiderhub list` 示例；指向 `AGENTS.md`
- `.env.example`：仅键名占位（如 `SPIDERHUB_LOG_LEVEL=`），无密钥
- `.gitignore`：`.venv/`、`__pycache__/`、`.env`、`dist/`、`.mypy_cache/`、`.ruff_cache/`、`.pytest_cache/`、`uv.lock` 是否提交：A 阶段**提交 `uv.lock`** 以便复现（若生成）

## 验收标准

- [ ] `brew` 可用时能安装/使用 `uv`；否则已提示用户手动安装
- [ ] `uv sync` 成功
- [ ] `uv run ruff check .` 通过
- [ ] `uv run mypy src` 通过
- [ ] `uv run pytest` 通过
- [ ] `uv run spiderhub list` 可执行且退出码 0
- [ ] 目录结构与 `AGENTS.md` 推荐布局一致（A 范围内）

## 后续（非本规格）

- **B**：Spider 基类与注册表、L1 httpx、限速/重试、挑战 stub、示例 Spider、JSON/内存 pipeline
- **C**：L2 curl_cffi、L3 Playwright、自动升维与会话复用

## 风险与约束

- 本机当前可能无 `uv`：实现阶段必须走 brew，失败则停
- A 不做反爬，避免在空壳阶段引入 Playwright 系统依赖
