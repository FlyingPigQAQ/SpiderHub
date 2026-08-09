from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from spiderhub.cli import (
    _format_run_error,
    _publish_run_finished,
    _run_status,
)
from spiderhub.core.runner import RunResult
from spiderhub.events.types import SpiderRunFinished


def test_run_status_success_and_partial() -> None:
    assert (
        _run_status(RunResult(items_ok=1, items_failed=0, urls_failed=0)) == "success"
    )
    assert (
        _run_status(RunResult(items_ok=1, items_failed=1, urls_failed=0)) == "partial"
    )
    assert (
        _run_status(RunResult(items_ok=0, items_failed=0, urls_failed=2)) == "partial"
    )


def test_format_run_error_truncates() -> None:
    long = "x" * 600
    text = _format_run_error(RuntimeError(long))
    assert text.startswith("RuntimeError: ")
    assert text.endswith("...")
    assert len(text) == 500


@pytest.mark.asyncio
async def test_publish_run_finished_skips_dry_run() -> None:
    with patch("spiderhub.cli.publish", new_callable=AsyncMock) as mock_publish:
        await _publish_run_finished(
            spider_name="demo",
            dry_run=True,
            result=RunResult(items_ok=1),
        )
    mock_publish.assert_not_called()


@pytest.mark.asyncio
async def test_publish_run_finished_success() -> None:
    with patch("spiderhub.cli.publish", new_callable=AsyncMock) as mock_publish:
        await _publish_run_finished(
            spider_name="demo",
            dry_run=False,
            result=RunResult(items_ok=2, items_failed=0, urls_failed=0),
        )
    mock_publish.assert_awaited_once()
    event = mock_publish.await_args.args[0]
    assert isinstance(event, SpiderRunFinished)
    assert event.status == "success"
    assert event.spider_name == "demo"
    assert event.error is None


@pytest.mark.asyncio
async def test_publish_run_finished_failed() -> None:
    with patch("spiderhub.cli.publish", new_callable=AsyncMock) as mock_publish:
        await _publish_run_finished(
            spider_name="demo",
            dry_run=False,
            error="RuntimeError: boom",
        )
    event = mock_publish.await_args.args[0]
    assert event.status == "failed"
    assert event.error == "RuntimeError: boom"
    assert event.items_ok == 0


@pytest.mark.asyncio
async def test_run_async_publishes_on_success() -> None:
    from spiderhub.cli import _run_async

    args = MagicMock(
        name="missav_actress",
        dry_run=False,
        start_url=None,
        max_pages=None,
    )
    # MagicMock(name=...) sets mock name, not attribute — set explicitly
    args.name = "missav_actress"

    spider = MagicMock()
    spider.name = "missav_actress"
    spider_cls = MagicMock(return_value=spider)

    result = RunResult(items_ok=1, items_failed=0, urls_failed=0)

    with (
        patch("spiderhub.cli.discover_builtin_spiders"),
        patch("spiderhub.cli.get_spider", return_value=spider_cls),
        patch("spiderhub.cli.load_settings", return_value=MagicMock()),
        patch("spiderhub.cli.setup_feishu_notifier", new_callable=AsyncMock),
        patch("spiderhub.cli.MySQLPipeline"),
        patch("spiderhub.cli.AutoFetcher") as auto_fetcher,
        patch(
            "spiderhub.cli.run_spider",
            new_callable=AsyncMock,
            return_value=result,
        ),
        patch("spiderhub.cli._publish_run_finished", new_callable=AsyncMock) as pub,
    ):
        auto_fetcher.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        auto_fetcher.return_value.__aexit__ = AsyncMock(return_value=None)
        code = await _run_async(args)

    assert code == 0
    pub.assert_awaited_once()
    assert pub.await_args.kwargs["spider_name"] == "missav_actress"
    assert pub.await_args.kwargs["dry_run"] is False
    assert pub.await_args.kwargs["result"] is result


@pytest.mark.asyncio
async def test_run_async_publishes_on_exception() -> None:
    from spiderhub.cli import _run_async

    args = MagicMock(dry_run=False, start_url=None, max_pages=None)
    args.name = "missav_actress"

    spider = MagicMock()
    spider.name = "missav_actress"
    spider_cls = MagicMock(return_value=spider)

    with (
        patch("spiderhub.cli.discover_builtin_spiders"),
        patch("spiderhub.cli.get_spider", return_value=spider_cls),
        patch("spiderhub.cli.load_settings", return_value=MagicMock()),
        patch("spiderhub.cli.setup_feishu_notifier", new_callable=AsyncMock),
        patch("spiderhub.cli.MySQLPipeline"),
        patch("spiderhub.cli.AutoFetcher") as auto_fetcher,
        patch(
            "spiderhub.cli.run_spider",
            new_callable=AsyncMock,
            side_effect=RuntimeError("boom"),
        ),
        patch("spiderhub.cli._publish_run_finished", new_callable=AsyncMock) as pub,
    ):
        auto_fetcher.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        auto_fetcher.return_value.__aexit__ = AsyncMock(return_value=None)
        code = await _run_async(args)

    assert code == 2
    pub.assert_awaited_once()
    assert pub.await_args.kwargs["error"] == "RuntimeError: boom"


@pytest.mark.asyncio
async def test_run_async_dry_run_still_calls_publish_helper() -> None:
    """Helper itself no-ops; CLI still invokes it with dry_run=True."""
    from spiderhub.cli import _run_async

    args = MagicMock(dry_run=True, start_url=None, max_pages=None)
    args.name = "missav_actress"

    spider = MagicMock()
    spider.name = "missav_actress"
    spider_cls = MagicMock(return_value=spider)
    result = RunResult(items_ok=1)

    with (
        patch("spiderhub.cli.discover_builtin_spiders"),
        patch("spiderhub.cli.get_spider", return_value=spider_cls),
        patch("spiderhub.cli.load_settings", return_value=MagicMock()),
        patch("spiderhub.cli.setup_feishu_notifier", new_callable=AsyncMock),
        patch("spiderhub.cli.NullPipeline"),
        patch("spiderhub.cli.AutoFetcher") as auto_fetcher,
        patch(
            "spiderhub.cli.run_spider",
            new_callable=AsyncMock,
            return_value=result,
        ),
        patch("spiderhub.cli._publish_run_finished", new_callable=AsyncMock) as pub,
    ):
        auto_fetcher.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        auto_fetcher.return_value.__aexit__ = AsyncMock(return_value=None)
        code = await _run_async(args)

    assert code == 0
    assert pub.await_args.kwargs["dry_run"] is True
