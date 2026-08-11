from __future__ import annotations

import asyncio
import contextlib
import os
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from pokebot.config import data_dir
from pokebot.doctor import (
    decode_jwt_claims,
    missing_target_auth_cookies,
    probe_target_cart_guest_type,
    target_access_token_is_guest,
    target_access_token_is_soft_remembered,
)
from pokebot.platform_util import (
    clear_profile_singleton,
    kill_browsers_using_profile,
    profile_singleton_present,
    profile_still_locked_hint,
)
from pokebot.session_auth import save_session_auth


def target_session_profile() -> Path:
    path = data_dir() / "sessions" / "target"
    path.mkdir(parents=True, exist_ok=True)
    return path.resolve()


def _pick_chrome_exe(channel: str = "chrome") -> Path | None:
    channel = (channel or "chrome").lower()
    candidates: list[Path] = []
    if sys.platform == "darwin":
        if channel in ("msedge", "edge"):
            candidates.append(
                Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")
            )
        candidates += [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"),
        ]
    elif sys.platform == "win32":
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        pf = Path(os.environ.get("PROGRAMFILES", r"C:\\Program Files"))
        pf86 = Path(os.environ.get("PROGRAMFILES(X86)", r"C:\\Program Files (x86)"))
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
        for name in ("google-chrome", "chromium", "microsoft-edge"):
            found = shutil.which(name)
            if found:
                candidates.append(Path(found))
    return next((p for p in candidates if p.exists()), None)


def _singleton_lock_pid(profile: Path) -> int | None:
    """Parse Chrome SingletonLock symlink target (``host-pid``) when present."""
    lock = profile / "SingletonLock"
    if not lock.exists():
        return None
    try:
        target = lock.readlink().name if lock.is_symlink() else lock.name
    except OSError:
        return None
    # Typical: "MacBook-Pro.local-12345" or bare "12345"
    tail = target.rsplit("-", 1)[-1]
    if tail.isdigit():
        return int(tail)
    return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def clear_profile_singleton_lock(profile: Path, *, wait_s: float = 8.0) -> str | None:
    """Ensure no other Chrome holds ``profile`` before a debug launch.

    If another process already owns the profile, a new Chrome with
    ``--remote-debugging-port=N`` exits immediately (code 0) and never binds N —
    the user may still see a window from the *old* non-debug instance.

    Returns an error message if the lock could not be cleared, else None.
    """
    profile = profile.resolve()
    lock = profile / "SingletonLock"
    if not lock.exists():
        return None

    kill_browsers_using_profile(profile)
    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if not lock.exists():
            return None
        pid = _singleton_lock_pid(profile)
        if pid is not None and not _pid_alive(pid):
            # Stale lock after a crash — remove Chrome's singleton files.
            for name in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                path = profile / name
                with contextlib.suppress(OSError):
                    if path.is_symlink() or path.exists():
                        path.unlink()
            if not lock.exists():
                return None
        kill_browsers_using_profile(profile)
        time.sleep(0.35)

    pid = _singleton_lock_pid(profile)
    pid_bit = f" (pid {pid})" if pid else ""
    return (
        f"Profile still locked{pid_bit}: {lock}\n"
        "Another Chrome is using this PokeBot profile, so remote debugging "
        "will not come up on the new port.\n"
        f"{profile_still_locked_hint()}"
    )


def cdp_attach_failure_hint(
    *,
    debug_port: int,
    proc: subprocess.Popen | None,
    profile: Path,
    exc: BaseException | None = None,
) -> str:
    """Actionable message when CDP HTTP attach fails."""
    parts: list[str] = []
    if exc is not None:
        parts.append(f"could not attach to login browser ({exc})")
    if proc is not None and proc.poll() is not None:
        parts.append(
            f"Chrome exited early (code {proc.returncode}) before CDP listened "
            f"on 127.0.0.1:{debug_port}. Usually another Chrome still holds "
            f"user-data-dir={profile}."
        )
    elif (profile / "SingletonLock").exists():
        pid = _singleton_lock_pid(profile)
        parts.append(
            f"Profile SingletonLock present"
            + (f" (pid {pid})" if pid else "")
            + " — quit that Chrome, then re-run login."
        )
    else:
        parts.append(
            f"Nothing is listening on 127.0.0.1:{debug_port}. "
            "Re-run login after fully quitting the PokeBot Chrome window."
        )
    parts.append(profile_still_locked_hint())
    return "\n".join(parts)


