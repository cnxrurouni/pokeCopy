from __future__ import annotations

import platform
import sys
from dataclasses import dataclass


@dataclass(frozen=True)
class ArchReport:
    os_machine: str
    python_machine: str
    python_executable: str
    ok: bool
    messages: tuple[str, ...]


def check_architecture() -> ArchReport:
    """Detect Rosetta / wrong-arch Python on Apple Silicon."""
    python_machine = platform.machine()
    messages: list[str] = []
    ok = True

    if sys.platform == "darwin":
        host = python_machine
        try:
            import subprocess

            brand = subprocess.check_output(
                ["sysctl", "-n", "machdep.cpu.brand_string"], text=True
            ).strip()
            if "Apple" in brand and python_machine == "x86_64":
                ok = False
                messages.append(
                    "Apple Silicon Mac is running an Intel (x86_64) Python under Rosetta. "
                    "Recreate the venv with arm64 Python "
                    "(e.g. ~/miniforge3/bin/python -m venv .venv)."
                )
            host = brand
        except Exception:
            host = python_machine
        if not messages:
            messages.append(f"Arch OK — process={python_machine}, cpu={host}")
    else:
        messages.append(f"Arch — process={python_machine}")

    return ArchReport(
        os_machine=platform.machine(),
        python_machine=python_machine,
        python_executable=sys.executable,
        ok=ok,
        messages=tuple(messages),
    )


# Hard-required auth cookies for a registered Target session. ``login-session`` is
# optional/legacy — see session_auth.TARGET_OPTIONAL_AUTH.
TARGET_AUTH_COOKIES = ("accessToken", "idToken")


def missing_target_auth_cookies(cookie_names: set[str] | list[str]) -> list[str]:
    names = set(cookie_names)
    return [k for k in TARGET_AUTH_COOKIES if k not in names]


def decode_jwt_claims(token: str) -> dict:
    """Decode a JWT payload without verifying the signature (diagnostics only)."""
    import base64
    import json

    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        data = json.loads(base64.urlsafe_b64decode(payload))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def target_access_token_is_guest(access_token: str | None) -> bool:
    """Target guest tokens use sut=G; registered accounts use a non-G subject type."""
    if not access_token:
        return True
    sut = str(decode_jwt_claims(access_token).get("sut") or "").upper()
    return sut == "G" or sut == ""


def target_access_token_is_soft_remembered(access_token: str | None) -> bool:
    """Soft / "Keep me signed in" JWTs: asl=L or sco contains ecom.low.

    These still claim sut=R but cart APIs return guest_type=REMEMBERED and
    pre_checkout fails with 403 INVALID_GUEST_STATUS.
    """
    if not access_token:
        return False
    claims = decode_jwt_claims(access_token)
    asl = str(claims.get("asl") or "").strip().upper()
    if asl == "L":
        return True
    sco = str(claims.get("sco") or "").lower()
    return "ecom.low" in sco


_SOFT_REMEMBERED_HINT = (
    "re-run: python -m pokebot login target — sign OUT on target.com first, then "
    "sign in with password/email code (hard session, not soft/'Keep me signed in') "
    "until cart guest_type is REGISTERED"
)


