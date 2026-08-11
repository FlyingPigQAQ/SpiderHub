# 本机 CDP 自动启动 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 配置 `browser_cdp_enabled` 后由代码自动探测/启动本机 Chromium 系浏览器并连接 CDP，用户无需手动 `mkdir` / 起 Chrome。

**Architecture:** 新增 `ChromeCdpLauncher` 负责探测可执行文件、Popen 原生浏览器、轮询 `/json/version`、按 `keep_alive` 关进程。`PlaywrightFetcher` 在 CDP 模式下只 `connect_over_cdp`，不再 launch Playwright Chromium。`browser_cdp_enabled` 或非空 `browser_cdp_url` 均视为 CDP 模式。

**Tech Stack:** Python ≥3.11、`subprocess`、`httpx`（就绪探测）、现有 Playwright CDP、`pytest`。

## Global Constraints

- Spec: `docs/superpowers/specs/2026-08-11-cdp-auto-launch-design.md`
- 探测顺序固定：Chrome → Chromium → Edge
- 无浏览器时提示 `brew install --cask google-chrome`；代码不代装
- 用户提供 `browser_cdp_url` → 永不自动 Popen
- 默认端口 `9222`；占用但非 CDP → 报错，不换端口
- CDP 开启时不 launch Playwright / Camoufox / Patchright
- 单测禁止起真实浏览器；全部 mock
- 不主动 commit（除非用户明确要求）；下列 Commit 步骤默认跳过

---

## File map

| Path | Change |
|------|--------|
| `src/spiderhub/core/settings.py` | 新增 `browser_cdp_enabled` / `browser_cdp_keep_alive`；`cdp_mode_active()` |
| `src/spiderhub/downloaders/cdp_launcher.py` | **新建** `ChromeCdpLauncher` |
| `src/spiderhub/downloaders/playwright_fetcher.py` | CDP 分支调用 launcher；exit 时 shutdown |
| `src/spiderhub/downloaders/browser_factory.py` | `cdp_mode_active` 强制 PlaywrightFetcher |
| `src/spiderhub/downloaders/auto_fetcher.py` | `_prefer_http_after_browser` 用 `cdp_mode_active` |
| `config.example.toml` / `.env.example` | 新字段 |
| `README.md` / `AGENTS.md` | 文档改为自动启动优先 |
| `tests/unit/test_settings.py` | 新字段解析 |
| `tests/unit/test_cdp_launcher.py` | **新建** launcher 单测 |
| `tests/unit/test_browser_factory.py` | enabled 强制 CDP |
| `tests/unit/test_auto_fetcher.py` | enabled 时 prefer L2 |
| `tests/unit/test_playwright_fetcher.py` | mock launcher，断言不 launch |

---

### Task 1: Settings — `browser_cdp_enabled` / `keep_alive` / `cdp_mode_active`

**Files:**
- Modify: `src/spiderhub/core/settings.py`
- Modify: `tests/unit/test_settings.py`

**Interfaces:**
- Produces:
  - `Settings.browser_cdp_enabled: bool`（默认 `False`）
  - `Settings.browser_cdp_keep_alive: bool`（默认 `False`）
  - `cdp_mode_active(settings: Settings) -> bool`

- [ ] **Step 1: Write the failing test**

在 `tests/unit/test_settings.py` 增加：

