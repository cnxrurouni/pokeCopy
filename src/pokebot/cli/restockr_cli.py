from __future__ import annotations

import argparse
import getpass

from rich.console import Console
from rich.table import Table

from pokebot.config import load_settings
from pokebot.restockr.auth import load_token, load_username
from pokebot.restockr.client import RestockRClient

console = Console()


def add_login_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("login", help="Log in to RestockR or Target (Chrome export)")
    parser.add_argument(
        "target",
        choices=["restockr", "target", "target-mobile"],
        help=(
            "restockr = API token; target = export auth+_px3 from real Chrome; "
            "target-mobile = import iOS app tokens from a Proxyman HAR"
        ),
    )
    parser.add_argument("--username", help="RestockR username (or export RESTOCKR_USERNAME)")
    parser.add_argument("--password", help="RestockR password (or export RESTOCKR_PASSWORD)")
    parser.add_argument(
        "--channel",
        default="chrome",
        help="Browser for Target login: chrome (default) or msedge",
    )
    parser.add_argument(
        "--from-har",
        default=None,
        help=(
            "For target-mobile: path to Proxyman HAR containing "
            "gsp oauth_tokens login (default: data/captures/target-mobile/full.har)"
        ),
    )
    parser.set_defaults(func=run_login)


def add_open_alerts_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "open-alerts",
        help=(
            "Log into RestockR, listen for restock alerts, and open each product URL "
            "in your normal Chrome (no checkout)"
        ),
    )
    parser.add_argument(
        "--force-login",
        action="store_true",
        help="Re-authenticate to RestockR even if a saved token exists",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Open all retailer alerts (ignore RestockR watchlist filter)",
    )
    parser.add_argument(
        "--retailers",
        default=None,
        help="Comma-separated retailers to open (default: autobuy.retailers)",
    )
    parser.set_defaults(func=run_open_alerts)


def add_status_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser("status", help="Show RestockR session and watchlist")
    parser.set_defaults(func=run_status)


def add_doctor_parser(subparsers: argparse._SubParsersAction) -> None:
    parser = subparsers.add_parser(
        "doctor",
        help="Check Python arch, RestockR reachability, and Target auth+_px3 sidecar",
    )
    parser.set_defaults(func=run_doctor)


async def run_login(args: argparse.Namespace) -> None:
    if args.target == "restockr":
        settings = load_settings()
        from pokebot.restockr.client import _env

        username = args.username or _env("RESTOCKR_USERNAME")
        password = args.password or _env("RESTOCKR_PASSWORD")
        parent = _env("RESTOCKR_PARENT_ACCOUNT")
        if not username:
            username = input("RestockR username: ").strip()
        if not password:
            password = getpass.getpass("RestockR password: ")

        client = RestockRClient(settings.restockr.api_base)
        token = await client.login(username, password, parent_account=parent)
        profile = await client.get_profile()
        console.print(f"[green]RestockR login OK[/green] — {profile.username}")
        console.print(f"Watchlist SKUs: {len(profile.product_skus)}")
        console.print(f"Token saved to data/restockr_token.json ({len(token)} chars)")
        return

    if args.target == "target-mobile":
        from pokebot.config import data_dir
        from pokebot.doctor import decode_jwt_claims
        from pokebot.mobile_auth import import_mobile_auth_from_har
        from pokebot.session_auth import load_mobile_session_auth

        har = getattr(args, "from_har", None) or (
            data_dir() / "captures" / "target-mobile" / "full.har"
        )
        path = import_mobile_auth_from_har(har)
        cookies = load_mobile_session_auth()
        claims = decode_jwt_claims(cookies.get("accessToken") or "")
        console.print(
            f"[green]Target mobile auth imported[/green] — {path}\n"
            f"  sut={claims.get('sut')} asl={claims.get('asl')} "
            f"cli={claims.get('cli')} sco={claims.get('sco')}\n"
            f"  Next: python -m pokebot doctor"
        )
        return

    from pokebot.chrome_login import login_target_chrome

    await login_target_chrome(channel=getattr(args, "channel", "chrome") or "chrome")