def probe_target_cart_guest_type(cookies: dict[str, str]) -> str | None:
    """GET carts.target.com cart; return guest_type or None on failure."""
    access = (cookies.get("accessToken") or "").strip()
    if not access:
        return None
    # Same public web key / URL as TargetHttpCheckout cart verify.
    cart_url = (
        "https://carts.target.com/web_checkouts/v1/cart"
        "?cart_type=REGULAR&field_groups=CART%2CCART_ITEMS%2CSUMMARY"
        "&key=9f36aeafbe60771e321a7cc95a78140772ab3e96"
    )
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    headers = {
        "accept": "application/json",
        "origin": "https://www.target.com",
        "referer": "https://www.target.com/cart",
        "authorization": f"Bearer {access}",
        "x-application-name": "web",
        "cookie": cookie_header,
    }
    try:
        from pokebot.reseller.fingerprint_contract import resolve_client_identity

        ident = resolve_client_identity("chrome")
        headers.update(ident.browser_headers())
        impersonate = ident.curl_impersonate
    except Exception:
        headers["accept-language"] = "en-US,en;q=0.9"
        from pokebot.reseller.impersonation import curl_impersonate_for_channel

        impersonate = curl_impersonate_for_channel("chrome")
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.get(
            cart_url,
            headers=headers,
            impersonate=impersonate,
            timeout=20,
        )
        data = resp.json() if resp.text else {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    guest = data.get("guest_type")
    return str(guest) if guest else None


def probe_target_mobile_cart_guest_type(
    cookies: dict[str, str],
    *,
    headers: dict[str, str] | None = None,
) -> str | None:
    """GET mobile cart_views; return guest_type or None on failure."""
    access = (cookies.get("accessToken") or "").strip()
    if not access:
        return None
    cart_url = (
        "https://carts.target.com/web_checkouts/v1/cart_views"
        "?cart_type=REGULAR&field_groups=CART%2CCART_ITEMS%2CSUMMARY"
        "&iOSAppVersion=2026.30.0"
        "&key=3d4d4435710335df6435c68e19a7cf67c635a01d"
    )
    cookie_header = "; ".join(f"{k}={v}" for k, v in cookies.items() if v)
    req_headers = {
        "accept": "*/*",
        "authorization": f"Bearer {access}",
        "x-application-name": "Mobile App",
        "x-channel-id": "APPS",
        "cookie": cookie_header,
    }
    extra = headers or {}
    for key in (
        "x-visitor-id",
        "x-scr",
        "x-sapphire-context",
        "x-client-access-token",
    ):
        if extra.get(key):
            req_headers[key] = extra[key]
    visitor = cookies.get("visitorId") or extra.get("x-visitor-id")
    if visitor:
        req_headers["x-visitor-id"] = visitor
    try:
        from pokebot.reseller.fingerprint_contract import resolve_client_identity
        from pokebot.reseller.impersonation import curl_impersonate_for_channel

        ident = resolve_client_identity("ios_app")
        req_headers["user-agent"] = ident.user_agent
        req_headers["accept-language"] = f"{ident.locale},en;q=0.9"
        impersonate = curl_impersonate_for_channel(
            "safari", override=ident.curl_impersonate
        )
    except Exception:
        req_headers["user-agent"] = (
            "Target/2026.30.0 iPhone17,3 iOS/26.5.2 "
            "CFNetwork/3860.600.12 Darwin/25.5.0"
        )
        from pokebot.reseller.impersonation import curl_impersonate_for_channel

        impersonate = curl_impersonate_for_channel("safari")
    try:
        from curl_cffi import requests as curl_requests

        resp = curl_requests.get(
            cart_url,
            headers=req_headers,
            impersonate=impersonate,
            timeout=20,
        )
        data = resp.json() if resp.text else {}
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    guest = data.get("guest_type")
    return str(guest) if guest else None


def check_target_auth_sidecar() -> tuple[bool, str]:
    """Validate registered Target auth + PX from the login sidecar (no browser)."""
    from pokebot.session_auth import (
        load_session_auth,
        missing_sidecar_cookies,
        session_auth_path,
    )

    path = session_auth_path("target")
    cookies = load_session_auth("target")
    if not cookies:
        return False, f"no auth sidecar at {path} — run: python -m pokebot login target"
    missing = missing_sidecar_cookies(cookies)
    if missing:
        return False, f"sidecar missing {missing} ({path})"
    if target_access_token_is_guest(cookies.get("accessToken")):
        return False, f"sidecar accessToken is still GUEST (sut=G) at {path}"
    if target_access_token_is_soft_remembered(cookies.get("accessToken")):
        claims = decode_jwt_claims(cookies["accessToken"])
        return (
            False,
            (
                f"sidecar is a soft/REMEMBERED session "
                f"(asl={claims.get('asl')!r}, sco={claims.get('sco')!r}) at {path} — "
                f"{_SOFT_REMEMBERED_HINT}"
            ),
        )
    guest_type = probe_target_cart_guest_type(cookies)
    if guest_type is not None and guest_type.upper() != "REGISTERED":
        return (
            False,
            (
                f"sidecar cart guest_type={guest_type} (need REGISTERED) at {path} — "
                f"{_SOFT_REMEMBERED_HINT}"
            ),
        )
    sut = decode_jwt_claims(cookies["accessToken"]).get("sut")
    px_len = len(cookies.get("_px3") or "")
    optional_note = ""
    if "login-session" not in cookies:
        optional_note = ", login-session absent (ok if Target no longer sets it)"
    guest_note = f", guest_type={guest_type}" if guest_type else ""
    px2_note = ", _px2 present" if cookies.get("_px2") else ""
    return (
        True,
        (
            f"registered auth+PX sidecar OK "
            f"(sut={sut}, _px3_len={px_len}{guest_note}{px2_note}{optional_note}, {path})"
        ),
    )


def check_target_mobile_auth_sidecar() -> tuple[bool, str]:
    """Validate iOS app auth sidecar (no web _px3 requirement)."""
    from pokebot.session_auth import (
        MOBILE_RETAILER,
        load_mobile_session_auth,
        load_mobile_session_headers,
        missing_mobile_sidecar_cookies,
        session_auth_path,
    )

    path = session_auth_path(MOBILE_RETAILER)
    cookies = load_mobile_session_auth()
    headers = load_mobile_session_headers()
    if not cookies:
        return (
            False,
            (
                f"no mobile auth sidecar at {path} — run: "
                "python -m pokebot login target-mobile "
                "--from-har data/captures/target-mobile/full.har"
            ),
        )
    missing = missing_mobile_sidecar_cookies(cookies)
    if missing:
        return False, f"mobile sidecar missing {missing} ({path})"
    if target_access_token_is_guest(cookies.get("accessToken")):
        return False, f"mobile accessToken is GUEST (sut=G) at {path}"
    if target_access_token_is_soft_remembered(cookies.get("accessToken")):
        claims = decode_jwt_claims(cookies["accessToken"])
        return (
            False,
            (
                f"mobile sidecar is soft/REMEMBERED "
                f"(asl={claims.get('asl')!r}, sco={claims.get('sco')!r}) at {path}"
            ),
        )
    claims = decode_jwt_claims(cookies["accessToken"])
    cli = str(claims.get("cli") or "")
    if cli and "ios" not in cli.lower():
        return False, f"mobile accessToken cli={cli!r} (expected ecom-ios-*) at {path}"
    guest_type = probe_target_mobile_cart_guest_type(cookies, headers=headers)
    if guest_type is not None and guest_type.upper() != "REGISTERED":
        return (
            False,
            f"mobile cart guest_type={guest_type} (need REGISTERED) at {path}",
        )
    guest_note = f", guest_type={guest_type}" if guest_type else ""
    return (
        True,
        (
            f"mobile iOS auth sidecar OK "
            f"(sut={claims.get('sut')}, cli={cli or '?'}{guest_note}, {path})"
        ),
    )


def check_http_fingerprint_ready(
    *, curl_impersonate: str | None = None
) -> tuple[bool, str]:
    """Validate curl_cffi impersonate target + ClientIdentity header alignment."""
    from pokebot.reseller.fingerprint_contract import (
        chromium_major_from_impersonate,
        resolve_client_identity,
    )
    from pokebot.reseller.impersonation import (
        check_curl_impersonate_ready,
        curl_impersonate_for_channel,
    )

    resolved = curl_impersonate_for_channel("chrome", override=curl_impersonate)
    ok, detail = check_curl_impersonate_ready(resolved)
    if not ok:
        return False, detail
    ident = resolve_client_identity(
        "chrome", curl_impersonate_override=resolved
    )
    headers = ident.browser_headers()
    missing = [k for k in ("user-agent", "sec-ch-ua", "sec-ch-ua-platform") if not headers.get(k)]
    if missing:
        return False, f"ClientIdentity missing headers {missing}"
    major = chromium_major_from_impersonate(ident.curl_impersonate)
    if f"Chrome/{major}" not in headers["user-agent"]:
        return (
            False,
            f"UA {headers['user-agent']!r} does not look like chrome{major}",
        )
    return True, f"{detail}; {ident.summary()}"
