from __future__ import annotations

import re

from pokebot.enums import Retailer
from pokebot.url_parser import canonical_target_product_url, parse_product_url

_TCIN_RE = re.compile(r"^\d{5,}$")


def is_plausible_tcin(value: str | None) -> bool:
    return bool(value and _TCIN_RE.fullmatch(value.strip()))


def resolve_target_tcin(*, url: str | None = None, sku: str | None = None) -> str | None:
    """Prefer a numeric TCIN from the product URL; fall back to sku only if numeric.

    Never treat labels like ``TEST-SKU`` as a TCIN — Target's cart API 400s on those.
    """
    if url:
        try:
            retailer, tcin = parse_product_url(url)
            if retailer == Retailer.TARGET and is_plausible_tcin(tcin):
                return tcin
        except ValueError:
            pass
        cleaned = canonical_target_product_url(url)
        if cleaned:
            try:
                _, tcin = parse_product_url(cleaned)
                if is_plausible_tcin(tcin):
                    return tcin
            except ValueError:
                pass
    if is_plausible_tcin(sku):
        return sku.strip()  # type: ignore[union-attr]
    return None


def resolve_target_product_url(url: str, *, tcin: str | None = None) -> str:
    cleaned = canonical_target_product_url(url)
    if cleaned:
        return cleaned
    if tcin and is_plausible_tcin(tcin):
        return f"https://www.target.com/p/-/A-{tcin}"
    return url
