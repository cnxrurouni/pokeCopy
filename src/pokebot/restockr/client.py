from __future__ import annotations

import getpass
import os
import sys
from typing import Any

from pokebot.restockr.auth import load_token, load_username, save_token
from pokebot.restockr.models import UserProfile

# RestockR rejects stock Python TLS clients (httpx/requests) with
# 403 "Automated clients are not permitted." Browser JA3 via curl_cffi works.


def _restockr_impersonate() -> str:
    from pokebot.reseller.impersonation import curl_impersonate_for_channel

    return curl_impersonate_for_channel("chrome")


def browser_headers(*, authorization: str | None = None) -> dict[str, str]:
    """Extra request headers (UA comes from curl_cffi / Socket.IO User-Agent)."""
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://www.restockr.app",
        "Referer": "https://www.restockr.app/",
    }
    if authorization:
        headers["Authorization"] = authorization
    return headers


def _clean_secret(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = value.strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "\"'":
        cleaned = cleaned[1:-1].strip()
    return cleaned or None


def _env(name: str) -> str | None:
    """Read an env var from the process, then (on Windows) the User registry.

    ``setx`` writes User env vars but does not update already-open shells / IDE
    terminals. Falling back to the registry picks those up without a restart.
    """
    value = _clean_secret(os.environ.get(name))
    if value:
        return value
    if sys.platform != "win32":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            raw, _ = winreg.QueryValueEx(key, name)
        cleaned = _clean_secret(raw if isinstance(raw, str) else None)
        if cleaned:
            os.environ[name] = cleaned  # cache for this process
            return cleaned
    except OSError:
        return None
    return None


class RestockRHttpError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        self.status_code = status_code
        super().__init__(message)


async def _restockr_request(
    method: str,
    url: str,
    *,
    headers: dict[str, str] | None = None,
    json: dict[str, Any] | None = None,
    timeout: float = 30.0,
) -> Any:
    try:
        from curl_cffi.requests import AsyncSession
    except ImportError as exc:
        raise RuntimeError(
            'RestockR requires curl_cffi (httpx is blocked with 403). '
            'Install with: pip install -e ".[reseller]"'
        ) from exc

    async with AsyncSession(impersonate=_restockr_impersonate()) as session:
        response = await session.request(
            method,
            url,
            headers=headers,
            json=json,
            timeout=timeout,
        )
    if response.status_code >= 400:
        body = (response.text or "")[:200]
        raise RestockRHttpError(
            response.status_code,
            f"RestockR HTTP {response.status_code} for {url}: {body}",
        )
    return response


class RestockRClient:
    def __init__(self, api_base: str, token: str | None = None) -> None:
        self.api_base = api_base.rstrip("/")
        self.token = token or load_token()

    def _headers(self) -> dict[str, str]:
        if not self.token:
            raise ValueError("Not logged in. Run: python -m pokebot login restockr")
        return {"Authorization": f"Bearer {self.token}"}

    async def login(
        self,
        username: str,
        password: str,
        *,
        parent_account: str | None = None,
    ) -> str:
        username = _clean_secret(username) or ""
        password = _clean_secret(password) or ""
        parent_account = _clean_secret(parent_account)
        payload: dict[str, str] = {"username": username, "password": password}
        if parent_account:
            payload["parentAccount"] = parent_account

        try:
            response = await _restockr_request(
                "POST",
                f"{self.api_base}/auth/login",
                json=payload,
            )
        except RestockRHttpError as exc:
            if exc.status_code == 401:
                raise ValueError(
                    f"RestockR login rejected (401) for user {username!r}. "
                    "Check RESTOCKR_USERNAME / RESTOCKR_PASSWORD"
                    + (
                        " / RESTOCKR_PARENT_ACCOUNT"
                        if parent_account
                        else " (set RESTOCKR_PARENT_ACCOUNT if this is a child account)"
                    )
                    + "."
                ) from exc
            raise

        data = response.json()
        if data.get("requiresUpgrade"):
            raise ValueError("Account requires upgrade before login can complete.")

        token = data.get("token")
        if not token:
            raise ValueError("Login failed: no token in response.")

        self.token = token
        save_token(token, username=username)
        return token

    async def get_profile(self) -> UserProfile:
        response = await _restockr_request(
            "GET",
            f"{self.api_base}/me",
            headers=self._headers(),
        )
        return UserProfile.model_validate(response.json())

    @classmethod
    def from_env(cls, api_base: str) -> RestockRClient:
        token = load_token()
        if token:
            return cls(api_base, token=token)

        username = _env("RESTOCKR_USERNAME")
        password = _env("RESTOCKR_PASSWORD")
        if not username or not password:
            raise ValueError(
                "Set RESTOCKR_USERNAME and RESTOCKR_PASSWORD in your environment, "
                "or run: python -m pokebot login restockr"
            )
        return cls(api_base)

    def _resolve_login_credentials(self) -> tuple[str, str, str | None]:
        """Env vars first (incl. Windows setx User env), then prompt if needed."""
        username = _env("RESTOCKR_USERNAME") or load_username()
        password = _env("RESTOCKR_PASSWORD")
        parent = _env("RESTOCKR_PARENT_ACCOUNT")

        if username and password:
            return username, password, parent

        if not sys.stdin.isatty():
            raise ValueError(
                "RestockR session expired. Set RESTOCKR_USERNAME and "
                "RESTOCKR_PASSWORD (e.g. setx), or run interactively: "
                "python -m pokebot login restockr"
            )

        if not username:
            username = input("RestockR username: ").strip()
        else:
            print(f"Username: {username}")
        if not password:
            password = getpass.getpass("RestockR password: ")
        if not username or not password:
            raise ValueError("RestockR username and password are required.")
        return username, password, parent

    async def ensure_authenticated(self) -> UserProfile:
        """Return profile, re-logging in automatically when the saved token is stale."""
        if self.token:
            try:
                return await self.get_profile()
            except RestockRHttpError as exc:
                if exc.status_code != 401:
                    raise

        print("RestockR token expired or missing — signing in again…")
        username, password, parent = self._resolve_login_credentials()
        try:
            await self.login(username, password, parent_account=parent)
        except ValueError as exc:
            # Env/setx password wrong — fall back to interactive so run isn't blocked.
            if "401" not in str(exc) or not sys.stdin.isatty():
                raise
            print(f"{exc}")
            print("Retrying with interactive password…")
            password = getpass.getpass("RestockR password: ")
            await self.login(username, password, parent_account=parent)
        return await self.get_profile()
