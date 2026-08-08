from __future__ import annotations

import pytest

from spiderhub.cli import main
from spiderhub.core.runner import RunResult


class _FakeFetcher:
    async def __aenter__(self) -> _FakeFetcher:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None


def test_run_dry_run_skips_mysql(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    async def fail_open(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("MySQLPipeline.open should not be called in dry-run")

    monkeypatch.setattr(
        "spiderhub.pipelines.mysql.MySQLPipeline.open",
        fail_open,
    )

    async def fake_run_spider(*_args: object, **_kwargs: object) -> RunResult:
        return RunResult(items_ok=1)

    monkeypatch.setattr("spiderhub.cli.run_spider", fake_run_spider)
    monkeypatch.setattr(
        "spiderhub.cli.AutoFetcher",
        lambda *_args, **_kwargs: _FakeFetcher(),
    )

    code = main(["run", "missav_actress", "--dry-run"])
    out = capsys.readouterr().out

    assert code == 0
    assert "items_ok=1" in out