def launch_chrome_with_debug(
    *,
    profile: Path,
    start_url: str,
    channel: str = "chrome",
) -> tuple[subprocess.Popen | None, str, int | None]:
    """Launch system Chrome on a dedicated profile with remote debugging."""
    profile = profile.resolve()
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        debug_port = int(sock.getsockname()[1])

    exe = _pick_chrome_exe(channel)
    chrome_args = [
        f"--user-data-dir={profile}",
        f"--remote-debugging-port={debug_port}",
        "--remote-debugging-address=127.0.0.1",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        start_url,
    ]
    if exe is None:
        hint = (
            f'chrome --user-data-dir="{profile}" --remote-debugging-port={debug_port} '
            f'--remote-debugging-address=127.0.0.1 "{start_url}"'
        )
        return None, hint, None

    lock_err = clear_profile_singleton_lock(profile)
    if lock_err:
        # Still try to launch — caller waits for CDP and surfaces diagnostics —
        # but print immediately so the user knows before pressing Enter.
        print(f"Warning: {lock_err}\n")

    proc = subprocess.Popen(
        [str(exe), *chrome_args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return (
        proc,
        (
            f'{exe} --user-data-dir="{profile}" '
            f"--remote-debugging-port={debug_port} "
            f'--remote-debugging-address=127.0.0.1 "{start_url}"'
        ),
        debug_port,
    )


async def _cdp_get_all_cookies(debug_port: int) -> tuple[dict[str, str], str]:
    """Read cookies via raw CDP WebSocket (no Playwright)."""
    import aiohttp

    base = f"http://127.0.0.1:{debug_port}"
    url = "?"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{base}/json/version", timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            resp.raise_for_status()
            version = await resp.json()
        ws_url = version.get("webSocketDebuggerUrl")
        if not ws_url:
            raise RuntimeError("Chrome CDP did not expose webSocketDebuggerUrl")

        with contextlib.suppress(Exception):
            async with session.get(
                f"{base}/json", timeout=aiohttp.ClientTimeout(total=5)
            ) as resp:
                tabs = await resp.json()
            for tab in tabs or []:
                tab_url = str(tab.get("url") or "")
                if "target.com" in tab_url and tab.get("webSocketDebuggerUrl"):
                    ws_url = tab["webSocketDebuggerUrl"]
                    url = tab_url
                    break

        async with session.ws_connect(ws_url, heartbeat=20) as ws:
            await ws.send_json({"id": 1, "method": "Network.getAllCookies"})
            while True:
                msg = await asyncio.wait_for(ws.receive_json(), timeout=10.0)
                if msg.get("id") == 1:
                    cookies = (msg.get("result") or {}).get("cookies") or []
                    break

    by_name: dict[str, str] = {}
    for c in cookies:
        domain = str(c.get("domain") or "")
        name = c.get("name")
        value = c.get("value")
        if name and value and "target.com" in domain:
            by_name[str(name)] = str(value)
    return by_name, url


async def inspect_target_cookies_via_cdp(
    debug_port: int,
) -> tuple[bool, str, dict[str, str]]:
    try:
        by_name, url = await _cdp_get_all_cookies(debug_port)
    except Exception as exc:
        return False, f"could not attach to login browser ({exc})", {}

    print("Reading cookies from the open Chrome via CDP (no Playwright)...")
    missing = missing_target_auth_cookies(by_name)
    access = by_name.get("accessToken")
    sut = decode_jwt_claims(access or "").get("sut")
    px_missing = [k for k in ("_px3",) if k not in by_name]
    watch = (
        "accessToken",
        "idToken",
        "refreshToken",
        "login-session",
        "_tgt_session",
        "_px3",
    )
    print(
        f"  url={url or '?'} sut={sut or '?'} "
        f"have={[k for k in watch if k in by_name]} "
        f"missing_auth={missing or []} missing_px={px_missing or []}"
    )

    soft_hint = (
        "Sign OUT on target.com in THIS window, then sign in with password/email "
        "code (hard session — not soft/'Keep me signed in'). Confirm /account stays "
        "logged in, then press Enter again."
    )
    if missing:
        return (
            False,
            f"missing {missing} at {url or '?'} — finish sign-in in THIS window",
            by_name,
        )
    if target_access_token_is_guest(access):
        return (
            False,
            f"still a GUEST session (sut=G) at {url}. Complete password/code sign-in.",
            by_name,
        )
    if target_access_token_is_soft_remembered(access):
        claims = decode_jwt_claims(access or "")
        return (
            False,
            (
                f"soft/REMEMBERED JWT (asl={claims.get('asl')!r}, "
                f"sco={claims.get('sco')!r}) at {url}. {soft_hint}"
            ),
            by_name,
        )
    if "_px3" not in by_name:
        return (
            False,
            (
                f"registered auth OK (sut={sut}) but no _px3 yet at {url}. "
                "Browse target.com /account a few seconds, then Enter again."
            ),
            by_name,
        )

    print("  Probing carts.target.com guest_type (need REGISTERED)...")
    guest_type = await asyncio.to_thread(probe_target_cart_guest_type, by_name)
    if guest_type is not None and guest_type.upper() != "REGISTERED":
        return (
            False,
            (
                f"cart guest_type={guest_type} (need REGISTERED) at {url}. {soft_hint}"
            ),
            by_name,
        )
    if guest_type:
        print(f"  cart guest_type={guest_type}")

    path = save_session_auth("target", by_name)
    if "login-session" not in by_name:
        # Target frequently no longer mints this cookie; cart APIs still return
        # guest_type=REGISTERED with sut=R accessToken + idToken (+ _px3).
        print(
            "  note: login-session cookie not present (common now). "
            "Exporting registered auth without it. In DevTools → Application → "
            "Cookies → .target.com you should still see accessToken / idToken / _px3."
        )
    return True, f"{url} (auth+PX snapshot → {path})", by_name


async def _close_chrome(proc: subprocess.Popen | None, profile: Path) -> bool:
    if proc is not None and proc.poll() is None:
        with contextlib.suppress(ProcessLookupError, OSError):
            proc.terminate()
        try:
            await asyncio.to_thread(proc.wait, 10)
        except Exception:
            with contextlib.suppress(ProcessLookupError, OSError):
                proc.kill()
    if profile_singleton_present(profile):
        await asyncio.to_thread(kill_browsers_using_profile, profile)
        await asyncio.to_thread(clear_profile_singleton, profile)
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        if not profile_singleton_present(profile):
            await asyncio.sleep(0.5)
            return True
        await asyncio.sleep(0.4)
        await asyncio.to_thread(kill_browsers_using_profile, profile)
        await asyncio.to_thread(clear_profile_singleton, profile)
    return False


async def _wait_cdp_ready(
    debug_port: int,
    *,
    timeout_s: float = 25.0,
    proc: subprocess.Popen | None = None,
    profile: Path | None = None,
) -> None:
    """Block until ``GET /json/version`` succeeds, or Chrome exited early."""
    import aiohttp

    deadline = time.monotonic() + timeout_s
    last_exc: Exception | None = None
    while time.monotonic() < deadline:
        if proc is not None and proc.poll() is not None:
            hint = cdp_attach_failure_hint(
                debug_port=debug_port,
                proc=proc,
                profile=profile or Path("."),
                exc=last_exc,
            )
            raise RuntimeError(hint)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"http://127.0.0.1:{debug_port}/json/version",
                    timeout=aiohttp.ClientTimeout(total=2),
                ) as resp:
                    if resp.status == 200:
                        return
        except Exception as exc:
            last_exc = exc
        await asyncio.sleep(0.25)
    hint = cdp_attach_failure_hint(
        debug_port=debug_port,
        proc=proc,
        profile=profile or Path("."),
        exc=last_exc,
    )
    raise RuntimeError(f"Chrome CDP not ready on :{debug_port}\n{hint}")


async def _cdp_navigate_and_dwell(
    debug_port: int,
    url: str,
    *,
    dwell_seconds: float,
) -> dict[str, Any]:
    """Navigate a page target, wait for PX/JS, return url/title/login-wall signals."""
    import aiohttp

    base = f"http://127.0.0.1:{debug_port}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{base}/json/version", timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            resp.raise_for_status()
            version = await resp.json()
        ws_url = version.get("webSocketDebuggerUrl")
        async with session.get(
            f"{base}/json/list", timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            tabs = await resp.json()
        for tab in tabs or []:
            if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                ws_url = tab["webSocketDebuggerUrl"]
                break
        if not ws_url:
            raise RuntimeError("No CDP page WebSocket for navigation")

        async with session.ws_connect(ws_url, heartbeat=20) as ws:
            msg_id = 1

            async def call(method: str, params: dict | None = None) -> dict:
                nonlocal msg_id
                payload: dict = {"id": msg_id, "method": method}
                if params:
                    payload["params"] = params
                await ws.send_json(payload)
                want = msg_id
                msg_id += 1
                while True:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=45.0)
                    if msg.get("id") == want:
                        if "error" in msg:
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result") or {}

            await call("Page.enable")
            await call("Runtime.enable")
            await call("Page.navigate", {"url": url})
            deadline = time.monotonic() + 40
            while time.monotonic() < deadline:
                res = await call(
                    "Runtime.evaluate",
                    {"expression": "document.readyState", "returnByValue": True},
                )
                if (res.get("result") or {}).get("value") == "complete":
                    break
                await asyncio.sleep(0.3)
            await asyncio.sleep(max(0.5, dwell_seconds))
            return await _cdp_eval_page_state_ws(call)


async def _cdp_eval_page_state_ws(call) -> dict[str, Any]:
    state = await call(
        "Runtime.evaluate",
        {
            "expression": """
(() => {
  const href = location.href || '';
  const title = document.title || '';
  const text = (document.body && document.body.innerText || '').slice(0, 4000);
  const low = (href + ' ' + title + ' ' + text).toLowerCase();
  // Hard wall = navigated off target.com checkout onto auth hosts / login paths.
  // Do NOT use loose "sign in"+"password" text — logged-in checkout pages often
  // still contain those strings in header/footer and false-positive forever.
  const hardLoginWall = (
    href.includes('gsp.target.com')
    || href.includes('oauth')
    || /target\\.com\\/(login|signin|sign-in|account\\/signin)\\b/i.test(href)
    || /\\/gsp\\//i.test(href)
  );
  const onCheckout = /target\\.com\\/checkout/i.test(href);
  const onCart = /target\\.com\\/cart/i.test(href);
  const hasPasswordField = !!document.querySelector(
    'input[type="password"], input[name*="password" i], input[id*="password" i]'
  );
  return {
    href,
    title,
    hardLoginWall,
    onCheckout,
    onCart,
    hasPasswordField,
    textSnip: text.slice(0, 500),
  };
})()
""",
            "returnByValue": True,
        },
    )
    return (state.get("result") or {}).get("value") or {}


async def _cdp_current_page_state(debug_port: int) -> dict[str, Any]:
    """Evaluate login-wall signals on the current page (no navigation)."""
    import aiohttp

    base = f"http://127.0.0.1:{debug_port}"
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{base}/json/list", timeout=aiohttp.ClientTimeout(total=5)
        ) as resp:
            tabs = await resp.json()
        ws_url = None
        for tab in tabs or []:
            if tab.get("type") == "page" and tab.get("webSocketDebuggerUrl"):
                ws_url = tab["webSocketDebuggerUrl"]
                break
        if not ws_url:
            raise RuntimeError("No CDP page WebSocket")
        async with session.ws_connect(ws_url, heartbeat=20) as ws:
            msg_id = 1

            async def call(method: str, params: dict | None = None) -> dict:
                nonlocal msg_id
                payload: dict = {"id": msg_id, "method": method}
                if params:
                    payload["params"] = params
                await ws.send_json(payload)
                want = msg_id
                msg_id += 1
                while True:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=30.0)
                    if msg.get("id") == want:
                        if "error" in msg:
                            raise RuntimeError(f"{method}: {msg['error']}")
                        return msg.get("result") or {}

            await call("Runtime.enable")
            return await _cdp_eval_page_state_ws(call)


