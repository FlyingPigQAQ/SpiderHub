from __future__ import annotations

from spiderhub.models.items import Actress, Work
from spiderhub.pipelines.mysql import upsert_actress_sql, upsert_work_sql


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
