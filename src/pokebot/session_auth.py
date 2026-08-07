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


def session_auth_path(retailer: str) -> Path:
    return data_dir() / "sessions" / f"{retailer}-auth.json"


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


def missing_sidecar_cookies(cookies: dict[str, str]) -> list[str]:
    names = set(cookies)
    return [k for k in (*TARGET_REQUIRED_AUTH, *TARGET_REQUIRED_PX) if k not in names]
