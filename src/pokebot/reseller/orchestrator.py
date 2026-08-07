from __future__ import annotations

from rich.console import Console

from pokebot.alert_tracker import ActedAlertTracker, AlertKey
from pokebot.config import Settings
from pokebot.doctor import check_target_auth_sidecar
from pokebot.reseller.pipeline import TargetPipeline
from pokebot.reseller.settings import ResellerSettings, load_reseller_settings
from pokebot.restockr.client import RestockRClient
from pokebot.restockr.listener import RestockRListener
from pokebot.restockr.models import RestockAlert
from pokebot.stores import normalize_store

console = Console()


class ResellerOrchestrator:
    """RestockR-driven Target HTTP checkout using Chrome-exported sidecar cookies."""

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
        self._tracker = ActedAlertTracker(
            cooldown_seconds=settings.autobuy.cooldown_seconds,
            dedup_window_seconds=settings.autobuy.dedup_window_seconds,
        )
        self._purchased_skus: set[str] = set()

    async def start(self) -> None:
        self.profile = await self.client.ensure_authenticated()
        console.print(
            f"[green]Logged in as[/green] {self.profile.username} "
            f"— watchlist: {len(self.profile.product_skus)} SKU(s)"
        )

        if self.pipeline.ensure_default_account():
            console.print(
                "[dim]No reseller accounts YAML — using session-target default account "
                "+ data/sessions/target-auth.json sidecar.[/dim]"
            )

        ok, detail = check_target_auth_sidecar()
        if ok:
            console.print(f"[green]{detail}[/green]")
        else:
            console.print(
                f"[red]Sidecar not ready:[/red] {detail}\n"
                "  Run: [bold]python -m pokebot login target[/bold]"
            )

        from pokebot.reseller.target_credentials import load_target_credentials
        from pokebot.reseller.target_login import ensure_target_login

        if load_target_credentials() is not None:
            console.print("[cyan]Ensuring Target commerce login…[/cyan]")
            result = await ensure_target_login(
                browser_settings=self.settings.playwright,
                headless=False,
            )
            if not result.ok:
                console.print(
                    "[yellow]Warning:[/yellow] Target not commerce-signed-in. "
                    "Checkouts may fail until: "
                    "python -m pokebot login target --auto"
                )

        console.print("[bold]Reseller pipeline mode:[/bold] LIVE")

        listener = RestockRListener(
            self.settings.restockr.socket_url, self.client.token or ""
        )
        listener.set_parent_id(self.parent_id)
        listener.on_restock(self._handle_restock)

        await listener.connect()
        console.print(
            "[green]Connected to RestockR — waiting for Target signals "
            "(HTTP checkout via curl_cffi + Chrome sidecar)…[/green]"
        )
        try:
            await listener.wait_forever()
        finally:
            await listener.disconnect()

    async def _handle_restock(self, alert: RestockAlert) -> None:
        store = normalize_store(alert.store)
        sku = alert.sku or alert.id
        url = alert.resolve_url(self.parent_id)

        if store != "target":
            return

        console.print(
            f"[cyan]Target restock signal[/cyan] {alert.product or sku} "
            f"(qty={alert.stock_quantity})"
        )

        if not url:
            console.print("[dim]Skipped — no product URL in alert[/dim]")
            return

        if self.settings.autobuy.watchlist_only and self.profile:
            watchlist = set(self.profile.product_skus)
            if sku not in watchlist and "TEST-SKU" not in sku:
                console.print(f"[dim]Skipped — SKU {sku} not on watchlist[/dim]")
                return

        if alert.stock_quantity is not None:
            min_qty = self.settings.autobuy.target_min_quantity
            if alert.stock_quantity < min_qty:
                console.print(
                    f"[dim]Skipped — Target qty {alert.stock_quantity} < min {min_qty}[/dim]"
                )
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
                f"(qty={alert.stock_quantity})"
            )
            result = await self.pipeline.handle_alert(alert, parent_id=self.parent_id)
            if result is None:
                console.print("[red]No result — task could not be built[/red]")
                return
            if result.success:
                self._purchased_skus.add(sku)
            color = "green" if result.success else "red"
            console.print(
                f"[{color}]{result.status.value.upper()}[/{color}] — "
                f"sku={result.sku} order_id={result.order_id} "
                f"attempts={result.attempts} msg={result.message}"
            )
