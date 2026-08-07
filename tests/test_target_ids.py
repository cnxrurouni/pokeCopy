from __future__ import annotations

from pokebot.reseller.target_ids import (
    is_plausible_tcin,
    resolve_target_product_url,
    resolve_target_tcin,
)


def test_rejects_test_sku_label():
    assert is_plausible_tcin("TEST-SKU") is False
    assert resolve_target_tcin(sku="TEST-SKU") is None


def test_prefers_url_tcin_over_bad_sku():
    tcin = resolve_target_tcin(
        url="https://www.target.com/p/some-product/-/A-1001560450",
        sku="TEST-SKU",
    )
    assert tcin == "1001560450"


def test_accepts_numeric_sku_fallback():
    assert resolve_target_tcin(sku="1001560450") == "1001560450"


def test_product_url_canonicalizes():
    url = resolve_target_product_url(
        "https://www.target.com/p/foo/-/A-1001560450?afid=restockr",
        tcin="1001560450",
    )
    assert "1001560450" in url
    assert "afid" not in url
