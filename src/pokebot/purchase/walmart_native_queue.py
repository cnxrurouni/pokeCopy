from __future__ import annotations

"""Walmart queue client using a real browser + OS click (no Playwright).

v1: open RestockR queue URL in Edge/Chrome with ``data/sessions/walmart``,
OS-click Join queue / Get in line, then poll window titles and notify the
human when it looks like their turn. Press-and-hold + purchase stay manual.
"""

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from rich.console import Console

from pokebot.config import (
    InvisiblePlaywrightSettings,
    PlaywrightSettings,
    data_dir,
    session_dir,
)
from pokebot.purchase.native_browser import NativeBrowserSession, launch_native_browser
from pokebot.purchase.os_input import (
    find_walmart_window_titles,
    prompt_join_fallback,
    title_suggests_queue_ready,
    title_suggests_queue_waiting,
    try_click_join_queue,
)

console = Console()


def walmart_native_profile(browser_settings: PlaywrightSettings | None = None) -> Path:
    """Profile used by ``login walmart --manual-chrome`` (not invisible Firefox)."""
    settings = browser_settings or PlaywrightSettings()
    # Force classic Chromium profile even if invisible_playwright is enabled.
    classic = settings.model_copy(
        update={"invisible_playwright": InvisiblePlaywrightSettings(enabled=False)}
    )
    path = session_dir("walmart", browser_settings=classic)
    # Extra safety: never point at walmart-invisible.
    if "invisible" in path.name:
        path = data_dir() / "sessions" / "walmart"
    return path


@dataclass
class NativeQueueEntry:
    url: str
    sku: str
    label: str
    session: NativeBrowserSession
    joined_at: float = field(default_factory=time.time)
    status: str = "opened"  # opened | join_clicked | waiting | ready | stopped


class WalmartNativeQueueClient:
    """Manage one or more real-browser Walmart queue tabs/windows."""

    def __init__(
        self,
        *,
        browser_settings: PlaywrightSettings | None = None,
        max_queues: int = 10,
        page_load_wait_s: float = 8.0,
        join_click_timeout_s: float = 45.0,
        watch_poll_s: float = 3.0,
    ) -> None:
        self.browser_settings = browser_settings or PlaywrightSettings()
        self.max_queues = max(1, max_queues)
        self.page_load_wait_s = page_load_wait_s
        self.join_click_timeout_s = join_click_timeout_s
        self.watch_poll_s = watch_poll_s
        self._entries: dict[str, NativeQueueEntry] = {}
        self._watch_task: asyncio.Task[None] | None = None

    @property
    def active_count(self) -> int:
        return sum(1 for e in self._entries.values() if e.status != "stopped")

    def _key(self, sku: str, url: str) -> str:
        return f"{sku}|{url}"

    async def join_queue(
        self,
        url: str,
        sku: str,
        *,
        label: str | None = None,
        watch: bool = True,
    ) -> bool:
        """Open URL in real Edge, OS-click Join, optionally watch for your-turn."""
        key = self._key(sku, url)
        if key in self._entries and self._entries[key].status != "stopped":
            console.print(f"[dim]Already tracking queue for {sku}[/dim]")
            return True
        if self.active_count >= self.max_queues:
            console.print(
                f"[yellow]Native queue cap reached ({self.max_queues}) — skip {sku}[/yellow]"
            )
            return False

        profile = walmart_native_profile(self.browser_settings)
        channel = self.browser_settings.browser_channel or "msedge"
        console.print(
            f"[cyan]Walmart native queue[/cyan] — real browser (no Playwright)\n"
            f"  profile: {profile}\n"
            f"  url: {url}"
        )
        session = await asyncio.to_thread(
            launch_native_browser,
            profile=profile,
            start_url=url,
            channel=channel,
        )
        if session.proc is None:
            console.print(
                "[red]Could not launch Edge/Chrome.[/red] Run manually:\n"
                f"  {session.command}"
            )
            prompt_join_fallback(reason="browser launch failed")
            return False

        entry = NativeQueueEntry(
            url=url,
            sku=sku,
            label=label or sku,
            session=session,
            status="opened",
        )
        self._entries[key] = entry

        console.print(
            f"[dim]Waiting {self.page_load_wait_s:.0f}s for page load…[/dim]"
        )
        await asyncio.sleep(self.page_load_wait_s)

        click = await asyncio.to_thread(
            try_click_join_queue, timeout_s=self.join_click_timeout_s
        )
        if click.ok:
            entry.status = "join_clicked"
            console.print(
                f"[green]Join attempted[/green] for {entry.label} — "
                "confirm you see waiting / in-line UI."
            )
        else:
            entry.status = "waiting"
            console.print(
                f"[yellow]Auto-join incomplete[/yellow] for {entry.label} — "
                "click Join queue in the browser if needed."
            )

        entry.status = "waiting"
        if watch:
            self._ensure_watch_loop()
        return True

    def _ensure_watch_loop(self) -> None:
        if self._watch_task is not None and not self._watch_task.done():
            return
        self._watch_task = asyncio.create_task(self._watch_loop())

    async def _watch_loop(self) -> None:
        """Poll window titles; beep when a title looks like your turn."""
        console.print(
            "[dim]Watching Walmart window titles for your-turn signals "
            "(press-and-hold + buy stay manual)…[/dim]"
        )
        notified: set[str] = set()
        try:
            while any(e.status in ("waiting", "join_clicked", "ready") for e in self._entries.values()):
                titles = await asyncio.to_thread(find_walmart_window_titles)
                for entry in list(self._entries.values()):
                    if entry.status == "stopped":
                        continue
                    if not entry.session.is_running():
                        # User may have closed the window.
                        if entry.status != "ready":
                            entry.status = "stopped"
                        continue
                    for title in titles:
                        if title_suggests_queue_ready(title):
                            entry.status = "ready"
                            if entry.sku not in notified:
                                notified.add(entry.sku)
                                self._notify_your_turn(entry, title)
                        elif (
                            entry.status == "waiting"
                            and title_suggests_queue_waiting(title)
                        ):
                            pass  # still waiting — quiet
                await asyncio.sleep(self.watch_poll_s)
        except asyncio.CancelledError:
            raise

    def _notify_your_turn(self, entry: NativeQueueEntry, title: str) -> None:
        try:
            import sys

            if sys.platform == "win32":
                import winsound

                winsound.MessageBeep(winsound.MB_ICONHAND)
        except Exception:
            print("\a", end="", flush=True)
        console.print(
            f"\n[bold green]YOUR TURN[/bold green] — {entry.label} ({entry.sku})\n"
            f"  window: {title}\n"
            "[bold yellow]Complete press-and-hold + purchase in that Edge window "
            "(not automated).[/bold yellow]\n"
        )

    async def stop(self) -> None:
        if self._watch_task is not None:
            self._watch_task.cancel()
            try:
                await self._watch_task
            except asyncio.CancelledError:
                pass
            self._watch_task = None
        for entry in self._entries.values():
            entry.status = "stopped"
        # Do not kill Edge — user may still be checking out.
        console.print(
            "[dim]Native queue client stopped (Edge windows left open).[/dim]"
        )


# Lazy singleton for orchestrator
_client: WalmartNativeQueueClient | None = None


def get_native_queue_client(
    *,
    browser_settings: PlaywrightSettings | None = None,
    max_queues: int = 10,
) -> WalmartNativeQueueClient:
    global _client
    if _client is None:
        _client = WalmartNativeQueueClient(
            browser_settings=browser_settings,
            max_queues=max_queues,
        )
    return _client
