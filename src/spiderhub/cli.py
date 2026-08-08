from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from collections.abc import Sequence

from spiderhub.core.registry import discover_builtin_spiders, get_spider, list_spiders
from spiderhub.core.runner import run_spider
from spiderhub.core.settings import load_settings
from spiderhub.downloaders.httpx_fetcher import HttpxFetcher
from spiderhub.pipelines.mysql import MySQLPipeline
from spiderhub.pipelines.null import NullPipeline


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="spiderhub")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("list", help="List registered spiders")

    run = sub.add_parser("run", help="Run a spider")
    run.add_argument("name", help="Spider name")
    run.add_argument(
        "--dry-run",
        action="store_true",
        help="Parse and validate without executing side effects",
    )
    run.add_argument("--start-url", default=None, help="Override spider start URL")
    return parser


async def _run_async(args: argparse.Namespace) -> int:
    discover_builtin_spiders()
    try:
        spider_cls = get_spider(args.name)
    except KeyError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    settings = load_settings()
    spider = spider_cls(start_url=args.start_url) if args.start_url else spider_cls()

    pipeline: NullPipeline | MySQLPipeline
    if args.dry_run:
        pipeline = NullPipeline()
    else:
        pipeline = MySQLPipeline(settings)

    try:
        async with HttpxFetcher(settings) as fetcher:
            result = await run_spider(
                spider,
                fetcher=fetcher,
                pipeline=pipeline,
                start_urls=[args.start_url] if args.start_url else None,
                settings=settings,
            )
    except Exception as exc:  # noqa: BLE001
        logging.exception("run failed: %s", exc)
        print(f"run failed: {exc}", file=sys.stderr)
        return 2

    print(
        f"done items_ok={result.items_ok} items_failed={result.items_failed} "
        f"urls_failed={result.urls_failed}"
    )
    return 0 if result.urls_failed == 0 and result.items_failed == 0 else 1


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    discover_builtin_spiders()
    if args.command == "list":
        names = list_spiders()
        if not names:
            print("No spiders registered yet.")
        else:
            print("\n".join(names))
        return 0

    return asyncio.run(_run_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
