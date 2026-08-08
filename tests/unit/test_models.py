from __future__ import annotations

import pytest
from pydantic import ValidationError

from spiderhub.models.items import Actress, Work


def test_actress_and_work_minimal() -> None:
    actress = Actress(
        slug="kitano-mina",
        name="北野未奈",
        profile_url="https://missav.ws/cn/actresses/kitano-mina",
    )
    work = Work(
        code="ABC-123",
        title="Sample",
        detail_url="https://missav.ws/cn/abc-123",
        actress_slugs=["kitano-mina"],
        tags=["solo"],
    )
    assert actress.source == "missav"
    assert work.code == "ABC-123"
    assert work.tags == ["solo"]


def test_work_requires_code() -> None:
    with pytest.raises(ValidationError):
        Work(code="", title="t", detail_url="https://missav.ws/cn/x")
