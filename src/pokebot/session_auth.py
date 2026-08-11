from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pokebot.config import data_dir

# Snapshot after real-Chrome login. Includes PerimeterX cookies so HTTP checkout
# does not need a separate (detectable) harvest browser.
TARGET_AUTH_EXPORT_NAMES = (
    "accessToken",
    "idToken",
    "refreshToken",
    # Optional legacy cookie — Target often no longer sets it; export when present.
    "login-session",
    "_tgt_session",
    "_tgt_token",
    "ffsession",
    "visitorId",
    "sapphire",
    "loyaltyid",
    "_px3",
    "_pxvid",
    "_pxhd",
    "_px2",
    "pxcts",
)

# Hard requirements for a usable registered sidecar. ``login-session`` used to be
# required for carts.target.com, but Target now commonly omits it entirely while
# still returning guest_type=REGISTERED for sut=R accessToken + idToken jars.
TARGET_REQUIRED_AUTH = ("accessToken", "idToken")
TARGET_REQUIRED_PX = ("_px3",)
TARGET_OPTIONAL_AUTH = ("login-session",)

# iOS app channel — separate sidecar (MI6 + ecom-ios JWT is expected here).
MOBILE_RETAILER = "target-mobile"
MOBILE_AUTH_EXPORT_NAMES = (
    "accessToken",
    "idToken",
    "refreshToken",
    "login-session",
    "egsSessionId",
    "auth-session",
    "TealeafAkaSid",
    "visitorId",
    "_pxhd",
    "_pxvid",
    "_px3",
    "_px2",
    "pxcts",
    "loyaltyid",
)
MOBILE_AUTH_HEADER_NAMES = (
    "x-visitor-id",
    "x-scr",
    "x-sapphire-context",
    "x-client-access-token",
)
MOBILE_REQUIRED_AUTH = ("accessToken", "idToken")

# Auth cookies that Go-Proxy must accept. CDP /cart|/checkout warm can mint an
# accessToken with iss=MI6 that carts.target.com rejects — keep prior auth then.
_AUTH_COOKIE_NAMES = (
    "accessToken",
    "idToken",
    "refreshToken",
    "login-session",
    "_tgt_session",
    "_tgt_token",
)
_PX_COOKIE_NAMES = ("_px3", "_px2", "_pxhd", "_pxvid", "pxcts")


def session_auth_path(retailer: str) -> Path:
    # Mobile uses target-auth-mobile.json (parallel to target-auth.json).
    if retailer in (MOBILE_RETAILER, "target_mobile", "target-auth-mobile"):
        return data_dir() / "sessions" / "target-auth-mobile.json"
    return data_dir() / "sessions" / f"{retailer}-auth.json"


