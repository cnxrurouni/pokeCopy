from __future__ import annotations

from pokebot.reseller.checkout.target_mobile import TargetMobileCheckout
from pokebot.reseller.fingerprint_contract import resolve_client_identity
from pokebot.reseller.pipeline import TargetPipeline
from pokebot.reseller.settings import ResellerSettings


def test_mobile_identity_has_no_client_hints():
    ident = resolve_client_identity("ios_app")
    assert ident.channel == "ios_app"
    assert "Target/" in ident.user_agent
    headers = ident.browser_headers()
    assert "sec-ch-ua" not in headers
    assert headers["user-agent"].startswith("Target/")


def test_settings_mobile_capture_path():
    s = ResellerSettings(checkout_channel="mobile")
    assert s.is_mobile_channel
    assert s.resolved_capture_path().name == "reseller.capture.target.mobile.json"
    web = ResellerSettings(checkout_channel="web")
    assert not web.is_mobile_channel
    assert web.resolved_capture_path().name == "reseller.capture.target.json"


def test_pipeline_builds_mobile_checkout():
    pipeline = TargetPipeline.build(ResellerSettings(checkout_channel="mobile"))
    assert isinstance(pipeline.checkout, TargetMobileCheckout)
    assert pipeline.checkout.browser_assist_atc is False


def test_pipeline_builds_web_checkout_by_default():
    from pokebot.reseller.checkout.target_http import TargetHttpCheckout

    pipeline = TargetPipeline.build(ResellerSettings())
    assert isinstance(pipeline.checkout, TargetHttpCheckout)
    assert not isinstance(pipeline.checkout, TargetMobileCheckout)


def test_mobile_request_headers_are_app_shaped():
    from pokebot.reseller.capture import CapturedRequest

    checkout = TargetMobileCheckout()
    checkout.bind_identity()
    checkout._mobile_headers = {
        "x-sapphire-context": "app_name=Target",
        "x-scr": "scr1",
    }
    headers = checkout._request_headers(
        CapturedRequest(name="add_to_cart", method="POST", url="https://example"),
        {"tcin": "1", "quantity": 2},
        {"accessToken": "tok", "visitorId": "vid123"},
    )
    assert headers["x-application-name"] == "Mobile App"
    assert headers["x-channel-id"] == "APPS"
    assert headers["authorization"] == "Bearer tok"
    assert headers["x-visitor-id"] == "vid123"
    assert headers["x-sapphire-context"] == "app_name=Target"
    assert headers["x-scr"] == "scr1"
    assert "sec-ch-ua" not in headers
    assert "origin" not in headers


def test_mobile_merged_cookies_use_mobile_sidecar(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "pokebot.session_auth.load_mobile_session_auth",
        lambda: {"accessToken": "ios-tok", "idToken": "ios-id", "visitorId": "v1"},
    )
    monkeypatch.setattr(
        "pokebot.session_auth.load_mobile_session_headers",
        lambda: {"x-scr": "scr9"},
    )
    monkeypatch.setattr(
        "pokebot.session_auth.load_session_auth",
        lambda retailer: {"accessToken": "web-tok", "_px3": "px"},
    )
    checkout = TargetMobileCheckout()
    ctx = SimpleNamespace(
        account=SimpleNamespace(session_cookies={}),
        token=None,
    )
    cookies = checkout._merged_cookies(ctx)
    assert cookies["accessToken"] == "ios-tok"
    assert "_px3" not in cookies
    assert checkout._mobile_headers["x-scr"] == "scr9"
