from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

import pymysql
from pymysql.connections import Connection

from spiderhub.core.settings import Settings
from spiderhub.models.items import Actress, Work

logger = logging.getLogger(__name__)
ConnectFn = Callable[..., Connection]


def upsert_actress_sql(actress: Actress) -> tuple[str, tuple[object, ...]]:
    sql = """
    INSERT INTO actresses (
      slug, name, name_ja, name_en, profile_url, cover_url, bio, source
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      name=VALUES(name),
      name_ja=VALUES(name_ja),
      name_en=VALUES(name_en),
      profile_url=VALUES(profile_url),
      cover_url=VALUES(cover_url),
      bio=VALUES(bio),
      source=VALUES(source)
    """
    params = (
        actress.slug,
        actress.name,
        actress.name_ja,
        actress.name_en,
        actress.profile_url,
        actress.cover_url,
        actress.bio,
        actress.source,
    )
    return sql, params


def upsert_work_sql(work: Work) -> tuple[str, tuple[object, ...]]:
    sql = """
    INSERT INTO works (
      code, title, description, release_date, duration_seconds,
      maker, label, series, cover_url, detail_url, source
    ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    ON DUPLICATE KEY UPDATE
      title=VALUES(title),
      description=VALUES(description),
      release_date=VALUES(release_date),
      duration_seconds=VALUES(duration_seconds),
      maker=VALUES(maker),
      label=VALUES(label),
      series=VALUES(series),
      cover_url=VALUES(cover_url),
      detail_url=VALUES(detail_url),
      source=VALUES(source)
    """
    params = (
        work.code,
        work.title,
        work.description,
        work.release_date,
        work.duration_seconds,
        work.maker,
        work.label,
        work.series,
        work.cover_url,
        work.detail_url,
        work.source,
    )
    return sql, params


def _upsert_tag(cursor: Any, name: str, source: str) -> int:
    cursor.execute(
        """
        INSERT INTO tags (name, source) VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE source=VALUES(source)
        """,
        (name, source),
    )
    cursor.execute("SELECT id FROM tags WHERE name=%s", (name,))
    row = cursor.fetchone()
    assert row is not None
    return int(row[0])


def _actress_id_by_slug(cursor: Any, slug: str) -> int | None:
    cursor.execute("SELECT id FROM actresses WHERE slug=%s", (slug,))
    row = cursor.fetchone()
    return int(row[0]) if row else None


def sync_work_relations(
    cursor: Any,
    work_id: int,
    *,
    actress_slugs: list[str],
    tags: list[str],
    source: str,
) -> None:
    cursor.execute("DELETE FROM work_tags WHERE work_id=%s", (work_id,))
    cursor.execute("DELETE FROM work_actresses WHERE work_id=%s", (work_id,))
    for tag in tags:
        tag_id = _upsert_tag(cursor, tag, source)
        cursor.execute(
            "INSERT INTO work_tags (work_id, tag_id) VALUES (%s, %s)",
            (work_id, tag_id),
        )
    for slug in actress_slugs:
        actress_id = _actress_id_by_slug(cursor, slug)
        if actress_id is None:
            logger.warning("skip work_actress missing slug=%s", slug)
            continue
        cursor.execute(
            "INSERT INTO work_actresses (work_id, actress_id) VALUES (%s, %s)",
            (work_id, actress_id),
        )


class MySQLPipeline:
    def __init__(
        self,
        settings: Settings,
        *,
        connect: ConnectFn | None = None,
    ) -> None:
        self._settings = settings
        self._connect = connect or pymysql.connect
        self._conn: Connection | None = None

    def _connect_sync(self) -> Connection:
        return self._connect(
            host=self._settings.mysql_host,
            port=self._settings.mysql_port,
            user=self._settings.mysql_user,
            password=self._settings.mysql_password,
            database=self._settings.mysql_database,
            charset="utf8mb4",
            autocommit=False,
        )

    async def open(self) -> None:
        self._conn = await asyncio.to_thread(self._connect_sync)

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    def _process_sync(self, item: Actress | Work) -> None:
        assert self._conn is not None
        try:
            with self._conn.cursor() as cursor:
                if isinstance(item, Actress):
                    sql, params = upsert_actress_sql(item)
                    cursor.execute(sql, params)
                else:
                    sql, params = upsert_work_sql(item)
                    cursor.execute(sql, params)
                    cursor.execute("SELECT id FROM works WHERE code=%s", (item.code,))
                    row = cursor.fetchone()
                    assert row is not None
                    sync_work_relations(
                        cursor,
                        int(row[0]),
                        actress_slugs=item.actress_slugs,
                        tags=item.tags,
                        source=item.source,
                    )
            self._conn.commit()
        except Exception:
            self._conn.rollback()
            raise

    async def process_item(self, item: Actress | Work) -> None:
        await asyncio.to_thread(self._process_sync, item)
