import pytest

from pokebot.msrp import is_msrp_match, parse_price


@pytest.mark.parametrize(
    "text,expected,unknown",
    [
        ("$49.99", 49.99, False),
        ("49.99", 49.99, False),
        ("See price in cart", None, True),
        ("", None, True),
        (None, None, True),
    ],
)
def test_parse_price(text, expected, unknown):
    price, is_unknown = parse_price(text)
    assert price == expected
    assert is_unknown is unknown


def test_is_msrp_match():
    assert is_msrp_match(49.99, 49.99, True) is True
    assert is_msrp_match(50.0, 49.99, True) is False
    assert is_msrp_match(49.99, 49.99, False) is False
    assert is_msrp_match(None, 49.99, True, price_unknown=True) is False


def test_parse_product_url_target():
    from pokebot.enums import Retailer
    from pokebot.url_parser import canonical_target_product_url, parse_product_url

    retailer, product_id = parse_product_url(
        "https://www.target.com/p/pokemon/-/A-89444929"
    )
    assert retailer == Retailer.TARGET
    assert product_id == "89444929"
    assert canonical_target_product_url(
        "https://www.target.com/p/restockr/A-95298172?clkid=abc&ref=tgt_adv_xasd0002"
    ) == "https://www.target.com/p/-/A-95298172"


def test_parse_product_url_walmart():
    from pokebot.enums import Retailer
    from pokebot.url_parser import canonical_walmart_product_url, parse_product_url

    retailer, product_id = parse_product_url(
        "https://www.walmart.com/ip/Pokemon-ETB/1234567890"
    )
    assert retailer == Retailer.WALMART
    assert product_id == "1234567890"
    assert canonical_walmart_product_url(
        "https://goto.walmart.com/c/6477652/1398372/16662?u=https%3A%2F%2Fwww.walmart.com%2Fip%2Frestockr%2F15494520186"
    ) == "https://www.walmart.com/ip/-/15494520186"