def access_token_issuer(access_token: str | None) -> str | None:
    """Return JWT ``iss`` claim without logging the token."""
    if not access_token or access_token.count(".") < 2:
        return None
    try:
        import base64

        payload = access_token.split(".")[1]
        payload += "=" * ((4 - len(payload) % 4) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        iss = claims.get("iss")
        return str(iss) if iss is not None else None
    except Exception:
        return None


def is_mi6_access_token(access_token: str | None) -> bool:
    """True when Bearer JWT issuer is MI6 (rejected by carts Go-Proxy)."""
    return (access_token_issuer(access_token) or "").upper() == "MI6"


def merge_warm_session_cookies(
    existing: dict[str, str],
    fresh: dict[str, str],
) -> dict[str, str]:
    """Merge Chrome warm cookies into the sidecar without burning auth.

    Always take fresh PX / visitor / ffsession values. For auth cookies, reject a
    fresh ``accessToken`` with iss=MI6 when ``existing`` still has a non-MI6 token
    (CDP warm commonly rotates to MI6 and Go-Proxy then 401s every cart call).
    """
    merged = {
        name: value
        for name, value in existing.items()
        if name in TARGET_AUTH_EXPORT_NAMES and value
    }
    fresh_auth = (fresh.get("accessToken") or "").strip()
    existing_auth = (existing.get("accessToken") or "").strip()
    reject_fresh_auth = bool(
        fresh_auth
        and is_mi6_access_token(fresh_auth)
        and existing_auth
        and not is_mi6_access_token(existing_auth)
    )

    for name, value in fresh.items():
        if not value or name not in TARGET_AUTH_EXPORT_NAMES:
            continue
        if name in _AUTH_COOKIE_NAMES and reject_fresh_auth:
            continue
        if name == "accessToken" and is_mi6_access_token(value) and existing_auth:
            if not is_mi6_access_token(existing_auth):
                continue
        merged[name] = value

    # PX from warm always wins when present.
    for name in _PX_COOKIE_NAMES:
        if fresh.get(name):
            merged[name] = fresh[name]
    for name in ("ffsession", "visitorId", "sapphire", "loyaltyid"):
        if fresh.get(name):
            merged[name] = fresh[name]
    return merged


def save_session_auth(retailer: str, cookies: dict[str, str]) -> Path:
    """Persist auth + PX cookies exported from a verified manual Chrome login."""
    path = session_auth_path(retailer)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "retailer": retailer,
        "cookies": {
            name: value
            for name, value in cookies.items()
            if name in TARGET_AUTH_EXPORT_NAMES and value
        },
    }
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def save_session_auth_warm(retailer: str, fresh: dict[str, str]) -> Path:
    """Persist after Chrome warm: refresh PX, protect non-MI6 auth from overwrite."""
    existing = load_session_auth(retailer)
    merged = merge_warm_session_cookies(existing, fresh)
    return save_session_auth(retailer, merged)


def load_session_auth(retailer: str) -> dict[str, str]:
    path = session_auth_path(retailer)
    if not path.exists():
        return {}
    try:
        raw: Any = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    cookies = raw.get("cookies") if isinstance(raw, dict) else None
    if not isinstance(cookies, dict):
        return {}
    return {str(k): str(v) for k, v in cookies.items() if v}


def load_session_auth_headers(retailer: str) -> dict[str, str]:
    """Optional app/request headers stored beside cookies (mobile sidecar)."""
    path = session_auth_path(retailer)
    if not path.exists():
        return {}
    try:
        raw: Any = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    headers = raw.get("headers") if isinstance(raw, dict) else None
    if not isinstance(headers, dict):
        return {}
    return {str(k).lower(): str(v) for k, v in headers.items() if v}


def save_mobile_session_auth(
    cookies: dict[str, str],
    *,
    headers: dict[str, str] | None = None,
    meta: dict[str, Any] | None = None,
) -> Path:
    """Persist Target iOS app tokens/headers from a Proxyman login capture."""
    path = session_auth_path(MOBILE_RETAILER)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "retailer": MOBILE_RETAILER,
        "cookies": {
            name: value
            for name, value in cookies.items()
            if name in MOBILE_AUTH_EXPORT_NAMES and value
        },
        "headers": {
            str(name).lower(): value
            for name, value in (headers or {}).items()
            if str(name).lower() in MOBILE_AUTH_HEADER_NAMES and value
        },
    }
    if meta:
        payload["meta"] = dict(meta)
    path.write_text(json.dumps(payload, indent=2) + "\n")
    return path


def load_mobile_session_auth() -> dict[str, str]:
    return load_session_auth(MOBILE_RETAILER)


def load_mobile_session_headers() -> dict[str, str]:
    return load_session_auth_headers(MOBILE_RETAILER)


def missing_sidecar_cookies(cookies: dict[str, str]) -> list[str]:
    names = set(cookies)
    return [k for k in (*TARGET_REQUIRED_AUTH, *TARGET_REQUIRED_PX) if k not in names]


def missing_mobile_sidecar_cookies(cookies: dict[str, str]) -> list[str]:
    """Mobile app jar needs registered tokens; ``_px3`` is not required."""
    names = set(cookies)
    return [k for k in MOBILE_REQUIRED_AUTH if k not in names]
