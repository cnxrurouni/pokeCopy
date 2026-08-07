from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import datetime
from typing import Awaitable, Callable

from pokebot.enums import Retailer
from pokebot.reseller.models import HarvestedToken, TokenKind

HarvestHook = Callable[[], Awaitable[HarvestedToken | None]]

_PoolKey = tuple[Retailer, TokenKind]


class TokenBank:
    """TTL-aware pool of anti-bot tokens minted ahead of a drop.

    Harvesters continuously ``deposit`` fresh tokens; the checkout engine
    ``acquire``s one per protected request (Shape ATC cookies are single-use, so
    acquire pops). When a pool is empty an optionally registered harvest hook is
    invoked to mint one on demand.
    """

    def __init__(self, *, refresh_margin_seconds: float = 5.0) -> None:
        self._refresh_margin = refresh_margin_seconds
        self._pools: dict[_PoolKey, list[HarvestedToken]] = defaultdict(list)
        self._hooks: dict[_PoolKey, HarvestHook] = {}
        self._lock = asyncio.Lock()

    def register_harvester(
        self, retailer: Retailer, kind: TokenKind, hook: HarvestHook
    ) -> None:
        self._hooks[(retailer, kind)] = hook

    async def deposit(self, token: HarvestedToken) -> None:
        async with self._lock:
            self._pools[(token.retailer, token.kind)].append(token)

    def _purge_stale(self, key: _PoolKey, at: datetime | None) -> None:
        pool = self._pools[key]
        self._pools[key] = [
            t for t in pool if t.is_fresh(at=at, margin_seconds=self._refresh_margin)
        ]

    async def acquire(
        self,
        retailer: Retailer,
        kind: TokenKind,
        *,
        account_id: str | None = None,
        at: datetime | None = None,
    ) -> HarvestedToken | None:
        """Pop the freshest non-stale token, harvesting on demand if empty.

        When ``account_id`` is given, only tokens minted by that account are
        eligible — a PerimeterX ``_px3`` is bound to the session/IP that minted it,
        so it must be spent by the same account. In that case the on-demand hook is
        skipped (the caller harvests for the specific account instead).
        """
        key = (retailer, kind)
        async with self._lock:
            self._purge_stale(key, at)
            pool = self._pools[key]
            eligible = [t for t in pool if account_id is None or t.account_id == account_id]
            if eligible:
                eligible.sort(key=lambda t: t.created_at)
                chosen = eligible[-1]
                pool.remove(chosen)
                return chosen

        if account_id is not None:
            return None

        hook = self._hooks.get(key)
        if hook is None:
            return None

        token = await hook()
        if token is None:
            return None
        if not token.is_fresh(at=at, margin_seconds=self._refresh_margin):
            return None
        return token

    def count(
        self,
        retailer: Retailer,
        kind: TokenKind,
        *,
        account_id: str | None = None,
        at: datetime | None = None,
    ) -> int:
        key = (retailer, kind)
        self._purge_stale(key, at)
        pool = self._pools[key]
        if account_id is None:
            return len(pool)
        return sum(1 for t in pool if t.account_id == account_id)
