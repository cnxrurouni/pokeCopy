from __future__ import annotations

from pokebot.discord_alerts.listener import normalize_bot_token
from pokebot.discord_alerts.parse import iter_target_urls, parse_discord_alert_text


def test_normalize_bot_token_strips_quotes_and_prefix() -> None:
    assert normalize_bot_token('  "abc.def.ghi"  ') == "abc.def.ghi"
    assert normalize_bot_token("Bot abc.def.ghi") == "abc.def.ghi"
    assert normalize_bot_token("abc.def.ghi") == "abc.def.ghi"


def test_iter_target_urls_dedupes_and_cleans() -> None:
    text = (
        "Live https://www.target.com/p/foo/-/A-12345678?presearch=1 "
        "and again https://www.target.com/p/foo/-/A-12345678"
    )
    urls = iter_target_urls(text)
    assert urls == ["https://www.target.com/p/-/A-12345678"]


def test_parse_discord_alert_from_content_url() -> None:
    alert = parse_discord_alert_text(
        message_id="99",
        content="TARGET RESTOCK Qty: 12\nhttps://www.target.com/p/-/A-87654321",
    )
    assert alert is not None
    assert alert.sku == "87654321"
    assert alert.store == "target"
    assert alert.stock_quantity == 12
    assert alert.id == "discord-99"
    assert alert.resolve_url() == "https://www.target.com/p/-/A-87654321"


def test_parse_discord_alert_from_embed_fields() -> None:
    alert = parse_discord_alert_text(
        message_id="1",
        content="",
        embed_texts=["Product: Prismatic Evolutions", "Stock: 4"],
        embed_urls=["https://www.target.com/p/something/-/A-11223344"],
    )
    assert alert is not None
    assert alert.sku == "11223344"
    assert alert.stock_quantity == 4
    assert alert.product == "Prismatic Evolutions"


def test_parse_discord_alert_bare_tcin_path() -> None:
    alert = parse_discord_alert_text(
        message_id="2",
        content="Drop /A-99887766 now",
    )
    assert alert is not None
    assert alert.sku == "99887766"


def test_parse_discord_ignores_non_target() -> None:
    assert (
        parse_discord_alert_text(
            message_id="3",
            content="https://www.walmart.com/ip/foo/123",
        )
        is None
    )
