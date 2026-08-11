from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from pokebot.config import DiscordSettings, Settings
from pokebot.reseller.orchestrator import ResellerOrchestrator
from pokebot.restockr.models import RestockAlert


@pytest.mark.asyncio
async def test_discord_source_skips_watchlist_by_default() -> None:
    settings = Settings(discord=DiscordSettings(watchlist_only=False))
    orch = ResellerOrchestrator(settings)
    orch.pipeline = MagicMock()
    orch.pipeline.handle_alert = AsyncMock(
        return_value=MagicMock(
            success=True,
            status=MagicMock(value="placed"),
            sku="87654321",
            order_id="o1",
            attempts=1,
            message="ok",
        )
    )
    orch._open_in_chrome = AsyncMock()

    alert = RestockAlert(
        id="discord-1",
        sku="87654321",
        store="target",
        url="https://www.target.com/p/-/A-87654321",
        stockQuantity=3,
        alertType="discord",
    )
    await orch._handle_restock(alert, source="discord")
    orch.pipeline.handle_alert.assert_awaited_once()
    orch._open_in_chrome.assert_awaited_once()
    assert "87654321" in orch._purchased_skus
