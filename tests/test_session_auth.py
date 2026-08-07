from __future__ import annotations

from pokebot.session_auth import (
    TARGET_AUTH_EXPORT_NAMES,
    load_session_auth,
    missing_sidecar_cookies,
    save_session_auth,
    session_auth_path,
)


def test_session_auth_roundtrip(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(
        "pokebot.session_auth.data_dir", lambda: tmp_path
    )
    path = save_session_auth(
        "target",
        {
            "accessToken": "a",
            "idToken": "b",
            "login-session": "c",
            "_px3": "px-token",
            "_tgt_token": "tgt",
            "sapphire": "sap",
            "loyaltyid": "loy",
            "noise": "skip-me",
        },
    )
    assert path == session_auth_path("target")
    loaded = load_session_auth("target")
    assert loaded == {
        "accessToken": "a",
        "idToken": "b",
        "login-session": "c",
        "_px3": "px-token",
        "_tgt_token": "tgt",
        "sapphire": "sap",
        "loyaltyid": "loy",
    }
    assert "noise" not in loaded


def test_export_names_include_tgt_token() -> None:
    assert "_tgt_token" in TARGET_AUTH_EXPORT_NAMES
    assert "sapphire" in TARGET_AUTH_EXPORT_NAMES
    assert "loyaltyid" in TARGET_AUTH_EXPORT_NAMES


def test_missing_sidecar_cookies_login_session_optional(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("pokebot.session_auth.data_dir", lambda: tmp_path)
    assert missing_sidecar_cookies(
        {"accessToken": "a", "idToken": "b", "_px3": "px"}
    ) == []
    assert missing_sidecar_cookies({"accessToken": "a", "_px3": "px"}) == ["idToken"]
    # Still exported when present, but not required.
    save_session_auth(
        "target",
        {"accessToken": "a", "idToken": "b", "_px3": "px", "refreshToken": "r"},
    )
    loaded = load_session_auth("target")
    assert "login-session" not in loaded
    assert loaded["refreshToken"] == "r"
