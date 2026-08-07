from __future__ import annotations

"""Native Edge Target PX harvest — no Playwright, no CDP during sensor warm.

Launches the saved Target Edge profile on the PDP, OS-warms the window, quits,
then reads cookies from the Chromium profile DB. Auth keys are overlaid from
the Edge commerce-cookie export (REGISTERED ``login-session``).
"""

import asyncio
import time

from rich.console import Console

from pokebot.config import PlaywrightSettings, InvisiblePlaywrightSettings, session_dir
from pokebot.enums import Retailer
from pokebot.purchase.native_browser import launch_native_browser
from pokebot.purchase.os_input import warm_browser_window, window_title_looks_like_target
from pokebot.reseller.chrome_cookies import CookieDecryptError, read_profile_cookies
from pokebot.reseller.commerce_cookies import load_commerce_cookies
from pokebot.reseller.harvester.base import HarvestContext, TokenHarvester
from pokebot.reseller.harvester.interception import PX_PRIMARY_COOKIE, px_token_present
from pokebot.reseller.models import HarvestedToken, TokenKind
from pokebot.reseller.target_ids import resolve_target_product_url, resolve_target_tcin

console = Console()

_AUTH_KEYS = (
    "accessToken",
    "idToken",
    "refreshToken",
    "login-session",
    "_tgt_session",
    "mid",
    "loyaltyid",
)
_PX_KEYS = ("_px3", "_px2", "_pxhd", "_pxvid", "pxcts")


