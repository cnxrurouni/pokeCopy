"""Target iOS/Android *app* checkout channel (separate from desktop web).

Replays ``config/reseller.capture.target.mobile.json`` captured via Proxyman
from the official Target iOS app. Intentionally does **not** use browser-assist
ATC — this channel exists to A/B whether app HTTP ATC clears hot-SKU gates.

Desktop / Chrome path remains ``target_http.TargetHttpCheckout``.
"""

from __future__ import annotations

import base64
import contextlib
import json
from pathlib import Path
from typing import Any

from pokebot.config import config_dir
from pokebot.reseller.capture import CapturedRequest, substitute
from pokebot.reseller.checkout.target_http import TargetHttpCheckout
from pokebot.reseller.fingerprint_contract import (
    ClientIdentity,
    mobile_app_headers,
    resolve_client_identity,
)

# From Proxyman HAR (Target iOS 2026.30.0) — distinct from the web API key.
_MOBILE_API_KEY = "3d4d4435710335df6435c68e19a7cf67c635a01d"
_IOS_APP_VERSION = "2026.30.0"

_MOBILE_CART_VIEWS_URL = (
    "https://carts.target.com/web_checkouts/v1/cart_views"
    "?cart_type=REGULAR"
    "&field_groups=CART%2CCART_ITEMS%2CSUMMARY"
    f"&iOSAppVersion={_IOS_APP_VERSION}"
    f"&key={_MOBILE_API_KEY}"
)

_MOBILE_BASE_HEADERS = {
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "content-type": "application/json",
    "x-application-name": "Mobile App",
    "x-channel-id": "APPS",
}

# App jar cookies (no web _px3 requirement).
_MOBILE_ESSENTIAL_COOKIE_NAMES = frozenset(
    {
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
    }
)

_MOBILE_SIDECAR_HEADER_KEYS = (
    "x-visitor-id",
    "x-scr",
    "x-sapphire-context",
    "x-client-access-token",
)


