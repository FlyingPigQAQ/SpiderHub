from __future__ import annotations

import pytest

from spiderhub.cli import main


def test_list_shows_missav(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["list"])
    out = capsys.readouterr().out
    assert code == 0
    assert "missav_actress" in out


def test_run_unknown_spider(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "no-such-spider"])
    err = capsys.readouterr().err
    assert code == 2
    assert "unknown" in err.lower() or "no-such-spider" in err
