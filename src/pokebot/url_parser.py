from __future__ import annotations

import re
from urllib.parse import parse_qs, unquote, urlparse

from pokebot.enums import Retailer


TARGET_TCIN_RE = re.compile(r"/A-(\d+)", re.IGNORECASE)
WALMART_ITEM_RE = re.compile(r"/ip/[^/]+/(\d+)")


def parse_product_url(url: str) -> tuple[Retailer, str]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    path = parsed.path

    if "target.com" in host:
        match = TARGET_TCIN_RE.search(path + parsed.fragment)
        if not match:
            raise ValueError(f"Could not extract Target TCIN from URL: {url}")
        return Retailer.TARGET, match.group(1)

    if "walmart.com" in host:
        match = WALMART_ITEM_RE.search(path)
        if not match:
            raise ValueError(f"Could not extract Walmart item ID from URL: {url}")
        return Retailer.WALMART, match.group(1)

    raise ValueError(f"Unsupported retailer URL: {url}")


def canonical_target_product_url(url: str) -> str | None:
    """Return a clean Target PDP URL (no affiliate query params) when TCIN is known."""
    try:
        retailer, tcin = parse_product_url(url)
    except ValueError:
        return None
    if retailer != Retailer.TARGET:
        return None
    return f"https://www.target.com/p/-/A-{tcin}"


def _resolve_walmart_url(url: str) -> str:
    """Unwrap goto.walmart.com affiliate links to the embedded product URL."""
    parsed = urlparse(url)
    if "goto.walmart.com" in parsed.netloc.lower():
        embedded = parse_qs(parsed.query).get("u", [None])[0]
        if embedded:
            return unquote(embedded)
    return url


def canonical_walmart_product_url(url: str) -> str | None:
    """Return a clean Walmart PDP URL when the item ID is known."""
    url = _resolve_walmart_url(url)
    try:
        retailer, item_id = parse_product_url(url)
    except ValueError:
        match = WALMART_ITEM_RE.search(urlparse(url).path)
        if not match:
            return None
        item_id = match.group(1)
        retailer = Retailer.WALMART
    if retailer != Retailer.WALMART:
        return None
    return f"https://www.walmart.com/ip/-/{item_id}"
