from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any

from pokebot.config import data_dir

_SENSITIVE_HEADER = {"authorization", "proxy-authorization", "cookie"}
_SENSITIVE_COOKIE = {
    "accesstoken",
    "idtoken",
    "refreshtoken",
    "login-session",
    "_px3",
    "_px2",
    "_tgt_session",
}


def _redact_value(name: str, value: str) -> str:
    key = name.lower()
    if key in _SENSITIVE_COOKIE or key in _SENSITIVE_HEADER:
        return f"<len={len(value)}>"
    if len(value) > 200:
        return value[:200] + f"…<len={len(value)}>"
    return value


def redact_headers(headers: dict[str, Any] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in (headers or {}).items():
        key = str(k)
        val = str(v)
        if key.lower() in _SENSITIVE_HEADER:
            if key.lower() == "cookie":
                parts = []
                for part in val.split(";"):
                    part = part.strip()
                    if not part or "=" not in part:
                        continue
                    n, _, rest = part.partition("=")
                    parts.append(f"{n.strip()}={_redact_value(n.strip(), rest)}")
                out[key] = "; ".join(parts)
            elif key.lower() == "authorization" and val.lower().startswith("bearer "):
                out[key] = f"Bearer <len={len(val) - 7}>"
            else:
                out[key] = f"<len={len(val)}>"
        else:
            out[key] = val if len(val) <= 500 else val[:500] + "…"
    return out


def redact_cookie_map(cookies: dict[str, str] | None) -> dict[str, str]:
    return {
        k: _redact_value(k, v)
        for k, v in (cookies or {}).items()
        if v
    }


def redact_body_snip(body: str | None, *, limit: int = 2048) -> str:
    """Truncate body and scrub obvious CVV / card fields from telemetry."""
    if not body:
        return ""
    text = body
    try:
        import re

        text = re.sub(
            r'("cvv"\s*:\s*")[^"]*(")',
            r"\1<redacted>\2",
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(
            r'("card_number"\s*:\s*")[^"]*(")',
            r"\1<redacted>\2",
            text,
            flags=re.IGNORECASE,
        )
    except Exception:
        pass
    return text[:limit]


class HttpTelemetry:
    """NDJSON dump of every Target HTTP attempt under data/logs/reseller-http/."""

    def __init__(
        self,
        *,
        run_id: str | None = None,
        account_id: str | None = None,
        tcin: str | None = None,
        qty: int | None = None,
        impersonate: str | None = None,
        proxy_host: str | None = None,
    ) -> None:
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.account_id = account_id
        self.tcin = tcin
        self.qty = qty
        self.impersonate = impersonate
        self.proxy_host = proxy_host
        self.path = self._open_path()
        try:
            import curl_cffi

            self.curl_cffi_version = getattr(curl_cffi, "__version__", "unknown")
        except Exception:
            self.curl_cffi_version = "unavailable"

    def _open_path(self) -> Path:
        root = data_dir() / "logs" / "reseller-http"
        root.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        tcin = self.tcin or "unknown"
        return root / f"{stamp}-{tcin}-{self.run_id}.ndjson"

    def write(self, record: dict[str, Any]) -> None:
        base = {
            "ts": time.time(),
            "run_id": self.run_id,
            "account_id": self.account_id,
            "tcin": self.tcin,
            "qty": self.qty,
            "impersonate": self.impersonate,
            "proxy_host": self.proxy_host,
            "curl_cffi_version": self.curl_cffi_version,
        }
        base.update(record)
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(base, default=str) + "\n")

    def meta(self, **fields: Any) -> None:
        self.write({"event": "session_meta", **fields})

    def request(
        self,
        *,
        step: str,
        attempt: int,
        phase: str,
        method: str,
        url: str,
        request_headers: dict[str, Any] | None,
        request_cookies: dict[str, str] | None,
        request_body_snip: str | None,
        status: int | None,
        response_headers: dict[str, Any] | None,
        response_body_snip: str | None,
        elapsed_s: float | None,
        http_version: Any = None,
        primary_ip: str | None = None,
        primary_port: int | None = None,
        local_ip: str | None = None,
        local_port: int | None = None,
        redirect_count: int | None = None,
        redirect_url: str | None = None,
        curl_infos: dict[str, Any] | None = None,
        step_ok: bool | None = None,
        fatal: bool | None = None,
        error_key: str | None = None,
        error_code: str | None = None,
    ) -> None:
        set_cookie_names: list[str] = []
        for k, v in (response_headers or {}).items():
            if str(k).lower() == "set-cookie":
                # name is before first '='
                for part in str(v).split("\n"):
                    name = part.split("=", 1)[0].strip()
                    if name:
                        set_cookie_names.append(name)

        self.write(
            {
                "event": "http_request",
                "step": step,
                "attempt": attempt,
                "phase": phase,
                "method": method,
                "url": url,
                "request_headers": redact_headers(request_headers),
                "request_cookie_names": sorted((request_cookies or {}).keys()),
                "request_cookies_redacted": redact_cookie_map(request_cookies),
                "request_body_snip": redact_body_snip(request_body_snip),
                "status": status,
                "response_headers": {
                    str(k): (
                        f"<set-cookie names>"
                        if str(k).lower() == "set-cookie"
                        else (str(v)[:500] if len(str(v)) > 500 else str(v))
                    )
                    for k, v in (response_headers or {}).items()
                },
                "set_cookie_names": set_cookie_names,
                "response_body_snip": redact_body_snip(response_body_snip),
                "elapsed_s": elapsed_s,
                "http_version": http_version,
                "primary_ip": primary_ip,
                "primary_port": primary_port,
                "local_ip": local_ip,
                "local_port": local_port,
                "redirect_count": redirect_count,
                "redirect_url": redirect_url,
                "curl_infos": curl_infos or {},
                "step_ok": step_ok,
                "fatal": fatal,
                "error_key": error_key,
                "error_code": error_code,
            }
        )