def _warm_session_ok(cookies: dict[str, str]) -> tuple[bool, str]:
    from pokebot.doctor import (
        decode_jwt_claims,
        target_access_token_is_guest,
        target_access_token_is_soft_remembered,
    )

    if not cookies.get("_px3"):
        return False, "missing _px3"
    if not cookies.get("accessToken") or not cookies.get("idToken"):
        return False, "missing accessToken/idToken"
    if target_access_token_is_guest(cookies.get("accessToken")):
        return False, "accessToken is GUEST (sut=G) — finish password sign-in"
    if target_access_token_is_soft_remembered(cookies.get("accessToken")):
        claims = decode_jwt_claims(cookies.get("accessToken") or "")
        return (
            False,
            (
                f"soft/REMEMBERED JWT (asl={claims.get('asl')!r}, "
                f"sco={claims.get('sco')!r}) — hard password sign-in required"
            ),
        )
    sut = decode_jwt_claims(cookies.get("accessToken") or "").get("sut")
    return True, f"sut={sut} _px3_len={len(cookies.get('_px3') or '')}"


async def warm_target_cart_checkout(
    *,
    channel: str = "chrome",
    dwell_seconds: float = 3.0,
) -> dict[str, str]:
    """Open real Chrome: /cart → /checkout, refresh PX sidecar, then quit.

    Call this *before* HTTP ATC. Opening Chrome after curl_cffi ATC often demotes
    the profile to guest (sut=G) / REMEMBERED and forces a second login.
    """
    from pokebot.session_auth import save_session_auth_warm

    profile = target_session_profile()
    print(
        f"Warming PX — real Chrome /cart → /checkout (dwell {dwell_seconds:.1f}s each)…\n"
        "  (This reuses the same profile as `login target`; sign in only if Target asks.)"
    )
    proc, cmd, debug_port = launch_chrome_with_debug(
        profile=profile,
        start_url="https://www.target.com/cart",
        channel=channel,
    )
    if proc is None or debug_port is None:
        raise RuntimeError(f"Could not launch Chrome for cart warm-up. Try: {cmd}")

    try:
        await _wait_cdp_ready(debug_port, proc=proc, profile=profile)
        cart_state = await _cdp_navigate_and_dwell(
            debug_port, "https://www.target.com/cart", dwell_seconds=dwell_seconds
        )
        print(f"  /cart → {cart_state.get('href') or '?'}")
        checkout_state = await _cdp_navigate_and_dwell(
            debug_port,
            "https://www.target.com/checkout",
            dwell_seconds=dwell_seconds,
        )
        print(f"  /checkout → {checkout_state.get('href') or '?'}")

        while True:
            page = await _cdp_current_page_state(debug_port)
            cookies, final_url = await _cdp_get_all_cookies(debug_port)
            ok, detail = _warm_session_ok(cookies)
            hard_wall = bool(page.get("hardLoginWall"))
            # Registered cookies on www.target.com/checkout are enough. Header
            # "Sign in" / footer text must not block warm-up (false positive loop).
            if ok and not hard_wall:
                # PX-only-safe merge: never overwrite a good accessToken with MI6.
                path = save_session_auth_warm("target", cookies)
                where = final_url or page.get("href") or "?"
                print(f"PX warm OK — url={where} {detail} → {path}")
                return cookies

            reason = []
            if hard_wall:
                reason.append(f"auth redirect ({page.get('href')})")
            if not ok:
                reason.append(detail)
            print(
                "Checkout needs a fresh login in the Chrome window that just opened.\n"
                f"  url={page.get('href') or final_url}\n"
                f"  why: {'; '.join(reason) or 'session not fully registered'}\n"
                "Sign in there (Keep me signed in), stay on /checkout until you see "
                "payment / place order, then press Enter here."
            )
            await asyncio.to_thread(
                input, "Press Enter after signing in on /checkout… "
            )
            await _cdp_navigate_and_dwell(
                debug_port,
                "https://www.target.com/checkout",
                dwell_seconds=max(2.0, dwell_seconds),
            )
    finally:
        await _close_chrome(proc, profile)


