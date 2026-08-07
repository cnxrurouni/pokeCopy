from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pokebot.config import AutobuySettings, PlaywrightSettings
from pokebot.purchase.native_browser import (
    NativeBrowserSession,
    launch_cmd_hint,
    resolve_browser_exe,
)
from pokebot.purchase.os_input import (
    name_matches_join_button,
    title_suggests_queue_ready,
    title_suggests_queue_waiting,
    window_title_looks_like_walmart,
)
from pokebot.purchase.walmart_native_queue import (
    WalmartNativeQueueClient,
    walmart_native_profile,
)
from pokebot.restockr.models import RestockAlert


def test_join_button_name_matchers():
    assert name_matches_join_button("Join queue")
    assert name_matches_join_button("Get in line")
    assert name_matches_join_button("  JOIN THE QUEUE  ")
    assert not name_matches_join_button("Add to cart")
    assert not name_matches_join_button(None)


def test_window_and_title_heuristics():
    assert window_title_looks_like_walmart("Walmart.com | Save Money")
    assert not window_title_looks_like_walmart("Target : Expect More")
    assert title_suggests_queue_waiting("Walmart - You're in line")
    assert title_suggests_queue_ready("Walmart - It's your turn to shop")
    assert not title_suggests_queue_ready("Walmart.com Homepage")


def test_launch_cmd_hint_includes_profile_and_url():
    profile = Path("C:/data/sessions/walmart")
    hint = launch_cmd_hint(profile, "https://www.walmart.com/ip/x", channel="msedge")
    assert "user-data-dir" in hint
    assert "walmart" in hint
    assert "walmart.com" in hint


def test_walmart_native_profile_avoids_invisible():
    settings = PlaywrightSettings()
    # Even if invisible is on, native profile must be classic walmart.
    settings.invisible_playwright.enabled = True
    path = walmart_native_profile(settings)
    assert "invisible" not in path.name
    assert path.name == "walmart"


def test_autobuy_defaults_native_queue_on():
    assert AutobuySettings().walmart_native_queue is True


@pytest.mark.asyncio
async def test_join_queue_launches_native_browser_and_clicks():
    settings = PlaywrightSettings(browser_channel="msedge")
    client = WalmartNativeQueueClient(
        browser_settings=settings,
        max_queues=2,
        page_load_wait_s=0.01,
        join_click_timeout_s=0.5,
    )
    fake_session = NativeBrowserSession(
        proc=MagicMock(poll=MagicMock(return_value=None), pid=1234),
        exe=Path("msedge.exe"),
        profile=Path("data/sessions/walmart"),
        start_url="https://www.walmart.com/ip/test",
        command="msedge …",
    )
    click = MagicMock(ok=True, method="pywinauto_uia_coords", detail="Join queue")

    with (
        patch(
            "pokebot.purchase.walmart_native_queue.launch_native_browser",
            return_value=fake_session,
        ) as launch,
        patch(
            "pokebot.purchase.walmart_native_queue.try_click_join_queue",
            return_value=click,
        ) as click_fn,
        patch(
            "pokebot.purchase.walmart_native_queue.WalmartNativeQueueClient._ensure_watch_loop"
        ),
    ):
        ok = await client.join_queue(
            "https://www.walmart.com/ip/test",
            "SKU1",
            label="Test Item",
            watch=True,
        )

    assert ok is True
    launch.assert_called_once()
    assert launch.call_args.kwargs["start_url"] == "https://www.walmart.com/ip/test"
    click_fn.assert_called_once()
    assert client.active_count == 1


@pytest.mark.asyncio
async def test_join_queue_respects_max_queues():
    client = WalmartNativeQueueClient(
        browser_settings=PlaywrightSettings(),
        max_queues=1,
        page_load_wait_s=0.01,
    )
    fake = NativeBrowserSession(
        proc=MagicMock(poll=MagicMock(return_value=None)),
        exe=Path("msedge.exe"),
        profile=Path("p"),
        start_url="u",
        command="c",
    )
    with (
        patch(
            "pokebot.purchase.walmart_native_queue.launch_native_browser",
            return_value=fake,
        ),
        patch(
            "pokebot.purchase.walmart_native_queue.try_click_join_queue",
            return_value=MagicMock(ok=True, method="x", detail=""),
        ),
        patch(
            "pokebot.purchase.walmart_native_queue.WalmartNativeQueueClient._ensure_watch_loop"
        ),
    ):
        assert await client.join_queue("https://w/1", "A", watch=False) is True
        assert await client.join_queue("https://w/2", "B", watch=False) is False


def test_orchestrator_routes_native_when_flag_on():
    """Smoke: queue alert detection + flag default used by orchestrator path."""
    from pokebot.purchase.walmart_queue import is_walmart_queue_alert

    alert = RestockAlert(
        id="1",
        sku="123",
        store="walmart",
        product="[QUEUE] Something",
        url="https://www.walmart.com/ip/x",
    )
    assert is_walmart_queue_alert(alert) is True
    assert AutobuySettings().walmart_native_queue is True


def test_resolve_browser_exe_returns_path_or_none():
    # On CI without Edge this may be None — just ensure no crash.
    exe = resolve_browser_exe("msedge")
    assert exe is None or exe.exists()
