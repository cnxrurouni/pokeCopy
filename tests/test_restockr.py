
from pokebot.restockr.models import RestockAlert


def test_restock_alert_resolve_url_default():
    alert = RestockAlert(
        id="1",
        store="Target",
        url="https://www.target.com/p/-/A-123",
        productUrls={"default": "https://www.target.com/p/-/A-123"},
    )
    assert alert.resolve_url() == "https://www.target.com/p/-/A-123"


def test_restock_alert_resolve_url_parent():
    alert = RestockAlert(
        id="1",
        store="Target",
        url="https://fallback.com",
        productUrls={"parent123": "https://affiliate.com/product"},
    )
    assert alert.resolve_url("parent123") == "https://affiliate.com/product"


def test_restock_alert_from_socket_payload():
    alert = RestockAlert.from_socket_payload(
        {
            "id": "abc",
            "sku": "SKU-1",
            "store": "Walmart",
            "stockQuantity": 5,
            "url": "https://walmart.com/ip/test/1",
        }
    )
    assert alert.sku == "SKU-1"
    assert alert.stock_quantity == 5


def test_restock_alert_price_dollar_string():
    alert = RestockAlert.from_socket_payload(
        {
            "id": "abc",
            "store": "Target",
            "price": "$74.87",
            "url": "https://www.target.com/p/-/A-123",
        }
    )
    assert alert.price == 74.87


def test_restock_alert_price_na():
    alert = RestockAlert.from_socket_payload(
        {
            "id": "abc",
            "store": "Best Buy",
            "price": "N/A",
            "url": "https://bestbuy.com/product/1",
        }
    )
    assert alert.price is None
