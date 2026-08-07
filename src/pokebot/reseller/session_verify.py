from __future__ import annotations

from dataclasses import dataclass, field

from rich.console import Console

from pokebot.config import PlaywrightSettings, session_dir
from pokebot.purchase.browser import has_retailer_session, launch_retailer_context
from pokebot.reseller.harvester.interception import filter_domain_cookies

console = Console()

_AUTH_COOKIES = ("accessToken", "idToken", "login-session", "_tgt_session")
_CHECKOUT_SIGNIN = "sign in to your account"
_FIX_LOGIN = (
    r".\.venv\Scripts\python.exe -m pokebot login target --monitor"
)


@dataclass
class TargetSessionCheck:
    ok: bool
    profile_present: bool = False
    cookies_present: list[str] = field(default_factory=list)
    cookies_missing: list[str] = field(default_factory=list)
    ui_signed_in: bool | None = None
    cart_api_status: int | None = None
    cart_api_ok: bool | None = None
    page_url: str | None = None
    detail: str = ""


async def _page_body_lower(page) -> str:
    try:
        return ((await page.locator("body").inner_text()) or "").lower()
    except Exception:
        return ""


async def _cookie_names(context) -> set[str]:
    cookies = await context.cookies()
    names = {c["name"] for c in filter_domain_cookies(cookies, "target.com")}
    names |= {c["name"] for c in cookies if c["name"] in _AUTH_COOKIES}
    return names