async def run_open_alerts(args: argparse.Namespace) -> None:
    from pokebot.alert_open import AlertOpenOrchestrator

    settings = load_settings()
    retailers = None
    if args.retailers:
        retailers = [r.strip() for r in args.retailers.split(",") if r.strip()]
    orchestrator = AlertOpenOrchestrator(
        settings,
        watchlist_only=False if args.all else None,
        retailers=retailers,
        force_login=bool(args.force_login),
    )
    await orchestrator.start()


async def run_doctor(args: argparse.Namespace) -> None:
    from pokebot.doctor import (
        check_architecture,
        check_http_fingerprint_ready,
        check_target_auth_sidecar,
        check_target_mobile_auth_sidecar,
    )
    from pokebot.reseller.settings import load_reseller_settings
    from pokebot.restockr.client import RestockRClient

    settings = load_settings()
    arch = check_architecture()
    console.print("[bold]Environment[/bold]")
    console.print(f"  python: {arch.python_executable}")
    console.print(f"  process arch: {arch.python_machine}")
    for msg in arch.messages:
        color = "green" if arch.ok else "red"
        console.print(f"  [{color}]{msg}[/{color}]")

    console.print("\n[bold]RestockR[/bold]")
    client = RestockRClient(settings.restockr.api_base)
    try:
        profile = await client.ensure_authenticated()
        console.print(
            f"  [green]OK[/green] as {profile.username} "
            f"({len(profile.product_skus)} watchlist SKUs)"
        )
    except Exception as exc:
        console.print(f"  [red]failed:[/red] {exc}")

    console.print("\n[bold]HTTP fingerprint[/bold]")
    reseller = load_reseller_settings()
    fp_ok, fp_detail = check_http_fingerprint_ready(
        curl_impersonate=reseller.curl_impersonate
    )
    if fp_ok:
        console.print(f"  [green]{fp_detail}[/green]")
    else:
        console.print(f"  [red]{fp_detail}[/red]")

    console.print("\n[bold]Target cookie sidecar[/bold]")
    ok, detail = check_target_auth_sidecar()
    if ok:
        console.print(f"  [green]{detail}[/green]")
    else:
        console.print(
            f"  [red]{detail}[/red]\n"
            "  Fix: python -m pokebot login target"
        )

    console.print("\n[bold]Target mobile (iOS app) sidecar[/bold]")
    mob_ok, mob_detail = check_target_mobile_auth_sidecar()
    if mob_ok:
        console.print(f"  [green]{mob_detail}[/green]")
    else:
        console.print(
            f"  [red]{mob_detail}[/red]\n"
            "  Fix: python -m pokebot login target-mobile "
            "--from-har data/captures/target-mobile/full.har"
        )


async def run_status(args: argparse.Namespace) -> None:
    settings = load_settings()
    token = load_token()
    username = load_username()

    if not token:
        console.print("[red]Not logged in.[/red] Run: python -m pokebot login restockr")
        return

    client = RestockRClient(settings.restockr.api_base, token=token)
    try:
        profile = await client.get_profile()
    except Exception as exc:
        console.print(f"[red]Session invalid:[/red] {exc}")
        console.print("Re-login: python -m pokebot login restockr")
        return

    console.print(f"[green]Logged in[/green] as {profile.username or username}")
    console.print(f"Watchlist SKUs: {len(profile.product_skus)}")

    table = Table(title="Autobuy / open-alerts filters")
    table.add_column("Setting")
    table.add_column("Value")
    ab = settings.autobuy
    table.add_row("watchlist_only", str(ab.watchlist_only))
    table.add_row("retailers", ", ".join(ab.retailers))
    table.add_row("target_min_quantity", str(ab.target_min_quantity))
    table.add_row("cooldown_seconds", str(ab.cooldown_seconds))
    console.print(table)
