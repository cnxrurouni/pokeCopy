from __future__ import annotations

import sys

from pokebot.reseller.fingerprint_contract import resolve_client_identity
from pokebot.reseller.models import FingerprintProfile


def test_chrome_identity_default_channel_and_platform():
    ident = resolve_client_identity(None)
    assert ident.channel == "chrome"
    assert "Edg/" not in ident.user_agent
    assert "Chrome/146" in ident.user_agent
    assert "Google Chrome" in ident.sec_ch_ua or "Chromium" in ident.sec_ch_ua
    assert ident.curl_impersonate.startswith("chrome")
    assert "user-agent" in ident.fingerprint_header_keys
    if sys.platform == "darwin":
        assert ident.sec_ch_ua_platform == '"macOS"'
        assert "Macintosh" in ident.user_agent
    elif sys.platform == "win32":
        assert ident.sec_ch_ua_platform == '"Windows"'


def test_msedge_identity_has_edge_ua_and_chrome_tls():
    ident = resolve_client_identity("msedge")
    assert "Edg/" in ident.user_agent
    assert "Microsoft Edge" in ident.sec_ch_ua
    assert ident.curl_impersonate.startswith("chrome")


def test_curl_override_and_fingerprint_ua():
    fp = FingerprintProfile(
        user_agent="Mozilla/5.0 custom-ua",
        locale="en-US",
        timezone_id="America/New_York",
    )
    ident = resolve_client_identity(
        "chrome", curl_impersonate_override="chrome120", fingerprint=fp
    )
    assert ident.curl_impersonate == "chrome120"
    assert ident.user_agent == "Mozilla/5.0 custom-ua"
    assert ident.timezone_id == "America/New_York"
    headers = ident.browser_headers()
    assert headers["user-agent"] == ident.user_agent
    assert headers["sec-ch-ua-mobile"] == "?0"


def test_pipeline_defaults_browser_atc_off():
    from pokebot.reseller.settings import ResellerSettings

    assert ResellerSettings().browser_atc_enabled is False
    assert ResellerSettings().auth_denied_abort_after == 3
    assert ResellerSettings().rate_limit_abort_after == 3
    assert ResellerSettings().rate_limit_cooldown_seconds == 30.0
