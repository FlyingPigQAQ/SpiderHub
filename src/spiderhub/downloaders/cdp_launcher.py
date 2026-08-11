from __future__ import annotations

import asyncio
import logging
import shutil
import socket
import subprocess
from pathlib import Path

import httpx

from spiderhub.core.settings import Settings

logger = logging.getLogger(__name__)

DEFAULT_CDP_PORT = 9222
DEFAULT_CDP_URL = f"http://127.0.0.1:{DEFAULT_CDP_PORT}"
_READY_TIMEOUT_S = 30.0
_READY_POLL_S = 0.25

_MAC_CHROME = Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")
_MAC_CHROMIUM = Path("/Applications/Chromium.app/Contents/MacOS/Chromium")
_MAC_EDGE = Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")


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
                raise CdpEndpointError(f"浏览器进程已退出，CDP 未就绪 url={cdp_url}")
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
                "未找到支持 CDP 的本机浏览器"
                "（Google Chrome / Chromium / Microsoft Edge）。"
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
