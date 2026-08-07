from __future__ import annotations

import json
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pokebot.config import data_dir

_SENSITIVE_HEADER = re.compile(
    r"^(cookie|authorization|x-api-key)$", re.I
)
_SENSITIVE_COOKIE = re.compile(
    r"(accessToken|idToken|refreshToken|login-session|_px3|_pxhd|password|Bearer)",
    re.I,
)

_lock = threading.Lock()
_current: TrafficLogger | None = None


def traffic_logs_dir() -> Path:
    path = data_dir() / "logs" / "reseller-traffic"
    path.mkdir(parents=True, exist_ok=True)
    return path


def redact_value(value: str, *, keep: int = 8) -> str:
    if not value:
        return value
    if len(value) <= keep * 2:
        return "***"
    return f"{value[:keep]}…{value[-4:]}(len={len(value)})"


def redact_headers(headers: dict[str, str] | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, raw in (headers or {}).items():
        if _SENSITIVE_HEADER.match(key):
            if key.lower() == "cookie":
                parts = []
                for piece in raw.split(";"):
                    piece = piece.strip()
                    if not piece or "=" not in piece:
                        continue
                    name, _, val = piece.partition("=")
                    if _SENSITIVE_COOKIE.search(name):
                        parts.append(f"{name}={redact_value(val)}")
                    else:
                        parts.append(f"{name}={val[:24]}")
                out[key] = "; ".join(parts)
            else:
                out[key] = redact_value(raw)
        else:
            out[key] = raw
    return out


def truncate(text: str | None, limit: int = 4000) -> str:
    if not text:
        return ""
    if len(text) <= limit:
        return text
    return text[:limit] + f"…(+{len(text) - limit} chars)"


class TrafficLogger:
    """Append-only JSONL log of reseller harvest/checkout traffic for debugging."""

    def __init__(
        self,
        *,
        sku: str | None = None,
        account_id: str | None = None,
        path: Path | None = None,
    ) -> None:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        safe_sku = re.sub(r"[^\w.-]+", "_", sku or "unknown")[:32]
        self.path = path or (
            traffic_logs_dir() / f"traffic-{stamp}-{safe_sku}.jsonl"
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.sku = sku
        self.account_id = account_id
        self.started_at = time.time()
        self._seq = 0
        self.event(
            "run_start",
            {
                "sku": sku,
                "account_id": account_id,
                "path": str(self.path),
            },
        )

    def event(self, kind: str, data: dict[str, Any] | None = None) -> None:
        self._seq += 1
        payload = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "seq": self._seq,
            "kind": kind,
            "sku": self.sku,
            "account_id": self.account_id,
            "elapsed_s": round(time.time() - self.started_at, 3),
            **(data or {}),
        }
        line = json.dumps(payload, default=str, ensure_ascii=False) + "\n"
        with _lock:
            with self.path.open("a", encoding="utf-8") as fh:
                fh.write(line)

    def http(
        self,
        *,
        channel: str,
        name: str,
        method: str,
        url: str,
        status: int | None = None,
        request_headers: dict[str, str] | None = None,
        request_body: str | None = None,
        response_body: str | None = None,
        error: str | None = None,
        attempt: int | None = None,
        extra: dict[str, Any] | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "channel": channel,
            "name": name,
            "method": method,
            "url": url,
            "status": status,
            "attempt": attempt,
            "request_headers": redact_headers(request_headers),
            "request_body": truncate(request_body, 2000),
            "response_body": truncate(response_body, 4000),
            "error": error,
        }
        if extra:
            data.update(extra)
        self.event("http", data)

    def note(self, message: str, **fields: Any) -> None:
        self.event("note", {"message": message, **fields})

    def close(self, *, success: bool | None = None, message: str | None = None) -> None:
        self.event(
            "run_end",
            {
                "success": success,
                "message": message,
                "path": str(self.path),
            },
        )


def get_traffic_logger() -> TrafficLogger | None:
    return _current


def set_traffic_logger(logger: TrafficLogger | None) -> None:
    global _current
    with _lock:
        _current = logger


def start_traffic_log(
    *,
    sku: str | None = None,
    account_id: str | None = None,
    enabled: bool = True,
) -> TrafficLogger | None:
    """Begin a per-purchase traffic log; returns None when disabled."""
    if not enabled:
        set_traffic_logger(None)
        return None
    logger = TrafficLogger(sku=sku, account_id=account_id)
    set_traffic_logger(logger)
    return logger
