from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import socketio

from pokebot.restockr.client import browser_headers
from pokebot.restockr.models import RestockAlert

RestockHandler = Callable[[RestockAlert], Awaitable[None]]


class RestockRListener:
    def __init__(self, socket_url: str, token: str) -> None:
        self.socket_url = socket_url.rstrip("/")
        self.token = token
        self._sio = socketio.AsyncClient(
            reconnection=True,
            reconnection_attempts=0,
            reconnection_delay=1,
            reconnection_delay_max=5,
        )
        self._handlers: list[RestockHandler] = []
        self._profile_parent_id: str | None = None

        @self._sio.on("restock")
        async def _on_restock(data: dict[str, Any]) -> None:
            alert = RestockAlert.from_socket_payload(data)
            for handler in self._handlers:
                await handler(alert)

        @self._sio.on("session-invalid")
        async def _on_session_invalid(data: dict[str, Any]) -> None:
            reason = data.get("reason", "unknown")
            raise RuntimeError(f"RestockR session invalidated: {reason}")

    def on_restock(self, handler: RestockHandler) -> None:
        self._handlers.append(handler)

    def set_parent_id(self, parent_id: str | None) -> None:
        self._profile_parent_id = parent_id

    def resolve_alert_url(self, alert: RestockAlert) -> str | None:
        return alert.resolve_url(self._profile_parent_id)

    async def connect(self) -> None:
        # Socket.IO can't use curl_cffi; send Chromium-like headers so RestockR
        # doesn't reject the handshake as an automated client.
        headers = {
            **browser_headers(),
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/146.0.0.0 Safari/537.36"
            ),
        }
        await self._sio.connect(
            self.socket_url,
            auth={"token": self.token},
            transports=["websocket", "polling"],
            wait_timeout=30,
            headers=headers,
        )

    async def disconnect(self) -> None:
        if self._sio.connected:
            await self._sio.disconnect()

    async def wait_forever(self) -> None:
        while True:
            await asyncio.sleep(3600)
