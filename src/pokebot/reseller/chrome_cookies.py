from __future__ import annotations

"""Read Chromium/Edge profile cookies from disk after the browser has quit.

Used by the native-Edge Target harvester so we never attach CDP during PX
sensor warm-up. On Windows, cookie values are AES-GCM (v10) under a DPAPI-
wrapped key from ``Local State``. App-bound (v20) encryption is not supported
here — surface a clear error and fall back to ``login target --monitor``.
"""

import base64
import json
import shutil
import sqlite3
import sys
import tempfile
from pathlib import Path
from typing import Any


class CookieDecryptError(RuntimeError):
    """Raised when the profile cookie DB cannot be decrypted."""


def find_cookies_db(profile_root: Path) -> Path | None:
    """Return the Cookies SQLite path under a Chromium user-data dir."""
    candidates = [
        profile_root / "Default" / "Network" / "Cookies",
        profile_root / "Default" / "Cookies",
        profile_root / "Network" / "Cookies",
    ]
    for path in candidates:
        if path.is_file():
            return path
    return None


def find_local_state(profile_root: Path) -> Path | None:
    path = profile_root / "Local State"
    return path if path.is_file() else None


def _dpapi_unprotect(blob: bytes) -> bytes:
    if sys.platform != "win32":
        raise CookieDecryptError("Cookie DPAPI unwrap is only implemented on Windows")
    try:
        import win32crypt

        return win32crypt.CryptUnprotectData(blob, None, None, None, 0)[1]
    except ImportError:
        return _dpapi_unprotect_ctypes(blob)


