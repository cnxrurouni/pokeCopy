from __future__ import annotations

"""Pinned client identity for curl_cffi Target HTTP (and RestockR).

TLS/JA3 (``curl_impersonate``), User-Agent, and Client Hints (``sec-ch-ua*``) must
tell the same story. Login cookies come from real Chrome; HTTP replay must use a
matching Chrome-family identity (default: chrome146 + host OS platform).
"""

from dataclasses import dataclass

from pokebot.platform_util import browser_ua_platform
from pokebot.reseller.impersonation import curl_impersonate_for_channel
from pokebot.reseller.models import FingerprintProfile

_CHROME_SEC_CH_UA = (
    '"Chromium";v="146", "Not=A?Brand";v="24", "Google Chrome";v="146"'
)
_EDGE_SEC_CH_UA = (
    '"Not=A?Brand";v="99", "Microsoft Edge";v="151", "Chromium";v="151"'
)

_FINGERPRINT_HEADER_KEYS = frozenset(
    {
        "user-agent",
        "sec-ch-ua",
        "sec-ch-ua-mobile",
        "sec-ch-ua-platform",
    }
)


def _chrome_ua(os_token: str) -> str:
    return (
        f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
    )


def _edge_ua(os_token: str) -> str:
    return (
        f"Mozilla/5.0 ({os_token}) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36 Edg/151.0.0.0"
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
        return {
            "user-agent": self.user_agent,
            "sec-ch-ua": self.sec_ch_ua,
            "sec-ch-ua-mobile": self.sec_ch_ua_mobile,
            "sec-ch-ua-platform": self.sec_ch_ua_platform,
            "accept-language": f"{self.locale},en;q=0.9",
        }

    @property
    def fingerprint_header_keys(self) -> frozenset[str]:
        return _FINGERPRINT_HEADER_KEYS

    def summary(self) -> str:
        return (
            f"channel={self.channel} impersonate={self.curl_impersonate} "
            f"ua={self.user_agent[:56]}…"
        )


def resolve_client_identity(
    channel: str | None = None,
    *,
    curl_impersonate_override: str | None = None,
    fingerprint: FingerprintProfile | None = None,
) -> ClientIdentity:
    """Build the identity Target HTTP checkout must send.

    Default channel is **chrome** (matches ``login target`` + ``chrome146``).
    ``fingerprint.user_agent`` overrides the UA string when set; Client Hints
    still follow the channel family.
    """
    ch = (channel or "chrome").lower()
    if ch in ("", "edge"):
        ch = "msedge"
    impersonate = curl_impersonate_for_channel(
        ch, override=curl_impersonate_override
    )
    os_token, platform_hint = browser_ua_platform()

    if ch in ("chrome", "chrome-beta", "chromium"):
        ua = _chrome_ua(os_token)
        sec = _CHROME_SEC_CH_UA
        ch = "chrome"
    else:
        ua = _edge_ua(os_token)
        sec = _EDGE_SEC_CH_UA
        ch = "msedge"

    if fingerprint is not None and fingerprint.user_agent:
        ua = fingerprint.user_agent

    locale = fingerprint.locale if fingerprint is not None else "en-US"
    tz = fingerprint.timezone_id if fingerprint is not None else "America/Los_Angeles"

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
