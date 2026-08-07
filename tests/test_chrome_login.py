from __future__ import annotations

import base64
import json

import pytest

from pokebot.chrome_login import inspect_target_cookies_via_cdp


def _jwt(**claims) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps(claims).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.x"


@pytest.mark.asyncio
async def test_inspect_exports_without_login_session(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pokebot.session_auth.data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "pokebot.chrome_login.probe_target_cart_guest_type",
        lambda _c: "REGISTERED",
    )

    async def fake_cookies(_port: int):
        return (
            {
                "accessToken": _jwt(sut="R", asl="H", sco="ecom,openid"),
                "idToken": "id",
                "refreshToken": "r",
                "_px3": "px" * 20,
            },
            "https://www.target.com/account",
        )

    monkeypatch.setattr(
        "pokebot.chrome_login._cdp_get_all_cookies", fake_cookies
    )
    ok, detail, by_name = await inspect_target_cookies_via_cdp(1)
    assert ok is True
    assert "login-session" not in by_name
    assert "auth+PX snapshot" in detail
    from pokebot.session_auth import load_session_auth

    loaded = load_session_auth("target")
    assert loaded["accessToken"].startswith("ey")
    assert "_px3" in loaded
    assert "login-session" not in loaded


@pytest.mark.asyncio
async def test_inspect_still_blocks_guest(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pokebot.session_auth.data_dir", lambda: tmp_path)

    async def fake_cookies(_port: int):
        return (
            {
                "accessToken": _jwt(sut="G"),
                "idToken": "id",
                "_px3": "px" * 20,
            },
            "https://www.target.com/account",
        )

    monkeypatch.setattr(
        "pokebot.chrome_login._cdp_get_all_cookies", fake_cookies
    )
    ok, detail, _ = await inspect_target_cookies_via_cdp(1)
    assert ok is False
    assert "GUEST" in detail


@pytest.mark.asyncio
async def test_inspect_blocks_soft_remembered_jwt(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pokebot.session_auth.data_dir", lambda: tmp_path)

    async def fake_cookies(_port: int):
        return (
            {
                "accessToken": _jwt(sut="R", asl="L", sco="ecom.low,openid"),
                "idToken": "id",
                "_px3": "px" * 20,
            },
            "https://www.target.com/account",
        )

    monkeypatch.setattr(
        "pokebot.chrome_login._cdp_get_all_cookies", fake_cookies
    )
    ok, detail, _ = await inspect_target_cookies_via_cdp(1)
    assert ok is False
    assert "soft" in detail.lower() or "REMEMBERED" in detail
    from pokebot.session_auth import load_session_auth

    assert load_session_auth("target") == {}


@pytest.mark.asyncio
async def test_inspect_blocks_remembered_guest_type(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("pokebot.session_auth.data_dir", lambda: tmp_path)
    monkeypatch.setattr(
        "pokebot.chrome_login.probe_target_cart_guest_type",
        lambda _c: "REMEMBERED",
    )

    async def fake_cookies(_port: int):
        return (
            {
                "accessToken": _jwt(sut="R", asl="H", sco="ecom,openid"),
                "idToken": "id",
                "_px3": "px" * 20,
            },
            "https://www.target.com/account",
        )

    monkeypatch.setattr(
        "pokebot.chrome_login._cdp_get_all_cookies", fake_cookies
    )
    ok, detail, _ = await inspect_target_cookies_via_cdp(1)
    assert ok is False
    assert "REMEMBERED" in detail
    from pokebot.session_auth import load_session_auth

    assert load_session_auth("target") == {}