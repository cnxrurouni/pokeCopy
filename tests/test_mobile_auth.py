from __future__ import annotations

import base64
import json
from pathlib import Path

from pokebot.mobile_auth import extract_mobile_auth_from_har, import_mobile_auth_from_har
from pokebot.session_auth import (
    MOBILE_RETAILER,
    load_mobile_session_auth,
    load_mobile_session_headers,
    missing_mobile_sidecar_cookies,
    session_auth_path,
)


def _jwt(**claims) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def _har_with_login(*, sut: str = "R", cli: str = "ecom-ios-3.0.0") -> dict:
    access = _jwt(sut=sut, asl="M", sco="ecom.med,openid", cli=cli, iss="MI6", exp=9)
    id_token = _jwt(sut=sut, cli=cli, ass="M", iss="MI6")
    refresh = "TGT.refresh-test-m"
    return {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "POST",
                        "url": (
                            "https://gsp.target.com/gsp/oauth_tokens/v2/tokens"
                            "?iOSAppVersion=2026.30.0"
                        ),
                        "headers": [
                            {
                                "name": "cookie",
                                "value": "login-session=ls1; _pxhd=pxhd1",
                            },
                            {
                                "name": "x-visitor-id",
                                "value": "VISITOR123",
                            },
                            {
                                "name": "x-client-access-token",
                                "value": "cat-uuid",
                            },
                            {"name": "x-scr", "value": "scr1"},
                        ],
                        "postData": {
                            "text": json.dumps(
                                {
                                    "grant_type": "authorization_code",
                                    "code": "abc",
                                }
                            )
                        },
                    },
                    "response": {
                        "status": 201,
                        "headers": [{"name": "content-type", "value": "application/json"}],
                        "content": {
                            "text": json.dumps(
                                {
                                    "access_token": access,
                                    "id_token": id_token,
                                    "refresh_token": refresh,
                                    "expires_in": 28800,
                                    "token_type": "Bearer",
                                }
                            )
                        },
                    },
                },
                {
                    "request": {
                        "method": "GET",
                        "url": (
                            "https://carts.target.com/web_checkouts/v1/cart_views"
                            "?cart_type=REGULAR"
                        ),
                        "headers": [
                            {
                                "name": "cookie",
                                "value": "login-session=ls1; _pxhd=pxhd1; egsSessionId=egs1",
                            },
                            {"name": "x-visitor-id", "value": "VISITOR123"},
                            {"name": "x-scr", "value": "scr1"},
                            {
                                "name": "x-sapphire-context",
                                "value": "app_name=Target&member_id=1",
                            },
                            {
                                "name": "authorization",
                                "value": f"Bearer {access}",
                            },
                        ],
                    },
                    "response": {
                        "status": 200,
                        "headers": [],
                        "content": {"text": '{"guest_type":"REGISTERED"}'},
                    },
                },
            ]
        }
    }


def test_extract_mobile_auth_from_har(tmp_path: Path):
    har_path = tmp_path / "login.har"
    har_path.write_text(json.dumps(_har_with_login()))
    extracted = extract_mobile_auth_from_har(har_path)
    cookies = extracted["cookies"]
    headers = extracted["headers"]
    assert cookies["accessToken"].count(".") == 2
    assert cookies["idToken"]
    assert cookies["refreshToken"] == "TGT.refresh-test-m"
    assert cookies["visitorId"] == "VISITOR123"
    assert cookies["login-session"] == "ls1"
    assert cookies["_pxhd"] == "pxhd1"
    assert cookies["egsSessionId"] == "egs1"
    assert headers["x-visitor-id"] == "VISITOR123"
    assert headers["x-sapphire-context"].startswith("app_name=Target")
    assert headers["x-client-access-token"] == "cat-uuid"
    assert extracted["meta"]["cli"] == "ecom-ios-3.0.0"
    assert missing_mobile_sidecar_cookies(cookies) == []


def test_extract_rejects_guest_only_har(tmp_path: Path):
    har_path = tmp_path / "guest.har"
    har_path.write_text(json.dumps(_har_with_login(sut="G")))
    try:
        extract_mobile_auth_from_har(har_path)
        raise AssertionError("expected ValueError")
    except ValueError as exc:
        assert "registered" in str(exc).lower()


def test_import_writes_sidecar(tmp_path: Path, monkeypatch):
    from pokebot import session_auth

    monkeypatch.setattr(session_auth, "data_dir", lambda: tmp_path)
    har_path = tmp_path / "login.har"
    har_path.write_text(json.dumps(_har_with_login()))
    path = import_mobile_auth_from_har(har_path)
    assert path == session_auth_path(MOBILE_RETAILER)
    assert path.exists()
    cookies = load_mobile_session_auth()
    headers = load_mobile_session_headers()
    assert cookies["accessToken"]
    assert headers["x-scr"] == "scr1"
