from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class Actress(BaseModel):
    slug: str
    name: str
    profile_url: str
    name_ja: str | None = None
    name_en: str | None = None
    cover_url: str | None = None
    bio: str | None = None
    source: Literal["missav"] = "missav"

    @field_validator("slug", "name", "profile_url")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value


class Work(BaseModel):
    code: str
    title: str
    detail_url: str
    description: str | None = None
    release_date: date | None = None
    duration_seconds: int | None = None
    maker: str | None = None
    label: str | None = None
    series: str | None = None
    cover_url: str | None = None
    actress_slugs: list[str] = Field(default_factory=list)
    actress_names: list[str] = Field(default_factory=list)
    tags: list[str] = Field(default_factory=list)
    source: Literal["missav"] = "missav"

    @field_validator("code", "title", "detail_url")
    @classmethod
    def _required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("must not be empty")
        return value
