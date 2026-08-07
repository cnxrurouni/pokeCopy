from __future__ import annotations

import argparse

from rich.console import Console

console = Console()


def add_reseller_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "reseller",
        help="Target HTTP checkout from RestockR (curl_cffi + Chrome cookie sidecar)",
    )
    retailer_sub = parser.add_subparsers(dest="retailer", required=True)

    target = retailer_sub.add_parser("target", help="Run one Target checkout / preflight")
    target.add_argument("--url", required=True, help="Target product URL")
    target.add_argument(
        "--sku",
        default=None,
        help="SKU/TCIN (default: parsed from --url)",
    )
    target.add_argument(
        "--quantity",
        type=int,
        default=None,
        help="Units to add (default: 1 for manual runs)",
    )
    target.add_argument(
        "--live",
        action="store_true",
        help="Disable dry-run and hit the network",
    )
    target.add_argument(
        "--preflight",
        action="store_true",
        help=(
            "Live-validate ATC/checkout WITHOUT buying: stop before place_order. "
            "Implies live network."
        ),
    )
    target.set_defaults(func=run_reseller_target)

    run = retailer_sub.add_parser(
        "run",
        help="Listen for RestockR Target signals and HTTP-checkout with sidecar cookies",
    )
    run.set_defaults(func=run_reseller_run)


def _resolve_target_sku(url: str, sku: str | None) -> str | None:
    from pokebot.reseller.target_ids import resolve_target_tcin

    return resolve_target_tcin(url=url, sku=sku)


def _clean_target_url(url: str) -> str:
    from pokebot.url_parser import canonical_target_product_url

    return (canonical_target_product_url(url) or url).strip()


async def run_reseller_target(args: argparse.Namespace) -> None:
    from pokebot.reseller.pipeline import TargetPipeline, run_dry_run
    from pokebot.reseller.settings import load_reseller_settings
    from pokebot.restockr.models import RestockAlert

    raw_url = (args.url or "").strip()
    url = _clean_target_url(raw_url)

    sku = _resolve_target_sku(url, args.sku) or _resolve_target_sku(raw_url, args.sku)
    if not sku:
        console.print(
            "[red]Could not resolve a numeric Target TCIN from --url / --sku.[/red]"
        )
        return
    quantity = getattr(args, "quantity", None)
    console.print(
        f"[dim]Using TCIN {sku}"
        + (f" qty={quantity}" if quantity is not None else " qty=1")
        + "[/dim]"
    )

    preflight = getattr(args, "preflight", False)
    if not args.live and not preflight:
        console.print("[cyan]Running Target pipeline in dry-run mode[/cyan]")
        result = await run_dry_run(url, sku=sku)
    else:
        if preflight:
            console.print(
                "[cyan]PREFLIGHT — live network, validates token + cart + checkout, "
                "stops before placing the order (no purchase).[/cyan]"
            )
        else:
            console.print("[yellow]LIVE mode — this can place a real order[/yellow]")
        settings = load_reseller_settings()
        settings.dry_run = False
        if preflight:
            settings.max_attempts = 1
        pipeline = TargetPipeline.build(settings)
        pipeline.checkout.preflight = preflight
        if pipeline.ensure_default_account():
            console.print(
                "[dim]No reseller accounts YAML — using Chrome sidecar as default "
                "account.[/dim]"
            )
        alert = RestockAlert(
            id=sku,
            sku=sku,
            store="target",
            url=url,
            restock_url=raw_url or url,
            stock_quantity=quantity,
        )
        result = await pipeline.handle_alert(alert)

    if result is None:
        console.print("[red]No result[/red]")
        return
    color = "green" if result.success else "red"
    console.print(
        f"[{color}]{'PLACED' if result.success else 'FAILED'}[/{color}] — "
        f"sku={result.sku} order_id={result.order_id} "
        f"attempts={result.attempts} msg={result.message}"
    )


async def run_reseller_run(args: argparse.Namespace) -> None:
    from pokebot.config import load_settings
    from pokebot.reseller.orchestrator import ResellerOrchestrator

    settings = load_settings()
    orchestrator = ResellerOrchestrator(settings)
    await orchestrator.start()
