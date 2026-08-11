from pokebot.config import Settings, load_settings
from pokebot.quantity import resolve_quantity_cap


def test_load_settings_restockr_defaults():
    settings = load_settings()
    assert settings.restockr.api_base
    assert settings.autobuy.watchlist_only is True
    assert "target" in settings.autobuy.retailers
    assert settings.discord.guild_id == "1457553935848964251"
    assert settings.discord.channel_id == "1457563918720569394"
    assert settings.discord.watchlist_only is False


def test_settings_model_defaults():
    settings = Settings()
    assert settings.autobuy.target_min_quantity == 1
    assert settings.autobuy.cooldown_seconds == 300
    assert settings.discord.token_env == "DISCORD_BOT_TOKEN"


def test_resolve_quantity_cap_prefers_lower_bound():
    assert resolve_quantity_cap(stock_quantity=10, config_cap=3) == 3
    assert resolve_quantity_cap(stock_quantity=2, config_cap=5) == 2
    assert resolve_quantity_cap(stock_quantity=10, config_cap=None) == 10
    assert resolve_quantity_cap(stock_quantity=None, config_cap=4) == 4
    assert resolve_quantity_cap(stock_quantity=None, config_cap=None) is None
