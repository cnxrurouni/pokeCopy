from __future__ import annotations

"""Pinned client identity for curl_cffi Target HTTP (and RestockR).

TLS/JA3 (``curl_impersonate``), User-Agent, and Client Hints (``sec-ch-ua*``) must
tell the same story. Login cookies come from real Chrome; HTTP replay must use a
matching Chrome-family identity for the resolved curl_cffi target.
"""

import re
from dataclasses import dataclass

from pokebot.platform_util import browser_ua_platform
from pokebot.reseller.impersonation import curl_impersonate_for_channel
from pokebot.reseller.models import FingerprintProfile

_FINGERPRINT_HEADER_KEYS = frozenset(
    {
        "user-agent",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
    }
)


def chromium_major_from_impersonate(impersonate: str) -> int:
    """Extract Chrome major from a curl_cffi target (e.g. chrome136 → 136)."""
    match = re.match(r"chrome(\d+)", (impersonate or "").lower())
    return int(match.group(1)) if match else 146


def _chrome_sec_ch_ua(major: int) -> str:
    return f'"Chromium";v="{major}", "Not=A?Brand";v="24", "Google Chrome";v="{major}"'


def _edge_sec_ch_ua(major: int) -> str:
    return (
        f'"Not=A?Brand";v="99", "Microsoft Edge";v="{major}", '
        f'"Chromium";v="{major}"'
    )


def _chrome_ua(os_token: str, major: int) -> str:
    return (
        f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
    )


def _edge_ua(os_token: str, major: int) -> str:
    return (
        f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 "
        f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36 Edg/{major}.0.0.0"
    )


@dataclass(frozen=True)
class ClientIdentity:
    """Pinned identity used by HTTP checkout (and optional harvest paths)."""

    channel: str
    user_agent: str
    sec_ch_ua: str
    sec_ch_ua_mobile: str
    sec_ch_ua_platform: str
    curl_impersonate: str
    locale: str = "en-US"
    timezone_id: str = "America/Los_Angeles"

    def browser_headers(self) -> dict[str, str]:
        headers = {
            "user-agent": self.user_agent,
            "accept-language": f"{self.locale},en;q=0.9",
        }
        # Native Target app identity has no Client Hints.
        if self.channel != "ios_app" and self.sec_ch_ua:
            headers["sec-ch-ua"] = self.sec_ch_ua
            headers["sec-ch-ua-mobile"] = self.sec_ch_ua_mobile
            headers["sec-ch-ua-platform"] = self.sec_ch_ua_platform
        return headers

    @property
    def fingerprint_header_keys(self) -> frozenset[str]:
        return _FINGERPRINT_HEADER_KEYS

    def summary(self) -> str:
        return (
            f"channel={self.channel} impersonate={self.curl_impersonate} "
            f"ua={self.user_agent[:56]}…"
        )


# Captured from Target iOS app (Proxyman HAR, 2026.30.0). Used only by the
# mobile checkout channel — desktop Chrome identity stays unchanged.
_IOS_APP_USER_AGENT_HAR = (
    "Target/2026.30.0 iPhone17,3 iOS/26.5.2 CFNetwork/3860.600.12 Darwin/25.5.0"
)


def resolve_client_identity(
    channel: str | None = None,
    *,
    curl_impersonate_override: str | None = None,
    fingerprint: FingerprintProfile | None = None,
) -> ClientIdentity:
    """Build the identity Target HTTP checkout must send.

    Default channel is **chrome** (matches ``login target``). UA / Client Hints
    major version follows the resolved ``curl_impersonate`` target so TLS and
    headers stay aligned when curl_cffi is older than the preferred pin.
    ``fingerprint.user_agent`` overrides the UA string when set; Client Hints
    still follow the channel family.

    Channel ``ios_app`` / ``mobile`` is a separate Target app identity (no
    Client Hints) used only by ``TargetMobileCheckout``.
    """
    ch = (channel or "chrome").lower()
    if ch in ("", "edge"):
        ch = "msedge"

    locale = fingerprint.locale if fingerprint is not None else "en-US"
    tz = fingerprint.timezone_id if fingerprint is not None else "America/Los_Angeles"

    if ch in ("ios_app", "ios-app", "mobile", "target_app"):
        impersonate = curl_impersonate_for_channel(
            "safari", override=curl_impersonate_override
        )
        ua = _IOS_APP_USER_AGENT_HAR
        if fingerprint is not None and fingerprint.user_agent:
            ua = fingerprint.user_agent
        return ClientIdentity(
            channel="ios_app",
            user_agent=ua,
            sec_ch_ua="",
            sec_ch_ua_mobile="?1",
            sec_ch_ua_platform='"iOS"',
            curl_impersonate=impersonate,
            locale=locale,
            timezone_id=tz,
        )

    impersonate = curl_impersonate_for_channel(
        ch, override=curl_impersonate_override
    )
    major = chromium_major_from_impersonate(impersonate)
    os_token, platform_hint = browser_ua_platform()

    if ch in ("chrome", "chrome-beta", "chromium"):
        ua = _chrome_ua(os_token, major)
        sec = _chrome_sec_ch_ua(major)
        ch = "chrome"
    else:
        # Edge still uses a Chrome TLS target; keep Edge branding in UA/hints.
        edge_major = max(major, 120)
        ua = _edge_ua(os_token, edge_major)
        sec = _edge_sec_ch_ua(edge_major)
        ch = "msedge"

    if fingerprint is not None and fingerprint.user_agent:
        ua = fingerprint.user_agent

    return ClientIdentity(
        channel=ch,
        user_agent=ua,
        sec_ch_ua=sec,
        sec_ch_ua_mobile="?0",
        sec_ch_ua_platform=platform_hint,
        curl_impersonate=impersonate,
        locale=locale,
        timezone_id=tz,
    )


def mobile_app_headers(*, visitor_id: str | None = None) -> dict[str, str]:
    """Static Target iOS app headers from the Proxyman capture (no secrets)."""
    headers = {
        "accept": "*/*",
        "accept-language": "en-US,en;q=0.9",
        "user-agent": _IOS_APP_USER_AGENT_HAR,
        "x-application-name": "Mobile App",
        "x-channel-id": "APPS",
    }
    if visitor_id:
        headers["x-visitor-id"] = visitor_id
    return headers
