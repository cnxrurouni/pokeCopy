from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator


@dataclass(frozen=True)
class AlertKey:
    vendor: str
    sku: str

    def as_str(self) -> str:
        return f"{self.vendor}:{self.sku}"


class ActedAlertTracker:
    """Track in-flight and recently-acted alerts by (vendor, sku)."""

    def __init__(
        self,
        *,
        cooldown_seconds: int = 300,
        dedup_window_seconds: int | None = None,
    ) -> None:
        self.cooldown_seconds = cooldown_seconds
        self.dedup_window_seconds = dedup_window_seconds or cooldown_seconds
        self._in_flight: set[str] = set()
        self._acted_at: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock_for(self, key: AlertKey) -> asyncio.Lock:
        token = key.as_str()
        lock = self._locks.get(token)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[token] = lock
        return lock

    def is_duplicate(self, key: AlertKey) -> bool:
        token = key.as_str()
        if token in self._in_flight:
            return True
        last = self._acted_at.get(token)
        if last is None:
            return False
        return (time.monotonic() - last) < self.dedup_window_seconds

    def mark_in_flight(self, key: AlertKey) -> None:
        self._in_flight.add(key.as_str())

    def clear_in_flight(self, key: AlertKey) -> None:
        self._in_flight.discard(key.as_str())

    def mark_acted(self, key: AlertKey) -> None:
        self._acted_at[key.as_str()] = time.monotonic()

    @asynccontextmanager
    async def acquire(self, key: AlertKey) -> AsyncIterator[bool]:
        """
        Acquire per-key lock. Yields True if caller should proceed, False if duplicate.
        Marks in-flight and acted when proceeding; clears in-flight on exit.
        """
        async with self._lock_for(key):
            if self.is_duplicate(key):
                yield False
                return
            self.mark_in_flight(key)
            self.mark_acted(key)
            try:
                yield True
            finally:
                self.clear_in_flight(key)