```python
from spiderhub.core.settings import Settings, cdp_mode_active, load_settings


def test_browser_cdp_enabled_and_keep_alive_env(tmp_path: Path) -> None:
    settings = load_settings(
        env={
            "SPIDERHUB_BROWSER_CDP_ENABLED": "true",
            "SPIDERHUB_BROWSER_CDP_KEEP_ALIVE": "true",
        },
        config_path=tmp_path / "missing.toml",
    )
    assert settings.browser_cdp_enabled is True
    assert settings.browser_cdp_keep_alive is True
    assert settings.browser_cdp_url == ""
    defaults = load_settings(env={}, config_path=tmp_path / "missing.toml")
    assert defaults.browser_cdp_enabled is False
    assert defaults.browser_cdp_keep_alive is False


def test_browser_cdp_flags_from_toml(tmp_path: Path) -> None:
    path = tmp_path / "config.toml"
    path.write_text(
        "[crawl]\nbrowser_cdp_enabled = true\nbrowser_cdp_keep_alive = true\n",
        encoding="utf-8",
    )
    settings = load_settings(env={}, config_path=path)
    assert settings.browser_cdp_enabled is True
    assert settings.browser_cdp_keep_alive is True


def test_cdp_mode_active() -> None:
    assert cdp_mode_active(Settings(browser_cdp_enabled=True)) is True
    assert cdp_mode_active(
        Settings(browser_cdp_url="http://127.0.0.1:9222")
    ) is True
    assert cdp_mode_active(Settings()) is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/unit/test_settings.py::test_browser_cdp_enabled_and_keep_alive_env tests/unit/test_settings.py::test_browser_cdp_flags_from_toml tests/unit/test_settings.py::test_cdp_mode_active -v`

Expected: FAIL（字段 / `cdp_mode_active` 不存在）

- [ ] **Step 3: Implement**

在 `Settings` dataclass 中、`browser_cdp_url` 附近增加：

```python
browser_cdp_enabled: bool = False
browser_cdp_keep_alive: bool = False
```

在模块级增加：

```python
def cdp_mode_active(settings: Settings) -> bool:
    """True when CDP path should be used (enabled flag or explicit URL)."""
    return bool(settings.browser_cdp_enabled) or bool(
        settings.browser_cdp_url.strip()
    )
```

在 `load_settings` 的 `data` 字典中增加：

```python
"browser_cdp_enabled": _as_bool(crawl.get("browser_cdp_enabled"), False),
"browser_cdp_keep_alive": _as_bool(crawl.get("browser_cdp_keep_alive"), False),
```

在 `env_map` 中增加：

```python
"browser_cdp_enabled": "SPIDERHUB_BROWSER_CDP_ENABLED",
"browser_cdp_keep_alive": "SPIDERHUB_BROWSER_CDP_KEEP_ALIVE",
```

把 `browser_cdp_enabled`、`browser_cdp_keep_alive` 加入 env 布尔字段集合（与 `allow_browser` 同一分支）。

注意：`_as_bool(environ[key], True)` 当前对布尔字段用 default `True`，但只要 key 存在就会解析字符串；保持与现有 `allow_browser` 一致即可。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_settings.py -v`

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add src/spiderhub/core/settings.py tests/unit/test_settings.py
git commit -m "feat: add browser_cdp_enabled and keep_alive settings"
```

---

### Task 2: `ChromeCdpLauncher` — 探测 / 启动 / 就绪 / shutdown

**Files:**
- Create: `src/spiderhub/downloaders/cdp_launcher.py`
- Create: `tests/unit/test_cdp_launcher.py`

**Interfaces:**
- Consumes: `Settings.browser_cdp_url`, `browser_user_data_dir`, `browser_cdp_keep_alive`
- Produces:
  - `class BrowserNotFoundError(RuntimeError)`
  - `class CdpEndpointError(RuntimeError)`
  - `class ChromeCdpLauncher`
  - `async def ensure_ready(self, settings: Settings) -> str`  # 最终 CDP URL
  - `async def shutdown(self) -> None`
  - `def find_chromium_executable() -> Path | None`（便于单测）
  - `DEFAULT_CDP_URL = "http://127.0.0.1:9222"`
  - `DEFAULT_CDP_PORT = 9222`

- [ ] **Step 1: Write the failing tests**

创建 `tests/unit/test_cdp_launcher.py`：

