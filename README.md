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

当前 `run` 为占位实现，尚未执行真实抓取。
