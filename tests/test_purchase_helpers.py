from pokebot.quantity import resolve_quantity_cap


def test_resolve_quantity_cap_prefers_lower_bound():
    assert resolve_quantity_cap(stock_quantity=10, config_cap=3) == 3
    assert resolve_quantity_cap(stock_quantity=2, config_cap=5) == 2
    assert resolve_quantity_cap(stock_quantity=10, config_cap=None) == 10
    assert resolve_quantity_cap(stock_quantity=None, config_cap=4) == 4
    assert resolve_quantity_cap(stock_quantity=None, config_cap=None) is None
