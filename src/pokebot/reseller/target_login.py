from __future__ import annotations

"""Ensure Target commerce login using stored email/password.

Opens the persistent ``data/sessions/target`` profile, autofills credentials when
the session is not commerce-authenticated, waits for any PerimeterX press-and-hold
(human only — never auto-holds during login), then settles /cart + /checkout so
``login-session`` is written.
"""

import asyncio
import re
import time
from dataclasses import dataclass

from rich.console import Console

from pokebot.config import PlaywrightSettings
from pokebot.reseller.session_verify import (
    TargetSessionCheck,
    inspect_commerce_login,
    print_session_check,
    verify_target_session,
)
from pokebot.reseller.target_credentials import (
    TargetCredentials,
    credentials_path,
    load_target_credentials,
)

console = Console()

_LOGIN_URL = (
    "https://www.target.com/login"
    "?client_id=ecom-web-1.0.0"
    "&ui_namespace=ui-default"
    "&back_button_action=browser"
    "&keep_me_signed_in=true"
    "&kmsi_default=true"
)


@dataclass
class TargetLoginResult:
    ok: bool
    check: TargetSessionCheck | None = None
    detail: str = ""


async def _wait_human_px(page, *, timeout_s: float = 180.0) -> None:
    """If a press-and-hold is up, wait for the human — do not auto-hold."""
    from pokebot.purchase.helpers import is_press_and_hold_challenge

    if not await is_press_and_hold_challenge(page, log=False):
        return
    console.print(
        "[yellow]PerimeterX press-and-hold detected — complete it in the browser "
        f"(waiting up to {timeout_s:.0f}s). Do not close the window.[/yellow]"
    )
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not await is_press_and_hold_challenge(page, log=False):
            console.print("[green]Challenge cleared.[/green]")
            return
        await asyncio.sleep(1.0)
    console.print("[red]Timed out waiting for press-and-hold.[/red]")


async def _scopes(page):
    """Page plus same-origin / login iframes (Target identity often embeds a frame)."""
    scopes = [page]
    try:
        for frame in page.frames:
            if frame is page.main_frame:
                continue
            url = (frame.url or "").lower()
            if any(
                x in url
                for x in ("login", "identity", "gator", "auth", "target.com")
            ):
                scopes.append(frame)
    except Exception:
        pass
    # Also try frame_locator for common identity iframes
    for sel in (
        'iframe[src*="login"]',
        'iframe[src*="identity"]',
        'iframe[src*="gator"]',
        'iframe[title*="Sign" i]',
        "iframe",
    ):
        try:
            fl = page.frame_locator(sel).first
            # Probe cheaply — if no iframe, later waits fail fast.
            scopes.append(fl)
        except Exception:
            continue
    return scopes


async def _find_visible(scopes, selectors: list[str], *, timeout_ms: int = 2500):
    """Return (scope, locator) for the first visible match across scopes."""
    for scope in scopes:
        for sel in selectors:
            try:
                loc = scope.locator(sel).first
                await loc.wait_for(state="visible", timeout=timeout_ms)
                return scope, loc
            except Exception:
                continue
    return None, None


async def _click_first_button(scope, patterns: tuple[str, ...]) -> bool:
    for pattern in patterns:
        btn = scope.get_by_role("button", name=re.compile(pattern, re.I))
        try:
            if await btn.count() and await btn.first.is_enabled():
                await btn.first.click(timeout=5_000)
                return True
        except Exception:
            continue
    # id=login is common on Target
    try:
        loc = scope.locator("button#login, button[type='submit']").first
        if await loc.count() and await loc.is_visible():
            await loc.click(timeout=5_000)
            return True
    except Exception:
        pass
    return False


async def _fill_and_submit_login(page, creds: TargetCredentials) -> None:
    """Autofill Target's username → continue → password → submit flow."""
    console.print("[dim]Navigating to Target login…[/dim]")
    await page.goto(_LOGIN_URL, wait_until="domcontentloaded", timeout=60_000)
    await page.wait_for_timeout(2000)
    await _wait_human_px(page)

    # If Target bounced us off /login (soft session), try Account → Sign in.
    if "/login" not in (page.url or "").lower():
        console.print(
            f"[dim]Landed on {page.url[:80]} — opening Sign in from header…[/dim]"
        )
        for sel in (
            '[data-test="@web/AccountLink"]',
            'a[href*="/login"]',
            'a[href*="account"]',
            'button:has-text("Sign in")',
            'a:has-text("Sign in")',
        ):
            try:
                loc = page.locator(sel).first
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=5_000)
                    await page.wait_for_timeout(1500)
                    break
            except Exception:
                continue
        # Account menu → Sign in
        try:
            sign = page.get_by_role(
                "link", name=re.compile(r"sign in", re.I)
            ).first
            if await sign.count() and await sign.is_visible():
                await sign.click(timeout=5_000)
                await page.wait_for_timeout(1500)
        except Exception:
            pass
        await _wait_human_px(page)

    scopes = await _scopes(page)
    console.print("[dim]Looking for email/username field (incl. iframes)…[/dim]")
    scope, user = await _find_visible(
        scopes,
        [
            "#username",
            'input[name="username"]',
            'input[id="username"]',
            'input[type="email"]',
            'input[autocomplete="username"]',
            'input[data-test="login-username"]',
        ],
        timeout_ms=4000,
    )
    if user is None:
        # Re-scan frames after SPA settle
        await page.wait_for_timeout(2000)
        scopes = await _scopes(page)
        scope, user = await _find_visible(
            scopes,
            [
                "#username",
                'input[name="username"]',
                'input[id="username"]',
                'input[type="email"]',
                'input[autocomplete="username"]',
            ],
            timeout_ms=5000,
        )
    if user is None:
        raise TimeoutError(
            "Could not find Target username field on page or in iframes. "
            "Complete sign-in in the browser window (or use --manual-chrome)."
        )

    console.print(f"[dim]Filling email ({creds.email})…[/dim]")
    await user.click(timeout=5_000)
    await user.fill("")
    await user.fill(creds.email)
    await page.wait_for_timeout(400)

    # Keep me signed in if present
    for sel in (
        'input[name="keepMeSignedIn"]',
        'input[type="checkbox"][id*="keep" i]',
        'label:has-text("Keep me signed in")',
    ):
        try:
            loc = (scope or page).locator(sel).first
            if await loc.count() and await loc.is_visible():
                tag = await loc.evaluate("el => el.tagName")
                if tag == "INPUT":
                    if not await loc.is_checked():
                        await loc.check(force=True)
                else:
                    await loc.click()
                break
        except Exception:
            continue

    console.print("[dim]Submitting username…[/dim]")
    await _click_first_button(scope or page, (r"continue", r"next", r"^sign in$"))
    await page.wait_for_timeout(2000)
    await _wait_human_px(page)

    scopes = await _scopes(page)
    console.print("[dim]Looking for password field…[/dim]")
    scope2, pwd = await _find_visible(
        scopes,
        [
            "#password",
            'input[name="password"]',
            'input[id="password"]',
            'input[type="password"]',
            'input[autocomplete="current-password"]',
        ],
        timeout_ms=8000,
    )
    if pwd is None:
        console.print(
            "[yellow]Password field not found — complete sign-in in the browser "
            "if Target is waiting on passkey/code.[/yellow]"
        )
        return

    console.print("[dim]Filling password and signing in…[/dim]")
    await pwd.click(timeout=5_000)
    await pwd.fill("")
    await pwd.fill(creds.password)
    await page.wait_for_timeout(400)
    await _click_first_button(
        scope2 or page, (r"sign in", r"continue", r"log in", r"submit")
    )

    await page.wait_for_timeout(2500)
    await _wait_human_px(page, timeout_s=180.0)
    await page.wait_for_timeout(2000)


