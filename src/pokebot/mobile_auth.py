"""Import Target iOS app auth from a Proxyman/mitm HAR into the mobile sidecar.

Desktop Chrome auth stays in ``data/sessions/target-auth.json``. Mobile uses
``data/sessions/target-auth-mobile.json`` (``cli=ecom-ios-*`` Bearer + app
headers such as ``x-sapphire-context`` / ``x-scr``).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from pokebot.session_auth import (
    MOBILE_AUTH_EXPORT_NAMES,
    MOBILE_AUTH_HEADER_NAMES,
    MOBILE_RETAILER,
    save_mobile_session_auth,
)

_TOKEN_PATH = "/gsp/oauth_tokens/v2/tokens"
_CARTS_HOST = "carts.target.com"


def _hdrs(msg: dict[str, Any]) -> dict[str, str]:
    return {
        str(h.get("name", "")).lower(): str(h.get("value", ""))
        for h in msg.get("headers") or []
        if h.get("name")
    }


def _body_text(msg: dict[str, Any]) -> str:
    post = msg.get("postData") or {}
    if post.get("text"):
        return str(post["text"])
    content = msg.get("content") or {}
    return str(content.get("text") or "")


def _maybe_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    try:
        padded = raw + "=" * (-len(raw) % 4)
        return json.loads(base64.b64decode(padded))
    except Exception:
        return None


def _parse_cookie_header(cookie_header: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (cookie_header or "").split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, value = part.split("=", 1)
        name = name.strip()
        value = value.strip()
        if name and value:
            out[name] = value
    return out


def _jwt_claims(token: str) -> dict[str, Any]:
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        claims = json.loads(base64.urlsafe_b64decode(payload))
        return claims if isinstance(claims, dict) else {}
    except Exception:
        return {}


def extract_mobile_auth_from_har(har_path: str | Path) -> dict[str, Any]:
    """Pull the latest registered iOS OAuth tokens + app headers from a HAR.

    Prefers ``grant_type=authorization_code`` token responses with ``sut=R``.
    Raises ``ValueError`` when no usable registered token is found.
    """
    path = Path(har_path)
    har = json.loads(path.read_text())
    entries = (har.get("log") or {}).get("entries") or []
    if not entries:
        raise ValueError(f"HAR has no entries: {path}")

    token_hits: list[tuple[int, dict[str, Any], dict[str, str]]] = []
    cart_headers: dict[str, str] = {}
    cart_cookies: dict[str, str] = {}

    for idx, entry in enumerate(entries):
        req = entry.get("request") or {}
        resp = entry.get("response") or {}
        url = str(req.get("url") or "")
        parsed = urlparse(url)
        method = str(req.get("method") or "").upper()
        status = int(resp.get("status") or 0)
        rh = _hdrs(req)

        if parsed.netloc == _CARTS_HOST and status in (200, 201):
            # Prefer post-login cart traffic for sapphire / visitor / cookies.
            cart_headers = {
                k: rh[k]
                for k in MOBILE_AUTH_HEADER_NAMES
                if rh.get(k)
            }
            cart_cookies = _parse_cookie_header(rh.get("cookie", ""))

        if method != "POST" or not parsed.path.endswith(_TOKEN_PATH):
            continue
        if status not in (200, 201):
            continue
        req_body = _maybe_json(_body_text(req))
        resp_body = _maybe_json(_body_text(resp))
        if not isinstance(resp_body, dict):
            continue
        access = str(resp_body.get("access_token") or "").strip()
        if not access:
            continue
        claims = _jwt_claims(access)
        if str(claims.get("sut") or "").upper() != "R":
            continue
        grant = ""
        if isinstance(req_body, dict):
            grant = str(req_body.get("grant_type") or "")
        token_hits.append((idx, resp_body, rh))
        # Prefer authorization_code; keep scanning for a later one.
        if grant == "authorization_code":
            pass

    if not token_hits:
        raise ValueError(
            f"No registered (sut=R) oauth token response in HAR: {path}. "
            "Capture a Target app sign-in (gsp oauth_tokens/v2/tokens)."
        )

    # Prefer the last authorization_code hit; else last registered token.
    chosen_idx = token_hits[-1][0]
    chosen_body = token_hits[-1][1]
    chosen_req_headers = token_hits[-1][2]
    for idx, body, rh in reversed(token_hits):
        # Re-check grant from nearby request body via entry index.
        req_body = _maybe_json(_body_text(entries[idx].get("request") or {}))
        if isinstance(req_body, dict) and req_body.get("grant_type") == "authorization_code":
            chosen_idx = idx
            chosen_body = body
            chosen_req_headers = rh
            break

    access = str(chosen_body.get("access_token") or "").strip()
    id_token = str(chosen_body.get("id_token") or "").strip()
    refresh = str(chosen_body.get("refresh_token") or "").strip()
    claims = _jwt_claims(access)
    cli = str(claims.get("cli") or "")
    if "ios" not in cli.lower() and "ecom-ios" not in cli.lower():
        # Still allow if sut=R; warn via meta.
        pass

    cookies: dict[str, str] = {}
    # Cookies from token request, then overlay richer cart cookies.
    for jar in (
        _parse_cookie_header(chosen_req_headers.get("cookie", "")),
        cart_cookies,
    ):
        for name, value in jar.items():
            if name in MOBILE_AUTH_EXPORT_NAMES and value:
                cookies[name] = value

    cookies["accessToken"] = access
    if id_token:
        cookies["idToken"] = id_token
    if refresh:
        cookies["refreshToken"] = refresh

    visitor = (
        cart_headers.get("x-visitor-id")
        or chosen_req_headers.get("x-visitor-id")
        or ""
    )
    if visitor:
        cookies["visitorId"] = visitor

    headers: dict[str, str] = {}
    for name in MOBILE_AUTH_HEADER_NAMES:
        value = cart_headers.get(name) or chosen_req_headers.get(name) or ""
        if value:
            headers[name] = value
    # client-access-token is GSP-only; keep from token request when cart lacks it.
    cat = chosen_req_headers.get("x-client-access-token")
    if cat:
        headers["x-client-access-token"] = cat

    return {
        "cookies": {
            k: v for k, v in cookies.items() if k in MOBILE_AUTH_EXPORT_NAMES and v
        },
        "headers": headers,
        "meta": {
            "source_har": str(path),
            "har_entry_index": chosen_idx,
            "cli": cli,
            "sut": claims.get("sut"),
            "asl": claims.get("asl"),
            "sco": claims.get("sco"),
            "iss": claims.get("iss"),
            "exp": claims.get("exp"),
            "expires_in": chosen_body.get("expires_in"),
        },
    }


def import_mobile_auth_from_har(har_path: str | Path) -> Path:
    """Extract mobile auth from HAR and write ``target-auth-mobile.json``."""
    extracted = extract_mobile_auth_from_har(har_path)
    return save_mobile_session_auth(
        extracted["cookies"],
        headers=extracted.get("headers") or {},
        meta=extracted.get("meta") or {},
    )


def login_target_mobile_from_har(har_path: str | Path) -> Path:
    """CLI entry: import HAR → mobile sidecar. Returns path written."""
    path = import_mobile_auth_from_har(har_path)
    # Touch retailer constant so callers know which sidecar.
    assert MOBILE_RETAILER == "target-mobile"
    return path