```python
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spiderhub.core.settings import Settings
from spiderhub.downloaders.cdp_launcher import (
    DEFAULT_CDP_URL,
    BrowserNotFoundError,
    CdpEndpointError,
    ChromeCdpLauncher,
    find_chromium_executable,
)


def test_find_chromium_prefers_chrome_then_chromium_then_edge(
    tmp_path: Path,
) -> None:
    chrome = tmp_path / "Google Chrome"
    chromium = tmp_path / "Chromium"
    edge = tmp_path / "Microsoft Edge"
    chrome.write_text("x")
    chromium.write_text("x")
    edge.write_text("x")
    candidates = [
        (chrome, "chrome"),
        (chromium, "chromium"),
        (edge, "edge"),
    ]
    with patch(
        "spiderhub.downloaders.cdp_launcher._candidate_binaries",
        return_value=candidates,
    ):
        assert find_chromium_executable() == chrome

    chrome.unlink()
    with patch(
        "spiderhub.downloaders.cdp_launcher._candidate_binaries",
        return_value=candidates,
    ):
        assert find_chromium_executable() == chromium

    chromium.unlink()
    with patch(
        "spiderhub.downloaders.cdp_launcher._candidate_binaries",
        return_value=candidates,
    ):
        assert find_chromium_executable() == edge


def test_find_chromium_none_returns_none(tmp_path: Path) -> None:
    missing = tmp_path / "nope"
    with patch(
        "spiderhub.downloaders.cdp_launcher._candidate_binaries",
        return_value=[(missing, "chrome")],
    ):
        assert find_chromium_executable() is None


@pytest.mark.asyncio
async def test_ensure_ready_with_explicit_url_does_not_popen() -> None:
    launcher = ChromeCdpLauncher()
    settings = Settings(browser_cdp_url="http://127.0.0.1:9222")
    with (
        patch.object(
            launcher, "_wait_cdp_ready", new=AsyncMock(return_value=None)
        ),
        patch("spiderhub.downloaders.cdp_launcher.subprocess.Popen") as popen,
    ):
        url = await launcher.ensure_ready(settings)
    assert url == "http://127.0.0.1:9222"
    popen.assert_not_called()
    assert launcher.started_by_us is False


@pytest.mark.asyncio
async def test_ensure_ready_reuses_existing_endpoint_without_popen(
    tmp_path: Path,
) -> None:
    launcher = ChromeCdpLauncher()
    settings = Settings(
        browser_cdp_enabled=True,
        browser_user_data_dir=str(tmp_path / "profile"),
    )
    with (
        patch.object(
            launcher, "_probe_cdp", new=AsyncMock(return_value=True)
        ),
        patch("spiderhub.downloaders.cdp_launcher.subprocess.Popen") as popen,
        patch(
            "spiderhub.downloaders.cdp_launcher.find_chromium_executable",
            return_value=tmp_path / "chrome",
        ),
    ):
        (tmp_path / "chrome").write_text("x")
        url = await launcher.ensure_ready(settings)
    assert url == DEFAULT_CDP_URL
    popen.assert_not_called()
    assert launcher.started_by_us is False


@pytest.mark.asyncio
async def test_ensure_ready_launches_when_enabled_and_port_free(
    tmp_path: Path,
) -> None:
    launcher = ChromeCdpLauncher()
    profile = tmp_path / "profile"
    chrome = tmp_path / "Google Chrome"
    chrome.write_text("x")
    settings = Settings(
        browser_cdp_enabled=True,
        browser_user_data_dir=str(profile),
    )
    proc = MagicMock()
    proc.poll.return_value = None
    with (
        patch.object(launcher, "_probe_cdp", new=AsyncMock(return_value=False)),
        patch.object(launcher, "_port_in_use", return_value=False),
        patch(
            "spiderhub.downloaders.cdp_launcher.find_chromium_executable",
            return_value=chrome,
        ),
        patch(
            "spiderhub.downloaders.cdp_launcher.subprocess.Popen",
            return_value=proc,
        ) as popen,
        patch.object(
            launcher, "_wait_cdp_ready", new=AsyncMock(return_value=None)
        ),
    ):
        url = await launcher.ensure_ready(settings)
    assert url == DEFAULT_CDP_URL
    assert profile.is_dir()
    popen.assert_called_once()
    args = popen.call_args.args[0]
    assert "--remote-debugging-port=9222" in args
    assert any(str(profile) in a for a in args)
    assert launcher.started_by_us is True


@pytest.mark.asyncio
async def test_ensure_ready_raises_when_no_browser(tmp_path: Path) -> None:
    launcher = ChromeCdpLauncher()
    settings = Settings(
        browser_cdp_enabled=True,
        browser_user_data_dir=str(tmp_path / "p"),
    )
    with (
        patch.object(launcher, "_probe_cdp", new=AsyncMock(return_value=False)),
        patch(
            "spiderhub.downloaders.cdp_launcher.find_chromium_executable",
            return_value=None,
        ),
        pytest.raises(BrowserNotFoundError, match="brew install --cask google-chrome"),
    ):
        await launcher.ensure_ready(settings)


@pytest.mark.asyncio
async def test_ensure_ready_port_busy_non_cdp_raises(tmp_path: Path) -> None:
    """Port open but /json/version invalid → do not launch, do not steal port."""
    launcher = ChromeCdpLauncher()
    settings = Settings(
        browser_cdp_enabled=True,
        browser_user_data_dir=str(tmp_path / "p"),
    )
    with (
        patch.object(
            launcher,
            "_port_in_use",
            return_value=True,
        ),
        patch.object(launcher, "_probe_cdp", new=AsyncMock(return_value=False)),
        patch("spiderhub.downloaders.cdp_launcher.subprocess.Popen") as popen,
        pytest.raises(CdpEndpointError, match="9222"),
    ):
        await launcher.ensure_ready(settings)
    popen.assert_not_called()


@pytest.mark.asyncio
async def test_shutdown_terminates_only_when_started_and_not_keep_alive() -> None:
    launcher = ChromeCdpLauncher()
    proc = MagicMock()
    proc.poll.return_value = None
    launcher._proc = proc
    launcher._started_by_us = True
    launcher._keep_alive = False
    await launcher.shutdown()
    proc.terminate.assert_called_once()

    launcher2 = ChromeCdpLauncher()
    proc2 = MagicMock()
    launcher2._proc = proc2
    launcher2._started_by_us = True
    launcher2._keep_alive = True
    await launcher2.shutdown()
    proc2.terminate.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_cdp_launcher.py -v`

