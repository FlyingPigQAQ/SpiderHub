from __future__ import annotations

import pytest

from spiderhub.cli import main


def test_list_exits_zero_and_reports_empty(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["list"])
    captured = capsys.readouterr()
    assert code == 0
    assert "No spiders registered" in captured.out


def test_run_reports_not_implemented(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "demo"])
    captured = capsys.readouterr()
    assert code == 2
    assert "not implemented" in captured.err.lower()
    assert "demo" in captured.err


def test_run_dry_run_mentions_flag(capsys: pytest.CaptureFixture[str]) -> None:
    code = main(["run", "demo", "--dry-run"])
    captured = capsys.readouterr()
    assert code == 2
    assert "not implemented" in captured.err.lower()
    assert "dry-run" in captured.err.lower()
