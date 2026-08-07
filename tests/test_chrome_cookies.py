from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from pokebot.reseller.chrome_cookies import (
    CookieDecryptError,
    _strip_domain_hash_prefix,
    decrypt_cookie_value,
    find_cookies_db,
    load_os_crypt_key,
)


def test_find_cookies_db_prefers_network_path(tmp_path: Path):
    network = tmp_path / "Default" / "Network"
    network.mkdir(parents=True)
    db = network / "Cookies"
    db.write_bytes(b"sqlite")
    assert find_cookies_db(tmp_path) == db


def test_load_os_crypt_key_rejects_app_bound(tmp_path: Path):
    (tmp_path / "Local State").write_text(
        json.dumps(
            {
                "os_crypt": {
                    "encrypted_key": base64.b64encode(b"DPAPI" + b"\x00" * 16).decode(),
                    "app_bound_encrypted_key": "x",
                }
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(CookieDecryptError, match="App-Bound"):
        load_os_crypt_key(tmp_path)


def test_decrypt_cookie_value_rejects_v20():
    with pytest.raises(CookieDecryptError, match="v20"):
        decrypt_cookie_value(b"v20" + b"\x00" * 40, key=b"\x00" * 32)


def test_strip_domain_hash_prefix():
    payload = b"abcdef0123456789abcdef0123456789:suffixOK"
    blob = b"\x00\x01" * 16 + payload
    assert _strip_domain_hash_prefix(blob) == payload
    assert _strip_domain_hash_prefix(payload) == payload