Expected: FAIL（模块不存在）

- [ ] **Step 3: Implement `cdp_launcher.py`**

创建 `src/spiderhub/downloaders/cdp_launcher.py`，核心结构如下（实现时保持类型注解与 logging，禁止 `print`）：

```python
from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import httpx

from spiderhub.core.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_URL = f"http://127.0.0.1:{DEFAULT_CDP_PORT}"
_READY_TIMEOUT_S = 30.0
_READY_POLL_S = 0.25

_MAC_CHROME = Path(
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
)
_MAC_CHROMIUM = Path("/Applications/Chromium.app/Contents/MacOS/Chromium")
_MAC_EDGE = Path(
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
)


class BrowserNotFoundError(RuntimeError):
    """No Chrome / Chromium / Edge binary found for CDP launch."""


class CdpEndpointError(RuntimeError):
    """CDP endpoint missing, invalid, or port conflict."""


def _candidate_binaries() -> list[tuple[Path, str]]:
    """Ordered (path, label) candidates: Chrome → Chromium → Edge."""
    items: list[tuple[Path, str]] = [
        (_MAC_CHROME, "chrome"),
        (_MAC_CHROMIUM, "chromium"),
        (_MAC_EDGE, "edge"),
    ]
    for name, label in (
        ("google-chrome", "chrome"),
        ("google-chrome-stable", "chrome"),
        ("chromium", "chromium"),
        ("chromium-browser", "chromium"),
        ("microsoft-edge", "edge"),
        ("microsoft-edge-stable", "edge"),
    ):
        found = shutil.which(name)
        if found:
            items.append((Path(found), label))
    return items


def find_chromium_executable() -> Path | None:
    seen: set[Path] = set()
    for path, _label in _candidate_binaries():
        resolved = path if path.is_absolute() else path
        if resolved in seen:
            continue
        seen.add(resolved)
        if resolved.is_file():
            return resolved
    return None


class ChromeCdpLauncher:
    def __init__(self) -> None:
        self._proc: subprocess.Popen[bytes] | None = None
        self._started_by_us = False
        self._keep_alive = False
        self._cdp_url = ""

    @property
    def started_by_us(self) -> bool:
        return self._started_by_us

    def _port_in_use(self, host: str, port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.3)
            return sock.connect_ex((host, port)) == 0

    async def _probe_cdp(self, cdp_url: str) -> bool:
        version_url = cdp_url.rstrip("/") + "/json/version"
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                resp = await client.get(version_url)
            if resp.status_code != 200:
                return False
            data = resp.json()
        except Exception:  # noqa: BLE001
            return False
        return bool(
            isinstance(data, dict)
            and (data.get("webSocketDebuggerUrl") or data.get("Browser"))
        )

    async def _wait_cdp_ready(self, cdp_url: str) -> None:
        deadline = asyncio.get_running_loop().time() + _READY_TIMEOUT_S
        while asyncio.get_running_loop().time() < deadline:
            if await self._probe_cdp(cdp_url):
                return
            if self._proc is not None and self._proc.poll() is not None:
                raise CdpEndpointError(
                    f"浏览器进程已退出，CDP 未就绪 url={cdp_url}"
                )
            await asyncio.sleep(_READY_POLL_S)
        if self._started_by_us:
            await self._terminate_proc()
        raise CdpEndpointError(f"等待 CDP 就绪超时 url={cdp_url}")

    def _launch(self, binary: Path, *, user_data_dir: Path, port: int) -> None:
        user_data_dir.mkdir(parents=True, exist_ok=True)
        args = [
            str(binary),
            f"--remote-debugging-port={port}",
            f"--user-data-dir={user_data_dir}",
            "--no-first-run",
            "--no-default-browser-check",
        ]
        logger.info(
            "launching native browser for CDP binary=%s port=%s profile=%s",
            binary,
            port,
            user_data_dir,
        )
        self._proc = subprocess.Popen(  # noqa: S603 — fixed binary + args
            args,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        self._started_by_us = True

    async def ensure_ready(self, settings: Settings) -> str:
        self._keep_alive = bool(settings.browser_cdp_keep_alive)
        explicit = settings.browser_cdp_url.strip()
        if explicit:
            self._cdp_url = explicit
            await self._wait_cdp_ready(explicit)
            return explicit

        cdp_url = DEFAULT_CDP_URL
        host, port = "127.0.0.1", DEFAULT_CDP_PORT
        if await self._probe_cdp(cdp_url):
            logger.info("reusing existing CDP endpoint url=%s", cdp_url)
            self._cdp_url = cdp_url
            self._started_by_us = False
            return cdp_url

        if self._port_in_use(host, port):
            raise CdpEndpointError(
                f"端口 {port} 已被占用但不是合法 CDP 端点；"
                f"请释放该端口，或设置 SPIDERHUB_BROWSER_CDP_URL 指向已有调试浏览器"
            )

        binary = find_chromium_executable()
        if binary is None:
            raise BrowserNotFoundError(
                "未找到支持 CDP 的本机浏览器（Google Chrome / Chromium / Microsoft Edge）。"
                "请安装 Google Chrome 后重试：\n"
                "  brew install --cask google-chrome"
            )

        profile = Path(settings.browser_user_data_dir)
        self._launch(binary, user_data_dir=profile, port=port)
        await self._wait_cdp_ready(cdp_url)
        self._cdp_url = cdp_url
        return cdp_url

    async def _terminate_proc(self) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                await asyncio.to_thread(self._proc.wait, 5)
            except Exception:  # noqa: BLE001
                self._proc.kill()
        self._proc = None

    async def shutdown(self) -> None:
        if not self._started_by_us or self._keep_alive:
            if self._keep_alive and self._started_by_us:
                logger.info(
                    "browser_cdp_keep_alive=true; leaving CDP browser running url=%s",
                    self._cdp_url or DEFAULT_CDP_URL,
                )
            self._proc = None
            self._started_by_us = False
            return
        await self._terminate_proc()
        self._started_by_us = False
```

