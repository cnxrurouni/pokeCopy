from __future__ import annotations

import argparse
import asyncio
import sys

from pokebot.cli import reseller_cli, restockr_cli


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pokebot",
        description=(
            "RestockR listener + Target HTTP checkout (curl_cffi) "
            "or open-alerts in normal Chrome"
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    restockr_cli.add_login_parser(subparsers)
    restockr_cli.add_open_alerts_parser(subparsers)
    restockr_cli.add_status_parser(subparsers)
    restockr_cli.add_doctor_parser(subparsers)
    reseller_cli.add_reseller_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    from pokebot.doctor import check_architecture

    arch = check_architecture()
    if not arch.ok:
        for msg in arch.messages:
            print(f"WARNING: {msg}", file=sys.stderr)

    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        asyncio.run(args.func(args))
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
