from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, TypeVar

T = TypeVar("T")


class TaskScheduler:
    """Runs checkout coroutines under two limits: a global concurrency cap and a
    strict per-account cap (default 1) so an account never checks out twice at once.
    """

    def __init__(self, *, per_account_concurrency: int = 1, global_concurrency: int = 10) -> None:
        self._per_account = max(1, per_account_concurrency)
        self._global = asyncio.Semaphore(max(1, global_concurrency))
        self._account_sems: dict[str, asyncio.Semaphore] = {}
        self._registry_lock = asyncio.Lock()

    async def _account_sem(self, account_id: str) -> asyncio.Semaphore:
        async with self._registry_lock:
            sem = self._account_sems.get(account_id)
            if sem is None:
                sem = asyncio.Semaphore(self._per_account)
                self._account_sems[account_id] = sem
            return sem

    async def run(self, account_id: str, coro_factory: Callable[[], Awaitable[T]]) -> T:
        account_sem = await self._account_sem(account_id)
        async with self._global:
            async with account_sem:
                return await coro_factory()
