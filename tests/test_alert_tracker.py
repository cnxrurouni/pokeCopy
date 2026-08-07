import asyncio
import time

import pytest

from pokebot.alert_tracker import ActedAlertTracker, AlertKey


@pytest.fixture
def tracker() -> ActedAlertTracker:
    return ActedAlertTracker(cooldown_seconds=300, dedup_window_seconds=60)


def test_same_sku_different_vendors_allowed(tracker: ActedAlertTracker) -> None:
    target = AlertKey(vendor="target", sku="SKU-1")
    walmart = AlertKey(vendor="walmart", sku="SKU-1")
    assert tracker.is_duplicate(target) is False
    assert tracker.is_duplicate(walmart) is False


def test_duplicate_while_in_flight(tracker: ActedAlertTracker) -> None:
    key = AlertKey(vendor="walmart", sku="SKU-1")
    tracker.mark_in_flight(key)
    tracker.mark_acted(key)
    assert tracker.is_duplicate(key) is True
    tracker.clear_in_flight(key)
    assert tracker.is_duplicate(key) is True


def test_duplicate_within_dedup_window(tracker: ActedAlertTracker) -> None:
    key = AlertKey(vendor="walmart", sku="SKU-1")
    tracker.mark_acted(key)
    assert tracker.is_duplicate(key) is True


def test_allowed_after_dedup_window_expires() -> None:
    tracker = ActedAlertTracker(cooldown_seconds=300, dedup_window_seconds=1)
    key = AlertKey(vendor="walmart", sku="SKU-1")
    tracker._acted_at[key.as_str()] = time.monotonic() - 2
    assert tracker.is_duplicate(key) is False


@pytest.mark.asyncio
async def test_acquire_blocks_duplicate() -> None:
    tracker = ActedAlertTracker(dedup_window_seconds=60)
    key = AlertKey(vendor="walmart", sku="SKU-1")
    results: list[bool] = []

    async def attempt() -> None:
        async with tracker.acquire(key) as should_proceed:
            results.append(should_proceed)
            if should_proceed:
                await asyncio.sleep(0.05)

    await asyncio.gather(attempt(), attempt())
    assert results.count(True) == 1
    assert results.count(False) == 1


@pytest.mark.asyncio
async def test_acquire_clears_in_flight_on_exit() -> None:
    tracker = ActedAlertTracker(dedup_window_seconds=60)
    key = AlertKey(vendor="target", sku="SKU-2")
    async with tracker.acquire(key) as should_proceed:
        assert should_proceed is True
        assert key.as_str() in tracker._in_flight
    assert key.as_str() not in tracker._in_flight
