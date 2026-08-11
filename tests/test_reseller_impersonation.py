from __future__ import annotations

from pokebot.reseller.impersonation import curl_impersonate_for_channel


def test_msedge_maps_to_latest_chromium():
    # Edge is Chromium — should impersonate a Chrome target, never edge99.
    target = curl_impersonate_for_channel("msedge")
    assert target.startswith("chrome")


def test_chrome_and_none_map_to_chromium():
    assert curl_impersonate_for_channel("chrome").startswith("chrome")
    assert curl_impersonate_for_channel(None).startswith("chrome")


def test_explicit_override_wins_when_available():
    assert curl_impersonate_for_channel("msedge", override="chrome120") == "chrome120"


def test_missing_override_falls_back(monkeypatch):
    from pokebot.reseller import impersonation

    monkeypatch.setattr(
        impersonation,
        "_available_targets",
        lambda: {"chrome136", "chrome131"},
    )
    assert curl_impersonate_for_channel("chrome", override="chrome146") == "chrome136"


def test_firefox_channel_maps_to_firefox():
    assert curl_impersonate_for_channel("firefox").startswith("firefox")
