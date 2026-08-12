"""Diagnostic: which RestockR host emits alerts, and under which event name.

Connects to every candidate socket host with the saved token, logs *all* events
via a catch-all handler, then optionally POSTs the web app's test-alert endpoint.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any

import socketio

sys.path.insert(0, "src")

from pokebot.restockr.auth import load_token  # noqa: E402
from pokebot.restockr.client import browser_headers  # noqa: E402

HOSTS = {
    "config-dev": "https://emerald-alerts-development.onrender.com",
    "prod-api": "https://api.restockr.app",
}

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
)


async def watch(label: str, url: str, token: str) -> socketio.AsyncClient:
    sio = socketio.AsyncClient(reconnection=True, reconnection_attempts=0)

    @sio.on("*")
    async def _any(event: str, *args: Any) -> None:
        body = json.dumps(args[0] if len(args) == 1 else args, default=str)
        print(f"[{label}] EVENT {event!r}: {body[:600]}", flush=True)

    @sio.event
    async def connect() -> None:
        print(f"[{label}] connected sid={sio.sid}", flush=True)

    @sio.event
    async def connect_error(data: Any) -> None:
        print(f"[{label}] connect_error: {data}", flush=True)

    @sio.event
    async def disconnect() -> None:
        print(f"[{label}] disconnected", flush=True)

    try:
        await sio.connect(
            url,
            auth={"token": token},
            transports=["websocket", "polling"],
            wait_timeout=30,
            headers={**browser_headers(), "User-Agent": UA},
        )
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] CONNECT FAILED: {type(exc).__name__}: {exc}", flush=True)
    return sio


async def fire_test_alert(host: str, token: str, body: dict[str, Any]) -> None:
    from curl_cffi.requests import AsyncSession

    async with AsyncSession(impersonate="chrome") as session:
        resp = await session.post(
            f"{host}/api/alerts-test-v3/test",
            headers={**browser_headers(authorization=f"Bearer {token}")},
            json=body,
            timeout=30,
        )
    print(
        f"[test-alert->{host}] HTTP {resp.status_code}: {(resp.text or '')[:600]}",
        flush=True,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seconds", type=float, default=45.0)
    parser.add_argument(
        "--fire",
        choices=["none", "config-dev", "prod-api", "both"],
        default="none",
        help="POST the test-alert endpoint on the given host(s)",
    )
    parser.add_argument(
        "--sku",
        default="1011209279",
        help="SKU the test alert fires for (must be on your watchlist to pass filters)",
    )
    parser.add_argument(
        "--body",
        help="Raw JSON body; overrides --sku",
    )
    args = parser.parse_args()

    # Tolerate stray shell punctuation on a pasted --body argument.
    body = json.loads(args.body.strip().strip(".")) if args.body else {"sku": args.sku}

    token = load_token()
    if not token:
        raise SystemExit("No saved RestockR token — run: python -m pokebot login restockr")

    clients = [await watch(label, url, token) for label, url in HOSTS.items()]
    try:
        if args.fire != "none":
            await asyncio.sleep(2)
            targets = HOSTS.values() if args.fire == "both" else [HOSTS[args.fire]]
            for host in targets:
                await fire_test_alert(host, token, body)

        print(f"listening {args.seconds:.0f}s…", flush=True)
        await asyncio.sleep(args.seconds)
    finally:
        for sio in clients:
            if sio.connected:
                await sio.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
