from __future__ import annotations

"""RestockR listener that only opens product URLs — no buy, no queue join."""

import asyncio
import sys
import time
import webbrowser
from urllib.parse import urlparse

from rich.console import Console

from pokebot.alert_tracker import ActedAlertTracker, AlertKey
from pokebot.config import Settings, load_settings
from pokebot.purchase.native_browser import launch_native_browser
from pokebot.restockr.client import RestockRClient
from pokebot.restockr.listener import RestockRListener
from pokebot.restockr.models import RestockAlert, UserProfile

console = Console()


def _normalize_store(store: str) -> str:
    return store.strip().lower().replace(" ", "")


def play_alert_sound() -> None:
    """Audible cue that a watchlist URL was opened (return to the computer)."""
    try:
        if sys.platform == "win32":
            import winsound

            # Three rising tones — louder / more noticeable than a single MessageBeep.
            for freq in (880, 1175, 1319):
                winsound.Beep(freq, 220)
                time.sleep(0.05)
            winsound.MessageBeep(winsound.MB_ICONEXCLAMATION)
            return
    except Exception:
        pass
    # Terminal bell fallback (non-Windows / winsound unavailable).
    print("\a\a\a", end="", flush=True)


class RestockUrlOpener:
    """Connect to RestockR and open watchlist alert URLs. Nothing else."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        retailers: list[str] | None = None,
        watchlist_only: bool = True,
        dedup_seconds: int = 60,
        use_session_profile: bool = False,
        queue_only: bool = False,
        sound: bool = True,
    ) -> None:
        self.settings = settings or load_settings()
        self.client = RestockRClient(self.settings.restockr.api_base)
        self.profile: UserProfile | None = None
        self.parent_id: str | None = None
        self.retailers = {
            _normalize_store(r)
            for r in (
                retailers
                if retailers is not None
                else self.settings.autobuy.retailers
            )
        }
        # Same list AutobuyOrchestrator uses: profile.product_skus from RestockR.
        self.watchlist_only = watchlist_only
        self.queue_only = queue_only
        self.use_session_profile = use_session_profile
        self.sound = sound
        self._tracker = ActedAlertTracker(
            cooldown_seconds=dedup_seconds,
            dedup_window_seconds=dedup_seconds,
        )
        self._opened = 0
        self._watch_skus: set[str] = set()

    async def _setup_restockr_login(self) -> UserProfile:
        """Pre-setup: same as ``pokebot login restockr`` (token or interactive)."""
        console.print(
            "[bold]Setup[/bold] — logging into RestockR "
            "(same as [cyan]pokebot login restockr[/cyan])…"
        )
        from pokebot.restockr.client import _env

        if self.parent_id is None:
            self.parent_id = _env("RESTOCKR_PARENT_ACCOUNT")
        profile = await self.client.ensure_authenticated()
        # Exact RestockR watchlist you built in the app (productSkus).
        self._watch_skus = {str(s) for s in (profile.product_skus or [])}
        console.print(
            f"[green]RestockR login OK[/green] — {profile.username} "
            f"(watchlist: {len(self._watch_skus)} SKU(s))"
        )
        return profile

    async def run(self) -> None:
        self.profile = await self._setup_restockr_login()
        console.print(
            "[bold]listen-open[/bold] — open URL only, no checkout"
        )
        console.print(
            f"[dim]retailers={sorted(self.retailers) or 'any'} "
            f"watchlist_only={self.watchlist_only} "
            f"queue_only={self.queue_only} "
            f"sound={self.sound} "
            f"session_profile={self.use_session_profile}[/dim]"
        )
        if self.watchlist_only and not self._watch_skus:
            console.print(
                "[yellow]Warning:[/yellow] RestockR watchlist is empty — "
                "nothing will open until you add SKUs in the RestockR app."
            )

        listener = RestockRListener(
            self.settings.restockr.socket_url,
            self.client.token or "",
        )
        listener.set_parent_id(self.parent_id)
        listener.on_restock(self._handle_restock)

        await listener.connect()
        console.print(
            "[green]Connected — waiting for watchlist signals "
            "(Ctrl+C to stop)…[/green]"
            if self.watchlist_only
            else "[green]Connected — waiting for signals (Ctrl+C to stop)…[/green]"
        )
        try:
            await listener.wait_forever()
        finally:
            await listener.disconnect()
            console.print(f"[dim]Opened {self._opened} URL(s) this session.[/dim]")

    def _should_open(self, alert: RestockAlert, store: str, url: str | None) -> bool:
        if not url:
            console.print("[yellow]Skip — no URL on alert[/yellow]")
            return False
        if self.retailers and store not in self.retailers:
            console.print(f"[dim]Skip — retailer {store!r} not in filter[/dim]")
            return False
        if self.queue_only:
            from pokebot.purchase.walmart_queue import is_walmart_queue_alert

            if not (store == "walmart" and is_walmart_queue_alert(alert)):
                console.print("[dim]Skip — not a Walmart queue alert[/dim]")
                return False
        if self.watchlist_only:
            sku = str(alert.sku or "").strip()
            if not sku or sku not in self._watch_skus:
                console.print(f"[dim]Skip — {sku or '?'} not on RestockR watchlist[/dim]")
                return False
        return True

    async def _handle_restock(self, alert: RestockAlert) -> None:
        sku = alert.sku or alert.id
        store = _normalize_store(alert.store)
        url = alert.resolve_url(self.parent_id)

        console.print(
            f"[cyan]Signal[/cyan] {alert.product or sku} @ {alert.store} "
            f"(qty={alert.stock_quantity})"
        )

        if not self._should_open(alert, store, url):
            return

        assert url is not None
        key = AlertKey(vendor=store, sku=str(sku))
        async with self._tracker.acquire(key) as should_proceed:
            if not should_proceed:
                console.print(f"[dim]Skip — recent duplicate {store}/{sku}[/dim]")
                return
            if self.sound:
                await asyncio.to_thread(play_alert_sound)
            await asyncio.to_thread(self._open_url, url, store)
            self._opened += 1
            console.print(f"[green]Opened[/green] {url}")

    def _open_url(self, url: str, store: str) -> None:
        if not self.use_session_profile:
            webbrowser.open(url, new=2)
            return

        from pokebot.config import InvisiblePlaywrightSettings, session_dir

        retailer = "walmart" if "walmart" in store else (
            "target" if "target" in store else None
        )
        if retailer is None:
            host = urlparse(url).netloc.lower()
            if "walmart" in host:
                retailer = "walmart"
            elif "target" in host:
                retailer = "target"
        if retailer is None:
            webbrowser.open(url, new=2)
            return

        classic = self.settings.playwright.model_copy(
            update={"invisible_playwright": InvisiblePlaywrightSettings(enabled=False)}
        )
        profile = session_dir(retailer, browser_settings=classic)
        channel = classic.browser_channel or "msedge"
        session = launch_native_browser(
            profile=profile, start_url=url, channel=channel
        )
        if session.proc is None:
            console.print(
                f"[yellow]Native launch failed — system browser:[/yellow] {session.command}"
            )
            webbrowser.open(url, new=2)
