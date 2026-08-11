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
    target.add_argument(
        "--url",
        default=None,
        help="Target product URL (optional with --from-cart)",
    )
    target.add_argument(
        "--sku",
        default=None,
        help="SKU/TCIN (default: parsed from --url, or first cart item with --from-cart)",
    )
    target.add_argument(
        "--quantity",
        type=int,
        default=None,
        help="Units to add (default: 1 for manual runs)",
    )
    target.add_argument(
        "--from-cart",
        action="store_true",
        help=(
            "Skip ATC and checkout whatever is already in the Target cart. "
            "Uses first cart TCIN unless --url/--sku matches an item."
        ),
    )
    target.add_argument(
        "--preflight",
        action="store_true",
        help=(
            "Validate ATC/checkout WITHOUT buying: stop before place_order. "
            "Still hits the live network."
        ),
    )
    target.add_argument(
        "--warm",
        action="store_true",
        help=(
            "Before ATC, open the bot Chrome profile on /cart→/checkout (CDP). "
            "Usually unnecessary and can demote the session — prefer browser "
            "assist after AUTH_DENIED instead."
        ),
    )
    target.add_argument(
        "--channel",
        choices=("web", "mobile"),
        default=None,
        help=(
            "Checkout infra: web (desktop capture) or mobile (iOS app capture). "
            "Default: reseller.checkout_channel in config/reseller.yaml."
        ),
    )
    target.add_argument(
        "--http-atc",
        action="store_true",
        help=(
            "Force HTTP ATC on the web channel (disable browser-assist). "
            "Useful for A/B vs --channel mobile."
        ),
    )
    target.add_argument(
        "--mobile",
        action="store_true",
        help="Shortcut for --channel mobile.",
    )
    target.set_defaults(func=run_reseller_target)

    run = retailer_sub.add_parser(
        "run",
        help=(
            "Listen for Target signals (RestockR and/or Discord) and HTTP-checkout "
            "with sidecar cookies"
        ),
    )
    run.add_argument(
        "--channel",
        choices=("web", "mobile"),
        default=None,
        help=(
            "Checkout infra: web (desktop/Chrome sidecar) or mobile (iOS app sidecar). "
            "Default: reseller.checkout_channel in config/reseller.yaml."
        ),
    )
    run.add_argument(
        "--mobile",
        action="store_true",
        help="Shortcut for --channel mobile (iOS app capture-replay + target-auth-mobile.json).",
    )
    run.add_argument(
        "--source",
        choices=("restockr", "discord", "both"),
        default=None,
        help=(
            "Alert source. Default: restockr. Discord needs DISCORD_BOT_TOKEN and "
            "discord.guild_id / discord.channel_id in config/settings.yaml."
        ),
    )
    run.add_argument(
        "--discord",
        action="store_true",
        help="Shortcut for --source discord.",
    )
    run.set_defaults(func=run_reseller_run)


def _resolve_target_sku(url: str, sku: str | None) -> str | None:
    from pokebot.reseller.target_ids import resolve_target_tcin

    return resolve_target_tcin(url=url, sku=sku)


def _clean_target_url(url: str) -> str:
    from pokebot.url_parser import canonical_target_product_url

    return (canonical_target_product_url(url) or url).strip()


async def run_reseller_target(args: argparse.Namespace) -> None:
    from pokebot.reseller.pipeline import TargetPipeline
    from pokebot.reseller.settings import load_reseller_settings
    from pokebot.restockr.models import RestockAlert

    from_cart = bool(getattr(args, "from_cart", False))
    raw_url = (getattr(args, "url", None) or "").strip()
    url = _clean_target_url(raw_url) if raw_url else ""

    sku = None
    if url or args.sku:
        sku = _resolve_target_sku(url, args.sku) or _resolve_target_sku(raw_url, args.sku)

    if not from_cart and not sku:
        console.print(
            "[red]Could not resolve a numeric Target TCIN from --url / --sku.[/red]\n"
            "  Or pass --from-cart to checkout whatever is already in the cart."
        )
        return

    if from_cart and not sku:
        # Placeholder until live cart discovery fills the real TCIN.
        sku = "00000000"
        url = url or "https://www.target.com/cart"

    quantity = getattr(args, "quantity", None)
    console.print(
        "[dim]"
        + (
            "FROM CART — skip ATC; will use cart contents"
            if from_cart and sku == "00000000"
            else f"Using TCIN {sku}"
        )
        + (f" qty={quantity}" if quantity is not None else "")
        + "[/dim]"
    )

    preflight = getattr(args, "preflight", False)
    if preflight:
        console.print(
            "[cyan]PREFLIGHT — live network, validates token + cart + checkout, "
            "stops before placing the order (no purchase).[/cyan]"
        )
    else:
        console.print("[yellow]LIVE — this can place a real order[/yellow]")

    settings = load_reseller_settings()
    if preflight:
        settings.max_attempts = 1
    channel = getattr(args, "channel", None)
    if getattr(args, "mobile", False):
        channel = "mobile"
    if channel:
        settings.checkout_channel = channel
    if getattr(args, "http_atc", False):
        settings.browser_assist_atc = False
        console.print(
            "[cyan]HTTP ATC[/cyan] — browser-assist disabled for this web run"
        )
    if getattr(args, "warm", False):
        settings.warm_cart_checkout = True
        console.print(
            "[cyan]PX warm enabled[/cyan] — real Chrome /cart→/checkout before ATC"
        )
    console.print(
        f"[dim]checkout_channel={settings.checkout_channel} "
        f"capture={settings.resolved_capture_path().name}[/dim]"
    )
    pipeline = TargetPipeline.build(settings)
    pipeline.checkout.preflight = preflight
    if from_cart:
        pipeline.checkout.skip_atc = True
        console.print("[cyan]FROM CART[/cyan] — ATC skipped; checkout existing cart")
    if pipeline.ensure_default_account():
        console.print(
            "[dim]No reseller accounts YAML — using Chrome sidecar as default "
            "account.[/dim]"
        )
    alert = RestockAlert(
        id=sku,
        sku=sku,
        store="target",
        url=url or f"https://www.target.com/p/-/A-{sku}",
        restock_url=raw_url or url or f"https://www.target.com/p/-/A-{sku}",
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
    from pokebot.reseller.orchestrator import AlertSource, ResellerOrchestrator
    from pokebot.reseller.settings import load_reseller_settings

    settings = load_settings()
    reseller = load_reseller_settings()
    channel = getattr(args, "channel", None)
    if getattr(args, "mobile", False):
        channel = "mobile"
    if channel:
        reseller.checkout_channel = channel

    source_arg = getattr(args, "source", None)
    if getattr(args, "discord", False):
        source_arg = source_arg or "discord"
    source_arg = source_arg or "restockr"
    sources: set[AlertSource]
    if source_arg == "both":
        sources = {"restockr", "discord"}
    elif source_arg == "discord":
        sources = {"discord"}
    else:
        sources = {"restockr"}

    console.print(
        f"[dim]checkout_channel={reseller.checkout_channel} "
        f"sources={'+'.join(sorted(sources))} "
        f"capture={reseller.resolved_capture_path().name}[/dim]"
    )
    orchestrator = ResellerOrchestrator(settings, reseller_settings=reseller)
    await orchestrator.start(sources=sources)
