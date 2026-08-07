from __future__ import annotations

import asyncio

from pokebot.reseller.scheduler import TaskScheduler


async def test_per_account_concurrency_is_serialized():
    scheduler = TaskScheduler(per_account_concurrency=1, global_concurrency=10)
    active = 0
    max_active = 0

    async def work() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1

    await asyncio.gather(
        *(scheduler.run("acct_1", work) for _ in range(5))
    )
    assert max_active == 1


async def test_different_accounts_run_in_parallel():
    scheduler = TaskScheduler(per_account_concurrency=1, global_concurrency=10)
    active = 0
    max_active = 0

    async def work() -> None:
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        await asyncio.sleep(0.02)
        active -= 1

    await asyncio.gather(
        *(scheduler.run(f"acct_{i}", work) for i in range(5))
    )
    assert max_active > 1