async def ensure_target_login(
    *,
    browser_settings: PlaywrightSettings | None = None,
    headless: bool = False,
    force: bool = False,
    credentials: TargetCredentials | None = None,
) -> TargetLoginResult:
    """Ensure the Target profile is commerce-signed-in; autofill login if needed."""
    settings = browser_settings or PlaywrightSettings()
    creds = credentials or load_target_credentials()

    if not force:
        check = await verify_target_session(
            browser_settings=settings,
            headless=headless,
        )
        if check.ok:
            return TargetLoginResult(ok=True, check=check, detail=check.detail)

    if creds is None:
        detail = (
            "Target credentials not configured. Set TARGET_EMAIL + TARGET_PASSWORD, "
            f"or copy {credentials_path().name} from "
            "config/target.credentials.example.yaml → config/target.credentials.yaml"
        )
        console.print(f"[red]{detail}[/red]")
        return TargetLoginResult(ok=False, detail=detail)

    console.print(
        f"[cyan]Auto-login Target[/cyan] as {creds.email} "
        f"(creds from {creds.source})…"
    )

    from playwright.async_api import async_playwright

    from pokebot.purchase.browser import launch_retailer_context

    playwright = await async_playwright().start()
    try:
        session = await launch_retailer_context(
            "target",
            headless=headless,
            browser_settings=settings,
            playwright=playwright,
        )
        try:
            page = (
                session.context.pages[0]
                if session.context.pages
                else await session.context.new_page()
            )

            if not force:
                ok, detail, _names = await inspect_commerce_login(page)
                if ok:
                    check = await verify_target_session(page=page)
                    return TargetLoginResult(ok=True, check=check, detail=detail)

            try:
                await _fill_and_submit_login(page, creds)
            except Exception as exc:
                console.print(
                    f"[yellow]Autofill issue ({exc})[/yellow]\n"
                    "[yellow]If you see a login form, finish it in the browser. "
                    "Otherwise waiting briefly then checking commerce session…[/yellow]"
                )
                deadline = time.monotonic() + 90.0
                while time.monotonic() < deadline:
                    await _wait_human_px(page, timeout_s=5.0)
                    # If commerce already OK, stop waiting early.
                    try:
                        await page.goto(
                            "https://www.target.com/checkout",
                            wait_until="domcontentloaded",
                            timeout=30_000,
                        )
                        await page.wait_for_timeout(1500)
                        body = ((await page.locator("body").inner_text()) or "").lower()
                        if "sign in to your account" not in body:
                            console.print(
                                "[dim]Checkout no longer asks to Sign in — continuing.[/dim]"
                            )
                            break
                    except Exception:
                        pass
                    await asyncio.sleep(3.0)

            # Settle cookies regardless — /checkout mints login-session.
            ok, detail, names = await inspect_commerce_login(page)
            present = [
                k
                for k in ("accessToken", "idToken", "login-session", "_tgt_session")
                if k in names
            ]
            missing = [
                k
                for k in ("accessToken", "idToken", "login-session", "_tgt_session")
                if k not in names
            ]
            check = TargetSessionCheck(
                ok=ok,
                profile_present=True,
                cookies_present=present,
                cookies_missing=missing,
                ui_signed_in=ok,
                page_url=page.url,
                detail=detail,
            )
            print_session_check(check)
            if not ok:
                console.print(
                    "[red]Auto-login did not reach commerce-signed-in state.[/red]\n"
                    "Fallback: .\\.venv\\Scripts\\python.exe -m pokebot login target "
                    "--manual-chrome"
                )
            return TargetLoginResult(ok=ok, check=check, detail=detail)
        finally:
            await session.close()
    finally:
        await playwright.stop()
