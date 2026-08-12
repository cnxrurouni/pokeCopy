from __future__ import annotations

import asyncio
from typing import Literal

from rich.console import Console

from pokebot.alert_tracker import ActedAlertTracker, AlertKey
from pokebot.config import Settings
from pokebot.doctor import check_target_auth_sidecar, check_target_mobile_auth_sidecar
from pokebot.platform_util import open_url_in_system_chrome
from pokebot.reseller.pipeline import TargetPipeline
from pokebot.reseller.settings import ResellerSettings, load_reseller_settings
from pokebot.restockr.client import RestockRClient
from pokebot.restockr.listener import RestockRListener
from pokebot.restockr.models import RestockAlert
from pokebot.stores import normalize_store

console = Console()

AlertSource = Literal["restockr", "discord"]

# Stores with an HTTP checkout client. Everything else in autobuy.retailers is
# surfaced and opened in Chrome for a manual buy.
CHECKOUT_STORES = {"target"}


def _sidecar_age_seconds(*, mobile: bool = False) -> float | None:
    from pokebot.session_auth import MOBILE_RETAILER, session_auth_path

    path = session_auth_path(MOBILE_RETAILER if mobile else "target")
    if not path.exists():
        return None
    import time

    return max(0.0, time.time() - path.stat().st_mtime)


