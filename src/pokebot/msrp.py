from __future__ import annotations

import re


def is_msrp_match(
    price: float | None,
    max_price: float,
    in_stock: bool,
    *,
    price_unknown: bool = False,
    allow_unknown_price: bool = False,
) -> bool:
    if not in_stock:
        return False
    if price_unknown or price is None:
        return allow_unknown_price
    return price <= max_price


def parse_price(value: str | float | int | None) -> tuple[float | None, bool]:
    if value is None:
        return None, True
    if isinstance(value, (int, float)):
        return float(value), False
    text = value.strip()
    if not text:
        return None, True
    lowered = text.lower()
    if "see price" in lowered or "not available" in lowered:
        return None, True
    match = re.search(r"[\d,]+\.?\d*", text.replace(",", ""))
    if not match:
        return None, True
    return float(match.group().replace(",", "")), False
