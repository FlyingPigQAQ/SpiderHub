from __future__ import annotations

from pathlib import Path
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
        patch.object(launcher, "_wait_cdp_ready", new=AsyncMock(return_value=None)),
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
        patch.object(launcher, "_probe_cdp", new=AsyncMock(return_value=True)),
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
        patch.object(launcher, "_wait_cdp_ready", new=AsyncMock(return_value=None)),
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
        patch.object(launcher, "_port_in_use", return_value=False),
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
