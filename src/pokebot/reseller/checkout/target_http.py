from __future__ import annotations

import asyncio
import contextlib
import json
import os
import random
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

from rich.console import Console

from pokebot.enums import Retailer
from pokebot.reseller.capture import CaptureFile, CapturedRequest, dot_get, substitute
from pokebot.reseller.checkout.base import CheckoutClient, CheckoutContext, CheckoutOutcome
from pokebot.reseller.fingerprint_contract import ClientIdentity, resolve_client_identity
from pokebot.reseller.target_ids import (
    is_plausible_tcin,
    resolve_target_product_url,
    resolve_target_tcin,
)

console = Console()


def _elapsed_seconds(elapsed: Any) -> float | None:
    """curl_cffi returns ``datetime.timedelta`` for ``Response.elapsed``."""
    if elapsed is None:
        return None
    if isinstance(elapsed, timedelta):
        return elapsed.total_seconds()
    try:
        return float(elapsed)
    except (TypeError, ValueError):
        return None


def resolve_target_cvv() -> str | None:
    """CVV for place_order — never stored in git; set ``TARGET_CVV`` in the env."""
    for key in ("TARGET_CVV", "POKEBOT_TARGET_CVV"):
        value = (os.environ.get(key) or "").strip()
        if value:
            return value
    return None

# Baseline browser-like headers. Capture-specific headers override these.
_BASE_HEADERS = {
    "accept": "application/json",
    "accept-language": "en-US,en;q=0.9",
    "origin": "https://www.target.com",
    "sec-fetch-dest": "empty",
    "sec-fetch-mode": "cors",
    "sec-fetch-site": "same-site",
    # Required by carts.target.com registered-guest flows (pre_checkout 403
    # INVALID_GUEST_STATUS without it even when accessToken / login-session are set).
    "x-application-name": "web",
}

# Same public web key the site's JS / browser ATC path uses.
_CART_API_KEY = "9f36aeafbe60771e321a7cc95a78140772ab3e96"
_CHECKOUT_API_KEY = "e59ce3b531b2c39afb2e2b8a71ff10113aac2a14"
_CART_GET_URL = (
    "https://carts.target.com/web_checkouts/v1/cart"
    f"?cart_type=REGULAR&field_groups=CART%2CCART_ITEMS%2CSUMMARY&key={_CART_API_KEY}"
)
_CART_PAYMENT_URL = (
    "https://carts.target.com/web_checkouts/v1/cart"
    "?cart_type=REGULAR&field_groups=PAYMENT_INSTRUCTIONS%2CSUMMARY"
    f"&key={_CHECKOUT_API_KEY}"
)

# Cookies that matter for carts.target.com — avoid blowing the Cookie header with
# analytics noise. ``login-session`` is optional/legacy (include when present);
# registered ATC works with sut=R accessToken + idToken + _px3.
_ESSENTIAL_COOKIE_NAMES = frozenset(
    {
        "accessToken",
        "idToken",
        "refreshToken",
        "login-session",
        "_tgt_session",
        "_tgt_token",
        "loyaltyid",
        "ffsession",
        "ecoOrderId",
        "_px3",
        "_px2",
        "_pxvid",
        "_pxhd",
        "pxcts",
        "visitorId",
        "UserLocation",
        "GuestLocation",
        "sapphire",
    }
)