class TargetMobileCheckout(TargetHttpCheckout):
    """App-API capture-replay. Isolated from desktop web + browser-assist."""

    def __init__(
        self,
        *,
        impersonate: str | None = None,
        capture_path: str | Path | None = None,
        preflight: bool = False,
        atc_spam_timeout_seconds: float = 300.0,
        checkout_spam_timeout_seconds: float = 1200.0,
        atc_retry_delay_ms_min: int = 1000,
        atc_retry_delay_ms_max: int = 2000,
        spam_delay_ms_min: int = 1000,
        spam_delay_ms_max: int = 2000,
        auth_denied_abort_after: int = 0,
        rate_limit_abort_after: int = 0,
        rate_limit_cooldown_seconds: float = 30.0,
        identity: ClientIdentity | None = None,
        default_quantity: int = 2,
    ) -> None:
        resolved_capture = (
            Path(capture_path)
            if capture_path
            else config_dir() / "reseller.capture.target.mobile.json"
        )
        mobile_identity = identity or resolve_client_identity(
            "ios_app", curl_impersonate_override=impersonate
        )
        super().__init__(
            impersonate=mobile_identity.curl_impersonate,
            capture_path=resolved_capture,
            preflight=preflight,
            atc_spam_timeout_seconds=atc_spam_timeout_seconds,
            checkout_spam_timeout_seconds=checkout_spam_timeout_seconds,
            atc_retry_delay_ms_min=atc_retry_delay_ms_min,
            atc_retry_delay_ms_max=atc_retry_delay_ms_max,
            spam_delay_ms_min=spam_delay_ms_min,
            spam_delay_ms_max=spam_delay_ms_max,
            auth_denied_abort_after=auth_denied_abort_after,
            rate_limit_abort_after=rate_limit_abort_after,
            rate_limit_cooldown_seconds=rate_limit_cooldown_seconds,
            # Never use everyday-Chrome ATC on this channel.
            warm_cart_checkout=False,
            browser_assist_atc=False,
            browser_assist_timeout_seconds=15.0,
            identity=mobile_identity,
        )
        self.default_quantity = max(1, int(default_quantity))
        self._mobile_headers: dict[str, str] = {}

    def bind_identity(
        self,
        *,
        fingerprint=None,
        curl_impersonate: str | None = None,
    ) -> ClientIdentity:
        self._identity = resolve_client_identity(
            "ios_app",
            curl_impersonate_override=curl_impersonate or self.impersonate,
            fingerprint=fingerprint,
        )
        self.impersonate = self._identity.curl_impersonate
        return self._identity

    def _headers_with_identity(self, base: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(base or _MOBILE_BASE_HEADERS)
        headers.update(mobile_app_headers())
        headers["user-agent"] = self._identity.user_agent
        headers["accept-language"] = f"{self._identity.locale},en;q=0.9"
        # Drop browser-only headers if a caller passed web defaults.
        for key in (
            "origin",
            "sec-fetch-dest",
            "sec-fetch-mode",
            "sec-fetch-site",
            "sec-ch-ua",
            "sec-ch-ua-mobile",
            "sec-ch-ua-platform",
            "referer",
        ):
            headers.pop(key, None)
        return headers

    def _merged_cookies(self, ctx) -> dict[str, str]:
        """iOS app sidecar is the source of truth (not Chrome target-auth.json)."""
        from pokebot.session_auth import (
            load_mobile_session_auth,
            load_mobile_session_headers,
        )

        self._mobile_headers = dict(load_mobile_session_headers())
        merged: dict[str, str] = {}
        merged.update(load_mobile_session_auth())
        merged.update(ctx.account.session_cookies or {})
        if ctx.token is not None:
            token_cookies = dict(ctx.token.cookies or {})
            from pokebot.doctor import target_access_token_is_guest

            if target_access_token_is_guest(token_cookies.get("accessToken")):
                for key in (
                    "accessToken",
                    "idToken",
                    "refreshToken",
                    "login-session",
                    "egsSessionId",
                ):
                    token_cookies.pop(key, None)
            merged.update(token_cookies)
        return merged

    def _cookies_for_request(self, cookies: dict[str, str]) -> dict[str, str]:
        essential = {
            k: v for k, v in cookies.items() if k in _MOBILE_ESSENTIAL_COOKIE_NAMES and v
        }
        for k, v in cookies.items():
            if not v:
                continue
            low = k.lower()
            if (
                "cart" in low
                or "login" in low
                or "session" in low
                or low.startswith("tealeaf")
                or low.startswith("egs")
            ):
                essential[k] = v
        return essential or dict(cookies)

    def _apply_mobile_sidecar_headers(
        self, headers: dict[str, str], cookies: dict[str, str]
    ) -> None:
        for key in _MOBILE_SIDECAR_HEADER_KEYS:
            value = (self._mobile_headers or {}).get(key)
            if value:
                headers[key] = value
        visitor = (
            headers.get("x-visitor-id")
            or cookies.get("visitorId")
            or (self._mobile_headers or {}).get("x-visitor-id")
        )
        if visitor:
            headers["x-visitor-id"] = visitor

    def _validate_ready(self, ctx, capture, variables) -> str | None:
        """Registered ecom-ios jar; no web _px3 / Chrome cart probe."""
        from pokebot.reseller.target_ids import is_plausible_tcin
        from pokebot.session_auth import (
            MOBILE_RETAILER,
            missing_mobile_sidecar_cookies,
            session_auth_path,
        )

        tcin = variables.get("tcin")
        if not self.skip_atc and not is_plausible_tcin(
            str(tcin) if tcin is not None else None
        ):
            return (
                f"Refusing to call Target cart API: invalid TCIN {tcin!r}. "
                "Pass a real product URL (…/A-<tcin>) — not a label like TEST-SKU."
            )
        if not capture.ordered():
            return "Capture has no ordered requests to replay."
        cookies = self._merged_cookies(ctx)
        missing = missing_mobile_sidecar_cookies(cookies)
        if missing:
            path = session_auth_path(MOBILE_RETAILER)
            return (
                "Refusing mobile checkout: missing iOS auth "
                f"{missing} at {path}. Run: python -m pokebot login target-mobile "
                "--from-har data/captures/target-mobile/full.har"
            )
        from pokebot.doctor import (
            decode_jwt_claims,
            probe_target_mobile_cart_guest_type,
            target_access_token_is_guest,
            target_access_token_is_soft_remembered,
        )

        access = cookies.get("accessToken")
        if target_access_token_is_guest(access):
            return (
                "Refusing mobile checkout: accessToken is GUEST (sut=G). "
                "Re-import a registered app login HAR."
            )
        if target_access_token_is_soft_remembered(access):
            claims = decode_jwt_claims(access or "")
            return (
                "Refusing mobile checkout: soft/REMEMBERED session "
                f"(asl={claims.get('asl')!r}, sco={claims.get('sco')!r}). "
                "Capture a hard password sign-in in the Target app."
            )
        claims = decode_jwt_claims(access or "")
        cli = str(claims.get("cli") or "")
        if cli and "ios" not in cli.lower():
            return (
                f"Refusing mobile checkout: accessToken cli={cli!r} "
                "(expected ecom-ios-*). Re-import from an iOS app HAR."
            )
        guest_type = probe_target_mobile_cart_guest_type(
            cookies, headers=self._mobile_headers
        )
        if guest_type is not None and guest_type.upper() != "REGISTERED":
            return (
                f"Refusing mobile checkout: cart guest_type={guest_type} "
                "(need REGISTERED). Re-import a fresh app login HAR."
            )
        for req in capture.ordered():
            if self.preflight and req.commits_order:
                break
            if self.skip_atc and req.name == "add_to_cart":
                continue
            rendered = substitute(req.body or "", variables)
            if "{{tcin}}" in rendered or "{{quantity}}" in rendered:
                return (
                    f"Capture request '{req.name}' still has unsubstituted placeholders "
                    f"after variable bind (tcin={tcin!r})."
                )
        return None

    def _fetch_cart_tcins(
        self,
        session,
        *,
        cookies: dict[str, str],
        variables: dict,
    ) -> tuple[list[str] | None, str]:
        jar = self._cookies_for_request(cookies)
        headers = self._headers_with_identity()
        headers["cookie"] = self._cookie_header(jar)
        access = jar.get("accessToken") or cookies.get("accessToken")
        if access:
            headers["authorization"] = f"Bearer {access}"
        self._apply_mobile_sidecar_headers(headers, jar)
        try:
            resp = session.request("GET", _MOBILE_CART_VIEWS_URL, headers=headers)
        except Exception as exc:
            return None, f"mobile cart list failed: {exc}"
        text = resp.text or ""
        try:
            data = resp.json()
        except Exception:
            data = None
            with contextlib.suppress(Exception):
                data = json.loads(base64.b64decode(text))
        if resp.status_code == 429 or resp.status_code >= 500:
            return None, f"mobile cart list HTTP {resp.status_code}: {text[:180]!r}"
        if resp.status_code != 200:
            return None, f"mobile cart list HTTP {resp.status_code}: {text[:180]!r}"
        tcins = self._parse_cart_tcins(data if isinstance(data, dict) else {})
        return tcins, f"mobile cart items={tcins or '[]'}"

    def _request_headers(
        self,
        req: CapturedRequest,
        variables: dict[str, Any],
        cookies: dict[str, str],
    ) -> dict[str, str]:
        headers = self._headers_with_identity()
        for k, v in req.headers.items():
            key = k.lower()
            # Never let a capture reintroduce desktop Client Hints.
            if key.startswith("sec-ch-ua") or key in (
                "origin",
                "sec-fetch-dest",
                "sec-fetch-mode",
                "sec-fetch-site",
                "referer",
            ):
                continue
            headers[key] = substitute(v, variables)
        headers.update(mobile_app_headers())
        headers["user-agent"] = self._identity.user_agent
        jar = self._cookies_for_request(cookies)
        headers["cookie"] = self._cookie_header(jar)
        access = jar.get("accessToken") or cookies.get("accessToken")
        if access:
            headers["authorization"] = f"Bearer {access}"
        self._apply_mobile_sidecar_headers(headers, jar)
        return headers

    def _initial_variables(self, ctx, capture) -> dict[str, Any]:
        variables = super()._initial_variables(ctx, capture)
        variables.setdefault("ios_app_version", _IOS_APP_VERSION)
        # Prefer qty=2 when the task does not constrain quantity (manual runs).
        if ctx.task.max_quantity is None:
            variables["quantity"] = self.default_quantity
        else:
            variables["quantity"] = max(1, int(ctx.task.max_quantity))
        return variables

    def _verify_cart_has_tcin(
        self,
        session,
        *,
        tcin: str,
        cookies: dict[str, str],
        variables: dict[str, Any],
        attempt: int = 1,
    ) -> tuple[bool, str]:
        """Use mobile cart_views + app headers (not the web cart GET)."""
        jar = self._cookies_for_request(cookies)
        headers = self._headers_with_identity()
        headers["cookie"] = self._cookie_header(jar)
        access = jar.get("accessToken") or cookies.get("accessToken")
        if access:
            headers["authorization"] = f"Bearer {access}"
        self._apply_mobile_sidecar_headers(headers, jar)
        try:
            resp = session.request("GET", _MOBILE_CART_VIEWS_URL, headers=headers)
        except Exception as exc:
            return False, f"mobile cart verify request failed: {exc}"
        text = resp.text or ""
        try:
            data = resp.json()
        except Exception:
            data = None
            with contextlib.suppress(Exception):
                data = json.loads(base64.b64decode(text))
        tcins = self._parse_cart_tcins(data)
        cart_qty = self._parse_cart_tcin_qty(data, tcin)
        if resp.status_code != 200:
            return False, f"mobile cart GET HTTP {resp.status_code}: {text[:180]!r}"
        if tcin in tcins:
            qty_bit = f" qty={cart_qty}" if cart_qty is not None else ""
            return True, f"mobile cart contains tcin={tcin}{qty_bit} (items={tcins})"
        return False, f"mobile cart missing tcin={tcin} (items={tcins or '[]'})"
