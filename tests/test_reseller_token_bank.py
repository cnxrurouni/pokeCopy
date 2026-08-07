from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from pokebot.enums import Retailer
from pokebot.reseller.models import HarvestedToken, TokenKind
from pokebot.reseller.token_bank import TokenBank


def _token(created_at: datetime, ttl: float = 300.0) -> HarvestedToken:
    return HarvestedToken(
        kind=TokenKind.PX3,
        retailer=Retailer.TARGET,
        value="v",
        ttl_seconds=ttl,
        created_at=created_at,
    )


async def test_acquire_pops_freshest_and_empties_pool():
    bank = TokenBank()
    now = datetime.now(timezone.utc)
    older = _token(now - timedelta(seconds=100))
    newer = _token(now - timedelta(seconds=1))
    await bank.deposit(older)
    await bank.deposit(newer)

    got = await bank.acquire(Retailer.TARGET, TokenKind.PX3, at=now)
    assert got is newer
    assert bank.count(Retailer.TARGET, TokenKind.PX3, at=now) == 1

    got2 = await bank.acquire(Retailer.TARGET, TokenKind.PX3, at=now)
    assert got2 is older
    assert bank.count(Retailer.TARGET, TokenKind.PX3, at=now) == 0


async def test_stale_tokens_are_purged():
    bank = TokenBank(refresh_margin_seconds=5.0)
    now = datetime.now(timezone.utc)
    stale = _token(now - timedelta(seconds=299), ttl=300.0)  # within margin -> stale
    await bank.deposit(stale)
    assert bank.count(Retailer.TARGET, TokenKind.PX3, at=now) == 0
    assert await bank.acquire(Retailer.TARGET, TokenKind.PX3, at=now) is None


async def test_account_scoped_acquire_and_count():
    bank = TokenBank()
    now = datetime.now(timezone.utc)
    a = HarvestedToken(
        kind=TokenKind.PX3, retailer=Retailer.TARGET, value="a",
        account_id="acct_a", created_at=now,
    )
    b = HarvestedToken(
        kind=TokenKind.PX3, retailer=Retailer.TARGET, value="b",
        account_id="acct_b", created_at=now,
    )
    await bank.deposit(a)
    await bank.deposit(b)

    assert bank.count(Retailer.TARGET, TokenKind.PX3, at=now) == 2
    assert bank.count(Retailer.TARGET, TokenKind.PX3, account_id="acct_a", at=now) == 1

    got = await bank.acquire(Retailer.TARGET, TokenKind.PX3, account_id="acct_a", at=now)
    assert got is not None and got.account_id == "acct_a"
    # The other account's token is untouched.
    assert bank.count(Retailer.TARGET, TokenKind.PX3, account_id="acct_b", at=now) == 1
    # Pool for acct_a is now empty; with an account filter the hook is skipped.
    assert await bank.acquire(Retailer.TARGET, TokenKind.PX3, account_id="acct_a", at=now) is None


async def test_on_demand_harvest_hook_when_empty():
    bank = TokenBank()
    now = datetime.now(timezone.utc)
    calls = {"n": 0}

    async def hook() -> HarvestedToken:
        calls["n"] += 1
        return _token(now)

    bank.register_harvester(Retailer.TARGET, TokenKind.PX3, hook)
    got = await bank.acquire(Retailer.TARGET, TokenKind.PX3, at=now)
    assert got is not None
    assert calls["n"] == 1
