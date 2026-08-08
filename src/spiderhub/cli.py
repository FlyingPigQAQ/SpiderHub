from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence


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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)

    if args.command == "list":
        print("No spiders registered yet.")
        return 0

    if args.command == "run":
        mode = " (dry-run)" if args.dry_run else ""
        print(
            f"spiderhub run {args.name}{mode}: not implemented",
            file=sys.stderr,
        )
        return 2

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