说明：`ensure_ready` 在「显式 URL」路径调用 `_wait_cdp_ready`（连不上则报错、不 Popen）。自动路径先 `_probe_cdp`，再 `_port_in_use`，再 launch。

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/unit/test_cdp_launcher.py -v`

Expected: PASS

若 `test_ensure_ready_launches_when_enabled_and_port_free` 因 `_probe_cdp` side_effect 次数与实现不完全一致而失败，按实现调整 mock（目标：port 空闲 + 需 launch + `_wait_cdp_ready` 成功）。

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add src/spiderhub/downloaders/cdp_launcher.py tests/unit/test_cdp_launcher.py
git commit -m "feat: add ChromeCdpLauncher for native browser CDP"
```

---

### Task 3: 接入 `PlaywrightFetcher` + `browser_factory` + `AutoFetcher`

**Files:**
- Modify: `src/spiderhub/downloaders/playwright_fetcher.py`
- Modify: `src/spiderhub/downloaders/browser_factory.py`
- Modify: `src/spiderhub/downloaders/auto_fetcher.py`
- Modify: `tests/unit/test_browser_factory.py`
- Modify: `tests/unit/test_auto_fetcher.py`
- Modify: `tests/unit/test_playwright_fetcher.py`

**Interfaces:**
- Consumes: `cdp_mode_active`, `ChromeCdpLauncher.ensure_ready` / `shutdown`
- Produces: CDP 模式下 `PlaywrightFetcher` 持有 launcher，exit 时关闭自启浏览器