async def inspect_commerce_login(page) -> tuple[bool, str, set[str]]:
    """Return (ok, detail, cookie_names) using /checkout as ground truth.

    Account pages can look signed-in while checkout still demands Sign in and
    pre_checkout returns INVALID_GUEST_STATUS. Also mint login-session by
    landing on /checkout when possible.
    """
    # Warm homepage + cart, then insist on /checkout (login-session is minted there).
    for url in ("https://www.target.com/", "https://www.target.com/cart"):
        try:
            await page.goto(url, wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(1500)
        except Exception:
            continue

    for attempt in range(3):
        try:
            await page.goto(
                "https://www.target.com/checkout",
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            await page.wait_for_timeout(3500)
        except Exception:
            continue
        url_now = (page.url or "").lower()
        if "/checkout" in url_now or "/login" in url_now:
            break
        # Empty cart often bounces back to /cart — still try once more.
        if attempt < 2:
            await page.wait_for_timeout(1000)

    body = await _page_body_lower(page)
    names = await _cookie_names(page.context)
    page_url = page.url or ""
    checkout_signin = _CHECKOUT_SIGNIN in body
    on_login = "/login" in page_url.lower()
    on_checkout = "/checkout" in page_url.lower()
    has_login_session = "login-session" in names
    has_tokens = "accessToken" in names and "idToken" in names

    commerce_ui_ok = (not checkout_signin) and (not on_login) and on_checkout
    if commerce_ui_ok and has_tokens and has_login_session:
        return (
            True,
            "Signed in for commerce — /checkout is not prompting Sign in; "
            "accessToken+idToken+login-session present.",
            names,
        )
    if checkout_signin or on_login:
        return (
            False,
            "NOT signed in for commerce — /checkout shows Sign in to your account "
            f"(or bounced to /login). Fully quit Edge, then: {_FIX_LOGIN} "
            "— sign in, open Cart→Checkout, complete any Sign in wall there, "
            "press Enter, close Edge with X.",
            names,
        )
    if not has_login_session:
        stuck = f" (ended on {page_url})" if page_url else ""
        return (
            False,
            "Identity cookies present but missing login-session"
            f"{stuck}. Account can look signed-in while commerce is not. "
            f"Fully quit Edge, then: {_FIX_LOGIN} — must land on Checkout "
            "(shipping/payment, not Sign in / not bounced to Cart), then "
            "press Enter and close Edge with X.",
            names,
        )
    if not has_tokens:
        missing = [k for k in ("accessToken", "idToken") if k not in names]
        return (
            False,
            f"Missing auth cookies {missing}. Fully quit Edge, then: {_FIX_LOGIN}",
            names,
        )
    return (
        False,
        f"Could not confirm Target commerce sign-in{f' (on {page_url})' if page_url else ''}. "
        f"Fully quit Edge, then: {_FIX_LOGIN}",
        names,
    )


async def verify_target_session(
    *,
    browser_settings: PlaywrightSettings | None = None,
    headless: bool = False,
    open_browser: bool = True,
    page=None,
) -> TargetSessionCheck:
    """Confirm Target commerce auth is available for HTTP checkout.

    ``login-session`` is minted in real Edge and does not survive into Playwright
    (and is cleared when Edge exits). Primary ground truth is the commerce-cookie
    export written by ``login target --monitor`` while Edge was still open.
    """
    from pokebot.reseller.commerce_cookies import load_commerce_cookies

    settings = browser_settings or PlaywrightSettings()
    profile_present = has_retailer_session("target", browser_settings=settings)
    export = load_commerce_cookies()

    if export is not None and export.has_login_session() and not export.missing_required():
        present = [k for k in _AUTH_COOKIES if k in export.cookies]
        missing = [k for k in _AUTH_COOKIES if k not in export.cookies]
        age_m = int(export.age_seconds // 60)
        return TargetSessionCheck(
            ok=True,
            profile_present=profile_present,
            cookies_present=present,
            cookies_missing=missing,
            ui_signed_in=True,
            detail=(
                f"Commerce cookie export OK (login-session present, age ~{age_m}m) "
                f"at {export.path}. Playwright cannot re-mint login-session — "
                "re-run login target --monitor if HTTP checkout starts failing."
            ),
        )

    if page is not None:
        ok, detail, names = await inspect_commerce_login(page)
        present = [k for k in _AUTH_COOKIES if k in names]
        missing = [k for k in _AUTH_COOKIES if k not in names]
        return TargetSessionCheck(
            ok=ok,
            profile_present=True,
            cookies_present=present,
            cookies_missing=missing,
            ui_signed_in=ok,
            page_url=page.url,
            detail=detail,
        )

    if not profile_present:
        return TargetSessionCheck(
            ok=False,
            profile_present=False,
            cookies_missing=list(_AUTH_COOKIES),
            detail=(
                f"No profile at {session_dir('target', browser_settings=settings)} and "
                f"no commerce cookie export. Run: {_FIX_LOGIN}"
            ),
        )

    # No usable export — explain; do not trust Playwright to mint login-session.
    export_note = (
        "no commerce cookie export on disk"
        if export is None
        else f"export missing {export.missing_required() or ['login-session']}"
    )
    if not open_browser:
        return TargetSessionCheck(
            ok=False,
            profile_present=True,
            cookies_missing=["login-session"],
            detail=(
                f"{export_note}. login-session only exists in live Edge and must be "
                f"exported via: {_FIX_LOGIN}"
            ),
        )

    from playwright.async_api import async_playwright

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
            ok, detail, names = await inspect_commerce_login(page)
            present = [k for k in _AUTH_COOKIES if k in names]
            missing = [k for k in _AUTH_COOKIES if k not in names]
            # Playwright almost never has login-session even when identity is OK.
            if "login-session" not in names:
                return TargetSessionCheck(
                    ok=False,
                    profile_present=True,
                    cookies_present=present,
                    cookies_missing=missing,
                    ui_signed_in=False,
                    page_url=page.url,
                    detail=(
                        f"Playwright profile has identity cookies but not login-session "
                        f"({export_note}). This is expected — login-session is cleared "
                        f"when Edge closes and is not re-minted under automation. "
                        f"Re-run: {_FIX_LOGIN} (Sign out → sign in → Checkout until "
                        "get_payment_cards → 200 → Enter; export happens automatically)."
                    ),
                )
            return TargetSessionCheck(
                ok=ok,
                profile_present=True,
                cookies_present=present,
                cookies_missing=missing,
                ui_signed_in=("login-session" in names) and ok,
                page_url=page.url,
                detail=detail,
            )
        finally:
            await session.close()
    finally:
        await playwright.stop()


def print_session_check(check: TargetSessionCheck) -> None:
    color = "green" if check.ok else "red"
    console.print(f"[{color}]Target session: {'OK' if check.ok else 'NOT SIGNED IN'}[/{color}]")
    console.print(f"  profile dir:     {'yes' if check.profile_present else 'no'}")
    console.print(f"  cookies present: {check.cookies_present or 'none'}")
    console.print(f"  cookies missing: {check.cookies_missing or 'none'}")
    if check.ui_signed_in is not None:
        console.print(f"  UI signed in:    {check.ui_signed_in}")
    if check.cart_api_status is not None:
        console.print(
            f"  cart API:        HTTP {check.cart_api_status} "
            f"({'ok' if check.cart_api_ok else 'FAIL'})"
        )
    if check.page_url:
        console.print(f"  page:            {check.page_url}")
    console.print(f"  detail:          {check.detail}")