async def login_target_chrome(*, channel: str = "chrome") -> None:
    """Open real Chrome (no Playwright), export registered auth + PX to sidecar."""
    profile = target_session_profile()
    start_url = "https://www.target.com/account"
    print(
        "\nOpening REAL Chrome (no Playwright) with the Target session profile:\n"
        f"  {profile}\n"
        "Sign OUT first if already soft-signed-in, then sign in with password/email "
        "code (hard session — soft/'Keep me signed in' alone is NOT enough).\n"
        "Stay on /account until your name shows. Cart must report guest_type=REGISTERED.\n"
        "Press Enter here to export accessToken + idToken + _px3 "
        "(login-session is optional if Target never sets it).\n"
    )
    proc, cmd, debug_port = launch_chrome_with_debug(
        profile=profile, start_url=start_url, channel=channel
    )
    if proc is None or debug_port is None:
        print("Could not auto-launch Chrome. Run:\n")
        print(f"  {cmd}\n")
        return
    print(f"Launched:\n  {cmd}\n")
    try:
        await _wait_cdp_ready(debug_port, timeout_s=25.0)
    except RuntimeError as exc:
        print(
            f"Chrome did not open CDP on port {debug_port}: {exc}\n"
            "Another Chrome may still own this profile. Quit bot Chrome windows "
            f"and retry.\n{profile_still_locked_hint()}"
        )
        await _close_chrome(proc, profile)
        return
    print(f"CDP ready on 127.0.0.1:{debug_port}\n")

    auth_ok = False
    while True:
        await asyncio.to_thread(
            input, "Press Enter when signed in (bot will verify via CDP, then quit)... "
        )
        await asyncio.sleep(0.5)
        ok, detail, _ = await inspect_target_cookies_via_cdp(debug_port)
        if not ok:
            print(f"Not saved yet: {detail}\nFinish sign-in, then press Enter again.\n")
            continue
        print(f"Registered Target session confirmed — {detail}")
        auth_ok = True
        break

    print("Quitting Chrome...")
    unlocked = await _close_chrome(proc, profile)
    if not unlocked:
        print(f"Profile may still be locked.\n{profile_still_locked_hint()}")

    from pokebot.doctor import check_target_auth_sidecar

    side_ok, side_detail = check_target_auth_sidecar()
    if auth_ok and side_ok:
        print(f"Target session OK — {side_detail}")
    elif auth_ok:
        print(f"Live export looked OK, but sidecar check failed: {side_detail}")
    else:
        print("Target session NOT ready.\nRe-run: python -m pokebot login target")