- [ ] **Step 1: Write / extend failing tests**

`tests/unit/test_browser_factory.py` 增加：

```python
def test_cdp_enabled_forces_playwright_without_url() -> None:
    settings = Settings(
        browser_engine="camoufox",
        browser_cdp_enabled=True,
    )
    assert isinstance(build_l3_fetcher(settings), PlaywrightFetcher)
```

并更新 `test_unknown_engine_raises` 的 fake，使 `browser_cdp_enabled=False`（或用真实 Settings）。

`tests/unit/test_auto_fetcher.py` 增加（沿用现有 CDP prefer-L2 风格，把 `browser_cdp_url` 换成 `browser_cdp_enabled=True`）：

```python
@pytest.mark.asyncio
async def test_auto_fetcher_cdp_enabled_prefers_l2_flag() -> None:
    settings = Settings(
        request_delay_seconds=0.0,
        browser_cdp_enabled=True,
        allow_fetcher_upgrade=True,
        allow_browser=True,
    )
    async with AutoFetcher(settings) as fetcher:
        assert fetcher._prefer_http_after_browser is True
```

`tests/unit/test_playwright_fetcher.py` 增加（mock launcher，断言不走 launch）：

```python
@pytest.mark.asyncio
async def test_playwright_fetcher_cdp_enabled_uses_launcher_not_launch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = Settings(
        request_delay_seconds=0.0,
        browser_cdp_enabled=True,
        browser_challenge_wait_seconds=1.0,
    )
    ensure = AsyncMock(return_value="http://127.0.0.1:9222")
    shutdown = AsyncMock()
    fake_launcher = SimpleNamespace(
        ensure_ready=ensure,
        shutdown=shutdown,
    )

    class FakeLauncherCls:
        def __init__(self) -> None:
            pass

        async def ensure_ready(self, _settings: Settings) -> str:
            return await ensure(_settings)

        async def shutdown(self) -> None:
            await shutdown()

    monkeypatch.setattr(
        "spiderhub.downloaders.playwright_fetcher.ChromeCdpLauncher",
        FakeLauncherCls,
    )

    launched = {"persistent": 0, "ephemeral": 0}

    async def boom_persistent(self: PlaywrightFetcher) -> None:
        launched["persistent"] += 1
        raise AssertionError("must not launch persistent under CDP")

    async def boom_ephemeral(self: PlaywrightFetcher) -> None:
        launched["ephemeral"] += 1
        raise AssertionError("must not launch ephemeral under CDP")

    monkeypatch.setattr(PlaywrightFetcher, "_launch_persistent", boom_persistent)
    monkeypatch.setattr(PlaywrightFetcher, "_launch_ephemeral", boom_ephemeral)

    connect = AsyncMock()
    monkeypatch.setattr(PlaywrightFetcher, "_connect_cdp", connect)

    # Avoid real playwright start
    class FakePw:
        async def stop(self) -> None:
            return None

    async def fake_start() -> FakePw:
        return FakePw()

    monkeypatch.setattr(
        "playwright.async_api.async_playwright",
        lambda: SimpleNamespace(start=fake_start),
    )

    fetcher = PlaywrightFetcher(settings)
    async with fetcher:
        ensure.assert_awaited()
        connect.assert_awaited()
    assert launched["persistent"] == 0
    assert launched["ephemeral"] == 0
    shutdown.assert_awaited()
```

