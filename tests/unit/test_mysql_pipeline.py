from __future__ import annotations

import pytest

from spiderhub.core.settings import Settings
from spiderhub.models.items import Actress, Work
from spiderhub.pipelines.mysql import (
    MySQLPipeline,
    upsert_actress_sql,
    upsert_failed_url_sql,
    upsert_work_sql,
)
from spiderhub.pipelines.null import NullPipeline


def test_upsert_actress_sql_uses_slug_key() -> None:
    sql, params = upsert_actress_sql(
        Actress(slug="a", name="A", profile_url="https://missav.ws/cn/actresses/a")
    )
    assert "INSERT INTO actresses" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params[0] == "a"


def test_upsert_work_sql_uses_code_key() -> None:
    sql, params = upsert_work_sql(
        Work(code="ABC-123", title="T", detail_url="https://missav.ws/cn/abc-123")
    )
    assert "INSERT INTO works" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert params[0] == "ABC-123"


def test_upsert_failed_url_sql() -> None:
    sql, params = upsert_failed_url_sql(
        url="https://missav.ws/dm91/cn/mdon-044",
        spider_name="missav_actress",
        error_type="fetch",
        error_message="Page.goto: Timeout 30000ms exceeded.",
    )
    assert "INSERT INTO failed_urls" in sql
    assert "ON DUPLICATE KEY UPDATE" in sql
    assert "fail_count" in sql.lower()
    assert params[0] == "https://missav.ws/dm91/cn/mdon-044"
    assert params[1] == "missav_actress"
    assert params[2] == "fetch"


def test_upsert_failed_url_sql_truncates_error_message() -> None:
    _, params = upsert_failed_url_sql(
        url="https://missav.ws/dm91/cn/mdon-044",
        spider_name="missav_actress",
        error_type="fetch",
        error_message="x" * 1025,
    )

    assert params[3] == "x" * 1024


class _RecordingCursor:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []

    def __enter__(self) -> _RecordingCursor:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def execute(self, sql: str, params: tuple[object, ...]) -> None:
        self.executed.append((sql, params))


class _RecordingConnection:
    def __init__(self) -> None:
        self.cursor_instance = _RecordingCursor()
        self.commits = 0

    def cursor(self) -> _RecordingCursor:
        return self.cursor_instance

    def commit(self) -> None:
        self.commits += 1


@pytest.mark.asyncio
async def test_mysql_pipeline_records_failed_url() -> None:
    connection = _RecordingConnection()
    pipeline = MySQLPipeline(Settings())
    pipeline._conn = connection  # type: ignore[assignment]

    await pipeline.record_failed_url(
        url="https://missav.ws/dm91/cn/mdon-044",
        spider_name="missav_actress",
        error_type="fetch",
        error_message="timeout",
    )

    sql, params = connection.cursor_instance.executed[0]
    assert "INSERT INTO failed_urls" in sql
    assert params == (
        "https://missav.ws/dm91/cn/mdon-044",
        "missav_actress",
        "fetch",
        "timeout",
    )
    assert connection.commits == 1


@pytest.mark.asyncio
async def test_null_pipeline_ignores_failed_url() -> None:
    await NullPipeline().record_failed_url(
        url="https://missav.ws/dm91/cn/mdon-044",
        spider_name="missav_actress",
        error_type="fetch",
        error_message="timeout",
    )
