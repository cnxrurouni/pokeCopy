from __future__ import annotations

"""Persist Target commerce cookies harvested from a live (non-Playwright) Edge session.

``login-session`` is minted in real Edge during Cart→Checkout after a full sign-in,
but it is effectively session-scoped: closing Edge clears it from the profile, and
Playwright never receives a fresh one (automation). Export the jar while Edge is
still open so HTTP checkout / verify can use it.
"""

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pokebot.config import data_dir

_EXPORT_NAME = "target-commerce-cookies.json"
_REQUIRED = ("accessToken", "idToken", "login-session")


def commerce_cookies_path() -> Path:
    path = data_dir() / "sessions" / _EXPORT_NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class CommerceCookieExport:
    cookies: dict[str, str]
    exported_at: float
    source: str = ""
    path: Path | None = None

    @property
    def age_seconds(self) -> float:
        return max(0.0, time.time() - self.exported_at)

    def has_login_session(self) -> bool:
        return bool(self.cookies.get("login-session"))

    def missing_required(self) -> list[str]:
        return [k for k in _REQUIRED if not self.cookies.get(k)]


def save_commerce_cookies(
    cookies: list[dict[str, Any]] | dict[str, str],
    *,
    source: str = "edge-cdp",
) -> CommerceCookieExport:
    """Write first-party Target cookies to disk (values included; path is gitignored)."""
    jar: dict[str, str] = {}
    if isinstance(cookies, dict):
        jar = {k: v for k, v in cookies.items() if k and v}
    else:
        for c in cookies:
            name = c.get("name") or ""
            value = c.get("value") or ""
            domain = (c.get("domain") or "").lower()
            if not name or not value:
                continue
            if "target.com" not in domain and domain:
                continue
            jar[name] = value

    exported_at = time.time()
    path = commerce_cookies_path()
    payload = {
        "exported_at": exported_at,
        "exported_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(exported_at)),
        "source": source,
        "cookies": jar,
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return CommerceCookieExport(
        cookies=jar,
        exported_at=exported_at,
        source=source,
        path=path,
    )


def load_commerce_cookies(
    *,
    max_age_seconds: float | None = 6 * 3600,
) -> CommerceCookieExport | None:
    path = commerce_cookies_path()
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    cookies = data.get("cookies") or {}
    if not isinstance(cookies, dict):
        return None
    jar = {str(k): str(v) for k, v in cookies.items() if k and v}
    exported_at = float(data.get("exported_at") or 0.0)
    export = CommerceCookieExport(
        cookies=jar,
        exported_at=exported_at,
        source=str(data.get("source") or ""),
        path=path,
    )
    if max_age_seconds is not None and export.age_seconds > max_age_seconds:
        return None
    return export