class TargetHttpCheckout(CheckoutClient):
    """Target checkout over raw HTTP using curl_cffi (browser TLS/JA3 impersonation).

    Replays a capture recorded from a successful browser buy. Session cookies must
    include both PerimeterX (``_px3``) and Target auth (``accessToken`` / ``idToken``).
    """

    retailer = Retailer.TARGET

    def __init__(
        self,
        *,
        impersonate: str = "chrome146",
        capture_path: str | Path | None = None,
        preflight: bool = False,
        atc_spam_timeout_seconds: float = 90.0,
        checkout_spam_timeout_seconds: float = 120.0,
        atc_retry_delay_ms_min: int = 1000,
        atc_retry_delay_ms_max: int = 2000,
        spam_delay_ms_min: int = 1000,
        spam_delay_ms_max: int = 2000,
        auth_denied_abort_after: int = 3,
        rate_limit_abort_after: int = 3,
        rate_limit_cooldown_seconds: float = 30.0,
        warm_cart_checkout: bool = False,
        warm_dwell_seconds: float = 3.0,
        identity: ClientIdentity | None = None,
    ) -> None:
        self.impersonate = impersonate
        self.capture_path = Path(capture_path) if capture_path else None
        self.preflight = preflight
        self.atc_spam_timeout_seconds = atc_spam_timeout_seconds
        self.checkout_spam_timeout_seconds = checkout_spam_timeout_seconds
        self.atc_retry_delay_ms_min = max(0, atc_retry_delay_ms_min)
        self.atc_retry_delay_ms_max = max(
            self.atc_retry_delay_ms_min, atc_retry_delay_ms_max
        )
        self.spam_delay_ms_min = max(0, spam_delay_ms_min)
        self.spam_delay_ms_max = max(self.spam_delay_ms_min, spam_delay_ms_max)
        self.auth_denied_abort_after = max(1, auth_denied_abort_after)
        self.rate_limit_abort_after = max(1, rate_limit_abort_after)
        self.rate_limit_cooldown_seconds = max(1.0, float(rate_limit_cooldown_seconds))
        self.warm_cart_checkout = warm_cart_checkout
        self.warm_dwell_seconds = max(0.5, float(warm_dwell_seconds))
        self._identity = identity or resolve_client_identity(
            "chrome", curl_impersonate_override=impersonate
        )
        if identity is not None:
            self.impersonate = identity.curl_impersonate
        self._telemetry = None

    def bind_identity(
        self,
        *,
        fingerprint=None,
        curl_impersonate: str | None = None,
    ) -> ClientIdentity:
        """Pin UA / Client Hints to match TLS impersonate (and optional account FP)."""
        self._identity = resolve_client_identity(
            "chrome",
            curl_impersonate_override=curl_impersonate or self.impersonate,
            fingerprint=fingerprint,
        )
        self.impersonate = self._identity.curl_impersonate
        return self._identity

    def _headers_with_identity(self, base: dict[str, str] | None = None) -> dict[str, str]:
        headers = dict(base or _BASE_HEADERS)
        # Identity wins for UA / sec-ch-ua* / accept-language so sparse captures
        # cannot strip Client Hints and leave TLS claiming chrome146 alone.
        headers.update(self._identity.browser_headers())
        return headers

    async def place_order(self, ctx: CheckoutContext) -> CheckoutOutcome:
        self.bind_identity(
            fingerprint=getattr(ctx.account, "fingerprint", None),
            curl_impersonate=self.impersonate,
        )
        return await self._place_order_live(ctx)

    def _merged_cookies(self, ctx: CheckoutContext) -> dict[str, str]:
        """Auth + PX jar. Chrome login sidecar is the source of truth."""
        from pokebot.session_auth import load_session_auth

        merged: dict[str, str] = {}
        merged.update(load_session_auth("target"))
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
                    "_tgt_session",
                ):
                    token_cookies.pop(key, None)
            merged.update(token_cookies)
        return merged

    def _cookies_for_request(self, cookies: dict[str, str]) -> dict[str, str]:
        essential = {k: v for k, v in cookies.items() if k in _ESSENTIAL_COOKIE_NAMES and v}
        # Always keep anything that looks like a cart/session/login id.
        for k, v in cookies.items():
            if not v:
                continue
            low = k.lower()
            if (
                "cart" in low
                or "login" in low
                or "session" in low
                or low.startswith("_tgt")
                or low.startswith("tealeaf")
            ):
                essential[k] = v
        return essential or dict(cookies)

    def _fallback_capture_cookies(self, ctx: CheckoutContext) -> dict[str, str]:
        token_cookies = (ctx.token.cookies if ctx.token else {}) or {}
        if token_cookies.get("accessToken"):
            return {}
        try:
            from pokebot.config import data_dir

            captures = sorted(
                (data_dir() / "captures").glob("target-browser-*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )
        except Exception:
            return {}
        for path in captures[:5]:
            try:
                cap = CaptureFile.load(path)
            except Exception:
                continue
            if cap.cookies.get("accessToken"):
                return dict(cap.cookies)
        return {}

    def _build_session(self, ctx: CheckoutContext):
        try:
            from curl_cffi import CurlInfo
            from curl_cffi import requests as curl_requests
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError(
                "curl_cffi is not installed. Run: pip install -e ."
            ) from exc

        curl_infos = None
        with contextlib.suppress(Exception):
            curl_infos = [
                CurlInfo.NAMELOOKUP_TIME,
                CurlInfo.CONNECT_TIME,
                CurlInfo.APPCONNECT_TIME,
                CurlInfo.STARTTRANSFER_TIME,
                CurlInfo.TOTAL_TIME,
            ]
        session = curl_requests.Session(
            impersonate=self.impersonate,
            **({"curl_infos": curl_infos} if curl_infos else {}),
        )
        if ctx.proxy is not None:
            proxy_url = ctx.proxy.as_curl_proxy()
            if proxy_url:
                session.proxies = {"http": proxy_url, "https": proxy_url}

        for name, value in self._cookies_for_request(self._merged_cookies(ctx)).items():
            try:
                session.cookies.set(name, value, domain=".target.com")
            except Exception:
                session.cookies.set(name, value)
        return session

    def _apply_cookies_to_session(self, session, cookies: dict[str, str]) -> None:
        for name, value in self._cookies_for_request(cookies).items():
            try:
                session.cookies.set(name, value, domain=".target.com")
            except Exception:
                session.cookies.set(name, value)

    def _cookie_header(self, cookies: dict[str, str]) -> str:
        return "; ".join(f"{k}={v}" for k, v in cookies.items() if v)

    def _initial_variables(self, ctx: CheckoutContext, capture: CaptureFile) -> dict[str, Any]:
        tcin = resolve_target_tcin(url=ctx.task.product_url, sku=ctx.task.sku)
        product_url = resolve_target_product_url(
            ctx.task.product_url, tcin=tcin or ctx.task.sku
        )
        quantity = ctx.task.max_quantity if ctx.task.max_quantity is not None else 1
        variables: dict[str, Any] = dict(capture.variables)
        variables.update(
            {
                "tcin": tcin or "",
                "sku": tcin or ctx.task.sku,
                "quantity": max(1, int(quantity)),
                "product_url": product_url,
            }
        )
        return variables

    def _validate_ready(
        self, ctx: CheckoutContext, capture: CaptureFile, variables: dict[str, Any]
    ) -> str | None:
        """Return an error message if we should not hit the network yet."""
        tcin = variables.get("tcin")
        if not is_plausible_tcin(str(tcin) if tcin is not None else None):
            return (
                f"Refusing to call Target cart API: invalid TCIN {tcin!r}. "
                "Pass a real product URL (…/A-<tcin>) — not a label like TEST-SKU."
            )
        if not capture.ordered():
            return "Capture has no ordered requests to replay."
        cookies = self._merged_cookies(ctx)
        missing_auth = [k for k in ("accessToken", "idToken") if k not in cookies]
        if missing_auth:
            return (
                "Refusing live checkout: missing Target auth cookies "
                f"{missing_auth}. Run: python -m pokebot login target "
                "(pre_checkout 403 INVALID_GUEST_STATUS = not seen as registered)."
            )
        from pokebot.doctor import (
            probe_target_cart_guest_type,
            target_access_token_is_guest,
            target_access_token_is_soft_remembered,
            decode_jwt_claims,
        )

        if target_access_token_is_guest(cookies.get("accessToken")):
            return (
                "Refusing live checkout: Target accessToken is a GUEST token (sut=G). "
                "Sign in fully with: python -m pokebot login target"
            )
        if target_access_token_is_soft_remembered(cookies.get("accessToken")):
            claims = decode_jwt_claims(cookies.get("accessToken") or "")
            return (
                "Refusing live/preflight: soft/REMEMBERED Target session "
                f"(asl={claims.get('asl')!r}, sco={claims.get('sco')!r}). "
                "Cart will be guest_type=REMEMBERED and pre_checkout returns 403. "
                "Re-run: python -m pokebot login target — hard password/email sign-in "
                "until cart guest_type is REGISTERED."
            )
        guest_type = probe_target_cart_guest_type(cookies)
        if guest_type is not None and guest_type.upper() != "REGISTERED":
            return (
                f"Refusing live/preflight: cart guest_type={guest_type} "
                "(need REGISTERED). Re-run: python -m pokebot login target — "
                "hard password/email sign-in (not soft remembered)."
            )
        if "_px3" not in cookies and (ctx.token is None or not ctx.token.value):
            return (
                "Refusing live checkout: no PerimeterX _px3 in sidecar. "
                "Re-run: python -m pokebot login target (browse until _px3 is set)."
            )
        for req in capture.ordered():
            if self.preflight and req.commits_order:
                break
            rendered = substitute(req.body or "", variables)
            if "{{tcin}}" in rendered or "{{quantity}}" in rendered:
                return (
                    f"Capture request '{req.name}' still has unsubstituted placeholders "
                    f"after variable bind (tcin={tcin!r})."
                )
        return None

    def _request_headers(
        self,
        req: CapturedRequest,
        variables: dict[str, Any],
        cookies: dict[str, str],
    ) -> dict[str, str]:
        headers = self._headers_with_identity()
        for k, v in req.headers.items():
            key = k.lower()
            if key in self._identity.fingerprint_header_keys:
                continue
            headers[key] = substitute(v, variables)
        # Re-apply so capture cannot blank Client Hints / UA.
        headers.update(self._identity.browser_headers())
        jar = self._cookies_for_request(cookies)
        headers["cookie"] = self._cookie_header(jar)
        # Target's web client also presents the access token as Bearer on checkout APIs.
        access = jar.get("accessToken") or cookies.get("accessToken")
        if access and "authorization" not in headers:
            headers["authorization"] = f"Bearer {access}"
        referer = headers.get("referer", "")
        product_url = variables.get("product_url")
        if not referer or "/p/" in referer or referer == "{{product_url}}":
            if product_url:
                headers["referer"] = str(product_url)
        return headers

    def _status_ok(self, req: CapturedRequest, status: int) -> bool:
        if req.name == "add_to_cart":
            # Browser path treats both as success (201 create / 200 update).
            return status in (200, 201)
        if req.name == "pre_checkout":
            return status in (200, 201)
        if req.expect_status is not None:
            return status == req.expect_status
        return status < 400

    @staticmethod
    def _parse_cart_tcins(payload: Any) -> list[str]:
        if not isinstance(payload, dict):
            return []
        items = payload.get("cart_items")
        if items is None and isinstance(payload.get("cart"), dict):
            items = payload["cart"].get("cart_items")
        tcins: list[str] = []
        for item in items or []:
            if not isinstance(item, dict):
                continue
            tcin = item.get("tcin") or (item.get("item") or {}).get("tcin")
            if tcin is not None:
                tcins.append(str(tcin))
        return tcins

    def _verify_cart_has_tcin(
        self,
        session,
        *,
        tcin: str,
        cookies: dict[str, str],
        variables: dict[str, Any],
        attempt: int = 1,
    ) -> tuple[bool, str]:
        jar = self._cookies_for_request(cookies)
        headers = self._headers_with_identity()
        headers["cookie"] = self._cookie_header(jar)
        headers["referer"] = "https://www.target.com/cart"
        access = jar.get("accessToken") or cookies.get("accessToken")
        if access:
            headers["authorization"] = f"Bearer {access}"
        try:
            resp = session.request("GET", _CART_GET_URL, headers=headers)
        except Exception as exc:
            if self._telemetry is not None:
                self._telemetry.request(
                    step="cart_verify",
                    attempt=attempt,
                    phase="cart_verify",
                    method="GET",
                    url=_CART_GET_URL,
                    request_headers=headers,
                    request_cookies=jar,
                    request_body_snip=None,
                    status=None,
                    response_headers=None,
                    response_body_snip=str(exc),
                    elapsed_s=None,
                    step_ok=False,
                    fatal=False,
                )
            return False, f"cart verify request failed: {exc}"
        text = resp.text or ""
        try:
            data = resp.json()
        except Exception:
            data = None
        tcins = self._parse_cart_tcins(data)
        ok = resp.status_code == 200 and tcin in tcins
        if self._telemetry is not None:
            elapsed = getattr(resp, "elapsed", None)
            infos = getattr(resp, "infos", None) or {}
            with contextlib.suppress(Exception):
                self._telemetry.request(
                    step="cart_verify",
                    attempt=attempt,
                    phase="cart_verify",
                    method="GET",
                    url=_CART_GET_URL,
                    request_headers=headers,
                    request_cookies=jar,
                    request_body_snip=None,
                    status=resp.status_code,
                    response_headers=dict(resp.headers or {}),
                    response_body_snip=text,
                    elapsed_s=_elapsed_seconds(elapsed),
                    http_version=getattr(resp, "http_version", None),
                    primary_ip=getattr(resp, "primary_ip", None),
                    primary_port=getattr(resp, "primary_port", None),
                    local_ip=getattr(resp, "local_ip", None),
                    local_port=getattr(resp, "local_port", None),
                    redirect_count=getattr(resp, "redirect_count", None),
                    redirect_url=getattr(resp, "redirect_url", None),
                    curl_infos={str(k): v for k, v in dict(infos).items()} if infos else {},
                    step_ok=ok,
                    fatal=False,
                )
        if resp.status_code != 200:
            return False, f"cart GET HTTP {resp.status_code}: {text[:180]!r}"
        if tcin in tcins:
            return True, f"cart contains tcin={tcin} (items={tcins})"
        return False, f"cart missing tcin={tcin} (items={tcins or '[]'})"

    def _auth_headers(self, cookies: dict[str, str], *, referer: str) -> dict[str, str]:
        jar = self._cookies_for_request(cookies)
        headers = self._headers_with_identity()
        headers["cookie"] = self._cookie_header(jar)
        headers["referer"] = referer
        headers["content-type"] = "application/json"
        access = jar.get("accessToken") or cookies.get("accessToken")
        if access:
            headers["authorization"] = f"Bearer {access}"
        return headers

    def _attach_cvv_if_required(
        self,
        session,
        *,
        cookies: dict[str, str],
        variables: dict[str, Any],
    ) -> CheckoutOutcome | None:
        """Target often requires CVV on saved cards (``is_cvv_required``).

        Browser checkout PUTs ``card_details.cvv`` to
        ``/checkout_payments/v1/payment_instructions/{id}`` before place_order.
        """
        headers = self._auth_headers(cookies, referer="https://www.target.com/checkout")
        try:
            resp = session.request("GET", _CART_PAYMENT_URL, headers=headers)
            data = resp.json() if resp.text else {}
        except Exception as exc:
            return CheckoutOutcome(
                False,
                message=f"payment_instructions lookup failed: {exc}",
                retryable=True,
            )
        if resp.status_code != 200:
            return CheckoutOutcome(
                False,
                message=(
                    f"payment_instructions lookup HTTP {resp.status_code}: "
                    f"{(resp.text or '')[:180]!r}"
                ),
                retryable=resp.status_code != 429,
            )

        instructions = data.get("payment_instructions") or []
        if not isinstance(instructions, list):
            instructions = []
        needed = [
            pi
            for pi in instructions
            if isinstance(pi, dict)
            and pi.get("is_cvv_required")
            and pi.get("payment_instruction_id")
            and pi.get("card_type") not in ("APPLEPAY", "PAYPAL", "EBTFOOD", "EBTCASH")
        ]
        if not needed:
            console.print("[dim]CVV not required on payment instructions[/dim]")
            return None

        cvv = resolve_target_cvv() or str(variables.get("cvv") or "").strip() or None
        if not cvv:
            pi_id = str(needed[0].get("payment_instruction_id"))
            return CheckoutOutcome(
                False,
                message=(
                    "Target requires CVV for saved card "
                    f"(payment_instruction_id={pi_id}; is_cvv_required=true). "
                    "Set env TARGET_CVV to your card security code (Amex=4 digits), "
                    "then retry. CVV is not stored in git/config."
                ),
                retryable=False,
            )

        cart_id = str(variables.get("cart_id") or "") or str(data.get("cart_id") or "")
        if not cart_id:
            return CheckoutOutcome(
                False,
                message="Cannot attach CVV: cart_id missing from cart response",
                retryable=False,
            )

        for pi in needed:
            pi_id = str(pi["payment_instruction_id"])
            url = (
                "https://carts.target.com/checkout_payments/v1/payment_instructions/"
                f"{pi_id}?key={_CHECKOUT_API_KEY}"
            )
            body = json.dumps(
                {
                    "card_details": {"cvv": cvv},
                    "cart_id": cart_id,
                    "payment_type": "CARD",
                    "wallet_mode": "NONE",
                }
            )
            try:
                put = session.request("PUT", url, headers=headers, data=body)
            except Exception as exc:
                return CheckoutOutcome(
                    False,
                    message=f"CVV attach failed for {pi_id}: {exc}",
                    retryable=True,
                )
            put_text = put.text or ""
            if self._telemetry is not None:
                with contextlib.suppress(Exception):
                    self._telemetry.request(
                        step="set_cvv",
                        attempt=1,
                        phase="set_cvv",
                        method="PUT",
                        url=url,
                        request_headers=headers,
                        request_cookies=self._cookies_for_request(cookies),
                        request_body_snip=body,
                        status=put.status_code,
                        response_headers=dict(put.headers or {}),
                        response_body_snip=put_text,
                        elapsed_s=_elapsed_seconds(getattr(put, "elapsed", None)),
                        step_ok=200 <= put.status_code < 300,
                        fatal=not (200 <= put.status_code < 300),
                    )
            if not (200 <= put.status_code < 300):
                return CheckoutOutcome(
                    False,
                    message=(
                        f"CVV attach HTTP {put.status_code} for {pi_id}: "
                        f"{put_text[:220]!r}"
                        + (
                            " — stopping to avoid a ban"
                            if put.status_code == 429
                            else ""
                        )
                    ),
                    retryable=put.status_code != 429,
                )
            console.print(
                f"[green]CVV attached[/green] to payment_instruction {pi_id} "
                f"(card {pi.get('card_number') or pi.get('card_type')})"
            )
        return None

    def _probe_guest_type(self, session, cookies: dict[str, str]) -> str | None:
        """Read cart guest_type (REGISTERED vs REMEMBERED) after Chrome warm-up."""
        headers = self._auth_headers(cookies, referer="https://www.target.com/cart")
        try:
            resp = session.request("GET", _CART_PAYMENT_URL, headers=headers)
            data = resp.json() if resp.text else {}
        except Exception:
            return None
        if not isinstance(data, dict):
            return None
        guest = data.get("guest_type")
        return str(guest) if guest else None

    def _spam_sleep(self, *, delay_ms_min: int | None = None, delay_ms_max: int | None = None) -> None:
        lo = self.spam_delay_ms_min if delay_ms_min is None else max(0, delay_ms_min)
        hi = self.spam_delay_ms_max if delay_ms_max is None else max(lo, delay_ms_max)
        delay_ms = random.randint(lo, hi)
        if delay_ms > 0:
            time.sleep(delay_ms / 1000.0)

    @staticmethod
    def _parse_retry_after_seconds(headers: dict[str, Any] | None) -> float | None:
        """Parse HTTP Retry-After (delta-seconds). Ignore HTTP-date forms."""
        if not headers:
            return None
        raw = None
        for key, value in headers.items():
            if str(key).lower() == "retry-after":
                raw = value
                break
        if raw is None:
            return None
        try:
            seconds = float(str(raw).strip())
        except (TypeError, ValueError):
            return None
        if seconds < 0:
            return None
        return min(seconds, 600.0)

    def _rate_limit_cooldown(
        self,
        *,
        response_headers: dict[str, Any] | None = None,
        label: str = "request",
    ) -> float:
        """Sleep on 429 using Retry-After or configured default. Returns seconds waited."""
        hinted = self._parse_retry_after_seconds(response_headers)
        wait_s = hinted if hinted is not None else self.rate_limit_cooldown_seconds
        wait_s = max(1.0, float(wait_s))
        source = "Retry-After" if hinted is not None else "default"
        console.print(
            f"[yellow]HTTP 429 on {label} — cooling down {wait_s:.0f}s ({source})…[/yellow]"
        )
        time.sleep(wait_s)
        return wait_s

    @staticmethod
    def _atc_stock_failure(
        req: CapturedRequest,
        status: int,
        text: str,
        parsed: Any,
    ) -> str | None:
        """If ATC response means the item cannot be added (OOS / purchase limit), return reason.

        Observed live: HTTP 400
        ``{"message":"Items cannot be added to cart as max purchase limit exceeded",
           "code":"MAX_PURCHASE_LIMIT_EXCEEDED"}``.
        """
        if req.name != "add_to_cart":
            return None
        if status not in (400, 404, 409, 422):
            return None

        code = ""
        message = ""
        if isinstance(parsed, dict):
            code = str(
                parsed.get("code")
                or parsed.get("errorCode")
                or parsed.get("error_code")
                or parsed.get("errorKey")
                or parsed.get("error_key")
                or ""
            )
            message = str(
                parsed.get("message")
                or parsed.get("errorMessage")
                or parsed.get("error_message")
                or ""
            )
        blob = f"{code} {message} {text or ''}".upper()
        known_codes = (
            "MAX_PURCHASE_LIMIT_EXCEEDED",
            "INVENTORY_UNAVAILABLE",
            "ITEM_UNAVAILABLE",
            "OUT_OF_STOCK",
            "PRODUCT_NOT_AVAILABLE",
            "NOT_AVAILABLE",
            "INSUFFICIENT_INVENTORY",
            "QUANTITY_UNAVAILABLE",
        )
        for known in known_codes:
            if known in blob.replace(" ", "_") or known in blob:
                return known
        # Message-only shapes (no stable code).
        low = (text or "").lower()
        needles = (
            "out of stock",
            "not available",
            "insufficient inventory",
            "inventory unavailable",
            "cannot be added to cart",
            "max purchase limit",
            "purchase limit exceeded",
            "sold out",
        )
        for needle in needles:
            if needle in low:
                return needle
        return None

    @staticmethod
    def _is_fatal_client_error(req: CapturedRequest, status: int, text: str) -> bool:
        """Errors that will not clear by mashing the same request."""
        if status == 404:
            return True
        low = (text or "").lower()
        if status == 403 and (
            "invalid_guest_status" in low or "guest is not registered" in low
        ):
            return True
        if "missing_credit_card_cvv" in low or "missing cvv" in low:
            return True
        if "invalid_guest_status" in low or "guest is not registered" in low:
            return True
        # Inventory / purchase-limit on ATC — never spam.
        if TargetHttpCheckout._atc_stock_failure(req, status, text, None):
            return True
        if status != 400:
            return False
        # Bad TCIN / malformed body — retrying forever just burns detection budget.
        return any(
            needle in low
            for needle in ("tcin", "invalid", "malformed", "deserialize", "parse", "cvv")
        )

    def _fire_request(
        self,
        session,
        req: CapturedRequest,
        variables: dict[str, Any],
        cookies: dict[str, str],
        *,
        attempt: int = 1,
        phase: str = "checkout",
    ) -> tuple[int, str, Any, dict[str, str]]:
        url = substitute(req.url, variables)
        headers = self._request_headers(req, variables, cookies)
        body = substitute(req.body, variables) if req.body else None
        jar = self._cookies_for_request(cookies)
        resp = session.request(req.method, url, headers=headers, data=body)
        text = resp.text or ""
        resp_headers = {str(k): str(v) for k, v in dict(resp.headers or {}).items()}
        try:
            parsed: Any = json.loads(text) if text else None
        except (ValueError, TypeError):
            parsed = None

        error_key = error_code = None
        if isinstance(parsed, dict):
            error_key = parsed.get("errorKey") or parsed.get("error_key")
            error_code = parsed.get("errorCode") or parsed.get("error_code")

        if self._telemetry is not None:
            elapsed = getattr(resp, "elapsed", None)
            infos = getattr(resp, "infos", None) or {}
            # Never let logging abort a successful HTTP response.
            with contextlib.suppress(Exception):
                self._telemetry.request(
                    step=req.name,
                    attempt=attempt,
                    phase=phase,
                    method=req.method,
                    url=url,
                    request_headers=headers,
                    request_cookies=jar,
                    request_body_snip=body,
                    status=resp.status_code,
                    response_headers=resp_headers,
                    response_body_snip=text,
                    elapsed_s=_elapsed_seconds(elapsed),
                    http_version=getattr(resp, "http_version", None),
                    primary_ip=getattr(resp, "primary_ip", None),
                    primary_port=getattr(resp, "primary_port", None),
                    local_ip=getattr(resp, "local_ip", None),
                    local_port=getattr(resp, "local_port", None),
                    redirect_count=getattr(resp, "redirect_count", None),
                    redirect_url=getattr(resp, "redirect_url", None),
                    curl_infos={str(k): v for k, v in dict(infos).items()} if infos else {},
                    step_ok=self._status_ok(req, resp.status_code),
                    fatal=self._is_fatal_client_error(req, resp.status_code, text),
                    error_key=str(error_key) if error_key else None,
                    error_code=str(error_code) if error_code else None,
                )
        return resp.status_code, text, parsed, resp_headers

    # Live Target place_order returns orders[0].reference_id (customer-facing)
    # and orders[0].order_id (often the cart UUID). Capture extract may be empty.
    _PLACE_ORDER_ID_PATHS: tuple[tuple[str, str], ...] = (
        ("reference_id", "orders.0.reference_id"),
        ("order_number", "orders.0.order_number"),
        ("order_id", "orders.0.order_id"),
    )

    def _apply_extract(self, req: CapturedRequest, parsed: Any, variables: dict[str, Any]) -> None:
        for var_name, path in req.extract.items():
            variables[var_name] = dot_get(parsed, path)
        if not req.commits_order or not isinstance(parsed, dict):
            return
        for var_name, path in self._PLACE_ORDER_ID_PATHS:
            if variables.get(var_name):
                continue
            value = dot_get(parsed, path)
            if value is not None:
                variables[var_name] = value

    @staticmethod
    def _resolve_placed_order_id(variables: dict[str, Any]) -> str | None:
        """Prefer customer-facing reference/order number over cart UUID order_id."""
        for key in ("order_number", "reference_id", "order_id"):
            value = variables.get(key)
            if value is not None and str(value).strip():
                return str(value)
        return None

    def _spam_until_ok(
        self,
        session,
        req: CapturedRequest,
        variables: dict[str, Any],
        cookies: dict[str, str],
        *,
        timeout_s: float,
        require_cart_tcin: str | None = None,
        label: str | None = None,
        delay_ms_min: int | None = None,
        delay_ms_max: int | None = None,
    ) -> CheckoutOutcome | None:
        """Retry one capture step until success. Returns None on success."""
        name = label or req.name
        deadline = time.monotonic() + timeout_s
        attempt = 0
        last_detail = "no attempts"
        tcin = str(variables.get("tcin") or "")
        auth_denied_streak = 0
        rate_limit_streak = 0

        while time.monotonic() < deadline:
            attempt += 1
            try:
                status, text, parsed, resp_headers = self._fire_request(
                    session,
                    req,
                    variables,
                    cookies,
                    attempt=attempt,
                    phase=name,
                )
            except Exception as exc:
                last_detail = f"{name} attempt {attempt}: request error {exc}"
                console.print(f"[dim]  {last_detail}[/dim]")
                self._spam_sleep(delay_ms_min=delay_ms_min, delay_ms_max=delay_ms_max)
                continue

            short = text[:120]
            if attempt == 1 or attempt % 10 == 0 or self._status_ok(req, status):
                console.print(f"[dim]HTTP {req.method} {name} #{attempt} → {status} {short!r}[/dim]")

            low = (text or "").lower()
            if status == 401 or "auth_denied" in low or "t83072242" in low:
                auth_denied_streak += 1
                rate_limit_streak = 0
            elif status == 429:
                rate_limit_streak += 1
                auth_denied_streak = 0
            else:
                auth_denied_streak = 0
                rate_limit_streak = 0

            # Go-Proxy rejects some Bearer JWTs right after Chrome/CDP warm.
            if status == 401 and "mi6" in low and "token issuer" in low:
                return CheckoutOutcome(
                    False,
                    message=(
                        f"{name}: Target Go-Proxy rejected accessToken issuer MI6 "
                        f"(tcin={tcin}). Session cookies are still saved — wait ~30s "
                        "and retry WITHOUT Chrome warm (warm_cart_checkout: false), "
                        "or re-run: python -m pokebot login target"
                    ),
                    retryable=True,
                )

            if auth_denied_streak >= self.auth_denied_abort_after:
                return CheckoutOutcome(
                    False,
                    message=(
                        f"{name}: aborting after {auth_denied_streak} consecutive "
                        f"AUTH_DENIED/401 (tcin={tcin}) — refresh sidecar: "
                        "python -m pokebot login target"
                    ),
                    retryable=False,
                )
            if status == 429:
                if rate_limit_streak >= self.rate_limit_abort_after:
                    return CheckoutOutcome(
                        False,
                        message=(
                            f"{name}: HTTP 429 rate-limited {rate_limit_streak} times "
                            f"(tcin={tcin}) — stopping. Cool down longer, then retry."
                        ),
                        retryable=False,
                    )
                waited = self._rate_limit_cooldown(
                    response_headers=resp_headers, label=name
                )
                deadline += waited
                continue

            stock_fail = self._atc_stock_failure(req, status, text, parsed)
            if stock_fail:
                return CheckoutOutcome(
                    False,
                    message=(
                        f"{name}: item not addable ({stock_fail}) HTTP {status} "
                        f"(tcin={tcin} qty={variables.get('quantity')}) — stopping ATC. "
                        f"body={text[:220]!r}"
                    ),
                    retryable=False,
                )

            if self._is_fatal_client_error(req, status, text):
                hint = ""
                low = (text or "").lower()
                if "invalid_guest_status" in low or "guest is not registered" in low:
                    hint = (
                        " Target sees a REMEMBERED/soft session, not fully REGISTERED — "
                        "in the warm Chrome window finish sign-in on /checkout "
                        "(or re-run: python -m pokebot login target), then retry."
                    )
                return CheckoutOutcome(
                    False,
                    message=(
                        f"{name}: fatal HTTP {status} after {attempt} tries "
                        f"(tcin={tcin} qty={variables.get('quantity')}) — "
                        f"body={text[:220]!r}.{hint}"
                    ),
                    retryable=False,
                )

            if self._status_ok(req, status):
                self._apply_extract(req, parsed, variables)
                if require_cart_tcin:
                    # ATC HTTP succeeded — do NOT re-POST ATC (would stack qty /
                    # hit purchase limits). Only poll cart GET until timeout.
                    while time.monotonic() < deadline:
                        ok, detail = self._verify_cart_has_tcin(
                            session,
                            tcin=require_cart_tcin,
                            cookies=cookies,
                            variables=variables,
                            attempt=attempt,
                        )
                        if ok:
                            console.print(
                                f"[green]ATC ok[/green] after {attempt} tries — {detail}"
                            )
                            return None
                        if "HTTP 429" in detail:
                            rate_limit_streak += 1
                            if rate_limit_streak >= self.rate_limit_abort_after:
                                return CheckoutOutcome(
                                    False,
                                    message=(
                                        f"{name}: cart verify HTTP 429 "
                                        f"{rate_limit_streak} times (tcin={tcin}) — stopping."
                                    ),
                                    retryable=False,
                                )
                            waited = self._rate_limit_cooldown(
                                label=f"{name}/cart_verify"
                            )
                            deadline += waited
                            last_detail = f"{name} #{attempt}: ATC ok but {detail}"
                            continue
                        last_detail = f"{name} #{attempt}: ATC ok but {detail}"
                        console.print(
                            f"[dim]  cart check: {detail} — polling cart (not re-ATC)[/dim]"
                        )
                        self._spam_sleep(
                            delay_ms_min=delay_ms_min, delay_ms_max=delay_ms_max
                        )
                    return CheckoutOutcome(
                        False,
                        message=(
                            f"{name}: ATC HTTP {status} but cart never showed "
                            f"tcin={tcin} within timeout — {last_detail}. "
                            "Not re-ATCing to avoid stacking quantity."
                        ),
                        retryable=False,
                    )
                if req.success_contains is not None and req.success_contains not in text:
                    # Non-commit steps may retry; commit steps use _commit_order_once.
                    last_detail = (
                        f"{name} #{attempt}: HTTP {status} but missing "
                        f"{req.success_contains!r}"
                    )
                    self._spam_sleep(delay_ms_min=delay_ms_min, delay_ms_max=delay_ms_max)
                    continue
                console.print(f"[green]{name} ok[/green] after {attempt} tries ({status})")
                return None

            last_detail = f"{name} #{attempt}: HTTP {status} — {short!r}"
            self._spam_sleep(delay_ms_min=delay_ms_min, delay_ms_max=delay_ms_max)

        return CheckoutOutcome(
            False,
            message=f"{name}: timed out after {attempt} tries ({timeout_s:.0f}s) — {last_detail}",
            retryable=True,
        )

    def _commit_order_once(
        self,
        session,
        req: CapturedRequest,
        variables: dict[str, Any],
        cookies: dict[str, str],
    ) -> CheckoutOutcome | None:
        """Fire place_order at most twice (second only after 429 cooldown).

        Never spam-retries a money commit — duplicate orders are worse than a miss.
        """
        name = req.name
        tcin = str(variables.get("tcin") or "")
        max_attempts = 2  # initial + one Retry-After retry
        last_detail = "no attempts"

        for attempt in range(1, max_attempts + 1):
            try:
                status, text, parsed, resp_headers = self._fire_request(
                    session,
                    req,
                    variables,
                    cookies,
                    attempt=attempt,
                    phase=name,
                )
            except Exception as exc:
                return CheckoutOutcome(
                    False,
                    message=f"{name}: request error {exc} (tcin={tcin}) — not retrying commit",
                    retryable=False,
                )

            short = (text or "")[:120]
            console.print(f"[dim]HTTP {req.method} {name} #{attempt} → {status} {short!r}[/dim]")
            last_detail = f"HTTP {status} body={short!r}"

            if status == 429:
                if attempt >= max_attempts:
                    return CheckoutOutcome(
                        False,
                        message=(
                            f"{name}: HTTP 429 after cooldown retry (tcin={tcin}) — "
                            "not placing again."
                        ),
                        retryable=False,
                    )
                self._rate_limit_cooldown(response_headers=resp_headers, label=name)
                continue

            low = (text or "").lower()
            if status == 401 and "mi6" in low and "token issuer" in low:
                return CheckoutOutcome(
                    False,
                    message=(
                        f"{name}: Go-Proxy rejected MI6 token issuer (tcin={tcin}). "
                        "Not retrying place_order."
                    ),
                    retryable=False,
                )
            if status == 401 or "auth_denied" in low or "t83072242" in low:
                return CheckoutOutcome(
                    False,
                    message=(
                        f"{name}: AUTH_DENIED/401 (tcin={tcin}) — "
                        "refresh sidecar: python -m pokebot login target"
                    ),
                    retryable=False,
                )
            if self._is_fatal_client_error(req, status, text):
                return CheckoutOutcome(
                    False,
                    message=(
                        f"{name}: fatal HTTP {status} (tcin={tcin}) — body={short!r}"
                    ),
                    retryable=False,
                )

            if self._status_ok(req, status):
                if req.success_contains is not None and req.success_contains not in text:
                    return CheckoutOutcome(
                        False,
                        message=(
                            f"{name}: HTTP {status} but missing "
                            f"{req.success_contains!r} (tcin={tcin}) — "
                            f"ambiguous commit response, not re-POSTing. body={short!r}"
                        ),
                        retryable=False,
                    )
                self._apply_extract(req, parsed, variables)
                console.print(f"[green]{name} ok[/green] ({status})")
                return None

            return CheckoutOutcome(
                False,
                message=(
                    f"{name}: failed {last_detail} (tcin={tcin}) — not re-POSTing place_order"
                ),
                retryable=False,
            )

        return CheckoutOutcome(
            False,
            message=f"{name}: exhausted commit attempts — {last_detail}",
            retryable=False,
        )

    async def _run_chain(
        self,
        session,
        capture: CaptureFile,
        variables: dict[str, Any],
        cookies: dict[str, str] | None = None,
    ) -> CheckoutOutcome:
        """HTTP ATC → checkout APIs (cookies already warmed before the session)."""
        cookies = cookies or {}
        completed: list[str] = []
        tcin = str(variables.get("tcin") or "")
        last_success = False
        last_message: str | None = None

        console.print(
            f"[cyan]ATC[/cyan] for tcin={tcin} qty={variables.get('quantity')} "
            f"(timeout {self.atc_spam_timeout_seconds:.0f}s, "
            f"retry {self.atc_retry_delay_ms_min}-{self.atc_retry_delay_ms_max}ms)…"
        )

        for req in capture.ordered():
            if self.preflight and req.commits_order:
                ok, detail = self._verify_cart_has_tcin(
                    session,
                    tcin=tcin,
                    cookies=cookies,
                    variables=variables,
                    attempt=1,
                )
                if not ok:
                    return CheckoutOutcome(
                        False,
                        message=(
                            "PREFLIGHT FAILED — "
                            f"({(' → '.join(completed) or 'none')}) but {detail}."
                        ),
                        retryable=False,
                    )
                return CheckoutOutcome(
                    success=True,
                    order_id=None,
                    message=(
                        "PREFLIGHT OK — "
                        + (" → ".join(completed) if completed else "no prior steps")
                        + f" succeeded; {detail}; stopped before '{req.name}' (no purchase)."
                    ),
                    retryable=False,
                )

            if req.commits_order:
                cvv_fail = self._attach_cvv_if_required(
                    session, cookies=cookies, variables=variables
                )
                if cvv_fail is not None:
                    return cvv_fail
                fail = await asyncio.to_thread(
                    self._commit_order_once,
                    session,
                    req,
                    variables,
                    cookies,
                )
                if fail is not None:
                    return fail
                completed.append(f"{req.name}:ok")
                if req.success_contains is not None:
                    last_success = True
                    last_message = f"{req.name}: matched success marker"
                else:
                    last_success = True
                    last_message = f"{req.name} completed"
                continue

            if req.name == "add_to_cart":
                fail = await asyncio.to_thread(
                    self._spam_until_ok,
                    session,
                    req,
                    variables,
                    cookies,
                    timeout_s=self.atc_spam_timeout_seconds,
                    require_cart_tcin=tcin,
                    delay_ms_min=self.atc_retry_delay_ms_min,
                    delay_ms_max=self.atc_retry_delay_ms_max,
                )
                if fail is not None:
                    return fail
                completed.append(f"{req.name}:ok")
                continue

            # Checkout / pre_checkout — paced retries (not money commit).
            fail = await asyncio.to_thread(
                self._spam_until_ok,
                session,
                req,
                variables,
                cookies,
                timeout_s=self.checkout_spam_timeout_seconds,
                delay_ms_min=self.spam_delay_ms_min,
                delay_ms_max=self.spam_delay_ms_max,
            )
            if fail is not None:
                return fail
            completed.append(f"{req.name}:ok")

            if req.success_contains is not None:
                last_success = True
                last_message = f"{req.name}: matched success marker"

        order_id = self._resolve_placed_order_id(variables)
        if not last_success and completed and any(n.startswith("place_order") for n in completed):
            last_success = True
            last_message = "place_order completed"
        return CheckoutOutcome(
            success=last_success,
            order_id=str(order_id) if order_id is not None else None,
            message=last_message
            or (
                "order placed"
                if last_success
                else "chain completed without success marker"
            ),
            retryable=not last_success,
        )

    async def _place_order_live(self, ctx: CheckoutContext) -> CheckoutOutcome:
        if ctx.token is None:
            return CheckoutOutcome(
                False, message="No PerimeterX token available", retryable=True
            )
        if self.capture_path is None or not self.capture_path.exists():
            return CheckoutOutcome(
                False,
                message=(
                    "No Target capture found at "
                    f"{self.capture_path or 'config/reseller.capture.target.json'}. "
                    "Keep/edit the capture JSON (see config/*.example)."
                ),
                retryable=False,
            )

        from pokebot.reseller.http_telemetry import HttpTelemetry

        proxy_host = None
        if ctx.proxy is not None:
            with contextlib.suppress(Exception):
                raw = ctx.proxy.server or ""
                # Drop credentials if embedded.
                if "@" in raw:
                    raw = raw.split("@", 1)[-1]
                proxy_host = raw[:120]

        self._telemetry = HttpTelemetry(
            account_id=ctx.account.id,
            tcin=ctx.task.sku,
            qty=ctx.task.max_quantity,
            impersonate=self.impersonate,
            proxy_host=proxy_host,
        )

        capture = CaptureFile.load(self.capture_path)
        variables = self._initial_variables(ctx, capture)
        if is_plausible_tcin(str(variables.get("tcin") or "")):
            ctx.task.sku = str(variables["tcin"])

        preflight_err = self._validate_ready(ctx, capture, variables)
        if preflight_err:
            return CheckoutOutcome(False, message=preflight_err, retryable=False)

        # Warm Chrome BEFORE any HTTP. Doing it after ATC burned the registered
        # browser session (sut→G / REMEMBERED) and forced a second login, defeating
        # `pokebot login target`. Order: login/warm once → export jar → HTTP only.
        if self.warm_cart_checkout:
            try:
                from pokebot.chrome_login import warm_target_cart_checkout

                await warm_target_cart_checkout(dwell_seconds=self.warm_dwell_seconds)
                # Give GSP a moment after CDP Chrome closes before HTTP reuse.
                await asyncio.sleep(3.0)
            except Exception as exc:
                return CheckoutOutcome(
                    False,
                    message=(
                        "Chrome /cart→/checkout warm-up failed before ATC: "
                        f"{exc}. Run: python -m pokebot login target"
                    ),
                    retryable=True,
                )

        cookies = self._merged_cookies(ctx)
        sent = self._cookies_for_request(cookies)
        auth_keys = [
            k
            for k in (
                "accessToken",
                "idToken",
                "login-session",
                "refreshToken",
                "_tgt_session",
                "_px3",
            )
            if k in sent
        ]
        missing = [
            k for k in ("accessToken", "idToken", "_px3") if k not in sent
        ]
        self._telemetry.meta(
            capture=str(self.capture_path.name),
            preflight=self.preflight,
            cookie_names=sorted(cookies.keys()),
            essential_names=sorted(sent.keys()),
            missing=missing,
            log_path=str(self._telemetry.path),
        )
        console.print(
            f"[dim]HTTP session cookies ready: {auth_keys} "
            f"(jar size {len(cookies)}; sending {len(sent)} essential"
            + (f"; MISSING {missing}" if missing else "")
            + f"; telemetry → {self._telemetry.path})[/dim]"
        )
        session = await asyncio.to_thread(self._build_session, ctx)
        return await self._run_chain(session, capture, variables, cookies)