若现有 playwright 测试对 `async_playwright` 的 mock 模式不同，对齐该文件已有 fixture 写法，目标不变：CDP enabled → `ensure_ready` + `_connect_cdp`，不 launch，exit 调 `shutdown`。

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/unit/test_browser_factory.py::test_cdp_enabled_forces_playwright_without_url tests/unit/test_auto_fetcher.py::test_auto_fetcher_cdp_enabled_prefers_l2_flag tests/unit/test_playwright_fetcher.py::test_playwright_fetcher_cdp_enabled_uses_launcher_not_launch -v`

Expected: FAIL

- [ ] **Step 3: Wire implementation**

**`browser_factory.py`：**

```python
from spiderhub.core.settings import BROWSER_ENGINES, Settings, cdp_mode_active

def build_l3_fetcher(...):
    if cdp_mode_active(settings):
        return PlaywrightFetcher(settings, fetch_page=fetch_page)
    ...
```

**`auto_fetcher.py`：**

```python
from spiderhub.core.settings import cdp_mode_active
# in __init__:
self._prefer_http_after_browser = cdp_mode_active(settings)
```

同步把现有「仅看 `browser_cdp_url`」的 ConnectError 升维判断改为 `cdp_mode_active`（搜索 `_prefer_http_after_browser` 与 `browser_cdp_url` 在 auto_fetcher 中的用法，凡表示「CDP 模式」处统一）。

**`playwright_fetcher.py`：**

```python
from spiderhub.core.settings import Settings, cdp_mode_active
from spiderhub.downloaders.cdp_launcher import ChromeCdpLauncher

# in __init__:
self._cdp_launcher: ChromeCdpLauncher | None = None
self._cdp_url = settings.browser_cdp_url.strip()
self._interactive = cdp_mode_active(settings) or not settings.browser_headless
self._keep_cdp = cdp_mode_active(settings)

# in __aenter__:
if cdp_mode_active(self._settings):
    self._cdp_launcher = ChromeCdpLauncher()
    self._cdp_url = await self._cdp_launcher.ensure_ready(self._settings)
    await self._connect_cdp()
elif not self._settings.browser_headless:
    ...
```

**`__aexit__`：** 在 `_close_browser_stack` / playwright stop 之后（或之前，但须保证先断开 CDP）：

```python
if self._cdp_launcher is not None:
    await self._cdp_launcher.shutdown()
    self._cdp_launcher = None
```

更新 `_connect_cdp` 日志：可写「本机浏览器（CDP）」而不写死「手动启动」。

`prefer_headless_for_content` 中 `if self._keep_cdp or self._cdp_url` 可简化为 `if self._keep_cdp`（因 CDP 模式已设 `_keep_cdp`）。

- [ ] **Step 4: Run related tests**

Run:

```bash
uv run pytest \
  tests/unit/test_browser_factory.py \
  tests/unit/test_auto_fetcher.py \
  tests/unit/test_playwright_fetcher.py \
  tests/unit/test_cdp_launcher.py \
  tests/unit/test_settings.py -v
```

Expected: PASS

- [ ] **Step 5: Commit（默认跳过）**

```bash
git add \
  src/spiderhub/downloaders/playwright_fetcher.py \
  src/spiderhub/downloaders/browser_factory.py \
  src/spiderhub/downloaders/auto_fetcher.py \
  tests/unit/test_browser_factory.py \
  tests/unit/test_auto_fetcher.py \
  tests/unit/test_playwright_fetcher.py
