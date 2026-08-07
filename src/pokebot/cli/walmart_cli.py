from __future__ import annotations

import argparse
import asyncio

from rich.console import Console

from pokebot.config import load_settings

console = Console()


def add_walmart_parser(subparsers: argparse._SubParsersAction) -> None:
    walmart = subparsers.add_parser(
        "walmart",
        help="Walmart helpers (native queue join without Playwright)",
    )
    sub = walmart.add_subparsers(dest="walmart_command", required=True)

    queue = sub.add_parser(
        "queue",
        help=(
            "Open a Walmart queue URL in real Edge/Chrome and OS-click "
            "Join queue / Get in line (press-and-hold + buy stay manual)"
        ),
    )
    queue.add_argument("--url", required=True, help="Walmart product / queue URL")
    queue.add_argument(
        "--sku",
        default="manual",
        help="Label/SKU for logs (default: manual)",
    )
    queue.add_argument(
        "--no-watch",
        action="store_true",
        help="Do not poll window titles for your-turn notify",
    )
    queue.add_argument(
        "--join-timeout",
        type=float,
        default=45.0,
        help="Seconds to search for Join queue via UI Automation (default 45)",
    )
    queue.set_defaults(func=run_walmart_queue)


async def run_walmart_queue(args: argparse.Namespace) -> None:
    from pokebot.purchase.walmart_native_queue import WalmartNativeQueueClient

    settings = load_settings()
    client = WalmartNativeQueueClient(
        browser_settings=settings.playwright,
        max_queues=settings.autobuy.walmart_max_queues,
        join_click_timeout_s=float(args.join_timeout),
    )
    ok = await client.join_queue(
        args.url,
        args.sku,
        label=args.sku,
        watch=not args.no_watch,
    )
    if not ok:
        raise SystemExit(1)
    if args.no_watch:
        console.print(
            "[dim]Browser left open. Complete Join / hold / buy yourself if needed.[/dim]"
        )
        return
    console.print(
        "[cyan]Watching for your turn[/cyan] — leave this terminal open. "
        "Ctrl+C stops the watcher (Edge stays open)."
    )
    try:
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:  # pragma: no cover
        pass