class ResellerOrchestrator:
    """Alert-driven Target HTTP checkout using Chrome-exported sidecar cookies."""

    def __init__(
        self,
        settings: Settings,
        *,
        reseller_settings: ResellerSettings | None = None,
    ) -> None:
        self.settings = settings
        self.reseller_settings = reseller_settings or load_reseller_settings()
        self.client = RestockRClient(settings.restockr.api_base)
        self.pipeline = TargetPipeline.build(self.reseller_settings)
        self.profile = None
        self.parent_id: str | None = None
        self.retailers = {
            normalize_store(r) for r in settings.autobuy.retailers
        } or {"target"}
        self._tracker = ActedAlertTracker(
            cooldown_seconds=settings.autobuy.cooldown_seconds,
            dedup_window_seconds=settings.autobuy.dedup_window_seconds,
        )
        self._purchased_skus: set[str] = set()

    async def start(self, *, sources: set[AlertSource] | None = None) -> None:
        active: set[AlertSource] = sources or {"restockr"}
        if not active:
            raise ValueError("At least one alert source is required")

        if "restockr" in active:
            self.profile = await self.client.ensure_authenticated()
            console.print(
                f"[green]Logged in as[/green] {self.profile.username} "
                f"— watchlist: {len(self.profile.product_skus)} SKU(s)"
            )
        else:
            console.print(
                "[dim]RestockR skipped — Discord-only mode "
                "(no watchlist login).[/dim]"
            )

        mobile = self.reseller_settings.is_mobile_channel
        if self.pipeline.ensure_default_account():
            sidecar = (
                "data/sessions/target-auth-mobile.json"
                if mobile
                else "data/sessions/target-auth.json"
            )
            console.print(
                "[dim]No reseller accounts YAML — using session-target default account "
                f"+ {sidecar} sidecar.[/dim]"
            )

        if mobile:
            ok, detail = check_target_mobile_auth_sidecar()
            login_hint = (
                "python -m pokebot login target-mobile "
                "--from-har data/captures/target-mobile/full.har"
            )
            stale_hint = (
                "iOS access tokens expire ~8h. If ATC returns AUTH_DENIED, re-import: "
                f"[bold]{login_hint}[/bold]"
            )
        else:
            ok, detail = check_target_auth_sidecar()
            login_hint = "python -m pokebot login target"
            stale_hint = (
                "_px3 often dies before JWT. If ATC returns AUTH_DENIED, re-run: "
                f"[bold]{login_hint}[/bold]"
            )

        if ok:
            console.print(f"[green]{detail}[/green]")
            age_s = _sidecar_age_seconds(mobile=mobile)
            if age_s is not None and age_s > 20 * 60:
                mins = int(age_s // 60)
                console.print(
                    f"[yellow]Sidecar is {mins}m old[/yellow] — {stale_hint}"
                )
        else:
            console.print(
                f"[red]Sidecar not ready:[/red] {detail}\n"
                f"  Run: [bold]{login_hint}[/bold]"
            )

        channel_label = "mobile (iOS app)" if mobile else "web (Chrome)"
        source_label = "+".join(sorted(active))
        console.print(
            f"[bold]Reseller pipeline mode:[/bold] LIVE "
            f"[dim]sources={source_label} "
            f"checkout_channel={self.reseller_settings.checkout_channel} "
            f"({channel_label})[/dim]"
        )
        buys = sorted(self.retailers & CHECKOUT_STORES)
        opens = sorted(self.retailers - CHECKOUT_STORES)
        console.print(
            f"[dim]Retailers: auto-checkout={', '.join(buys) or 'none'} "
            f"open-only={', '.join(opens) or 'none'} "
            "(edit autobuy.retailers in config/settings.yaml)[/dim]"
        )
        console.print(
            "[dim]After checkout attempt, watchlist / Discord hits open in everyday "
            "Chrome (default profile). Opens after ATC so browser + bot don't share a "
            "rate-limit window.[/dim]"
        )

        tasks: list[asyncio.Task[None]] = []
        restock_listener: RestockRListener | None = None
        discord_listener = None

        if "restockr" in active:
            restock_listener = RestockRListener(
                self.settings.restockr.socket_url, self.client.token or ""
            )
            restock_listener.set_parent_id(self.parent_id)
            restock_listener.on_restock(
                lambda alert: self._handle_restock(alert, source="restockr")
            )
            await restock_listener.connect()
            console.print(
                "[green]Connected to RestockR — waiting for Target signals…[/green]"
            )
            tasks.append(asyncio.create_task(restock_listener.wait_forever()))

        if "discord" in active:
            from pokebot.discord_alerts.listener import DiscordAlertListener

            discord_cfg = self.settings.discord
            if not discord_cfg.guild_id or not discord_cfg.channel_id:
                raise RuntimeError(
                    "config/settings.yaml discord.guild_id and discord.channel_id "
                    "are required for --source discord"
                )
            discord_listener = DiscordAlertListener.from_env(
                guild_id=discord_cfg.guild_id,
                channel_id=discord_cfg.channel_id,
                token_env=discord_cfg.token_env,
            )
            discord_listener.on_restock(
                lambda alert: self._handle_restock(alert, source="discord")
            )
            console.print(
                f"[green]Starting Discord listener[/green] "
                f"guild={discord_cfg.guild_id} channel={discord_cfg.channel_id}"
            )
            tasks.append(asyncio.create_task(discord_listener.run()))

        try:
            await asyncio.gather(*tasks)
        finally:
            if restock_listener is not None:
                await restock_listener.disconnect()
            if discord_listener is not None:
                await discord_listener.close()

    def _on_watchlist(self, sku: str) -> bool:
        if self.profile is None:
            return False
        if "TEST-SKU" in sku:
            return True
        return sku in set(self.profile.product_skus)

    async def _open_in_chrome(self, url: str) -> None:
        console.print(f"[bold green]Opening in Chrome[/bold green] → {url}")
        try:
            await asyncio.to_thread(open_url_in_system_chrome, url)
        except Exception as exc:
            console.print(f"[red]Failed to open Chrome:[/red] {exc}")

    async def _handle_restock(
        self,
        alert: RestockAlert,
        *,
        source: AlertSource = "restockr",
    ) -> None:
        store = normalize_store(alert.store)
        sku = alert.sku or alert.id
        url = alert.resolve_url(self.parent_id)

        if store not in self.retailers:
            return

        label = "Discord" if source == "discord" else f"{store.capitalize()} restock"
        console.print(
            f"[cyan]{label} signal[/cyan] {alert.product or sku} "
            f"(qty={alert.stock_quantity})"
        )

        if not url:
            console.print("[dim]Skipped — no product URL in alert[/dim]")
            return

        watchlist_only = (
            self.settings.discord.watchlist_only
            if source == "discord"
            else self.settings.autobuy.watchlist_only
        )
        on_watchlist = self._on_watchlist(sku)
        if watchlist_only and not on_watchlist:
            console.print(f"[dim]Skipped — SKU {sku} not on watchlist[/dim]")
            return

        if alert.stock_quantity is not None:
            min_qty = self.settings.autobuy.target_min_quantity
            if alert.stock_quantity < min_qty:
                console.print(
                    f"[dim]Skipped — {store} qty {alert.stock_quantity} "
                    f"< min {min_qty}[/dim]"
                )
                return

        if store not in CHECKOUT_STORES:
            async with self._tracker.acquire(
                AlertKey(vendor=store, sku=sku)
            ) as should_proceed:
                if not should_proceed:
                    console.print(
                        f"[dim]Skipped — already acting on {store}/{sku}[/dim]"
                    )
                    return
                console.print(
                    f"[yellow]No {store} checkout client[/yellow] — "
                    "opening in Chrome to buy manually"
                )
                await self._open_in_chrome(url)
            return

        if sku in self._purchased_skus:
            console.print(f"[dim]Skipped — already purchased {sku} this session[/dim]")
            return

        alert_key = AlertKey(vendor=store, sku=sku)
        async with self._tracker.acquire(alert_key) as should_proceed:
            if not should_proceed:
                console.print(f"[dim]Skipped — already acting on {store}/{sku}[/dim]")
                return

            console.print(
                f"[bold green]Reseller checkout triggered[/bold green] → {url} "
                f"(qty={alert.stock_quantity} source={source})"
            )
            result = await self.pipeline.handle_alert(alert, parent_id=self.parent_id)
            open_chrome = (
                self.settings.discord.open_in_chrome
                if source == "discord"
                else on_watchlist
            )
            if result is None:
                console.print("[red]No result — task could not be built[/red]")
                if open_chrome:
                    await self._open_in_chrome(url)
                return
            if result.success:
                self._purchased_skus.add(sku)
            color = "green" if result.success else "red"
            console.print(
                f"[{color}]{result.status.value.upper()}[/{color}] — "
                f"sku={result.sku} order_id={result.order_id} "
                f"attempts={result.attempts} msg={result.message}"
            )
            # Open after ATC/checkout so the PDP load doesn't compete for the
            # same Target account rate limit during the bot's cart POST.
            if open_chrome:
                await self._open_in_chrome(url)