git commit -m "feat: wire CDP auto-launch into L3 fetcher path"
```

---

### Task 4: 文档与示例配置

**Files:**
- Modify: `config.example.toml`
- Modify: `.env.example`
- Modify: `README.md`（L3 CDP 小节）
- Modify: `AGENTS.md`（浏览器 / CDP 相关行）

**Interfaces:** 无代码接口；文案需与 spec 一致。

- [ ] **Step 1: Update `config.example.toml`**

在 `browser_cdp_url` 附近改为：

```toml
# L3: playwright | camoufox | patchright（CDP 模式强制 playwright connect）
browser_engine = "playwright"
browser_challenge_wait_seconds = 15.0
browser_headless = true
browser_storage_state = ".spiderhub/storage_state.json"
# 推荐：开启后自动探测/启动本机 Chrome|Chromium|Edge 并连接 CDP
browser_cdp_enabled = false
# browser_cdp_url = "http://127.0.0.1:9222"  # 可选；填写则只连接、不自动启动
browser_cdp_keep_alive = false
browser_user_data_dir = ".spiderhub/chrome-profile"
```

- [ ] **Step 2: Update `.env.example`**

```bash
# 推荐：开启后自动起本机 Chrome/Chromium/Edge 并连 CDP（无需手动 mkdir/启动）
SPIDERHUB_BROWSER_CDP_ENABLED=false
# 可选：已有调试端口时只连接
# SPIDERHUB_BROWSER_CDP_URL=http://127.0.0.1:9222
SPIDERHUB_BROWSER_CDP_KEEP_ALIVE=false
SPIDERHUB_BROWSER_USER_DATA_DIR=.spiderhub/chrome-profile
```

- [ ] **Step 3: Update README L3 CDP 节**

将「手动启动 Chrome」改为：

1. 推荐：`SPIDERHUB_BROWSER_CDP_ENABLED=true`（或 toml `browser_cdp_enabled = true`）后直接 `uv run spiderhub run ...`
2. 说明自动探测顺序 Chrome → Chromium → Edge；缺失时错误会提示 `brew install --cask google-chrome`
3. `keep_alive` 含义
4. 高级：仍可手动起浏览器并设 `SPIDERHUB_BROWSER_CDP_URL`（只连接）

示例命令：

```bash
SPIDERHUB_BROWSER_CDP_ENABLED=true \
SPIDERHUB_BROWSER_CHALLENGE_WAIT_SECONDS=180 \
uv run spiderhub run missav_actress --dry-run --max-pages 1
```

- [ ] **Step 4: Update AGENTS.md**

表格「浏览器抓取」行与 L3 说明改为：有 `browser_cdp_enabled` 或 `browser_cdp_url` 时强制 Playwright CDP；enabled 且无 URL 时由 `ChromeCdpLauncher` 自动起本机浏览器。

- [ ] **Step 5: Quality gate**

Run:

```bash
uv run ruff check src tests
uv run ruff format --check src tests
uv run mypy src
uv run pytest
```

Expected: 全部通过

- [ ] **Step 6: Commit（默认跳过）**

```bash
git add config.example.toml .env.example README.md AGENTS.md
git commit -m "docs: document browser_cdp_enabled auto-launch"
```

---

## Spec coverage checklist

| Spec 要求 | Task |
|-----------|------|
| `browser_cdp_enabled` / `keep_alive` 配置 | Task 1 |
| `cdp_mode_active` 兼容旧 URL | Task 1 |
| Chrome→Chromium→Edge 探测 | Task 2 |
| mkdir + Popen + `/json/version` | Task 2 |
| 无浏览器 brew 提示 | Task 2 |
| 显式 URL 不 Popen | Task 2 |
| 端口占用非 CDP 报错 | Task 2 |
| shutdown / keep_alive | Task 2 |
| Playwright 仅 connect_over_cdp | Task 3 |
| factory / AutoFetcher 接入 | Task 3 |
| README / AGENTS / examples | Task 4 |

## Self-review notes

- 无 TBD /「类似 Task N」占位
- `ensure_ready` → `str` URL 与 PlaywrightFetcher `_cdp_url` 一致
- 不引入 Firefox 自动启动（非目标）
- Commit 步骤默认跳过，与仓库既有 plan 约定一致
