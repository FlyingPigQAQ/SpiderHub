from __future__ import annotations

from spiderhub.core.spider import Spider

_REGISTRY: dict[str, type[Spider]] = {}


def register_spider(spider_cls: type[Spider]) -> type[Spider]:
    if not getattr(spider_cls, "name", None):
        raise ValueError("spider class must define name")
    _REGISTRY[spider_cls.name] = spider_cls
    return spider_cls


def get_spider(name: str) -> type[Spider]:
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(f"unknown spider: {name}") from exc


def list_spiders() -> list[str]:
    return sorted(_REGISTRY)


def discover_builtin_spiders() -> None:
    return None
