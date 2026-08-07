from __future__ import annotations

import asyncio
import contextlib
from typing import Any
from urllib.parse import urlparse

from rich.console import Console

from pokebot.reseller.live_capture import (
    is_interesting_target_api,
    is_interesting_target_auth_api,
)
from pokebot.reseller.traffic_log import TrafficLogger, set_traffic_logger, start_traffic_log

console = Console()

_NAME_HINTS = (
    ("cart_items", "add_to_cart"),
    ("pre_checkout", "pre_checkout"),
    ("/checkout", "checkout"),
    ("place_order", "place_order"),
    ("carts.target.com", "cart_api"),
    ("oauth", "oauth"),
    ("/token", "token"),
    ("login", "login"),
    ("gator", "gator"),
    ("identity", "identity"),
    ("create_session", "create_session"),
    ("secure_orchest", "auth_orchestrate"),
)


def _name_for(url: str, method: str) -> str:
    low = url.lower()
    for needle, name in _NAME_HINTS:
        if needle in low:
            return name
    path = urlparse(url).path.rsplit("/", 1)[-1] or "request"
    return f"{method.lower()}_{path[:40]}"


async def _log_response(
    response,
    logger: TrafficLogger,
    *,
    seen: set[str],
    channel: str = "manual_browser",
    interesting=is_interesting_target_api,
) -> None:
    try:
        request = response.request
        url = request.url
        method = request.method
        if not interesting(url, method):
            return
        # Dedup identical status/url/method bursts (redirects / double fire).
        key = f"{method}:{url}:{response.status}"
        if key in seen:
            return
        seen.add(key)
        if len(seen) > 5000:
            seen.clear()

        body = request.post_data
        try:
            text = await response.text()
        except Exception:
            text = ""

        # Capture Set-Cookie *names* (not values) so we can see login-session minting.
        set_cookie_names: list[str] = []
        try:
            headers = response.headers
            raw = headers.get("set-cookie") or headers.get("Set-Cookie") or ""
            if raw:
                for part in str(raw).split("\n"):
                    name = part.split("=", 1)[0].strip()
                    if name:
                        set_cookie_names.append(name)
        except Exception:
            pass

        name = _name_for(url, method)
        logger.http(
            channel=channel,
            name=name,
            method=method,
            url=url,
            status=response.status,
            request_headers=dict(request.headers),
            request_body=body,
            response_body=text,
            extra={
                "resource_type": request.resource_type,
                "set_cookie_names": set_cookie_names,
            },
        )
        short = (text or "")[:100].replace("\n", " ")
        color = "green" if response.status < 400 else "red"
        cookie_note = (
            f" set-cookie={set_cookie_names}" if set_cookie_names else ""
        )
        console.print(
            f"[{color}]{channel}[/{color}] {method} {name} → {response.status} "
            f"{short!r}{cookie_note}"
        )
    except Exception as exc:
        logger.note("monitor_response_error", error=str(exc))


def _attach_context(
    context,
    logger: TrafficLogger,
    seen: set[str],
    *,
    channel: str = "manual_browser",
    interesting=is_interesting_target_api,
) -> None:
    def _schedule(response) -> None:
        try:
            asyncio.get_running_loop().create_task(
                _log_response(
                    response,
                    logger,
                    seen=seen,
                    channel=channel,
                    interesting=interesting,
                )
            )
        except RuntimeError:
            pass

    context.on("response", _schedule)
    logger.note(
        "context_attached",
        pages=len(context.pages),
        urls=[p.url for p in context.pages[:5]],
        channel=channel,
    )


async def monitor_playwright_context(
    context,
    *,
    sku: str | None = None,
    stop_event: asyncio.Event | None = None,
    channel: str = "manual_browser",
) -> TrafficLogger:
    """Log Target APIs from an already-open Playwright browser context."""
    stop_event = stop_event or asyncio.Event()
    logger = start_traffic_log(
        sku=sku or "manual",
        account_id="manual-browser",
        enabled=True,
    )
    assert logger is not None
    console.print(f"[cyan]Manual browser traffic log →[/cyan] {logger.path}")
    seen: set[str] = set()
    _attach_context(context, logger, seen, channel=channel)
    console.print(
        "[green]Monitoring[/green] — shop Target normally; ATC/checkout APIs "
        "will be logged. Ctrl+C to stop."
    )
    try:
        while not stop_event.is_set():
            await asyncio.sleep(0.5)
    except asyncio.CancelledError:
        stop_event.set()
    logger.close(success=True, message="monitor stopped")
    set_traffic_logger(None)
    console.print(f"[dim]Traffic log saved → {logger.path}[/dim]")
    return logger