class TargetNativeEdgeHarvester(TokenHarvester):
    """Mint ``_px3`` in real Edge (no CDP); HTTP owns ATC/checkout."""

    retailer = Retailer.TARGET
    kind = TokenKind.PX3

    def __init__(
        self,
        *,
        ttl_seconds: float = 300.0,
        browser_settings: PlaywrightSettings | None = None,
        interaction_seconds: float = 8.0,
        settle_seconds: float = 4.0,
        debug: bool = False,
    ) -> None:
        self.ttl_seconds = ttl_seconds
        self.browser_settings = browser_settings or PlaywrightSettings()
        self.interaction_seconds = interaction_seconds
        self.settle_seconds = settle_seconds
        self.debug = debug
        self.last_stop_reason: str | None = None

    def _log(self, message: str) -> None:
        if self.debug:
            print(f"[harvest:native_edge] {message}")

    async def harvest(
        self, ctx: HarvestContext, *, humanize: bool = True
    ) -> HarvestedToken | None:
        self.last_stop_reason = None
        return await asyncio.to_thread(self._harvest_sync, ctx, humanize)

    def _profile_dir(self):
        # Classic Edge profile — never the invisible-Firefox Walmart path.
        classic = self.browser_settings.model_copy(
            update={"invisible_playwright": InvisiblePlaywrightSettings(enabled=False)}
        )
        return session_dir("target", browser_settings=classic), classic

    def _harvest_sync(
        self, ctx: HarvestContext, humanize: bool
    ) -> HarvestedToken | None:
        export = load_commerce_cookies()
        if export is None or not export.has_login_session():
            self.last_stop_reason = "login_incomplete"
            console.print(
                "[red][LOGIN INCOMPLETE][/red] missing Edge commerce export "
                "with login-session.\n"
                "Fix: fully quit Edge, then:\n"
                "  [bold].\\.venv\\Scripts\\python.exe -m pokebot login target "
                "--monitor[/bold]\n"
                "Sign out → password sign-in → Cart → Checkout until cards → "
                "Enter (while Edge is still open)."
            )
            return None

        tcin = ctx.tcin or resolve_target_tcin(
            url=ctx.pdp_url or ctx.product_url, sku=None
        )
        start_url = ctx.pdp_url or ctx.product_url or "https://www.target.com/"
        if tcin:
            start_url = resolve_target_product_url(start_url, tcin=tcin)

        profile, classic = self._profile_dir()
        channel = classic.browser_channel or "msedge"
        console.print(
            f"[cyan][TOKEN FARM native_edge][/cyan] no CDP — profile={profile} "
            f"channel={channel} url={start_url}"
        )

        session = launch_native_browser(
            profile=profile, start_url=start_url, channel=channel
        )
        if session.proc is None:
            self.last_stop_reason = "browser_launch_failed"
            console.print(
                f"[red]Native Edge launch failed[/red] — tried: {session.command}"
            )
            return None

        try:
            # Let the PDP + PX sensor load before OS input.
            time.sleep(max(1.5, self.settle_seconds * 0.5))
            if humanize and self.interaction_seconds > 0:
                warm_browser_window(
                    title_predicate=window_title_looks_like_target,
                    duration_s=self.interaction_seconds,
                    label="Target Edge",
                )
            else:
                time.sleep(self.settle_seconds)
            # Extra settle so PX collector can finish POSTing.
            time.sleep(self.settle_seconds)
        finally:
            self._log("quitting Edge gently before cookie DB read")
            session.terminate_gently(wait_s=12.0)
            # Brief pause for SQLite WAL flush.
            time.sleep(0.8)

        try:
            jar = read_profile_cookies(profile, domain_substr="target.com")
        except CookieDecryptError as exc:
            self.last_stop_reason = "cookie_decrypt_failed"
            console.print(
                f"[red]Cookie DB read failed:[/red] {exc}\n"
                "[yellow]Fix:[/yellow] re-export while Edge is open:\n"
                "  [bold].\\.venv\\Scripts\\python.exe -m pokebot login target "
                "--monitor[/bold]"
            )
            return None

        # Auth from commerce export wins; PX from native jar wins.
        merged = dict(jar)
        for key in _AUTH_KEYS:
            value = export.cookies.get(key)
            if value:
                merged[key] = value
        for key in _PX_KEYS:
            if jar.get(key):
                merged[key] = jar[key]

        px3 = merged.get(PX_PRIMARY_COOKIE, "")
        # Reject binary/mojibake jars (broken DB decrypt used to pass len checks).
        if px3 and (
            any(ord(c) > 126 or ord(c) < 32 for c in px3)
            or "\ufffd" in px3
        ):
            self.last_stop_reason = "px_corrupt"
            console.print(
                "[red][TOKEN FARM][/red] _px3 from cookie DB is not ASCII — "
                "decrypt/prefix strip failed. Re-run preflight; if persistent, "
                "re-export via login target --monitor."
            )
            return None
        if not px_token_present({PX_PRIMARY_COOKIE: px3} if px3 else {}):
            self.last_stop_reason = "px_missing"
            console.print(
                "[red][TOKEN FARM][/red] no usable _px3 after native Edge warm. "
                "Retry preflight; if persistent, open the PDP manually in the "
                "bot Edge profile once, then re-run."
            )
            return None
        if not merged.get("login-session"):
            self.last_stop_reason = "login_incomplete"
            console.print(
                "[red][LOGIN INCOMPLETE][/red] jar missing login-session after merge."
            )
            return None
        if not merged.get("accessToken") or not merged.get("idToken"):
            self.last_stop_reason = "login_incomplete"
            console.print(
                "[red][LOGIN INCOMPLETE][/red] missing accessToken/idToken."
            )
            return None

        auth_keys = [k for k in _AUTH_KEYS if k in merged]
        console.print(
            f"[green][TOKEN FARM native_edge][/green] _px3 ready "
            f"(len={len(px3)}) auth={auth_keys} "
            f"tcin={tcin or ctx.tcin} qty={ctx.quantity} — HTTP will ATC"
        )
        self._log(
            f"export_age_m={export.age_seconds / 60:.1f} jar_keys={len(merged)}"
        )
        return HarvestedToken(
            kind=self.kind,
            retailer=self.retailer,
            value=px3,
            cookies=merged,
            ttl_seconds=self.ttl_seconds,
            account_id=ctx.account.id,
            cart_primed=False,
        )
