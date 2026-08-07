from pokebot.doctor import (
    check_architecture,
    check_http_fingerprint_ready,
    decode_jwt_claims,
    missing_target_auth_cookies,
    target_access_token_is_guest,
    target_access_token_is_soft_remembered,
)


def test_missing_target_auth_cookies() -> None:
    assert missing_target_auth_cookies([]) == [
        "accessToken",
        "idToken",
    ]
    assert missing_target_auth_cookies(
        {"accessToken", "idToken", "login-session", "_px3"}
    ) == []
    # login-session is optional/legacy — registered auth without it is complete.
    assert missing_target_auth_cookies({"accessToken", "idToken"}) == []
    assert missing_target_auth_cookies({"accessToken"}) == ["idToken"]


def test_check_architecture_runs() -> None:
    report = check_architecture()
    assert report.python_executable
    assert report.python_machine
    assert report.messages


def test_http_fingerprint_ready() -> None:
    ok, detail = check_http_fingerprint_ready(curl_impersonate="chrome146")
    assert ok is True
    assert "chrome146" in detail
    assert "impersonate=" in detail


def _make_jwt(**claims) -> str:
    import base64
    import json

    header = base64.urlsafe_b64encode(b'{"alg":"none"}').decode().rstrip("=")
    payload = (
        base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    )
    return f"{header}.{payload}.x"


def test_guest_access_token_detection() -> None:
    assert target_access_token_is_guest(_make_jwt(sut="G")) is True
    assert target_access_token_is_guest(_make_jwt(sut="R")) is False
    assert target_access_token_is_guest(None) is True
    assert decode_jwt_claims(_make_jwt(sut="G")).get("sut") == "G"


def test_soft_remembered_token_detection() -> None:
    assert target_access_token_is_soft_remembered(
        _make_jwt(sut="R", asl="L", sco="ecom.low,openid")
    ) is True
    assert target_access_token_is_soft_remembered(
        _make_jwt(sut="R", asl="l", sco="openid")
    ) is True
    assert target_access_token_is_soft_remembered(
        _make_jwt(sut="R", sco="ECOM.LOW,openid")
    ) is True
    assert target_access_token_is_soft_remembered(
        _make_jwt(sut="R", asl="H", sco="ecom,openid")
    ) is False
    assert target_access_token_is_soft_remembered(None) is False


def test_check_target_auth_sidecar_missing(tmp_path, monkeypatch) -> None:
    from pokebot import doctor
    from pokebot import session_auth

    monkeypatch.setattr(session_auth, "data_dir", lambda: tmp_path)
    ok, detail = doctor.check_target_auth_sidecar()
    assert ok is False
    assert "no auth sidecar" in detail


def test_check_target_auth_sidecar_rejects_soft_jwt(tmp_path, monkeypatch) -> None:
    from pokebot import doctor
    from pokebot import session_auth

    monkeypatch.setattr(session_auth, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor, "probe_target_cart_guest_type", lambda _c: None)
    session_auth.save_session_auth(
        "target",
        {
            "accessToken": _make_jwt(sut="R", asl="L", sco="ecom.low,openid"),
            "idToken": "id",
            "_px3": "px" * 20,
        },
    )
    ok, detail = doctor.check_target_auth_sidecar()
    assert ok is False
    assert "soft" in detail.lower() or "REMEMBERED" in detail
    assert "login target" in detail


def test_check_target_auth_sidecar_rejects_remembered_guest(tmp_path, monkeypatch) -> None:
    from pokebot import doctor
    from pokebot import session_auth

    monkeypatch.setattr(session_auth, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor, "probe_target_cart_guest_type", lambda _c: "REMEMBERED")
    session_auth.save_session_auth(
        "target",
        {
            "accessToken": _make_jwt(sut="R", asl="H", sco="ecom,openid"),
            "idToken": "id",
            "_px3": "px" * 20,
        },
    )
    ok, detail = doctor.check_target_auth_sidecar()
    assert ok is False
    assert "REMEMBERED" in detail
    assert "REGISTERED" in detail


def test_check_target_auth_sidecar_ok_registered(tmp_path, monkeypatch) -> None:
    from pokebot import doctor
    from pokebot import session_auth

    monkeypatch.setattr(session_auth, "data_dir", lambda: tmp_path)
    monkeypatch.setattr(doctor, "probe_target_cart_guest_type", lambda _c: "REGISTERED")
    session_auth.save_session_auth(
        "target",
        {
            "accessToken": _make_jwt(sut="R", asl="H", sco="ecom,openid"),
            "idToken": "id",
            "_px3": "px" * 20,
        },
    )
    ok, detail = doctor.check_target_auth_sidecar()
    assert ok is True
    assert "registered auth+PX sidecar OK" in detail
    assert "guest_type=REGISTERED" in detail