def _dpapi_unprotect_ctypes(blob: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buf = ctypes.create_string_buffer(blob)
    in_blob = DATA_BLOB(len(blob), ctypes.cast(in_buf, ctypes.POINTER(ctypes.c_char)))
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0, ctypes.byref(out_blob)
    ):
        raise CookieDecryptError("CryptUnprotectData failed")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _aes_gcm_decrypt(key: bytes, nonce: bytes, ciphertext_and_tag: bytes) -> bytes:
    """AES-256-GCM decrypt via Windows BCrypt (no third-party crypto deps)."""
    if sys.platform != "win32":
        raise CookieDecryptError("AES-GCM cookie decrypt requires Windows BCrypt")
    import ctypes
    from ctypes import wintypes

    bcrypt = ctypes.windll.bcrypt
    BCRYPT_AES_ALGORITHM = "AES"
    BCRYPT_CHAINING_MODE = "ChainingMode"
    BCRYPT_CHAIN_MODE_GCM = "ChainingModeGCM"

    class BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.ULONG),
            ("dwInfoVersion", wintypes.ULONG),
            ("pbNonce", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbNonce", wintypes.ULONG),
            ("pbAuthData", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbAuthData", wintypes.ULONG),
            ("pbTag", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbTag", wintypes.ULONG),
            ("pbMacContext", ctypes.POINTER(ctypes.c_ubyte)),
            ("cbMacContext", wintypes.ULONG),
            ("cbAAD", wintypes.ULONG),
            ("cbData", ctypes.c_ulonglong),
            ("dwFlags", wintypes.ULONG),
        ]

    if len(ciphertext_and_tag) < 16:
        raise CookieDecryptError("ciphertext too short for GCM tag")
    ciphertext = ciphertext_and_tag[:-16]
    tag = ciphertext_and_tag[-16:]

    alg = ctypes.c_void_p()
    key_handle = ctypes.c_void_p()
    status = bcrypt.BCryptOpenAlgorithmProvider(
        ctypes.byref(alg), BCRYPT_AES_ALGORITHM, None, 0
    )
    if status != 0:
        raise CookieDecryptError(f"BCryptOpenAlgorithmProvider failed: {status:#x}")
    try:
        mode = ctypes.create_unicode_buffer(BCRYPT_CHAIN_MODE_GCM)
        status = bcrypt.BCryptSetProperty(
            alg,
            BCRYPT_CHAINING_MODE,
            mode,
            ctypes.sizeof(mode),
            0,
        )
        if status != 0:
            raise CookieDecryptError(f"BCryptSetProperty GCM failed: {status:#x}")

        status = bcrypt.BCryptGenerateSymmetricKey(
            alg, ctypes.byref(key_handle), None, 0, key, len(key), 0
        )
        if status != 0:
            raise CookieDecryptError(f"BCryptGenerateSymmetricKey failed: {status:#x}")
        try:
            nonce_buf = (ctypes.c_ubyte * len(nonce)).from_buffer_copy(nonce)
            tag_buf = (ctypes.c_ubyte * len(tag)).from_buffer_copy(tag)
            info = BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO()
            info.cbSize = ctypes.sizeof(BCRYPT_AUTHENTICATED_CIPHER_MODE_INFO)
            info.dwInfoVersion = 1
            info.pbNonce = ctypes.cast(nonce_buf, ctypes.POINTER(ctypes.c_ubyte))
            info.cbNonce = len(nonce)
            info.pbTag = ctypes.cast(tag_buf, ctypes.POINTER(ctypes.c_ubyte))
            info.cbTag = len(tag)

            buf = (ctypes.c_ubyte * len(ciphertext)).from_buffer_copy(ciphertext)
            out_len = wintypes.ULONG()
            status = bcrypt.BCryptDecrypt(
                key_handle,
                buf,
                len(ciphertext),
                ctypes.byref(info),
                None,
                0,
                buf,
                len(ciphertext),
                ctypes.byref(out_len),
                0,
            )
            if status != 0:
                raise CookieDecryptError(f"BCryptDecrypt failed: {status:#x}")
            return bytes(buf[: out_len.value])
        finally:
            bcrypt.BCryptDestroyKey(key_handle)
    finally:
        bcrypt.BCryptCloseAlgorithmProvider(alg, 0)


def load_os_crypt_key(profile_root: Path) -> bytes:
    local_state = find_local_state(profile_root)
    if local_state is None:
        raise CookieDecryptError(f"Local State missing under {profile_root}")
    data = json.loads(local_state.read_text(encoding="utf-8"))
    os_crypt = data.get("os_crypt") or {}
    if os_crypt.get("app_bound_encrypted_key"):
        # Edge profile here currently has no app_bound key; if it appears later,
        # refuse rather than silently return garbage.
        raise CookieDecryptError(
            "Profile uses App-Bound Encryption (v20). "
            "Re-export cookies with: python -m pokebot login target --monitor "
            "(Enter while Edge is still open)."
        )
    enc_b64 = os_crypt.get("encrypted_key")
    if not enc_b64:
        raise CookieDecryptError("Local State missing os_crypt.encrypted_key")
    raw = base64.b64decode(enc_b64)
    if not raw.startswith(b"DPAPI"):
        raise CookieDecryptError("encrypted_key does not start with DPAPI prefix")
    return _dpapi_unprotect(raw[5:])


def _aes_gcm_decrypt_prefer_cryptography(
    key: bytes, nonce: bytes, ciphertext_and_tag: bytes
) -> bytes:
    try:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        return AESGCM(key).decrypt(nonce, ciphertext_and_tag, None)
    except ImportError:
        return _aes_gcm_decrypt(key, nonce, ciphertext_and_tag)


def _strip_domain_hash_prefix(plain: bytes) -> bytes:
    """Edge/Chromium may prepend a 32-byte domain hash before the cookie string.

    Observed on v10-encrypted ``_px3`` values in the bot Edge profile: first 32
    bytes are non-ASCII, remainder is the real ASCII cookie (``hex:suffix``).
    """
    if len(plain) <= 32:
        return plain
    head, tail = plain[:32], plain[32:]
    head_binary = any(b < 32 or b > 126 for b in head)
    tail_text = bool(tail) and all(32 <= b < 127 for b in tail)
    if head_binary and tail_text:
        return tail
    return plain


def _bytes_to_cookie_str(plain: bytes) -> str:
    plain = _strip_domain_hash_prefix(plain)
    # Cookie header values must be latin-1; Target PX/auth cookies are ASCII.
    try:
        return plain.decode("ascii")
    except UnicodeDecodeError:
        return plain.decode("latin-1")


def decrypt_cookie_value(encrypted_value: bytes, key: bytes) -> str:
    if not encrypted_value:
        return ""
    if encrypted_value.startswith(b"v20"):
        # v20 app-bound: still AES-GCM under a different key; also has 32-byte prefix.
        raise CookieDecryptError(
            "Cookie uses v20 App-Bound Encryption — "
            "re-export via: python -m pokebot login target --monitor"
        )
    if encrypted_value.startswith((b"v10", b"v11")):
        nonce = encrypted_value[3:15]
        rest = encrypted_value[15:]
        plain = _aes_gcm_decrypt_prefer_cryptography(key, nonce, rest)
        return _bytes_to_cookie_str(plain)
    # Pre-v80 DPAPI-per-value
    try:
        return _bytes_to_cookie_str(_dpapi_unprotect(encrypted_value))
    except CookieDecryptError:
        raise
    except Exception as exc:
        raise CookieDecryptError(f"legacy DPAPI cookie decrypt failed: {exc}") from exc


def read_profile_cookies(
    profile_root: Path,
    *,
    domain_substr: str = "target.com",
) -> dict[str, str]:
    """Copy+read the Cookies DB and return name→value for matching hosts.

    Caller must ensure the browser using ``profile_root`` is fully quit first
    (file lock / WAL). Newer values overwrite older ones for the same name.
    """
    profile_root = profile_root.resolve()
    db_path = find_cookies_db(profile_root)
    if db_path is None:
        raise CookieDecryptError(f"Cookies DB not found under {profile_root}")
    key = load_os_crypt_key(profile_root)

    tmp_dir = Path(tempfile.mkdtemp(prefix="pokebot-cookies-"))
    try:
        tmp_db = tmp_dir / "Cookies"
        shutil.copy2(db_path, tmp_db)
        # WAL/SHM if present (best-effort)
        for suffix in ("-wal", "-shm", "-journal"):
            side = Path(str(db_path) + suffix)
            if side.is_file():
                try:
                    shutil.copy2(side, tmp_dir / side.name)
                except Exception:
                    pass

        con = sqlite3.connect(str(tmp_db))
        try:
            rows = con.execute(
                "SELECT host_key, name, encrypted_value, value FROM cookies"
            ).fetchall()
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)

    jar: dict[str, str] = {}
    domain = domain_substr.lower()
    errors = 0
    for host_key, name, encrypted_value, value in rows:
        host = str(host_key or "").lower()
        if domain not in host:
            continue
        if not name:
            continue
        plain = ""
        if value:
            plain = str(value)
        else:
            blob = bytes(encrypted_value or b"")
            if not blob:
                continue
            try:
                plain = decrypt_cookie_value(blob, key)
            except CookieDecryptError:
                errors += 1
                continue
        if plain:
            jar[str(name)] = plain

    if not jar:
        raise CookieDecryptError(
            f"No decryptable {domain_substr!r} cookies in {db_path}"
            + (f" ({errors} decrypt failures)" if errors else "")
        )
    return jar


def cookie_list_from_jar(jar: dict[str, str], *, domain: str = ".target.com") -> list[dict[str, Any]]:
    """Shape compatible with Playwright-style cookie dicts / filter_domain_cookies."""
    return [
        {"name": name, "value": value, "domain": domain, "path": "/"}
        for name, value in jar.items()
    ]
