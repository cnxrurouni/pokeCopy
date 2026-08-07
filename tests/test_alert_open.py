from __future__ import annotations

from unittest.mock import patch

import pytest

from pokebot.alert_open import AlertOpenOrchestrator
from pokebot.config import Settings
from pokebot.restockr.models import RestockAlert, UserProfile


def test_open_url_in_system_chrome_macos() -> None:
    from pokebot.platform_util import open_url_in_system_chrome

    with (
        patch("pokebot.platform_util.is_macos", return_value=True),
        patch("pokebot.platform_util.is_windows", return_value=False),
        patch("pokebot.platform_util.Path.exists", return_value=True),
        patch("pokebot.platform_util.subprocess.Popen") as popen,
    ):
        open_url_in_system_chrome("https://www.target.com/p/-/A-1")
        popen.assert_called_once()
        args = popen.call_args[0][0]
        assert args[0] == "open"
        assert args[1] == "-a"
        assert args[2] == "Google Chrome"
        assert args[3].startswith("https://")


@pytest.mark.asyncio
async def test_alert_open_skips_off_watchlist() -> None:
    settings = Settings()
    orch = AlertOpenOrchestrator(settings, watchlist_only=True, retailers=["target"])
    orch.profile = UserProfile(username="u", product_skus=["111"])
    alert = RestockAlert(
        id="222",
        sku="222",
        store="target",
        url="https://www.target.com/p/-/A-222",
        stockQuantity=5,
    )
    with patch("pokebot.alert_open.open_url_in_system_chrome") as open_fn:
        await orch._handle_restock(alert)
        open_fn.assert_not_called()


@pytest.mark.asyncio
async def test_alert_open_opens_watchlist_url() -> None:
    settings = Settings()
    orch = AlertOpenOrchestrator(settings, watchlist_only=True, retailers=["target"])
    orch.profile = UserProfile(username="u", product_skus=["111"])
    alert = RestockAlert(
        id="111",
        sku="111",
        store="target",
        url="https://www.target.com/p/-/A-111",
        stockQuantity=5,
    )
    with patch("pokebot.alert_open.open_url_in_system_chrome") as open_fn:
        await orch._handle_restock(alert)
        open_fn.assert_called_once_with("https://www.target.com/p/-/A-111")