async def monitor_manual_browser(
    *,
    cdp_url: str = "http://127.0.0.1:9222",
    sku: str | None = None,
    stop_event: asyncio.Event | None = None,
) -> TrafficLogger:
    """Attach to a live Edge/Chrome via CDP and log Target checkout traffic.

    Start the browser first with remote debugging, e.g.::

        msedge.exe --remote-debugging-port=9222

    (Fully quit Edge before launching with that flag if it was already open.)
    """
    from playwright.async_api import async_playwright

    stop_event = stop_event or asyncio.Event()
    logger = start_traffic_log(
        sku=sku or "manual",
        account_id="manual-browser",
        enabled=True,
    )
    assert logger is not None
    console.print(f"[cyan]Manual browser traffic log →[/cyan] {logger.path}")
    console.print(f"[dim]Connecting to CDP {cdp_url}…[/dim]")

    seen: set[str] = set()
    playwright = await async_playwright().start()
    try:
        try:
            browser = await playwright.chromium.connect_over_cdp(cdp_url)
        except Exception as exc:
            set_traffic_logger(None)
            logger.close(success=False, message=str(exc))
            raise RuntimeError(
                f"Could not connect to {cdp_url}. Fully quit Edge/Chrome, then start:\n"
                "  msedge.exe --remote-debugging-port=9222\n"
                "Or use: python -m pokebot reseller monitor --launch\n"
                "Then re-run monitor if attaching to an already-open debug browser."
            ) from exc

        logger.note("cdp_connected", cdp_url=cdp_url, contexts=len(browser.contexts))
        console.print(
            f"[green]Connected[/green] — {len(browser.contexts)} context(s). "
            "Shop Target normally; ATC/checkout APIs will be logged. Ctrl+C to stop."
        )

        for context in browser.contexts:
            _attach_context(context, logger, seen)

        def _on_context(context) -> None:
            _attach_context(context, logger, seen)
            console.print("[dim]New browser context attached[/dim]")

        browser.on("context", _on_context)

        try:
            while not stop_event.is_set():
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            stop_event.set()

        logger.close(success=True, message="monitor stopped")
        console.print(f"[dim]Traffic log saved → {logger.path}[/dim]")
        return logger
    finally:
        set_traffic_logger(None)
        await playwright.stop()


async def launch_edge_for_monitor(
    *,
    port: int = 9222,
    user_data_dir: str | None = None,
) -> Any:
    """Launch Edge with remote debugging so ``monitor`` can attach.

    Uses a dedicated profile dir by default so it doesn't fight your daily Edge.
    Log into Target once in that window, then shop while ``reseller monitor`` runs.
    """
    from pathlib import Path

    from playwright.async_api import async_playwright

    from pokebot.config import data_dir

    profile = Path(user_data_dir) if user_data_dir else data_dir() / "sessions" / "target-manual-monitor"
    profile.mkdir(parents=True, exist_ok=True)
    cdp = f"http://127.0.0.1:{port}"

    playwright = await async_playwright().start()
    context = await playwright.chromium.launch_persistent_context(
        user_data_dir=str(profile),
        channel="msedge",
        headless=False,
        no_viewport=True,
        args=[f"--remote-debugging-port={port}"],
        ignore_default_args=["--enable-automation", "--no-sandbox"],
    )
    page = context.pages[0] if context.pages else await context.new_page()
    await page.goto("https://www.target.com/", wait_until="domcontentloaded")
    console.print(
        f"[green]Edge launched[/green] with debugging on port {port}\n"
        f"  profile: {profile}\n"
        f"  CDP: {cdp}\n"
        "[dim]Log into Target in this window if needed, then run monitor in another "
        "terminal (or keep this process and use --attach-only elsewhere).[/dim]"
    )
    return playwright, context, cdp


