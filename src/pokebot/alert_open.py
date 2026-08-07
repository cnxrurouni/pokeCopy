from __future__ import annotations

import asyncio

from rich.console import Console

from pokebot.alert_tracker import ActedAlertTracker, AlertKey
from pokebot.config import Settings
from pokebot.stores import normalize_store
from pokebot.platform_util import open_url_in_system_chrome
from pokebot.restockr.client import RestockRClient
from pokebot.restockr.listener import RestockRListener
from pokebot.restockr.models import RestockAlert, UserProfile

console = Console()


class AlertOpenOrchestrator:
    """Listen for RestockR alerts and open product URLs in the user's normal Chrome.

    No Playwright, no bot profile, no checkout — just ``open`` / Chrome with the
    default everyday profile so you can buy manually.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        watchlist_only: bool | None = None,
        retailers: list[str] | None = None,
        force_login: bool = False,
    ) -> None:
        self.settings = settings
        self.client = RestockRClient(settings.restockr.api_base)
        self.profile: UserProfile | None = None
        self.parent_id: str | None = None
        self.watchlist_only = (
            settings.autobuy.watchlist_only
            if watchlist_only is None
            else watchlist_only
        )
        self.retailers = {
            normalize_store(r)
            for r in (retailers if retailers is not None else settings.autobuy.retailers)
        }
        self.force_login = force_login
        self._tracker = ActedAlertTracker(
            cooldown_seconds=settings.autobuy.cooldown_seconds,
            dedup_window_seconds=settings.autobuy.dedup_window_seconds,
        )

    async def start(self) -> None:
        if self.force_login:
            from pokebot.restockr.client import _env

            username = _env("RESTOCKR_USERNAME")
            password = _env("RESTOCKR_PASSWORD")
            parent = _env("RESTOCKR_PARENT_ACCOUNT")
            if not username or not password:
                raise RuntimeError(
                    "force login needs RESTOCKR_USERNAME and RESTOCKR_PASSWORD in the env"
                )
            await self.client.login(username, password, parent_account=parent)
            self.profile = await self.client.get_profile()
        else:
            self.profile = await self.client.ensure_authenticated()

        console.print(
            f"[green]Logged in as[/green] {self.profile.username} "
            f"— watchlist: {len(self.profile.product_skus)} SKU(s)"
        )
        console.print(
            "[cyan]Alert-open mode[/cyan] — restocks open in your normal Chrome "
            "(no bot profile, no purchase)."
        )
        if self.watchlist_only:
            console.print("[dim]Filter: watchlist only[/dim]")
        console.print(f"[dim]Retailers: {', '.join(sorted(self.retailers)) or '(none)'}[/dim]")

        listener = RestockRListener(
            self.settings.restockr.socket_url,
            self.client.token or "",
        )
        listener.set_parent_id(self.parent_id)
        listener.on_restock(self._handle_restock)

        await listener.connect()
        console.print(
            "[green]Connected to RestockR — waiting for alerts to open in Chrome...[/green]"
        )
        try:
            await listener.wait_forever()
        finally:
            await listener.disconnect()

    async def _handle_restock(self, alert: RestockAlert) -> None:
        sku = alert.sku or alert.id
        store = normalize_store(alert.store)
        url = alert.resolve_url(self.parent_id)

        console.print(
            f"[cyan]Restock signal[/cyan] {alert.product or sku} @ {alert.store} "
            f"(qty={alert.stock_quantity})"
        )

        if not url:
            console.print("[dim]Skipped — no product URL in alert[/dim]")
            return
        if store not in self.retailers:
            console.print(f"[dim]Skipped — retailer {store} not allowed[/dim]")
            return
        if self.watchlist_only and self.profile:
            watchlist = set(self.profile.product_skus)
            if sku not in watchlist and "TEST-SKU" not in sku:
                console.print(f"[dim]Skipped — SKU {sku} not on RestockR watchlist[/dim]")
                return

        alert_key = AlertKey(vendor=store, sku=sku)
        async with self._tracker.acquire(alert_key) as should_proceed:
            if not should_proceed:
                console.print(f"[dim]Skipped — already opened {store}/{sku} recently[/dim]")
                return

            console.print(f"[bold green]Opening in Chrome[/bold green] → {url}")
            try:
                await asyncio.to_thread(open_url_in_system_chrome, url)
            except Exception as exc:
                console.print(f"[red]Failed to open Chrome:[/red] {exc}")
