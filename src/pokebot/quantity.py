from __future__ import annotations


def resolve_quantity_cap(*, stock_quantity: int | None, config_cap: int | None) -> int | None:
    """Combine RestockR stock qty with an optional hard cap.

    Returns None when neither side constrains quantity (caller defaults to 1).
    """
    if stock_quantity is None and config_cap is None:
        return None
    if stock_quantity is None:
        return config_cap
    if config_cap is None:
        return stock_quantity
    return min(stock_quantity, config_cap)