async def monitor_bot_profile_login(
    *,
    browser_settings=None,
    start_url: str = (
        "https://www.target.com/login"
        "?client_id=ecom-web-1.0.0&keep_me_signed_in=true&kmsi_default=true"
    ),
    debug_port: int = 9333,
) -> TrafficLogger:
    """Open real Edge on the bot profile (NO Playwright control) and CDP-monitor auth APIs.

    Playwright-controlled login gets ``T83072242`` on ``credential_validations``.
    This launches installed Edge with ``--user-data-dir=data/sessions/target`` and
    ``--remote-debugging-port``, then attaches for logging only — you type the
    password yourself. Press Enter when done.
    """
    import os
    import shutil
    import subprocess
    import sys
    from pathlib import Path

    from playwright.async_api import async_playwright

    from pokebot.config import load_settings, session_dir
    from pokebot.reseller.harvester.interception import filter_domain_cookies

    settings = browser_settings or load_settings().playwright
    profile = session_dir("target", browser_settings=settings).resolve()
    profile.mkdir(parents=True, exist_ok=True)

    logger = start_traffic_log(
        sku="login-monitor",
        account_id="bot-profile-manual-edge",
        enabled=True,
    )
    assert logger is not None
    console.print(f"[cyan]Login traffic log →[/cyan] {logger.path}")
    console.print(f"[dim]Bot profile: {profile}[/dim]")
    console.print(
        "\n[bold yellow]Important:[/bold yellow] Playwright-controlled login is "
        "AUTH_DENIED by Target. This opens [bold]real Edge[/bold] (no automation "
        "flags) so password entry can succeed.\n"
        "\n[bold]In the Edge window:[/bold]\n"
        "  1. If Account already says Hi, <name>: open Account → Sign out first\n"
        "  2. Sign in again (password or passkey; Keep me signed in)\n"
        "     You need a FULL login — soft session is not enough for login-session\n"
        "  3. Confirm Account shows Hi, <name>\n"
        "  4. Open Cart (add a cheap item if empty) → Checkout\n"
        "  5. Stay until shipping AND saved payment cards show\n"
        "     (monitor should log get_payment_cards → 200)\n"
        "  6. Press Enter here when done\n"
        "\n[dim]Fully quit other Edge windows using this profile if launch fails.[/dim]\n"
    )

    # Resolve Edge/Chrome exe (same candidates as login_manual_chrome).
    channel = (getattr(settings, "browser_channel", None) or "msedge").lower()
    candidates: list[Path] = []
    if sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        pf = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files"))
        pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"))
        if channel in ("msedge", "edge", ""):
            candidates += [
                pf / "Microsoft/Edge/Application/msedge.exe",
                pf86 / "Microsoft/Edge/Application/msedge.exe",
            ]
        if channel in ("chrome", ""):
            candidates += [
                pf / "Google/Chrome/Application/chrome.exe",
                pf86 / "Google/Chrome/Application/chrome.exe",
                local / "Google/Chrome/Application/chrome.exe",
            ]
    else:
        for name in ("microsoft-edge", "google-chrome", "chromium"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))

    exe = next((p for p in candidates if p and p.exists()), None)
    if exe is None:
        logger.close(success=False, message="no browser exe")
        set_traffic_logger(None)
        raise RuntimeError(
            "Could not find Edge/Chrome. Install Edge or set playwright.browser_channel."
        )

    cdp_url = f"http://127.0.0.1:{debug_port}"
    cmd = [
        str(exe),
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={debug_port}",
        "--no-first-run",
        "--no-default-browser-check",
        start_url,
    ]
    console.print(f"[dim]Launching: {' '.join(cmd[:3])} …[/dim]")
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    logger.note(
        "login_monitor_started",
        start_url=start_url,
        profile=str(profile),
        cdp=cdp_url,
        mode="manual_edge_cdp",
    )

    # Wait for CDP to come up
    playwright = await async_playwright().start()
    browser = None
    seen: set[str] = set()
    try:
        last_err: Exception | None = None
        for attempt in range(1, 21):
            try:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                break
            except Exception as exc:
                last_err = exc
                await asyncio.sleep(0.5)
        if browser is None:
            raise RuntimeError(
                f"Could not attach CDP at {cdp_url} after launch: {last_err}\n"
                "Fully quit Edge (Task Manager), then retry login target --monitor."
            ) from last_err

        console.print(
            f"[green]Attached to real Edge[/green] via CDP ({len(browser.contexts)} context(s)). "
            "Complete sign-in in that window — APIs will log here."
        )
        for context in browser.contexts:
            _attach_context(
                context,
                logger,
                seen,
                channel="login_monitor",
                interesting=is_interesting_target_auth_api,
            )

        def _on_context(context) -> None:
            _attach_context(
                context,
                logger,
                seen,
                channel="login_monitor",
                interesting=is_interesting_target_auth_api,
            )

        browser.on("context", _on_context)

        await asyncio.to_thread(
            input, "Press Enter when login/checkout guidance is done… "
        )

        # Cookie inventory from CDP contexts (known + any *session*/*login* names).
        # IMPORTANT: export while Edge is still alive — login-session is wiped on
        # process exit and Playwright never re-mints it.
        auth: list[str] = []
        sessionish: list[str] = []
        raw_cookies: list[dict] = []
        try:
            for context in browser.contexts:
                cookies = await context.cookies()
                raw_cookies = list(cookies)
                names = {c["name"] for c in cookies}
                auth = [
                    n
                    for n in (
                        "accessToken",
                        "idToken",
                        "refreshToken",
                        "login-session",
                        "_tgt_session",
                    )
                    if n in names
                ]
                sessionish = sorted(
                    n
                    for n in names
                    if any(
                        x in n.lower()
                        for x in ("session", "login", "token", "guest", "access")
                    )
                )
                if auth or sessionish:
                    break
        except Exception:
            pass
        logger.note(
            "login_monitor_cookies",
            auth_present=auth,
            sessionish_cookies=sessionish,
        )
        console.print(f"[dim]Auth cookies now: {auth or 'none'}[/dim]")
        if sessionish:
            console.print(f"[dim]Session-ish cookie names: {sessionish}[/dim]")
        if "login-session" in auth and raw_cookies:
            from pokebot.reseller.commerce_cookies import save_commerce_cookies

            export = save_commerce_cookies(raw_cookies, source="login-monitor-edge-cdp")
            console.print(
                f"[green]Exported commerce cookies →[/green] {export.path}\n"
                "[dim]login-session does not survive closing Edge / Playwright verify; "
                "HTTP checkout will load this export.[/dim]"
            )
            logger.note("commerce_cookies_exported", path=str(export.path))
        elif "login-session" not in auth:
            console.print(
                "[yellow]login-session still missing.[/yellow]\n"
                "A soft session (Hi, name + cart/checkout) is not enough.\n"
                "Next run: [bold]Sign out[/bold] in Edge, sign back in "
                "(watch for passwordless_authentications / auth_codes in the log), "
                "then Cart → Checkout until [bold]get_payment_cards → 200[/bold], "
                "then press Enter."
            )
        logger.close(success=True, message="login monitor stopped by user")
        console.print(f"[green]Traffic log saved →[/green] {logger.path}")
        console.print(
            "[dim]Say \"evaluate the last run\" (or paste the path) to map auth APIs.[/dim]"
        )
        return logger
    finally:
        set_traffic_logger(None)
        with contextlib.suppress(Exception):
            if browser is not None:
                await browser.close()
        with contextlib.suppress(Exception):
            await playwright.stop()
        # Ask Edge to exit gently so cookies flush into the bot profile.
        if proc.poll() is None:
            console.print(
                "[dim]Close the Edge window (X) if it is still open so cookies flush…[/dim]"
            )
            try:
                await asyncio.to_thread(proc.wait, 20)
            except Exception:
                pass
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.terminate()
                try:
                    await asyncio.to_thread(proc.wait, 10)
                except Exception:
                    pass
            if proc.poll() is None:
                with contextlib.suppress(Exception):
                    proc.kill()
        await asyncio.sleep(1.5)
